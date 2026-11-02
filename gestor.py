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
         "acesso": "api"},
        {"id": "gpt-sol-sub", "provider": "openai-codex",
         "modelo": "gpt-6.1-sol", "complexidade": ["media"],
         "acesso": "subscricao"}
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
import time
from pathlib import Path
from typing import Any

NOME = "vipernet-model-manager"
NIVEIS = ("leve", "media", "alta")
ESTRATEGIAS = ("rodizio", "prioridade")
POR = ("turno", "sessao")
ACESSOS = ("api", "subscricao")

# Unico transporte de subscricao que o plugin conhece (ChatGPT via Codex). Os
# restantes providers sao rota de API. A inferencia retrocompativel apoia-se nisto.
_PROVIDERS_SUBSCRICAO = frozenset({"openai-codex"})

ANALISE_PADRAO: dict[str, Any] = {
    "janela_mensagens": 8,
    "varredura_max_mensagens": 60,
    "limiares": {"media": 3, "alta": 7},
    "descer_apos": 2,
}

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
        panalise = copy.deepcopy(saida["analise"])
        if isinstance(pcfg.get("analise"), dict):
            for chave, valor in pcfg["analise"].items():
                if chave == "limiares" and isinstance(valor, dict):
                    panalise["limiares"].update(valor)
                else:
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
            "analise": panalise,
            "catalogo": catalogo,
        }
    return saida, erros


def carregar(caminho: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    caminho = Path(caminho) if caminho else ficheiro_config()
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return copy.deepcopy(SEED), []
    except Exception as exc:  # JSON partido nao pode deixar o turno sem plugin
        return copy.deepcopy(SEED), [f"config ilegivel: {type(exc).__name__}: {exc}"]
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
