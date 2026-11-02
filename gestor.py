"""Configuracao por perfil do vipernet-model-manager — puro, sem rede, testavel isolado.

Este modulo e a unica casa do esquema. O plugin (middleware) e o CLI (`vmm`) leem e
escrevem por aqui, para que a validacao nao divirja entre os dois.

Esquema (v3):

```
{
  "versao": 3,
  "activo": true,              # interruptor global
  "registar": true,
  "analise": {...},            # omissoes globais (limiares, janela, histerese)
  "perfis": {
    "pessoal": {
      "activo": true,          # interruptor do perfil
      "estrategia": "rodizio", # rodizio | prioridade
      "rodizio": {"por": "sessao"},
      "analise": {...},        # sobrepoe-se as omissoes globais
      "catalogo": [
        {"id": "deepseek-flash", "provider": "deepseek",
         "modelo": "deepseek-flash", "complexidade": ["leve", "media"],
         "acesso": "api", "esforco": "baixo"},
        {"id": "gpt-sol-sub", "provider": "openai-codex",
         "modelo": "gpt-6.1-sol", "complexidade": ["media"],
         "acesso": "subscricao", "esforco": "alto"}
      ]
    }
  }
}
```

Regras que decidem o resultado:

- **A lista de perfis e a lista de quem usa o plugin.** Um perfil ausente de
  `perfis` nao e gerido: o turno segue intacto e o facto fica no ledger com
  `perfil_nao_gerido`. Nao ha comportamento implicito para perfis que nascerem.
- **A complexidade e declarada por modelo**, na propria entrada: `complexidade`
  aceita um nivel (`"alta"`) ou uma lista (`["leve","media"]`). `niveis` continua
  aceite como sinonimo para nao partir catalogo antigo.
- **A rota de acesso e explicita ou inferida, nunca convertida.** `acesso` vale
  `api` ou `subscricao`; sem ele, infere-se do provider (openai-codex →
  `subscricao`, restantes → `api`). Uma incompatibilidade (`subscricao` com
  provider que nao openai-codex, ou `api` com openai-codex) e recusada. O campo
  nunca reescreve provider/modelo: e rotulo + validacao + registo.
- **A disponibilidade e do dono.** Nenhum provider entra no catalogo por
  inferencia; o plugin so usa o que aqui estiver escrito e cuja credencial
  resolva no escopo do perfil.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

NOME = "vipernet-model-manager"
NIVEIS = ("leve", "media", "alta")
ESTRATEGIAS = ("rodizio", "prioridade")
POR = ("turno", "sessao")
ACESSOS = ("api", "subscricao")

#: Esforco de raciocinio por rota. Vocabulario do dono (pt-PT); no fio fala a
#: escada canonica do Hermes (`agent.reasoning_effort`), que depois a adapta a
#: cada provider. Ausente = a rota nao diz nada e o pedido segue como o agente
#: o tinha (nao se inventa um valor por omissao).
ESFORCOS = ("baixo", "medio", "alto")
ESFORCO_WIRE = {"baixo": "low", "medio": "medium", "alto": "high"}
_ESFORCO_SINONIMOS = {
    "baixo": "baixo", "low": "baixo", "minimo": "baixo", "mínimo": "baixo",
    "minimal": "baixo",
    "medio": "medio", "médio": "medio", "medium": "medio", "normal": "medio",
    "alto": "alto", "high": "alto", "maximo": "alto", "máximo": "alto", "max": "alto",
}

# Unico transporte de subscricao que o plugin conhece (ChatGPT via Codex). Os
# restantes providers sao rota de API. A inferencia retrocompativel apoia-se nisto.
_PROVIDERS_SUBSCRICAO = frozenset({"openai-codex"})

ANALISE_PADRAO: dict[str, Any] = {
    "janela_mensagens": 8,
    "varredura_max_mensagens": 60,
    "limiares": {"media": 3, "alta": 7},
    "descer_apos": 2,
    # `classificador` e preenchido mais abaixo, a partir de
    # `analise_modelo.PADRAO`: os limites do classificador (timeout, tokens,
    # cache) tem UMA casa. Duplica-los aqui foi o que deixou a config viva com
    # `max_tokens: 12` depois de o modulo passar a 320.
}


def _analise_modelo():
    """`analise_modelo` carregado como irmao, com ou sem pacote.

    O `gestor` e importado de duas formas (pacote do Hermes e ficheiro solto no
    selftest/CLI); a importacao relativa so existe na primeira. O modulo irmao
    e a unica casa dos limites do classificador — valida aqui, decide la.

    Reutiliza o objecto ja carregado (`sys.modules["vmm_analise_modelo"]`) para
    nao haver duas instancias com dois caches: o plugin e o gestor tem de ver o
    mesmo cache, senao a mesma mensagem paga duas chamadas.
    """
    global _AM
    if _AM is not None:
        return _AM
    import sys as _sys

    existente = _sys.modules.get("vmm_analise_modelo")
    if existente is not None:
        _AM = existente
        return _AM
    try:
        from . import analise_modelo as mod  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - import solto
        import importlib.util as _ilu

        caminho = Path(__file__).resolve().parent / "analise_modelo.py"
        spec = _ilu.spec_from_file_location("vmm_analise_modelo", caminho)
        if spec is None or spec.loader is None:
            raise
        mod = _ilu.module_from_spec(spec)
        _sys.modules["vmm_analise_modelo"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _AM = mod
    return mod


_AM: Any = None

# Semente: os catalogos que o dono declarou (2026-11-02). Cada modelo traz a sua
# complexidade. Alternancia: `rodizio` com `por: sessao` — a sessao fica na rota
# que lhe calhou e sessoes diferentes rodam; `por: turno` existe e e mais caro
# (cada troca invalida o cache de prompt).
SEED: dict[str, Any] = {
    "versao": 3,
    "activo": True,
    "registar": True,
    "analise": copy.deepcopy(ANALISE_PADRAO),
    "perfis": {
        "default": {
            "activo": True,
            "estrategia": "rodizio",
            "rodizio": {"por": "sessao"},
            "catalogo": [
                {"id": "deepseek-flash", "provider": "deepseek",
                 "modelo": "deepseek-flash", "complexidade": ["leve", "media"]},
                {"id": "gpt-sol", "provider": "openrouter",
                 "modelo": "openai/gpt-6.1-sol", "complexidade": "media"},
                {"id": "deepseek-pro", "provider": "deepseek",
                 "modelo": "deepseek-v4-pro", "complexidade": "alta"},
            ],
        },
        "pessoal": {
            "activo": True,
            "estrategia": "rodizio",
            "rodizio": {"por": "sessao"},
            "catalogo": [
                {"id": "deepseek-flash", "provider": "deepseek",
                 "modelo": "deepseek-flash", "complexidade": ["leve", "media"]},
                {"id": "lightning-free", "provider": "openrouter",
                 "modelo": "nvidia/nemotron-3.5-lightning:free", "complexidade": "leve"},
                {"id": "north-code-free", "provider": "openrouter",
                 "modelo": "cohere/north-mini-code:free", "complexidade": "media"},
                {"id": "nemotron-ultra-free", "provider": "openrouter",
                 "modelo": "nvidia/nemotron-3-ultra-550b-a55b:free", "complexidade": "media"},
            ],
        },
        "trabalho": {
            "activo": True,
            "estrategia": "rodizio",
            "rodizio": {"por": "sessao"},
            "catalogo": [
                {"id": "deepseek-flash", "provider": "deepseek",
                 "modelo": "deepseek-flash", "complexidade": "leve"},
                {"id": "north-code-free", "provider": "openrouter",
                 "modelo": "cohere/north-mini-code:free", "complexidade": "media"},
                {"id": "deepseek-pro", "provider": "deepseek",
                 "modelo": "deepseek-v4-pro", "complexidade": "alta"},
                {"id": "gpt-sol", "provider": "openrouter",
                 "modelo": "openai/gpt-6.1-sol", "complexidade": "alta"},
            ],
        },
    },
}


# --------------------------------------------------------------------------- casa


def directorio() -> Path:
    """`<raiz de lancamento>/plugin-data/vipernet-model-manager/`.

    Usa `get_default_hermes_root()` e **nao** `get_hermes_home()`: dentro de um
    perfil o home ambiente e o do perfil, e o CLI escreveria um config que o
    gateway (que carrega o plugin no home de lancamento) nunca leria.
    """
    env = (os.environ.get("VMM_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    try:
        from hermes_constants import get_default_hermes_root

        base = Path(get_default_hermes_root())
    except Exception:
        base = Path(os.environ.get("HERMES_HOME") or "/opt/data")
    return base / "plugin-data" / NOME


def ficheiro_config() -> Path:
    return directorio() / "config.json"


def caminho_ledger() -> Path:
    return directorio() / "uso.jsonl"


def caminho_erros() -> Path:
    return directorio() / "uso.err"


# --------------------------------------------------------------------------- io


def _normalizar_entrada(entrada: Any, indice: int, erros: list[str]) -> dict[str, Any] | None:
    if not isinstance(entrada, dict):
        erros.append(f"catalogo[{indice}] nao e um objecto")
        return None
    provider = str(entrada.get("provider") or "").strip()
    modelo = str(entrada.get("modelo") or "").strip()
    bruto = entrada.get("complexidade", entrada.get("niveis"))
    if isinstance(bruto, str):
        niveis = [bruto.strip().lower()]
    elif isinstance(bruto, (list, tuple)):
        niveis = [str(n).strip().lower() for n in bruto]
    else:
        niveis = []
    niveis = list(dict.fromkeys(n for n in niveis if n))
    invalidos = [n for n in niveis if n not in NIVEIS]
    if not provider or not modelo:
        erros.append(f"catalogo[{indice}] sem provider/modelo")
        return None
    if not niveis:
        erros.append(f"catalogo[{indice}] ({provider}:{modelo}) sem complexidade")
        return None
    if invalidos:
        erros.append(f"catalogo[{indice}] complexidade invalida: {', '.join(invalidos)}")
        return None
    saida = {
        "id": str(entrada.get("id") or f"{provider}:{modelo}").strip(),
        "provider": provider,
        "modelo": modelo,
        "complexidade": niveis,
    }
    # rota de acesso: explicita (api|subscricao) ou inferida do provider
    # (retrocompativel: openai-codex=subscricao, restantes=api).
    acesso_bruto = entrada.get("acesso")
    if acesso_bruto is None:
        acesso = acesso_por_provider(provider)
    else:
        acesso = str(acesso_bruto).strip().lower()
        if acesso not in ACESSOS:
            erros.append(f"catalogo[{indice}] ({provider}:{modelo}) acesso invalido: "
                         f"'{acesso_bruto}' (use api|subscricao)")
            return None
    if acesso == "subscricao" and provider.lower() not in _PROVIDERS_SUBSCRICAO:
        erros.append(f"catalogo[{indice}] ({provider}:{modelo}) acesso 'subscricao' "
                     f"exige provider openai-codex")
        return None
    if acesso == "api" and provider.lower() in _PROVIDERS_SUBSCRICAO:
        erros.append(f"catalogo[{indice}] ({provider}:{modelo}) provider openai-codex "
                     f"e uma subscricao; use acesso 'subscricao'")
        return None
    saida["acesso"] = acesso
    # esforco de raciocinio: opcional e por rota. Ausente nao e "medio" — e
    # "nao declarado", para que uma rota que nao precise nao arraste o esforco
    # de outra.
    esforco, erro_esforco = normalizar_esforco(entrada.get("esforco"))
    if erro_esforco:
        erros.append(f"catalogo[{indice}] ({provider}:{modelo}) {erro_esforco}")
        return None
    if esforco:
        saida["esforco"] = esforco
    tarefas = entrada.get("tarefas", [])
    if not isinstance(tarefas, list) or any(not isinstance(t, str) or not t.strip() for t in tarefas):
        erros.append(f"catalogo[{indice}].tarefas deve ser lista de identificadores")
    else:
        saida["tarefas"] = list(dict.fromkeys(t.strip() for t in tarefas))
    if entrada.get("desligado"):
        saida["desligado"] = True
    for chave in ("nota",):
        if entrada.get(chave):
            saida[chave] = str(entrada[chave])
    return saida


def validar(cfg: Any) -> tuple[dict[str, Any], list[str]]:
    """Normaliza e valida; devolve `(config, erros)`. Nunca levanta."""
    erros: list[str] = []
    # tentativa defensiva: se vier string, tenta parsear JSON
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except (json.JSONDecodeError, ValueError):
            pass  # continuará à validação normal que rejeitará non-dict
    if not isinstance(cfg, dict):
        return copy.deepcopy(SEED), ["config nao e um objecto"]
    saida: dict[str, Any] = {
        "versao": 3,
        "activo": bool(cfg.get("activo", True)),
        "registar": bool(cfg.get("registar", True)),
        "analise": copy.deepcopy(ANALISE_PADRAO),
        "perfis": {},
    }
    analise = cfg.get("analise") or {}
    if isinstance(analise, dict):
        for chave, valor in analise.items():
            if chave == "limiares" and isinstance(valor, dict):
                saida["analise"]["limiares"].update(valor)
            elif chave == "classificador":
                limpo, erros_c = _analise_modelo().normalizar(valor)
                saida["analise"]["classificador"] = limpo
                erros.extend(erros_c)
            else:
                saida["analise"][chave] = valor
    perfis = cfg.get("perfis")
    if not isinstance(perfis, dict):
        erros.append("'perfis' ausente ou nao e um objecto")
        return saida, erros
    for nome, pcfg in perfis.items():
        nome = str(nome).strip()
        if not nome:
            erros.append("perfil com nome vazio")
            continue
        if not isinstance(pcfg, dict):
            erros.append(f"perfis.{nome} nao e um objecto")
            continue
        estrategia = str(pcfg.get("estrategia") or "rodizio").strip().lower()
        if estrategia not in ESTRATEGIAS:
            erros.append(f"perfis.{nome}.estrategia invalida: {estrategia}")
            estrategia = "rodizio"
        por = str((pcfg.get("rodizio") or {}).get("por") or "sessao").strip().lower()
        if por not in POR:
            erros.append(f"perfis.{nome}.rodizio.por invalido: {por}")
            por = "sessao"
        # O perfil grava SO o que declara: a heranca faz-se na leitura
        # (`perfil_config`). Gravar uma copia do global fixava o perfil ao valor
        # do dia em que foi gravado — um global que mudasse depois deixava de
        # chegar-lhe. Isto vale para a `analise` inteira, nao so para o
        # classificador: qualquer chave herdada e a mesma armadilha.
        panalise: dict[str, Any] = {}
        if isinstance(pcfg.get("analise"), dict):
            for chave, valor in pcfg["analise"].items():
                if chave == "limiares" and isinstance(valor, dict):
                    # So as chaves declaradas: o que falta vem do global.
                    globais = saida["analise"].get("limiares") or {}
                    declarados = {k: v for k, v in valor.items() if globais.get(k) != v}
                    if declarados:
                        panalise["limiares"] = declarados
                elif chave == "classificador":
                    # Substitui por inteiro (nao funde) o classificador global:
                    # e o que permite ao perfil desligar o que a config global
                    # liga, com `{"activo": false}` e nada mais.
                    limpo, erros_c = _analise_modelo().normalizar(
                        valor, prefixo=f"perfis.{nome}.analise.classificador")
                    erros.extend(erros_c)
                    if limpo == saida["analise"].get("classificador"):
                        # Igual ao global: nao se grava copia (ver acima).
                        continue
                    panalise["classificador"] = limpo
                else:
                    # Mesma regra para as chaves simples: um valor igual ao
                    # global nao e uma declaracao, e uma copia que fixa o perfil
                    # ao dia (medido a 2026-11-02: os tres perfis guardavam
                    # `descer_apos`, `janela_mensagens` e `varredura_max_mensagens`
                    # iguais ao global, herdados da semente antiga).
                    if chave in saida["analise"] and valor == saida["analise"][chave]:
                        continue
                    panalise[chave] = valor
        catalogo: list[dict[str, Any]] = []
        vistos: set[str] = set()
        if not isinstance(pcfg.get("catalogo"), list):
            erros.append(f"perfis.{nome}.catalogo ausente ou nao e lista")
        else:
            for i, entrada in enumerate(pcfg["catalogo"]):
                limpa = _normalizar_entrada(entrada, i, erros)
                if limpa is None:
                    continue
                if limpa["id"] in vistos:
                    erros.append(f"perfis.{nome}: id duplicado '{limpa['id']}'")
                    continue
                vistos.add(limpa["id"])
                catalogo.append(limpa)
        tarefas = {}
        regras = pcfg.get("tarefas", {})
        if not isinstance(regras, dict):
            erros.append(f"perfis.{nome}.tarefas deve ser objecto")
            regras = {}
        for tid, regra in regras.items():
            if not isinstance(tid, str) or not tid.strip() or not isinstance(regra, dict):
                erros.append(f"perfis.{nome}.tarefas: regra invalida")
                continue
            termos = regra.get("termos", [])
            prioridade = regra.get("prioridade", 0)
            if (not isinstance(termos, list) or not termos or
                    any(not isinstance(t, str) or not t.strip() for t in termos) or
                    type(prioridade) is not int):
                erros.append(f"perfis.{nome}.tarefas.{tid}: termos/prioridade invalidos")
                continue
            tarefas[tid] = {"termos": list(dict.fromkeys(t.strip() for t in termos)),
                            "prioridade": prioridade}
        for entrada in catalogo:
            for tid in entrada.get("tarefas", []):
                if tid not in tarefas:
                    erros.append(f"perfis.{nome}: tarefa desconhecida '{tid}' em {entrada['id']}")
        saida["perfis"][nome] = {
            "tarefas": tarefas,
            "activo": bool(pcfg.get("activo", True)),
            "estrategia": estrategia,
            "rodizio": {"por": por},
            # Sem nada declarado nao se grava `analise` vazia: a ausencia da
            # chave e o que significa "herda tudo do global".
            **({"analise": panalise} if panalise else {}),
            "catalogo": catalogo,
        }
    return saida, erros


def carregar(caminho: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    caminho = Path(caminho) if caminho else ficheiro_config()
    try:
        texto = caminho.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return copy.deepcopy(SEED), []
    # tentativa defensiva: se json.loads falhar, tenta ler como string e fazer parse
    try:
        bruto = json.loads(texto)
    except (json.JSONDecodeError, ValueError):
        # se o ficheiro contiver um objeto JSON em formato de string, tenta converter
        if isinstance(texto, str):
            try:
                bruto = json.loads(texto)
            except (json.JSONDecodeError, ValueError):
                return copy.deepcopy(SEED), ["config nao e um objecto"]
        else:
            return copy.deepcopy(SEED), ["config nao e um objecto"]
    return validar(bruto)


def gravar(cfg: dict[str, Any], caminho: Path | None = None) -> Path:
    """Grava com validacao, backup e `os.replace` (nunca meio ficheiro)."""
    caminho = Path(caminho) if caminho else ficheiro_config()
    limpo, erros = validar(cfg)
    if erros:
        raise ValueError("; ".join(erros))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if caminho.exists():
        carimbo = time.strftime("%Y%m%dT%H%M%S")
        shutil.copy2(caminho, caminho.with_suffix(f".json.bak-{carimbo}"))
    tmp = caminho.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(limpo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, caminho)
    return caminho


def garantir() -> Path:
    caminho = ficheiro_config()
    if not caminho.exists():
        cfg = _semear_de_config_antigo()
        try:
            gravar(cfg, caminho)
        except Exception:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(json.dumps(SEED, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    return caminho


def _semear_de_config_antigo() -> dict[str, Any]:
    """Semente, aproveitando o catalogo da v2 (`complexidade-modelo`) se existir.

    O catalogo antigo era global; entra no perfil `default` com as suas `niveis`
    traduzidas em `complexidade`. Pessoal e trabalho recebem a semente declarada.
    """
    cfg = copy.deepcopy(SEED)
    try:
        from hermes_constants import get_default_hermes_root

        base = Path(get_default_hermes_root())
    except Exception:
        base = Path(os.environ.get("HERMES_HOME") or "/opt/data")
    antigo = base / "plugin-data" / "complexidade-modelo" / "config.json"
    try:
        dados = json.loads(antigo.read_text(encoding="utf-8-sig"))
    except Exception:
        return cfg
    catalogo = dados.get("catalogo")
    if not isinstance(catalogo, list) or not catalogo:
        return cfg
    migrado: list[dict[str, Any]] = []
    for i, entrada in enumerate(catalogo):
        limpa = _normalizar_entrada(entrada, i, [])
        if limpa:
            migrado.append(limpa)
    if migrado:
        cfg["perfis"]["default"]["catalogo"] = migrado
        cfg["perfis"]["default"]["estrategia"] = str(dados.get("estrategia") or "prioridade")
    return cfg


# --------------------------------------------------------------------------- perfil


def acesso_por_provider(provider: str) -> str:
    """Rota de acesso de um provider: `subscricao` para o transporte de
    subscricao (openai-codex); `api` nos restantes. Base da inferencia
    retrocompativel quando a entrada nao declara `acesso`."""
    return "subscricao" if str(provider or "").strip().lower() in _PROVIDERS_SUBSCRICAO else "api"


def _perfil_provider(provider: str):
    """Perfil do provider do Hermes (o objecto que sabe a forma do fio).

    A importacao pode falhar fora da arvore do Hermes (CLI, selftest solto):
    nesse caso tenta-se a raiz deduzida de `hermes_constants`, que e onde o
    pacote `providers` vive, em vez de desistir em silencio.
    """
    nome = str(provider or "").strip().lower()
    try:
        from providers import get_provider_profile
    except Exception:  # noqa: BLE001
        raiz = getattr(sys.modules.get("hermes_constants"), "__file__", "")
        if not raiz:
            return None
        pasta = str(Path(raiz).resolve().parent)
        if pasta not in sys.path:
            sys.path.insert(0, pasta)
        from providers import get_provider_profile  # noqa: F811
    return get_provider_profile(nome)


def normalizar_esforco(valor: Any) -> tuple[str, str]:
    """`(valor, erro)` do vocabulario do dono. Vazio = nao declarado, sem erro.

    Aceita o vocabulario pt-PT e os sinonimos ingleses da escada do Hermes
    (`low|medium|high`): a config vive em JSON editado por tres vias e recusar
    `high` por ser ingles seria uma armadilha, nao uma validacao.
    """
    bruto = str(valor or "").strip()
    if not bruto:
        return "", ""
    nome = _ESFORCO_SINONIMOS.get(bruto.lower(), "")
    if not nome:
        return "", (f"esforco invalido: '{bruto}' (use {', '.join(ESFORCOS)}; "
                    f"low/medium/high tambem sao aceites)")
    return nome, ""


def esforco_wire(valor: Any) -> str:
    """Valor do dono -> escada canonica do Hermes (`low|medium|high`), ou ''."""
    nome, _ = normalizar_esforco(valor)
    return ESFORCO_WIRE.get(nome, "")


def extras_esforco(provider: str, modelo: str, esforco: Any,
                   *, session_id: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """`(extra_body, top_level)` com a forma que o provider de destino entende.

    O esforco nao tem uma forma unica no fio: o deepseek quer `thinking` em
    `extra_body` mais `reasoning_effort` no topo; o OpenRouter quer
    `extra_body.reasoning`. Perguntar ao perfil do provider — o mesmo objecto
    que o Hermes usa para construir o pedido — evita inventar campos e mantem
    uma so casa para a forma de cada fio.

    `supports_reasoning=True` porque a configuracao explicita do dono **e** a
    afirmacao de que a rota aceita o parametro; sem isso o OpenRouter omitia o
    campo em silencio e o esforco nunca chegava ao fio.

    `({}, {})` quando nao ha esforco, o valor e invalido ou o provider nao tem
    perfil conhecido: o chamador segue sem tocar no pedido. Nunca levanta.
    """
    valor = esforco_wire(esforco)
    if not valor:
        return {}, {}
    try:
        perfil = _perfil_provider(provider)
    except Exception:  # noqa: BLE001 — sem Hermes ao lado (CLI, selftest solto)
        perfil = None
    if perfil is None:
        return {}, {}
    try:
        extra, topo = perfil.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": valor},
            supports_reasoning=True,
            model=str(modelo or ""),
            session_id=session_id,
        )
    except Exception:  # noqa: BLE001
        return {}, {}
    extra = dict(extra or {})
    topo = dict(topo or {})
    # O perfil tambem pode reescrever o `model` (pins de endpoint do OpenRouter);
    # isso nao nos pertence — a rota e a do catalogo.
    topo.pop("model", None)
    return extra, topo


def casa_do_perfil(nome: str) -> Path:
    """Home do perfil pelo nome: `default` e a raiz de lancamento; os restantes
    em `<raiz>/profiles/<nome>`. O inverso de `nome_do_perfil`."""
    try:
        from hermes_constants import get_default_hermes_root

        raiz = Path(get_default_hermes_root())
    except Exception:
        raiz = Path(os.environ.get("HERMES_HOME") or "/opt/data")
    if nome == "default":
        return raiz
    return raiz / "profiles" / nome


def nome_do_perfil(home: Path | str) -> str:
    """`<raiz>/profiles/X` -> `X`; qualquer outro home -> `default`."""
    try:
        from hermes_constants import get_default_hermes_root

        raiz = (Path(get_default_hermes_root()) / "profiles").resolve()
    except Exception:
        raiz = Path(os.environ.get("HERMES_HOME") or "/opt/data") / "profiles"
        raiz = raiz.resolve()
    try:
        rel = Path(home).resolve().relative_to(raiz)
    except (ValueError, OSError):
        return "default"
    return rel.parts[0] if rel.parts else "default"


def perfil_atual(agente: Any = None) -> str:
    """Perfil a que o turno pertence, na ordem que o Hermes usa.

    1. `HERMES_HOME` ContextVar — o gateway multiplexado liga-o por turno;
    2. pai do `state.db` do agente — so quando o ContextVar se perdeu na thread;
    3. perfil ambiente do processo.
    """
    home: Path | None = None
    try:
        from hermes_constants import get_hermes_home_override

        override = get_hermes_home_override()
        if override:
            home = Path(override)
    except Exception:
        pass
    if home is None and agente is not None:
        try:
            db = getattr(getattr(agente, "_session_db", None), "db_path", None)
            if db:
                home = Path(db).parent
        except Exception:
            home = None
    if home is not None:
        return nome_do_perfil(home)
    try:
        from hermes_cli.profiles import get_active_profile_name

        return str(get_active_profile_name() or "default")
    except Exception:
        return "default"


# O bloco por omissao do classificador vem de `analise_modelo.PADRAO` — uma so
# casa para os numeros — e nao de uma copia literal que envelhece em silencio.
ANALISE_PADRAO["classificador"] = dict(_analise_modelo().PADRAO)


def perfil_config(cfg: dict[str, Any], perfil: str) -> dict[str, Any] | None:
    """Config efectiva do perfil (perfil exacto, senao `*`), ou None se nao gerido."""
    perfis = cfg.get("perfis") or {}
    pcfg = perfis.get(perfil)
    origem = perfil
    if not isinstance(pcfg, dict):
        pcfg = perfis.get("*")
        origem = "*"
    if not isinstance(pcfg, dict):
        return None
    saida = copy.deepcopy(pcfg)
    saida["origem_config"] = origem
    # A `analise` global e herdada por qualquer perfil que nao a declare: a
    # fundicao faz-se aqui, na leitura, e nao apenas na validacao — senao um
    # perfil guardado antes de o global mudar ficava preso a copia antiga.
    # Chave a chave: o que o perfil declara ganha; `classificador` substitui
    # por inteiro (e o que deixa um perfil desligar um global ligado).
    global_analise = cfg.get("analise")
    if isinstance(global_analise, dict):
        analise = dict(copy.deepcopy(global_analise))
        local = saida.get("analise")
        if isinstance(local, dict):
            for chave, valor in local.items():
                if chave == "limiares" and isinstance(valor, dict):
                    base = dict(analise.get("limiares") or {})
                    base.update(valor)
                    analise["limiares"] = base
                else:
                    analise[chave] = copy.deepcopy(valor)
        saida["analise"] = analise
    return saida


def catalogo_utilizavel(pcfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in (pcfg.get("catalogo") or []) if isinstance(e, dict) and not e.get("desligado")]


def candidatos(catalogo: list[dict[str, Any]], nivel: str) -> list[dict[str, Any]]:
    return [e for e in catalogo if nivel in (e.get("complexidade") or [])]


# --------------------------------------------------------------------------- estado


def chave_rodizio(perfil: str, sessao: str, nivel: str) -> str:
    return f"{perfil}\x1f{sessao}\x1f{nivel}"


def indice_rodizio(chave: str, n: int, *, por: str, perfil: str, sessao: str,
                   nivel: str, contadores: dict[str, int]) -> int:
    """Indice estavel: por sessao usa hash, por turno incrementa. Nunca negativo."""
    if n <= 0:
        return 0
    if por == "sessao":
        semente = int(hashlib.md5(f"{perfil}|{sessao}|{nivel}".encode("utf-8")).hexdigest()[:8], 16)
        return semente % n
    atual = contadores.get(chave, 0)
    contadores[chave] = atual + 1
    return atual % n


def assinatura(cfg: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
