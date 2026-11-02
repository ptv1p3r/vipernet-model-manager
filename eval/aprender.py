#!/usr/bin/env python3
"""Ciclo de aprendizagem do Vipernet Model Manager.

Porque existe: o classificador de complexidade tem duas vias — a heuristica
(`analise.py`, pesos escritos a mao) e o modelo por perfil. As duas foram
afinadas com um corpus pequeno e ficaram paradas. Este ciclo fecha o laco:
colhe pedidos reais, rotula-os com um juiz calibrado, procura pesos melhores,
mede-os num conjunto que o treino nunca viu, e so promove se ganhar.

O que este ciclo NAO faz, e por desenho:

  - nao aprende com as proprias decisoes. Um classificador que se treina na
    sua propria saida converge para os seus proprios vicios: as decisoes
    erradas voltam como rotulos. O rotulo vem de um juiz externo (outro
    modelo) e a confianca desse juiz e medida contra rotulos humanos.
  - nao promove sem portao. Se o juiz discordar dos rotulos humanos abaixo de
    `--kappa-minimo`, o ciclo para e escreve relatorio. Um juiz que nao
    concorda com pessoas nao tem autoridade para mudar nada.
  - nao reusa casos. O cursor guarda o sha1 de cada pedido ja rotulado; o
    conjunto de avaliacao de um ciclo nunca entra no treino do seguinte.
  - nao escreve conversa no repositorio. A amostra vive em
    `plugin-data/vipernet-model-manager/aprendiz/` (dados, nao codigo).

Uso:

    python3 eval/aprender.py                    # corre e escreve relatorio
    python3 eval/aprender.py --promover         # aplica se os portoes passarem
    python3 eval/aprender.py --abc-modelo       # compara variantes de prompt
    python3 eval/aprender.py --reverter         # volta aos pesos anteriores

Codigos de saida: 0 nada a fazer / tudo ok; 1 ha accao a fazer (portao
fechado, ou promovido); 2 uso invalido; 4 ambiente em falta.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as dt
import hashlib
import importlib.util
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parent
REPO = EVAL.parent
RAIZ = Path("/opt/data")
DADOS = RAIZ / "plugin-data/vipernet-model-manager"
LEDGER = DADOS / "uso.jsonl"
CONFIG = DADOS / "config.json"
APRENDIZ = DADOS / "aprendiz"
CURSOR = APRENDIZ / "cursor.json"
HISTORICO = APRENDIZ / "historico.jsonl"
RELATORIOS = APRENDIZ / "relatorios"

NIVEIS = ("leve", "media", "alta")
SEMENTE = 20261102
CHAVE_TEXTO = 400

# Os modulos do plugin importam-se pelo nome simples quando carregados fora de
# um pacote (`analise_modelo` faz `import gestor`). Com a raiz no path, o ciclo
# treina exactamente o codigo que corre em producao.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

# `seams.resolve_runtime_provider` importa modulos do core (`hermes_cli`), que
# vivem na raiz da instalacao do Hermes e nao no site-packages do python que
# corre este script. Sem isto, em cron, o juiz falha com "seam indisponivel" —
# que se le como falta de acesso quando e falta de path.
_INSTALACAO = Path(os.environ.get("HERMES_INSTALL_ROOT") or "/opt/hermes")
if (_INSTALACAO / "hermes_cli").is_dir() and str(_INSTALACAO) not in sys.path:
    sys.path.insert(0, str(_INSTALACAO))

#: Prompt do juiz. Nao e o `PROMPT` do classificador de proposito: um juiz com
#: o mesmo texto do avaliado mede a concordancia do modelo consigo mesmo, nao a
#: qualidade. Este e ancorado na RUBRICA (eixos, contra-ancoras) e mais longo.
PROMPT_JUIZ = (
    "Classificas o nivel de complexidade de um pedido feito a um agente de engenharia.\n"
    "Responde APENAS com um objecto json valido, sem texto fora dele:\n"
    '{"nivel": "<leve|media|alta>"}\n'
    "O nivel e o modelo MAIS BARATO que resolve o pedido com qualidade aceitavel. "
    "Nao e quao dificil o texto parece, nem o trabalho que deu responder.\n"
    "leve: saudacao, confirmacao, agradecimento, pergunta directa de uma linha, "
    "extrair um valor, sim/nao, pedir esclarecimento, reformular.\n"
    "media: resumir texto longo, traduzir, redigir, corrigir com sintoma claro, "
    "alterar um ficheiro identificado, escrever um teste para funcao nomeada, "
    "manter um sistema vivo, plano de uma tarefa concreta, explicar um conceito.\n"
    "alta: desenhar arquitetura ou decidir trade-offs, depurar sem reproducao nem "
    "mensagem, migrar com rollback, analisar varios ficheiros ou servicos em "
    "conjunto, otimizar um algoritmo, provar correcao, auditoria de sistema.\n"
    "NAO mudam o nivel: registo informal, giria, abreviaturas, erros ortograficos, "
    "urgencia, ou o pedido ser sobre uma avaria. Avalia o trabalho pedido, nao a forma."
)

#: Juiz de duas fronteiras. Medido: o juiz de tres classes concorda com os
#: rotulos humanos em kappa 0,53, e quase todo o erro e `media` -> `leve`; a
#: classe do meio nao tem fronteira operacional, e forcar uma regra de fronteira
#: piorou (0,35). Duas perguntas binarias sao mensuraveis em separado e cada uma
#: corresponde exactamente a um limiar que a producao usa.
PROMPT_JUIZ2 = (
    "Avalias um pedido feito a um agente de engenharia respondendo a DUAS perguntas "
    "binarias, cada uma independente.\n"
    "Responde APENAS com um objecto json valido e nada fora dele:\n"
    '{"leve": <true|false>, "alta": <true|false>}\n'
    "1) `leve` - e verdade se o pedido NAO exige produzir um artefacto nem tocar em "
    "nada: e conversa, confirmacao, pergunta de uma linha, extrair um valor, sim/nao, "
    "pedir esclarecimento. Se pede para fazer, alterar, escrever, corrigir ou "
    "verificar alguma coisa, entao `leve` e false.\n"
    "2) `alta` - e verdade se o pedido exige desenhar arquitetura ou decidir "
    "trade-offs, depurar sem reproducao nem mensagem de erro, mexer em varios "
    "ficheiros ou servicos em conjunto, provar correcao, otimizar um algoritmo ou "
    "auditar um sistema. Um pedido que produz um artefacto simples, com alvo "
    "conhecido e sem essas caracteristicas, nao e `alta`.\n"
    "As duas podem ser false ao mesmo tempo (trabalho de rotina com alvo conhecido). "
    "Nao mudam nenhuma das respostas: registo informal, giria, abreviaturas, erros "
    "ortograficos, urgencia do utilizador, ou o pedido ser sobre uma avaria."
)

JUIZ_3 = "3classes"
JUIZ_2 = "2fronteiras"


# --------------------------------------------------------------------------- modulos


def garantir_interpretador() -> None:
    """Re-executa no python do Hermes quando o seam de credenciais nao resolve.

    O ciclo precisa de `resolve_runtime_provider`, que vive na instalacao do
    Hermes e depende das dependencias dela. Corrido com outro python (cron, shell
    limpa, `python3` do sistema) o juiz nao resolve credencial e o ciclo morre
    com "seam indisponivel" — sintoma que se le como falta de acesso. Medido:
    `/usr/local/bin/python3` nao tem `ruamel`, `/opt/hermes/.venv/bin/python` tem.
    """
    if os.environ.get("VMM_APRENDER_REEXEC") == "1":
        return
    try:
        import ruamel.yaml  # noqa: F401,PLC0415
        from hermes_cli.runtime_provider import resolve_runtime_provider  # noqa: F401,PLC0415

        return
    except Exception:  # noqa: BLE001
        pass
    candidato = _INSTALACAO / ".venv/bin/python"
    if not candidato.exists():
        return
    os.environ["VMM_APRENDER_REEXEC"] = "1"
    os.execve(str(candidato), [str(candidato), __file__, *sys.argv[1:]],
              {**os.environ, "PYTHONPATH": str(_INSTALACAO)})


def _mod(nome: str):
    """Carrega um modulo do plugin por ficheiro (nao esta instalado como pacote)."""
    spec = importlib.util.spec_from_file_location(f"vmm_aprender_{nome}", REPO / f"{nome}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"nao consegui carregar {nome}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"vmm_aprender_{nome}"] = m
    spec.loader.exec_module(m)
    return m


def _analise():
    return _mod("analise")


def _modelo():
    return _mod("analise_modelo")


def _gestor():
    return _mod("gestor")


# --------------------------------------------------------------------------- utilidades


def _agora() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha(texto: str) -> str:
    return hashlib.sha1(texto[:CHAVE_TEXTO].encode("utf-8")).hexdigest()[:12]


def _ler_json(caminho: Path, omissao: Any) -> Any:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return omissao


def _escrever_json(caminho: Path, dados: Any) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, caminho)


# --------------------------------------------------------------------------- metricas


def conteudo_json(resposta: dict) -> dict[str, Any]:
    """Objecto JSON que o modelo devolveu.

    `_pedir_com_formato` devolve o corpo da API em `json`, nao o conteudo
    ja parseado: o conteudo vive em `choices[0].message.content`. Ler
    `resposta["json"]` como se fosse a resposta foi um erro medido — deu
    "json sem campos" em todos os casos.
    """
    corpo = resposta.get("json") or {}
    try:
        texto = corpo["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return {}
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.split("\n", 1)[-1]
        if "```" in texto:
            texto = texto.rsplit("```", 1)[0]
    i, j = texto.find("{"), texto.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        d = json.loads(texto[i:j + 1])
    except json.JSONDecodeError:
        return {}
    return d if isinstance(d, dict) else {}


def _f1_por_classe(pares: list[tuple[str, str]]) -> dict[str, dict[str, float]]:
    saida: dict[str, dict[str, float]] = {}
    for c in NIVEIS:
        vp = sum(1 for r, p in pares if r == c and p == c)
        fp = sum(1 for r, p in pares if r != c and p == c)
        fn = sum(1 for r, p in pares if r == c and p != c)
        prec = vp / (vp + fp) if vp + fp else 0.0
        rec = vp / (vp + fn) if vp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        saida[c] = {"precisao": round(prec, 4), "cobertura": round(rec, 4),
                    "f1": round(f1, 4), "suporte": sum(1 for r, _ in pares if r == c)}
    return saida


def metricas(pares: list[tuple[str, str]]) -> dict[str, Any]:
    if not pares:
        # Com `por_classe` vazio, quem consome (`treinar`, o relatorio) estoura
        # com KeyError a meio do ciclo; a forma tem de ser a mesma com 0 casos.
        vazio = {"precisao": 0.0, "cobertura": 0.0, "f1": 0.0, "suporte": 0}
        return {"n": 0, "macro_f1": 0.0, "acerto": 0.0,
                "por_classe": {c: dict(vazio) for c in NIVEIS},
                "confusao": {r: {p: 0 for p in NIVEIS} for r in NIVEIS}}
    por_classe = _f1_por_classe(pares)
    macro = sum(v["f1"] for v in por_classe.values()) / len(NIVEIS)
    acerto = sum(1 for r, p in pares if r == p) / len(pares)
    confusao: dict[str, dict[str, int]] = {r: {p: 0 for p in NIVEIS} for r in NIVEIS}
    for r, p in pares:
        confusao[r][p] += 1
    return {"n": len(pares), "macro_f1": round(macro, 4), "acerto": round(acerto, 4),
            "por_classe": por_classe, "confusao": confusao}


def kappa(a: list[dict], b: list[dict]) -> dict[str, Any]:
    """Kappa de Cohen entre dois rotuladores, por id comum.

    Generico no vocabulario: serve as tres classes e tambem perguntas binarias
    (`sim`/`nao`). Preso a `NIVEIS`, um juiz binario devolvia o acordo simples
    como se fosse kappa — numero com aspecto de validado e sem significado.
    """
    mapa_b = {r["id"]: r["rotulo"] for r in b}
    pares = [(r["rotulo"], mapa_b[r["id"]]) for r in a if r["id"] in mapa_b]
    if not pares:
        return {"n": 0, "kappa": 0.0, "acordo": 0.0}
    obs = sum(1 for x, y in pares if x == y) / len(pares)
    ca = collections.Counter(x for x, _ in pares)
    cb = collections.Counter(y for _, y in pares)
    categorias = set(ca) | set(cb)
    esp = sum((ca[c] / len(pares)) * (cb[c] / len(pares)) for c in categorias)
    k = (obs - esp) / (1 - esp) if esp < 1 else 0.0
    return {"n": len(pares), "kappa": round(k, 4), "acordo": round(obs, 4)}


# --------------------------------------------------------------------------- colheita


def _pool() -> list[dict]:
    """Todos os prompts de utilizador distintos. Reusa o extractor, uma so casa."""
    import extrair_amostra as ea  # noqa: PLC0415 — irmao no mesmo directorio

    return ea._pool_de_sessoes()


def _textos_gold() -> set[str]:
    """Shas dos casos que ja serviram para desenvolver o classificador.

    Barreira de contaminacao: o `gold` foi lido enquanto se afinava a
    heuristica, logo nao pode entrar como avaliacao de um ciclo que muda os
    pesos. Fica de fora do conjunto de avaliacao, para sempre.
    """
    out: set[str] = set()
    for nome in ("gold.jsonl", "gold2.jsonl"):
        caminho = EVAL / nome
        if not caminho.exists():
            continue
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha:
                continue
            try:
                d = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if d.get("texto"):
                out.add(_sha(d["texto"]))
    return out


def colher(limite: int, semente: int) -> tuple[list[dict], dict[str, Any]]:
    cursor = _ler_json(CURSOR, {}) or {}
    usados = set(cursor.get("usados") or [])
    gold = _textos_gold()

    pool = _pool()
    frescos = [p for p in pool if p["id"] not in usados and p["id"] not in gold]
    if not frescos:
        return [], {"pool": len(pool), "usados": len(usados), "gold_excluido": len(gold),
                    "motivo": "sem pedidos novos desde o ultimo ciclo"}

    def faixa(n: int) -> str:
        return "12-60" if n < 60 else "60-200" if n < 200 else "200-800" if n < 800 else "800+"

    estratos: dict[str, list[dict]] = collections.defaultdict(list)
    for p in frescos:
        estratos[faixa(p["comprimento"])].append(p)

    rnd = random.Random(semente)
    por_estrato = max(1, limite // max(1, len(estratos)))
    escolhidos: list[dict] = []
    for nome in sorted(estratos):
        grupo = sorted(estratos[nome], key=lambda p: p["id"])
        rnd.shuffle(grupo)
        escolhidos.extend(grupo[:por_estrato])
    rnd.shuffle(escolhidos)
    escolhidos = escolhidos[:limite]
    return escolhidos, {"pool": len(pool), "frescos": len(frescos), "usados": len(usados),
                        "gold_excluido": len(gold),
                        "estratos": {k: len(v) for k, v in sorted(estratos.items())}}


# --------------------------------------------------------------------------- juiz


def _juiz_definicao(provider: str, modelo: str) -> dict[str, Any]:
    definicao, erros = _modelo().normalizar({
        "activo": True, "provider": provider, "modelo": modelo,
        "esforco": "baixo", "formato_json": "auto",
        "max_tokens": 1200, "timeout_s": 30, "cache_ttl_s": 0,
    })
    if erros:
        raise SystemExit(f"definicao do juiz invalida: {erros}")
    return definicao


def _etiquetar(casos: list[dict], definicao: dict[str, Any], resolver, threads: int) -> list[dict]:
    modelo = _modelo()

    def um(caso: dict) -> dict:
        # Sem rede de seguranca de proposito: uma assinatura que nao aceite
        # `prompt` tem de estourar aqui, e nao virar "sem nivel" — o sintoma
        # seria um juiz que recusa tudo e um kappa de zero sem causa visivel.
        r = modelo.classificar([{"role": "user", "content": caso["texto"]}],
                               definicao, resolver, perfil="aprendiz", prompt=PROMPT_JUIZ)
        if r is None:
            # `classificar` devolve None quando a rota esta suspensa depois de
            # falhar (comportamento documentado). Uma falha transitoria suspende
            # a rota para todos os casos seguintes, por isso espera-se e tenta-se
            # uma vez; sem isto, um unico 429 apagava o resto do lote.
            time.sleep(3.0)
            r = modelo.classificar([{"role": "user", "content": caso["texto"]}],
                                   definicao, resolver, perfil="aprendiz", prompt=PROMPT_JUIZ)
        if r is None:
            return {"id": caso["id"], "rotulo": None, "erro": "rota suspensa apos falha"}
        nivel = (r.get("nivel") or "").strip().lower()
        if nivel not in NIVEIS:
            return {"id": caso["id"], "rotulo": None, "erro": r.get("erro") or r.get("motivo") or "sem nivel"}
        return {"id": caso["id"], "rotulo": nivel, "ms": r.get("ms")}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        return list(ex.map(um, casos))


def _resolver():
    return _mod("seams").resolver_provider


# --------------------------------------------------------------------------- treino


def decidir_score(score: int, limiares: dict[str, int]) -> str:
    if score >= int(limiares.get("alta", 6)):
        return "alta"
    if score >= int(limiares.get("media", 3)):
        return "media"
    return "leve"


def avaliar_pesos(pesos: dict[str, int], casos: list[dict], limiares: dict[str, int]) -> dict[str, Any]:
    """Macro-F1 dos pesos sobre casos rotulados. Reusa a funcao de producao."""
    analise = _analise()
    pares: list[tuple[str, str]] = []
    for caso in casos:
        rotulo = caso.get("rotulo")
        if rotulo not in NIVEIS:
            continue
        r = analise.pontuar([{"role": "user", "content": caso["texto"]}], pesos=pesos)
        pares.append((rotulo, decidir_score(int(r.get("score") or 0), limiares)))
    m = metricas(pares)
    m["pares"] = pares
    return m


def treinar(pesos_atuais: dict[str, int], casos: list[dict], limiares: dict[str, int],
            passes: int = 2, maximo: int = 8) -> tuple[dict[str, int], dict[str, Any]]:
    """Subida por coordenadas sobre os pesos discretos.

    Discreto de proposito: os pesos sao inteiros na config e o score e uma
    soma; um modelo continuo obrigaria a mudar a forma do score em producao.
    A funcao objectivo e a macro-F1 com desempate pela F1 de `media`, que e a
    classe que mais se perde (medido: cobertura 0,36 na heuristica).
    """
    melhor = dict(pesos_atuais)
    m_atual = avaliar_pesos(melhor, casos, limiares)

    def chave(m: dict[str, Any]) -> tuple[float, float, float]:
        return (m["macro_f1"], m["por_classe"]["media"]["f1"], m["acerto"])

    melhor_chave = chave(m_atual)
    for passe in range(max(0, passes)):
        mudou = False
        for nome in sorted(melhor):
            base = melhor[nome]
            for valor in range(0, maximo + 1):
                if valor == base:
                    continue
                cand = dict(melhor)
                cand[nome] = valor
                m = avaliar_pesos(cand, casos, limiares)
                if chave(m) > melhor_chave:
                    melhor, melhor_chave, mudou = cand, chave(m), True
            # a coordenada seguinte parte do melhor desta
        if not mudou:
            break
    m_final = avaliar_pesos(melhor, casos, limiares)
    return melhor, {"antes": m_atual, "depois": m_final,
                    "mudancas": {k: [pesos_atuais[k], melhor[k]] for k in melhor
                                 if melhor[k] != pesos_atuais.get(k)}}


# --------------------------------------------------------------------------- portoes


def portoes(k: dict[str, Any], antes: dict[str, Any], depois: dict[str, Any],
            kappa_minimo: float, ganho_minimo: float) -> tuple[bool, list[str]]:
    razoes: list[str] = []
    if k.get("n", 0) < 20:
        razoes.append(f"calibracao insuficiente: {k.get('n', 0)} casos comuns com o gold")
    if k.get("kappa", 0.0) < kappa_minimo:
        razoes.append(f"juiz discorda dos rotulos humanos (kappa {k.get('kappa')} < {kappa_minimo})")
    ganho = round(depois["macro_f1"] - antes["macro_f1"], 4)
    if ganho < ganho_minimo:
        razoes.append(f"ganho insuficiente (macro-F1 {ganho:+.4f} < {ganho_minimo})")
    if depois["por_classe"]["media"]["f1"] < antes["por_classe"]["media"]["f1"]:
        razoes.append("F1 de `media` desceu")
    for c in NIVEIS:
        queda = antes["por_classe"][c]["f1"] - depois["por_classe"][c]["f1"]
        if queda > 0.05:
            razoes.append(f"F1 de `{c}` caiu {queda:.2f} (limite 0.05)")
    return (not razoes), razoes


# --------------------------------------------------------------------------- modelo (A/B)


def abc_modelo(casos: list[dict], definicao: dict[str, Any], resolver,
               variantes: list[str], repeticoes: int, threads: int) -> dict[str, Any]:
    """Compara variantes de prompt do classificador no mesmo conjunto rotulado."""
    modelo = _modelo()
    out: dict[str, Any] = {}
    for variante in variantes:
        prompt = modelo.PROMPTS.get(variante)
        if prompt is None:
            out[variante] = {"erro": "variante desconhecida"}
            continue
        execucoes: list[dict[str, Any]] = []
        for _ in range(max(1, repeticoes)):
            def um(caso: dict) -> tuple[str, str]:
                r = modelo.classificar([{"role": "user", "content": caso["texto"]}],
                                       definicao, resolver, perfil="abc", prompt=prompt)
                return caso["rotulo"], (r.get("nivel") or "").strip().lower()
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
                pares = list(ex.map(um, casos))
            m = metricas([(r, p) for r, p in pares if p in NIVEIS])
            execucoes.append({"macro_f1": m["macro_f1"], "por_classe": m["por_classe"],
                              "n": m["n"]})
        macros = [e["macro_f1"] for e in execucoes]
        out[variante] = {"execucoes": execucoes,
                         "macro_f1_media": round(sum(macros) / len(macros), 4),
                         "macro_f1_min": min(macros), "macro_f1_max": max(macros),
                         "media_f1": round(sum(e["por_classe"]["media"]["f1"] for e in execucoes)
                                           / len(execucoes), 4)}
    return out


# --------------------------------------------------------------------------- promocao


def _config() -> dict[str, Any]:
    return _ler_json(CONFIG, {}) or {}


def promover(pesos: dict[str, int], meta: dict[str, Any], variante: str | None = None) -> dict[str, Any]:
    """Grava os pesos aprendidos na config viva, pelo caminho do gestor."""
    cfg = _config()
    if not isinstance(cfg.get("analise"), dict):
        cfg["analise"] = {}
    cfg["analise"]["pesos"] = dict(pesos)
    cfg["analise"]["aprendiz"] = meta
    if variante:
        bloco = cfg["analise"].get("classificador")
        if isinstance(bloco, dict):
            bloco["prompt_variante"] = variante
    erros = _gestor().validar(cfg) if hasattr(_gestor(), "validar") else []
    if erros:
        return {"ok": False, "erros": erros}
    caminho = _gestor().gravar(cfg)
    return {"ok": True, "config": str(caminho)}


def reverter() -> dict[str, Any]:
    """Volta aos pesos do ciclo anterior, lendo o historico."""
    linhas = []
    if HISTORICO.exists():
        for linha in HISTORICO.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha:
                continue
            try:
                linhas.append(json.loads(linha))
            except json.JSONDecodeError:
                continue
    promovidos = [x for x in linhas if x.get("estado") == "promovido"]
    if len(promovidos) < 2:
        return {"ok": False, "erro": "nao ha ciclo anterior para restaurar"}
    anterior = promovidos[-2].get("pesos")
    cfg = _config()
    if not isinstance(cfg.get("analise"), dict):
        cfg["analise"] = {}
    if anterior:
        cfg["analise"]["pesos"] = anterior
    else:
        cfg["analise"].pop("pesos", None)
    cfg["analise"].pop("aprendiz", None)
    caminho = _gestor().gravar(cfg)
    return {"ok": True, "config": str(caminho), "pesos": anterior}


# --------------------------------------------------------------------------- relatorio


def _relatorio(dados: dict[str, Any]) -> str:
    L: list[str] = []
    L.append(f"# Ciclo de aprendizagem — {dados['ts']}\n")
    L.append(f"- juiz: `{dados['juiz']}`")
    L.append(f"- casos rotulados: {dados['casos']} (colheita: {dados['colheita']})")
    L.append(f"- calibracao do juiz: kappa {dados['kappa'].get('kappa')} "
             f"em {dados['kappa'].get('n')} casos do gold (acordo {dados['kappa'].get('acordo')})")
    L.append(f"- portoes: {'PASS' if dados['passou'] else 'FALHOU'}")
    for r in dados["razoes"]:
        L.append(f"  - {r}")
    if dados.get("pesos"):
        L.append("\n## Pesos\n")
        L.append("| sinal | antes | depois |")
        L.append("|---|---|---|")
        for k, (a, b) in sorted(dados["mudancas"].items()):
            L.append(f"| {k} | {a} | {b} |")
    for rotulo, m in (("antes", dados.get("antes")), ("depois", dados.get("depois"))):
        if not m:
            continue
        L.append(f"\n## {rotulo}: macro-F1 {m['macro_f1']} · acerto {m['acerto']} (n={m['n']})\n")
        L.append("| classe | precisao | cobertura | F1 | suporte |")
        L.append("|---|---|---|---|---|")
        for c in NIVEIS:
            v = m["por_classe"][c]
            L.append(f"| {c} | {v['precisao']} | {v['cobertura']} | {v['f1']} | {v['suporte']} |")
    if dados.get("abc"):
        L.append("\n## Variantes de prompt\n")
        for nome, v in dados["abc"].items():
            if "erro" in v:
                L.append(f"- {nome}: {v['erro']}")
            else:
                L.append(f"- {nome}: macro-F1 {v['macro_f1_media']} "
                         f"({v['macro_f1_min']}–{v['macro_f1_max']}), media F1 {v['media_f1']}")
    if dados.get("falhas"):
        L.append("\n## Casos que o juiz recusou\n")
        for f in dados["falhas"][:10]:
            L.append(f"- {f['id']}: {f.get('erro')}")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- CLI


def main() -> int:
    ap = argparse.ArgumentParser(description="Ciclo de aprendizagem do ViperNet Model Manager")
    ap.add_argument("--casos", type=int, default=240, help="pedidos a rotular neste ciclo")
    ap.add_argument("--juiz-provider", default="deepseek")
    ap.add_argument("--juiz-modelo", default="deepseek-v4-pro")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--kappa-minimo", type=float, default=0.55)
    ap.add_argument("--ganho-minimo", type=float, default=0.02)
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--semente", type=int, default=SEMENTE)
    ap.add_argument("--promover", action="store_true", help="aplicar se os portoes passarem")
    ap.add_argument("--abc-modelo", action="store_true", help="comparar variantes de prompt")
    ap.add_argument("--repeticoes", type=int, default=2)
    ap.add_argument("--reverter", action="store_true", help="restaurar os pesos do ciclo anterior")
    ap.add_argument("--so-treinar", default="", help="ficheiro jsonl rotulado: salta juiz e calibracao")
    args = ap.parse_args()

    garantir_interpretador()

    if args.reverter:
        r = reverter()
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1

    APRENDIZ.mkdir(parents=True, exist_ok=True)
    RELATORIOS.mkdir(parents=True, exist_ok=True)

    cfg = _config()
    analise_cfg = cfg.get("analise") or {}
    limiares = analise_cfg.get("limiares") or {"media": 3, "alta": 6}
    pesos_atuais = dict(_analise().PESOS)
    pesos_atuais.update(analise_cfg.get("pesos") or {})

    # 1. colheita
    if args.so_treinar:
        casos = [json.loads(l) for l in Path(args.so_treinar).read_text(encoding="utf-8").splitlines()
                 if l.strip()]
        colheita = {"fonte": args.so_treinar, "n": len(casos)}
    else:
        casos, colheita = colher(args.casos, args.semente)
    if not casos:
        print(json.dumps({"ok": False, "colheita": colheita}, ensure_ascii=False))
        return 0

    # 2. juiz + calibracao
    falhas: list[dict] = []
    k: dict[str, Any] = {}
    if not args.so_treinar:
        definicao = _juiz_definicao(args.juiz_provider, args.juiz_modelo)
        resolver = _resolver()
        rotulos = _etiquetar(casos, definicao, resolver, args.threads)
        mapa = {r["id"]: r for r in rotulos}
        falhas = [r for r in rotulos if not r.get("rotulo")]
        casos = [dict(c, rotulo=mapa[c["id"]]["rotulo"]) for c in casos
                 if mapa.get(c["id"], {}).get("rotulo")]
        # calibracao contra os rotulos humanos do gold
        gold = []
        for nome in ("gold.jsonl", "gold2.jsonl"):
            caminho = EVAL / nome
            if not caminho.exists():
                continue
            for linha in caminho.read_text(encoding="utf-8").splitlines():
                linha = linha.strip()
                if not linha:
                    continue
                d = json.loads(linha)
                if d.get("esperado") in NIVEIS:
                    gold.append({"id": f"{nome}:{d['id']}", "texto": d["texto"],
                                 "rotulo": d["esperado"]})
        if gold:
            rot_gold = _etiquetar(gold, definicao, resolver, args.threads)
            k = kappa(gold, [r for r in rot_gold if r.get("rotulo")])
        if not casos:
            print(json.dumps({"ok": False, "falhas": falhas[:5], "colheita": colheita},
                             ensure_ascii=False))
            return 1

    # 3. treino e avaliacao
    #
    # Separacao treino/validacao obrigatoria: treinar e avaliar no mesmo
    # conjunto mede a capacidade de decorar. Medido num ciclo de 20 casos, a
    # macro-F1 "subia" de 0,44 para 0,70 no proprio conjunto de treino. O portao
    # decide sobre a validacao, que o treino nunca ve.
    ordenados = sorted(casos, key=lambda c: str(c["id"]))
    random.Random(args.semente).shuffle(ordenados)
    corte = max(1, int(len(ordenados) * 0.7))
    treino, validacao = ordenados[:corte], ordenados[corte:]
    if not validacao:
        validacao = treino
    pesos_novos, resultado = treinar(pesos_atuais, treino, limiares, passes=args.passes)
    antes_val = avaliar_pesos(pesos_atuais, validacao, limiares)
    depois_val = avaliar_pesos(pesos_novos, validacao, limiares)
    resultado["antes"] = antes_val
    resultado["depois"] = depois_val
    passou, razoes = portoes(k, antes_val, depois_val,
                             args.kappa_minimo, args.ganho_minimo)

    # 4. A/B do modelo (opcional)
    abc = None
    variante_vencedora = None
    if args.abc_modelo:
        bloco = (analise_cfg.get("classificador") or {})
        provider = bloco.get("provider") or args.juiz_provider
        modelo_nome = bloco.get("modelo") or args.juiz_modelo
        definicao_m = _juiz_definicao(provider, modelo_nome)
        amostra = casos[:min(120, len(casos))]
        abc = abc_modelo(amostra, definicao_m, _resolver(),
                         ["v1", "v2", "v3"], args.repeticoes, args.threads)
        validos = {n: v for n, v in abc.items() if "macro_f1_media" in v}
        if validos:
            melhor = max(validos, key=lambda n: (validos[n]["macro_f1_media"],
                                                 validos[n]["media_f1"]))
            atual = bloco.get("prompt_variante") or "v1"
            if melhor != atual and validos[melhor]["macro_f1_media"] > validos.get(
                    atual, {"macro_f1_media": 0})["macro_f1_media"] + 0.01:
                variante_vencedora = melhor

    # 5. registo
    meta = {"ts": _agora(), "juiz": f"{args.juiz_provider}/{args.juiz_modelo}",
            "kappa": k, "antes": resultado["antes"]["macro_f1"],
            "depois": resultado["depois"]["macro_f1"], "casos": len(casos),
            "passou": bool(passou), "razoes": razoes,
            "variante": variante_vencedora}

    estado = "promovido" if (args.promover and passou) else "relatorio"
    registo = {"ts": meta["ts"], "estado": estado, **meta, "pesos": pesos_novos}
    with HISTORICO.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registo, ensure_ascii=False) + "\n")

    aplicado = None
    if estado == "promovido":
        aplicado = promover(pesos_novos, meta, variante_vencedora)

    dados = {"ts": meta["ts"], "juiz": meta["juiz"], "casos": len(casos),
             "colheita": colheita, "kappa": k, "passou": passou, "razoes": razoes,
             "pesos": pesos_novos, "mudancas": resultado["mudancas"],
             "antes": resultado["antes"], "depois": resultado["depois"],
             "abc": abc, "falhas": falhas, "aplicado": aplicado, "estado": estado}
    nome = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    (RELATORIOS / f"ciclo-{nome}.md").write_text(_relatorio(dados), encoding="utf-8")
    _escrever_json(RELATORIOS / f"ciclo-{nome}.json",
                   {k2: v for k2, v in dados.items() if k2 not in ("mudancas",)})

    # cursor: os casos deste ciclo nunca mais entram
    if not args.so_treinar:
        cur = _ler_json(CURSOR, {}) or {}
        usados = sorted(set(cur.get("usados") or []) | {c["id"] for c in casos})
        _escrever_json(CURSOR, {"usados": usados, "actualizado": meta["ts"],
                                "ciclos": int(cur.get("ciclos") or 0) + 1})

    print(json.dumps({"estado": estado, "passou": passou, "razoes": razoes,
                      "kappa": k.get("kappa"), "antes": meta["antes"],
                      "depois": meta["depois"], "casos": len(casos),
                      "mudancas": resultado["mudancas"], "aplicado": aplicado,
                      "relatorio": f"ciclo-{nome}.md"}, ensure_ascii=False, indent=1))
    return 0 if (passou or estado == "relatorio") else 1


if __name__ == "__main__":
    raise SystemExit(main())
