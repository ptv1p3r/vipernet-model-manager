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
import threading
import time
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parent
REPO = EVAL.parent
RAIZ = Path("/opt/data")
DADOS = RAIZ / "plugin-data/vipernet-model-manager"
LEDGER = DADOS / "uso.jsonl"
#: Ficheiro de config do plugin. Injectável por ambiente para os testes e o
#: `--self-test` poderem exercer a promoção e a reversão sem tocar na config
#: viva: medido a 2026-11-02, sem isto o caminho `promover()`/`reverter()` nunca
#: era exercido por nenhum teste com escrita real.
CONFIG = Path(os.environ.get("VMM_CONFIG") or (DADOS / "config.json"))

#: Directorio de dados do ciclo. Sobrepõe-se por ambiente para os testes e o
#: `--self-test` poderem correr o ciclo inteiro sem escrever no historico real.
APRENDIZ = Path(os.environ.get("VMM_APRENDIZ_DIR") or (DADOS / "aprendiz"))
CURSOR = APRENDIZ / "cursor.json"
HISTORICO = APRENDIZ / "historico.jsonl"
RELATORIOS = APRENDIZ / "relatorios"

#: Gold escrito por humanos sobre conversa real. Vive FORA do repositorio: o
#: repo e publicado (espelho Gitea) e uma fila rotulada e texto de conversa —
#: `eval/gold*.jsonl` so pode ter casos sinteticos. Le-se dos dois sitios.
GOLD_HUMANO = Path(os.environ.get("VMM_GOLD_DIR") or (APRENDIZ / "gold"))

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


def _ficheiros_gold() -> list[Path]:
    """Todos os `gold*.jsonl` que contam: os do repo e os humanos, de fora.

    Duas casas, de proposito: `eval/` e publicado no espelho Gitea e so pode
    ter casos sinteticos; `GOLD_HUMANO` (por omissao em `aprendiz/gold`) tem
    as filas rotuladas sobre conversa real e nunca e publicada. Estava tudo
    escrito a mao como tuplo fixo (`gold.jsonl`, `gold2.jsonl`), logo um
    ficheiro novo era ignorado em silencio — sem erro, com kappa de aspecto
    valido.
    """
    ficheiros: list[Path] = []
    vistos: set[str] = set()
    for pasta in (EVAL, GOLD_HUMANO):
        if not pasta.is_dir():
            continue
        for caminho in sorted(pasta.glob("gold*.jsonl")):
            if caminho.name not in vistos:
                vistos.add(caminho.name)
                ficheiros.append(caminho)
    return ficheiros


def _casos_gold() -> list[dict]:
    """Casos do gold com rotulo conhecido, das duas grafias que existem.

    Os golds originais usam `esperado`; a fila de rotulagem humana usa
    `rotulo`. Aceitar so uma das grafias fazia a calibracao descartar o
    ficheiro inteiro por chave ausente, e o portao recusava sem dizer porquê.
    """
    out: list[dict] = []
    for caminho in _ficheiros_gold():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha:
                continue
            try:
                d = json.loads(linha)
            except json.JSONDecodeError:
                continue
            rotulo = d.get("esperado") or d.get("rotulo")
            if d.get("id") and d.get("texto") and rotulo in NIVEIS:
                out.append({"id": f"{caminho.name}:{d['id']}", "texto": d["texto"],
                            "rotulo": rotulo})
    return out


def _textos_gold() -> set[str]:
    """Shas dos casos que ja serviram para desenvolver o classificador.

    Barreira de contaminacao: o `gold` foi lido enquanto se afinava a
    heuristica, logo nao pode entrar como avaliacao de um ciclo que muda os
    pesos. Fica de fora do conjunto de avaliacao, para sempre. Tambem cobre a
    fila humana: um caso rotulado a mao nunca mais volta a ser colhido.
    """
    return {_sha(c["texto"]) for c in _casos_gold()}


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
    sobras: list[dict] = []
    for nome in sorted(estratos):
        grupo = sorted(estratos[nome], key=lambda p: p["id"])
        rnd.shuffle(grupo)
        escolhidos.extend(grupo[:por_estrato])
        sobras.extend(grupo[por_estrato:])
    # A divisao inteira pelos estratos deita fora o resto: pedir 40 dava 39
    # (40 // 3 = 13, e 13 x 3 = 39) e pedir 200 dava 198. `limite` e o que se
    # prometeu, por isso as sobras preenchem ate la — baralhadas, para nao
    # favorecer sempre o primeiro estrato por ordem alfabetica.
    rnd.shuffle(sobras)
    while len(escolhidos) < limite and sobras:
        escolhidos.append(sobras.pop())
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
        # `max_tokens` 1200 cortava a resposta a meio: medido a 2026-11-03, o
        # `deepseek-v4-pro` devolveu um corpo de 1182 tokens (o teto) sem nivel
        # reconhecivel, e no ciclo seguinte 71 dos 90 casos do gold morreram em
        # timeout/500 com `timeout_s` 30. O juiz raciocina antes de escrever o
        # JSON, por isso o orcamento tem de contar com o raciocinio.
        "max_tokens": 3000, "timeout_s": 120, "cache_ttl_s": 0,
    })
    if erros:
        raise SystemExit(f"definicao do juiz invalida: {erros}")
    return definicao


def _etiquetar(casos: list[dict], definicao: dict[str, Any], resolver, threads: int,
               lote: int = 0) -> list[dict]:
    if lote > 0:
        return _etiquetar_lote(casos, definicao, resolver, tamanho=lote)
    return _etiquetar_um_a_um(casos, definicao, resolver, threads)


def _etiquetar_um_a_um(casos: list[dict], definicao: dict[str, Any], resolver, threads: int) -> list[dict]:
    modelo = _modelo()

    def um(caso: dict) -> dict:
        # Sem rede de seguranca de proposito: uma assinatura que nao aceite
        # `prompt` tem de estourar aqui, e nao virar "sem nivel" — o sintoma
        # seria um juiz que recusa tudo e um kappa de zero sem causa visivel.
        r = None
        for tentativa in (1, 2):
            r = modelo.classificar([{"role": "user", "content": caso["texto"]}],
                                   definicao, resolver, perfil="aprendiz", prompt=PROMPT_JUIZ)
            if r is not None:
                break
            # `classificar` devolve None quando a rota esta suspensa: 3 falhas
            # suspendem-na 300 s. Esse disjuntor existe para nao travar cada
            # turno de um utilizador, mas num lote de 90 casos apaga o resto
            # (medido: 71 de 90 perdidos, e o portao fechou com "19 casos
            # comuns"). Aqui repoe-se o disjuntor e tenta-se outra vez.
            repor = getattr(modelo, "limpar_cache", None)
            if callable(repor):
                repor()
            time.sleep(2.0 * tentativa)
        if r is None:
            return {"id": caso["id"], "rotulo": None, "erro": "rota suspensa apos falha"}
        nivel = (r.get("nivel") or "").strip().lower()
        if nivel not in NIVEIS:
            return {"id": caso["id"], "rotulo": None, "erro": r.get("erro") or r.get("motivo") or "sem nivel"}
        return {"id": caso["id"], "rotulo": nivel, "ms": r.get("ms")}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        return list(ex.map(um, casos))


#: Tamanho do lote do juiz. Medido a 2026-11-03 no `inception/mercury-3`: 130
#: casos um a um com 4 threads dao `HTTP 429 too many requests per minute`, e a
#: corrida seguinte devolve **0/130** porque o limite e por minuto e o arranque
#: em rajada esgota-o (dois pedidos isolados 70 s depois voltaram a passar). Em
#: lotes de 10 sao 13 pedidos: cabe no limite e e o modo que o modelo faz
#: melhor (contexto longo — medido a 2026-11-02: 10 pedidos em 2,5 s e 9/10
#: acertos, contra 6,6 s e os mesmos 9/10 um a um). `0` mantem o um-a-um, que
#: serve os providers sem limite apertado.
JUIZ_LOTE_PADRAO = 10


def _etiquetar_lote(casos: list[dict], definicao: dict[str, Any], resolver,
                    tamanho: int = JUIZ_LOTE_PADRAO, pausa: float = 2.5) -> list[dict]:
    """Rotula por lotes com `classificar_lote`, com recuo proprio no 429.

    O lote paga-se em latencia e nao em qualidade (mesmo 9/10 medido), e evita
    o unico defeito que o mercury-3 trouxe: nao aceita 130 pedidos por minuto.
    Um 429 espera e repete o **mesmo** lote — tratado como falha definitiva,
    apagava o corpus e o portao fechava por falta de casos comuns, nao por
    discordancia.
    """
    modelo = _modelo()
    saida: list[dict] = []
    grupos = [casos[i:i + max(1, tamanho)] for i in range(0, len(casos), max(1, tamanho))]
    for n, grupo in enumerate(grupos, 1):
        for tentativa in (1, 2, 3):
            r = modelo.classificar_lote([c["texto"] for c in grupo], definicao, resolver,
                                       perfil="aprendiz")
            erro = str(r.get("erro") or "")
            if not erro:
                niveis = list(r.get("niveis") or [])
                for c, nivel in zip(grupo, niveis):
                    nivel = (nivel or "").strip().lower()
                    if nivel in NIVEIS:
                        saida.append({"id": c["id"], "rotulo": nivel, "lote": n})
                    else:
                        saida.append({"id": c["id"], "rotulo": None,
                                      "erro": "lote sem nivel para este caso"})
                break
            recuo = 20.0 * tentativa if "429" in erro else 2.0 * tentativa
            if tentativa == 3:
                saida.extend({"id": c["id"], "rotulo": None, "erro": erro} for c in grupo)
                break
            time.sleep(recuo)
        time.sleep(pausa)
    return saida


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


def fronteiras_operacionais(cfg: dict[str, Any]) -> dict[str, bool]:
    """Que cortes de nivel mudam de modelo na config viva.

    Medido a 2026-11-02 na config em uso: em `default` e `trabalho`, `leve` e
    `media` tem exactamente o mesmo conjunto de modelos, logo o corte entre eles
    nao muda a rota — nao ha razao para exigir que o juiz distinga duas classes
    que levam ao mesmo modelo. `alta` muda sempre. Uma fronteira conta como
    operacional se ALGUM perfil a distingue: o portao tem de cobrir o caso mais
    exigente, nao o mais comodo.
    """
    g = _gestor()
    perfis = list((cfg.get("perfis") or {}).keys()) or ["default"]
    alta = baixa = False
    for perfil in perfis:
        pcfg = g.perfil_config(cfg, perfil) or {}
        if not pcfg.get("catalogo"):
            # Sem bloco de perfil, vale o catalogo global: e o que o plugin usa.
            pcfg = cfg
        cat = g.catalogo_utilizavel(pcfg)
        modelos = {n: frozenset(str(c.get("modelo")) for c in g.candidatos(cat, n))
                   for n in NIVEIS}
        if modelos["media"] != modelos["leve"]:
            baixa = True
        if modelos["alta"] != modelos["media"] or modelos["alta"] != modelos["leve"]:
            alta = True
    return {"alta": alta, "baixa": baixa}


def kappa_fronteiras(a: list[dict], b: list[dict]) -> dict[str, Any]:
    """Kappa dos dois cortes que mudam de modelo: `alta|resto` e `media|leve`.

    Medido a 2026-11-02 com um juiz de kappa multiclasse 0,483: `alta` contra o
    resto da **0,734** (recall 1,0, 8 falsos positivos em 23). O kappa de tres
    classes penaliza duas vezes um erro de um passo e mistura um corte que muda
    de modelo com outro que, em dois dos tres perfis, nao muda nada.
    """
    mapa_b = {r["id"]: r["rotulo"] for r in b}
    comuns = [(r["rotulo"], mapa_b[r["id"]]) for r in a
              if r["id"] in mapa_b and r["rotulo"] in NIVEIS and mapa_b[r["id"]] in NIVEIS]

    def binario(pares: list[tuple[str, str]]) -> dict[str, Any]:
        if not pares:
            return {"n": 0, "kappa": 0.0}
        obs = sum(1 for x, y in pares if x == y) / len(pares)
        ca = collections.Counter(x for x, _ in pares)
        cb = collections.Counter(y for _, y in pares)
        esp = sum((ca[c] / len(pares)) * (cb[c] / len(pares)) for c in set(ca) | set(cb))
        k = (obs - esp) / (1 - esp) if esp < 1 else 0.0
        vp = sum(1 for x, y in pares if x == "sim" and y == "sim")
        prev = sum(1 for _, y in pares if y == "sim")
        return {"n": len(pares), "kappa": round(k, 4), "acordo": round(obs, 4),
                "precisao": round(vp / prev, 3) if prev else None}

    alta = binario([("sim" if x == "alta" else "nao", "sim" if y == "alta" else "nao")
                    for x, y in comuns])
    baixa = binario([(x, y) for x, y in comuns if x != "alta" and y != "alta"])
    return {"alta": alta, "baixa": baixa}


def fronteiras_estaveis(fronteiras: dict[str, Any],
                        historico: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pior valor de cada corte entre esta corrida e a anterior.

    Medido a 2026-11-02: a mesma fronteira `alta`, sobre os mesmos 90 casos do
    gold e com o mesmo prompt, deu 0,734 numa medicao isolada e 0,5385 dentro do
    ciclo. Um juiz que oscila assim nao tem autoridade por uma corrida boa — e
    exigi-lo custa zero, porque a corrida anterior ja esta no historico. Sem
    historico utilizavel, vale a corrida actual.
    """
    anterior = None
    for registo in reversed(historico or []):
        f = registo.get("fronteiras")
        if isinstance(f, dict) and f:
            anterior = f
            break
    if not anterior:
        return fronteiras, {"comparado": False, "motivo": "sem corrida anterior com fronteiras"}
    pior: dict[str, Any] = {}
    for nome, valor in fronteiras.items():
        antes = anterior.get(nome) if isinstance(anterior, dict) else None
        if not isinstance(antes, dict):
            pior[nome] = valor
            continue
        pior[nome] = dict(valor)
        pior[nome]["kappa"] = min(float(valor.get("kappa", 0.0)),
                                  float(antes.get("kappa", 0.0)))
        pior[nome]["kappa_corrida_anterior"] = antes.get("kappa")
    return pior, {"comparado": True,
                  "anterior": {k: v.get("kappa") for k, v in anterior.items()
                               if isinstance(v, dict)}}


def portoes(k: dict[str, Any], antes: dict[str, Any], depois: dict[str, Any],
            kappa_minimo: float, ganho_minimo: float,
            fronteiras: dict[str, Any] | None = None,
            operacionais: dict[str, bool] | None = None) -> tuple[bool, list[str]]:
    razoes: list[str] = []
    if k.get("n", 0) < 20:
        razoes.append(f"calibracao insuficiente: {k.get('n', 0)} casos comuns com o gold")
    if fronteiras:
        # So as fronteiras que mudam de modelo na config vivam dao autoridade.
        for nome in ("alta", "baixa"):
            if operacionais is not None and not operacionais.get(nome, True):
                continue
            f = fronteiras.get(nome) or {}
            if f.get("n", 0) < 20:
                razoes.append(f"fronteira `{nome}` sem casos suficientes ({f.get('n', 0)})")
            elif f.get("kappa", 0.0) < kappa_minimo:
                razoes.append(f"fronteira `{nome}`: juiz sem autoridade "
                              f"(kappa {f.get('kappa')} < {kappa_minimo})")
    elif k.get("kappa", 0.0) < kappa_minimo:
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


def separar(casos: list[dict], semente: int, fraccao: float = 0.7) -> tuple[list[dict], list[dict]]:
    """Divide em treino/validacao, deterministico e sem sobreposicao.

    Treinar e avaliar no mesmo conjunto mede a capacidade de decorar: medido
    num ciclo de 20 casos, a macro-F1 "subia" de 0,44 para 0,70 no proprio
    conjunto. O portao tem de decidir sobre casos que o treino nunca viu.
    """
    ordenados = sorted(casos, key=lambda c: str(c["id"]))
    random.Random(semente).shuffle(ordenados)
    corte = max(1, int(len(ordenados) * fraccao))
    treino, validacao = ordenados[:corte], ordenados[corte:]
    return treino, (validacao or treino)


# --------------------------------------------------------------------------- modelo (A/B)


def _ritmador(intervalo_s: float):
    """Devolve um callable que garante um intervalo minimo entre chamadas.

    O provedor do classificador responde `429 too many requests per minute` a
    rajadas: medido a 5 threads, 24 de 60 casos ficaram sem nivel e o A/B
    comparou conjuntos diferentes (resultado 0,0 em todas as variantes). Um A/B
    que mede o rate limit nao mede a qualidade do prompt.
    """
    trava = threading.Lock()
    estado = {"ultimo": 0.0}

    def esperar() -> None:
        if intervalo_s <= 0:
            return
        with trava:
            agora = time.monotonic()
            falta = estado["ultimo"] + intervalo_s - agora
            if falta > 0:
                time.sleep(falta)
            estado["ultimo"] = time.monotonic()

    return esperar


def abc_modelo(casos: list[dict], definicao: dict[str, Any], resolver,
               variantes: list[str], repeticoes: int, threads: int,
               intervalo_s: float = 0.0) -> dict[str, Any]:
    """Compara variantes de prompt do classificador no mesmo conjunto rotulado.

    Duas regras que evitam resultados bonitos e falsos:

    - **`None` e falha, nao rotulo.** `classificar` devolve `None` quando a rota
      fica suspensa; tratar isso como "sem nivel" e nao como erro fazia o ciclo
      estourar com `AttributeError` a meio do A/B, depois de pagar as chamadas.
    - **Comparar no mesmo subconjunto.** Se uma variante falha mais que outra, a
      macro-F1 de cada uma e calculada sobre conjuntos diferentes e a diferenca
      mede a taxa de falha, nao a qualidade. A comparacao usa a interseccao dos
      casos em que *todas* responderam, e reporta quantos ficaram de fora.
    """
    modelo = _modelo()
    out: dict[str, Any] = {}
    previsoes: dict[str, list[dict[str, str]]] = {}

    for variante in variantes:
        prompt = modelo.PROMPTS.get(variante)
        if prompt is None:
            out[variante] = {"erro": "variante desconhecida"}
            continue

        esperar = _ritmador(intervalo_s)

        def um(caso: dict) -> tuple[str, str]:
            # Ritmo antes de cada chamada; uma recusa 429 nao e um rotulo, e
            # espera-se uma vez antes de desistir do caso.
            esperar()
            r = modelo.classificar([{"role": "user", "content": caso["texto"]}],
                                   definicao, resolver, perfil="abc", prompt=prompt)
            if not isinstance(r, dict):
                time.sleep(2.5)
                esperar()
                r = modelo.classificar([{"role": "user", "content": caso["texto"]}],
                                       definicao, resolver, perfil="abc", prompt=prompt)
            if not isinstance(r, dict):
                return caso["id"], ""
            n = (r.get("nivel") or "").strip().lower()
            return caso["id"], (n if n in NIVEIS else "")

        execucoes: list[dict[str, str]] = []
        for _ in range(max(1, repeticoes)):
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
                execucoes.append(dict(ex.map(um, casos)))
        previsoes[variante] = execucoes

    if not previsoes:
        return out

    compativeis = list(previsoes)
    rotulos = {c["id"]: c["rotulo"] for c in casos}
    for indice in range(max(len(v) for v in previsoes.values())):
        # casos em que todas as variantes deram nivel nesta execucao
        comuns = [cid for cid in rotulos
                  if all(indice < len(previsoes[v]) and previsoes[v][indice].get(cid)
                         for v in compativeis)]
        for variante in compativeis:
            execs = out.setdefault(variante, {}).setdefault("execucoes", [])
            prev = previsoes[variante][indice]
            pares = [(rotulos[cid], prev[cid]) for cid in comuns]
            m = metricas(pares)
            execs.append({"macro_f1": m["macro_f1"], "por_classe": m["por_classe"],
                          "n": m["n"], "comparaveis": len(comuns),
                          "falhas": sum(1 for cid in rotulos if not prev.get(cid))})

    for variante in compativeis:
        execs = out[variante]["execucoes"]
        macros = [e["macro_f1"] for e in execs]
        out[variante].update({
            "macro_f1_media": round(sum(macros) / len(macros), 4),
            "macro_f1_min": min(macros), "macro_f1_max": max(macros),
            "media_f1": round(sum(e["por_classe"]["media"]["f1"] for e in execs) / len(execs), 4),
            "comparaveis": min(e["comparaveis"] for e in execs),
            "falhas": max(e["falhas"] for e in execs),
        })
    return out


# --------------------------------------------------------------------------- promocao


def _config() -> dict[str, Any]:
    return _ler_json(CONFIG, {}) or {}


def promover(pesos: dict[str, int], meta: dict[str, Any], variante: str | None = None) -> dict[str, Any]:
    """Grava os pesos aprendidos na config viva, pelo caminho do gestor.

    Medido a 2026-11-02: aqui fazia-se `erros = _gestor().validar(cfg)` e
    `if erros:`. Mas `validar` devolve `(cfg_limpa, erros)` — a tupla e sempre
    verdadeira, logo `promover` respondia `ok: False` mesmo com a config
    valida. O caminho de promocao estava partido desde sempre, e nunca se viu
    porque o portao do juiz fechava antes. Agora a validacao e a do `gravar`
    (que levanta `ValueError` com a lista), e o caminho de escrita e um so.
    """
    cfg = _config()
    if not isinstance(cfg.get("analise"), dict):
        cfg["analise"] = {}
    cfg["analise"]["pesos"] = dict(pesos)
    cfg["analise"]["aprendiz"] = meta
    if variante:
        bloco = cfg["analise"].get("classificador")
        if isinstance(bloco, dict):
            bloco["prompt_variante"] = variante
    try:
        caminho = _gestor().gravar(cfg, CONFIG)
    except ValueError as exc:
        return {"ok": False, "erros": str(exc)}
    return {"ok": True, "config": str(caminho)}


def reverter() -> dict[str, Any]:
    """Desfaz a última promoção, lendo o historico.

    Três casos distintos, e cada um tem de ter saida:
      - duas ou mais promocoes: volta aos pesos da penultima;
      - uma so promocao: nao ha "anterior", volta aos pesos de origem do
        codigo (remove a chave `pesos` da config) — sem isto a primeira
        promocao nunca se podia desfazer, que e o caso mais provavel;
      - nenhuma: nao ha nada a desfazer.
    Valida antes de gravar: um historico corrompido nao pode entrar na config
    viva, e `promover` ja validava enquanto `reverter` nao.
    """
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
    if not promovidos:
        return {"ok": False, "erro": "nunca houve promocao para desfazer"}
    anterior = promovidos[-2].get("pesos") if len(promovidos) >= 2 else None
    cfg = _config()
    if not isinstance(cfg.get("analise"), dict):
        cfg["analise"] = {}
    if anterior:
        cfg["analise"]["pesos"] = dict(anterior)
    else:
        cfg["analise"].pop("pesos", None)
    cfg["analise"].pop("aprendiz", None)
    try:
        caminho = _gestor().gravar(cfg, CONFIG)
    except ValueError as exc:
        return {"ok": False, "erros": str(exc), "origem": "historico"}
    return {"ok": True, "config": str(caminho), "pesos": anterior,
            "para_origem": anterior is None}


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
    fr = dados.get("fronteiras") or {}
    if fr:
        L.append("\n## Autoridade do juiz por corte\n")
        L.append("| corte | kappa | operacional | n | kappa na corrida anterior |")
        L.append("|---|---|---|---|---|")
        op = dados.get("fronteiras_operacionais") or {}
        for nome in ("alta", "baixa"):
            v = fr.get(nome) or {}
            L.append(f"| {nome} | {v.get('kappa')} | {'sim' if op.get(nome) else 'nao'} "
                     f"| {v.get('n')} | {v.get('kappa_corrida_anterior', '—')} |")
        comp = dados.get("comparacao_corridas") or {}
        if comp.get("comparado"):
            L.append(f"\nVale o **pior** das duas ultimas corridas. Anterior: {comp.get('anterior')}")
        else:
            L.append(f"\nSem corrida anterior comparavel ({comp.get('motivo')}).")
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
                         f"({v['macro_f1_min']}–{v['macro_f1_max']}), media F1 {v['media_f1']}, "
                         f"comparaveis {v.get('comparaveis')}, falhas {v.get('falhas')}")
        if any(v.get("comparaveis") == 0 for v in dados["abc"].values() if "macro_f1_media" in v):
            L.append("\n> Nenhum caso comparavel: as variantes nao responderam ao mesmo conjunto "
                     "(ver falhas). Um 0,0 aqui e ausencia de dados, nao um resultado.")
    if dados.get("falhas"):
        L.append("\n## Casos que o juiz recusou\n")
        for f in dados["falhas"][:10]:
            L.append(f"- {f['id']}: {f.get('erro')}")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- CLI


def _lote_juiz(provider: str, valor: int | None) -> int:
    """Resolve `--juiz-lote`: `None` decide pelo provider, `0` forca um a um."""
    if valor is None:
        return JUIZ_LOTE_PADRAO if provider == "inception" else 0
    return max(0, int(valor))


def main() -> int:
    ap = argparse.ArgumentParser(description="Ciclo de aprendizagem do ViperNet Model Manager")
    ap.add_argument("--casos", type=int, default=240, help="pedidos a rotular neste ciclo")
    ap.add_argument("--juiz-provider", default="inception")
    # O dono proibiu o `deepseek-v4-pro` em qualquer funcao (2026-11-03), salvo
    # indicacao explicita: o v4-pro so entra com `--juiz-permitir-v4-pro`, para
    # que a regra seja executavel e nao uma nota num README que a proxima sessao
    # nao le.
    #
    # 2026-11-03, decisao do dono: o juiz alinha com o classificador, em
    # `inception/mercury-3`. O deepseek deixa de ser o default em qualquer
    # funcao do ciclo; continua escolhivel por `--juiz-provider deepseek`.
    ap.add_argument("--juiz-modelo", default="mercury-3")
    ap.add_argument("--juiz-lote", type=int, default=None,
                    help=("casos por pedido ao juiz; 0 = um a um. Por omissao 10 "
                          f"quando o provider e `inception` (limite de pedidos por "
                          f"minuto medido), 0 nos restantes"))
    ap.add_argument("--juiz-permitir-v4-pro", action="store_true",
                    help="autoriza explicitamente o deepseek-v4-pro como juiz")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--kappa-minimo", type=float, default=0.60,
                    help="kappa binario minimo por fronteira (leve/media/alta vs resto)")
    ap.add_argument("--ganho-minimo", type=float, default=0.02)
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--semente", type=int, default=SEMENTE)
    ap.add_argument("--promover", action="store_true", help="aplicar se os portoes passarem")
    ap.add_argument("--abc-modelo", action="store_true", help="comparar variantes de prompt")
    ap.add_argument("--repeticoes", type=int, default=2)
    ap.add_argument("--reverter", action="store_true", help="restaurar os pesos do ciclo anterior")
    ap.add_argument("--so-treinar", default="", help="ficheiro jsonl rotulado: salta juiz e calibracao")
    ap.add_argument("--exportar-fila", type=int, default=0,
                    help="escreve N casos por rotular (cego, sem sugestao) e sai")
    ap.add_argument("--pausa-abc", type=float, default=1.2,
                    help="segundos entre chamadas do A/B (o provedor limita por minuto)")
    args = ap.parse_args()

    args.juiz_lote = _lote_juiz(args.juiz_provider, args.juiz_lote)

    if "v4-pro" in (args.juiz_modelo or "") and not args.juiz_permitir_v4_pro:
        print(json.dumps({"ok": False, "erro": (
            "juiz deepseek-v4-pro recusado: o dono proibiu esse modelo salvo "
            "indicacao explicita. Usa o default (deepseek-flash) ou passa "
            "--juiz-permitir-v4-pro.")}, ensure_ascii=False))
        return 2

    garantir_interpretador()

    if args.reverter:
        r = reverter()
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1

    # Fila de rotulagem humana: escrita CEGA (sem a sugestao do juiz). Se o
    # rotulador humano vir a sugestao, o kappa que se mede a seguir e acordo com
    # o juiz, nao concordancia entre rotuladores — e a calibracao perde o valor.
    if args.exportar_fila:
        casos, colheita = colher(args.exportar_fila, args.semente)
        if not casos:
            print(json.dumps({"ok": False, "colheita": colheita}, ensure_ascii=False))
            return 0
        APRENDIZ.mkdir(parents=True, exist_ok=True)
        nome = dt.datetime.now().strftime("fila-%Y%m%dT%H%M%S.jsonl")
        destino = APRENDIZ / nome
        with destino.open("w", encoding="utf-8") as f:
            for c in casos:
                f.write(json.dumps({"id": c["id"], "texto": c["texto"], "rotulo": ""},
                                   ensure_ascii=False) + "\n")
        print(json.dumps({"fila": str(destino), "casos": len(casos),
                          "como_rotular": "preencher `rotulo` com leve|media|alta e gravar em "
                                          f"{GOLD_HUMANO}/gold3.jsonl; o ciclo seguinte calibra o "
                                          "juiz contra ele (--so-treinar nao calibra: o portao recusa)",
                          "colheita": colheita}, ensure_ascii=False, indent=1))
        return 0

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
    gold: list[dict] = []
    rot_gold: list[dict] = []
    if not args.so_treinar:
        definicao = _juiz_definicao(args.juiz_provider, args.juiz_modelo)
        resolver = _resolver()
        rotulos = _etiquetar(casos, definicao, resolver, args.threads, args.juiz_lote)
        mapa = {r["id"]: r for r in rotulos}
        falhas = [r for r in rotulos if not r.get("rotulo")]
        casos = [dict(c, rotulo=mapa[c["id"]]["rotulo"]) for c in casos
                 if mapa.get(c["id"], {}).get("rotulo")]
        # calibracao contra os rotulos humanos do gold (inclui a fila humana)
        gold = _casos_gold()
        if gold:
            rot_gold = _etiquetar(gold, definicao, resolver, args.threads, args.juiz_lote)
            k = kappa(gold, [r for r in rot_gold if r.get("rotulo")])
        if not casos:
            print(json.dumps({"ok": False, "falhas": falhas[:5], "colheita": colheita},
                             ensure_ascii=False))
            return 1

    # 3. treino e avaliacao — o portao decide sobre a validacao, nunca sobre o treino
    treino, validacao = separar(casos, args.semente)
    pesos_novos, resultado = treinar(pesos_atuais, treino, limiares, passes=args.passes)
    antes_val = avaliar_pesos(pesos_atuais, validacao, limiares)
    depois_val = avaliar_pesos(pesos_novos, validacao, limiares)
    resultado["antes"] = antes_val
    resultado["depois"] = depois_val
    resultado["treino"] = len(treino)
    resultado["validacao"] = len(validacao)
    # O portao mede os dois cortes que mudam de modelo (alta|resto e media|leve),
    # e so exige os que a config viva distingue de facto.
    fronteiras = kappa_fronteiras(gold, [r for r in rot_gold if r.get("rotulo")]) if rot_gold else None
    operacionais = None
    if fronteiras:
        try:
            cfg_viva, _avisos = _gestor().carregar(CONFIG)
            operacionais = fronteiras_operacionais(cfg_viva)
        except Exception:  # noqa: BLE001 — sem config legivel, exige-se tudo
            operacionais = {"alta": True, "baixa": True}
        # O juiz oscila entre corridas: vale o pior valor das duas ultimas, e
        # usar o historico ja escrito nao custa chamadas nenhumas.
        anteriores: list[dict[str, Any]] = []
        if HISTORICO.exists():
            for linha in HISTORICO.read_text(encoding="utf-8").splitlines():
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    anteriores.append(json.loads(linha))
                except json.JSONDecodeError:
                    continue
        estaveis, comparacao = fronteiras_estaveis(fronteiras, anteriores)
        resultado["fronteiras"] = fronteiras
        resultado["fronteiras_estaveis"] = estaveis
        resultado["fronteiras_operacionais"] = operacionais
        resultado["comparacao_corridas"] = comparacao
        fronteiras = estaveis
    passou, razoes = portoes(k, antes_val, depois_val,
                             args.kappa_minimo, args.ganho_minimo,
                             fronteiras, operacionais)

    # 4. A/B do modelo (opcional)
    abc = None
    variante_vencedora = None
    if args.abc_modelo:
        bloco = (analise_cfg.get("classificador") or {})
        provider = bloco.get("provider") or args.juiz_provider
        modelo_nome = bloco.get("modelo") or args.juiz_modelo
        definicao_m = _juiz_definicao(provider, modelo_nome)
        # A/B sobre a validacao, nao sobre o treino: comparar variantes de prompt
        # em casos que ja serviram para escolher pesos mistura duas causas numa
        # so diferenca.
        amostra = validacao[:min(120, len(validacao))]
        abc: dict[str, Any] | None
        try:
            abc = abc_modelo(amostra, definicao_m, _resolver(),
                             ["v1", "v2", "v3"], args.repeticoes, args.threads,
                             intervalo_s=args.pausa_abc)
        except Exception as exc:  # a metade opcional nunca derruba o ciclo
            abc = {"erro": f"{type(exc).__name__}: {exc}"}
        validos: dict[str, dict[str, Any]] = {
            n: v for n, v in (abc or {}).items() if "macro_f1_media" in v}
        if validos:
            # Uma variante com muitas falhas nao ganha por comparabilidade: a
            # escolha exige o mesmo numero de casos respondidos.
            maior = max(v["comparaveis"] for v in validos.values())
            validos = {n: v for n, v in validos.items() if v["comparaveis"] >= 0.9 * maior}
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
            "fronteiras": resultado.get("fronteiras_estaveis"),
            "fronteiras_operacionais": resultado.get("fronteiras_operacionais"),
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
             "fronteiras": meta["fronteiras"],
             "fronteiras_operacionais": meta["fronteiras_operacionais"],
             "comparacao_corridas": resultado.get("comparacao_corridas"),
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
