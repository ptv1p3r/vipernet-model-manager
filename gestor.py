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
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

NOME = "vipernet-model-manager"

#: Nome de sinal aceite em `analise.pesos`: minusculas, digitos e `_`.
_RE_SINAL = re.compile(r"^[a-z][a-z0-9_]*$")
NIVEIS = ("leve", "media", "alta")
ESTRATEGIAS = ("rodizio", "prioridade")
POR = ("turno", "sessao")
ACESSOS = ("api", "subscricao")

#: Estrategia: listas FECHADAS. `modo` decide como se escolhe dentro do nivel;
#: `por` so tem efeito em `rodizio` e diz quando a roda avanca (por turno ou
#: por sessao). Um valor fora destas listas e erro de validacao, nao um
#: silencioso "usa o default": gravar `rodizioo` e ver o plugin a ignorar a
#: escolha e pior do que o painel recusar.
ESTRATEGIA_PADRAO: dict[str, str] = {"modo": "rodizio", "por": "sessao"}
#: Rotulos do painel. Ficam aqui — e nao no JS — para o `select` ser construido
#: a partir da mesma lista que valida, e nao de uma copia que envelhece.
ROTULOS_ESTRATEGIA: dict[str, dict[str, str]] = {
    "modo": {"rodizio": "rotação", "prioridade": "prioridade (primeira da lista)"},
    "por": {"turno": "por turno", "sessao": "por sessão"},
}


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
    # Um turno faz varios pedidos (laco de ferramentas) e cada um reavaliava
    # nivel, tarefa e histerese: medido a 2026-11-02, 37,7% dos turnos trocavam
    # de nivel a meio. Com isto o nivel fixa-se no primeiro pedido e os
    # seguintes reutilizam-no; so a capacidade (contexto/modalidade) e a rota
    # que morre o podem mudar, e ficam marcadas com `excecao`.
    "fixar_nivel_por_turno": True,
    # Superficie -> rota, por perfil. Uma superficie e o contexto de sessao
    # que o core ja entrega ao middleware (`cron`, `api_server`, `cli`,
    # `whatsapp`, `subagent`): e deterministica, nao se adivinha por texto.
    # Medido a 2026-11-03: 7118 respostas, `api_server` 6700, `cron` 173,
    # `cli` 27. Uma regra aqui ganha a tarefa e a analise de complexidade —
    # so o marcador explicito do utilizador (`complexidade: alta`) manda mais.
    "superficies": {},
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

    Devolve sempre a **mesma instancia que o plugin tem em memoria**. O plugin
    recarrega `analise_modelo.py` a quente (mtime) e substitui a entrada em
    `sys.modules`; se este modulo ficasse com a instancia do arranque, o
    `validar` usaria o `normalizar` antigo — foi assim que uma chave nova do
    classificador foi apagada em silencio ao carregar a config e o transporte
    de decisoes nunca chegou a ser usado, com 3.14.0 a correr.
    """
    global _AM, _AM_MTIME
    import sys as _sys

    caminho_am = Path(__file__).resolve().parent / "analise_modelo.py"
    try:
        mtime = caminho_am.stat().st_mtime_ns
    except OSError:
        mtime = 0
    nome_pkg = f"{__package__}.analise_modelo" if __package__ else None
    if nome_pkg:
        ao_vivo = _sys.modules.get(nome_pkg)
        if ao_vivo is not None and getattr(ao_vivo, "__file__", ""):
            _AM, _AM_MTIME = ao_vivo, mtime
            return ao_vivo
    if _AM is not None and _AM_MTIME == mtime:
        return _AM

    existente = _sys.modules.get("vmm_analise_modelo")
    if existente is not None:
        _AM, _AM_MTIME = existente, mtime
        return _AM
    try:
        from . import analise_modelo as mod  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - import solto
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("vmm_analise_modelo", caminho_am)
        if spec is None or spec.loader is None:
            raise
        mod = _ilu.module_from_spec(spec)
        _sys.modules["vmm_analise_modelo"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _AM, _AM_MTIME = mod, mtime
    return mod


_AM: Any = None
_AM_MTIME: int | None = None

# Semente: os catalogos que o dono declarou (2026-11-02). Cada modelo traz a sua
# complexidade. Alternancia: `rodizio` com `por: sessao` — a sessao fica na rota
# que lhe calhou e sessoes diferentes rodam; `por: turno` existe e e mais caro
# (cada troca invalida o cache de prompt).
SEED: dict[str, Any] = {
    "versao": 3,
    "activo": True,
    "registar": True,
    "analise": copy.deepcopy(ANALISE_PADRAO),
    # Estrategia global: e a partir daqui que todos os perfis herdam. A semente
    # nao repete o bloco em cada perfil — repetir era precisamente o que impedia
    # mudar a estrategia num sitio so.
    "estrategia": dict(ESTRATEGIA_PADRAO),
    "perfis": {
        "default": {
            "activo": True,
            "catalogo": [
                {"id": "deepseek-flash", "provider": "deepseek",
                 "modelo": "deepseek-flash", "complexidade": ["leve", "media"]},
                {"id": "gpt-sol", "provider": "openrouter",
                 "modelo": "openai/gpt-6.1-sol", "complexidade": "media"},
                {"id": "nemotron-ultra-free", "provider": "openrouter",
                 "modelo": "nvidia/nemotron-3-ultra-550b-a55b:free",
                 "complexidade": "alta"},
            ],
        },
        "pessoal": {
            "activo": True,
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
            "catalogo": [
                {"id": "deepseek-flash", "provider": "deepseek",
                 "modelo": "deepseek-flash", "complexidade": "leve"},
                {"id": "north-code-free", "provider": "openrouter",
                 "modelo": "cohere/north-mini-code:free", "complexidade": "media"},
                {"id": "nemotron-ultra-free", "provider": "openrouter",
                 "modelo": "nvidia/nemotron-3-ultra-550b-a55b:free",
                 "complexidade": "alta"},
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


MODALIDADES = ("texto", "imagem", "audio", "ficheiro")

# Limites declarados por rota (todos opcionais). Ausente NAO e zero nem
# infinito: e "nao declarado", e uma rota que nao declara nada continua a ser
# utilizavel — declarar e o que da a garantia, nao declarar nao proibe. So
# `json_nativo: false` e uma proibicao explicita.
_LIMITES_INTERNOS = ("contexto_max", "latencia_p95_ms")


# Superficies que o core ja entrega ao middleware `llm_request` via
# `agent.platform` (medidas em producao a 2026-11-03). Nao e uma lista
# fechada: `superficies` aceita qualquer valor, porque um provider novo traz
# superficies novas. A lista serve so para o aviso de gralha obvia.
SUPERFICIES_CONHECIDAS = ("cron", "api_server", "cli", "whatsapp", "subagent",
                          "tui", "desktop", "oneshot", "telegram", "discord")


def normalizar_superficies(
    valor: Any,
    contexto: str,
    erros: list[str],
    *,
    catalogo: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Valida `analise.superficies`: superficie -> regra de rota.

    Tres formas, e so tres:

    - `{"entrada": "<id do catalogo>"}` — a forma preferida: o `acesso`, o
      `esforco` e os limites vem da entrada, logo nao ha uma segunda copia
      do modelo a divergir do catalogo.
    - `{"provider": "<p>", "modelo": "<m>"}` — atalho; exige que exista uma
      entrada com esse par no catalogo, senao a regra nunca resolve.
    - `{"nivel": "leve|media|alta"}` — fixa o nivel e deixa o catalogo
      escolher (util quando o mesmo nivel serve varias superficies).
    - `{"activo": false}` — anula uma regra herdada do global sem apagar a
      do global.

    Com `catalogo` a validacao e estreita (a entrada tem de existir); sem
    ele valida so a forma, que e o que o bloco global pode garantir — o
    catalogo e por perfil.
    """
    if not isinstance(valor, dict):
        erros.append(f"{contexto} tem de ser um objecto (superficie -> regra)")
        return {}
    saida: dict[str, dict[str, Any]] = {}
    for bruto, regra in valor.items():
        nome = str(bruto or "").strip().lower()
        if not nome:
            erros.append(f"{contexto}: superficie com nome vazio")
            continue
        if not isinstance(regra, dict):
            erros.append(f"{contexto}.{nome} tem de ser um objecto")
            continue
        if regra.get("activo") is False:
            saida[nome] = {"activo": False}
            continue
        # Anti-gralha: vale com e sem catalogo. Uma regra para `crom` nunca
        # dispara (a superficie e `cron`) e o silencio e o pior resultado —
        # o utilizador fica convencido de ter configurado o cron.
        if nome not in SUPERFICIES_CONHECIDAS and nome.isalpha():
            parecida = difflib.get_close_matches(
                nome, SUPERFICIES_CONHECIDAS, n=1, cutoff=0.75)
            if parecida:
                erros.append(f"{contexto}.{nome}: superficie parecida com "
                             f"'{parecida[0]}' — corrija o nome ou use uma "
                             f"superficie real")
                continue
        limpa: dict[str, Any] = {}
        if regra.get("entrada") not in (None, ""):
            limpa["entrada"] = str(regra["entrada"]).strip()
        if regra.get("nivel") not in (None, ""):
            nivel = str(regra["nivel"]).strip().lower()
            if nivel not in ("leve", "media", "alta"):
                erros.append(f"{contexto}.{nome}.nivel invalido: {nivel}")
            else:
                limpa["nivel"] = nivel
        provider = str(regra.get("provider") or "").strip()
        modelo = str(regra.get("modelo") or "").strip()
        if provider or modelo:
            if not (provider and modelo):
                erros.append(f"{contexto}.{nome}: provider e modelo vao juntos")
            else:
                limpa["provider"] = provider
                limpa["modelo"] = modelo
        if not limpa:
            erros.append(f"{contexto}.{nome}: sem entrada, nivel nem provider/modelo")
            continue
        if catalogo is not None:
            if limpa.get("entrada") and not any(
                    str(e.get("id")) == limpa["entrada"] for e in catalogo):
                erros.append(f"{contexto}.{nome}: entrada desconhecida "
                             f"'{limpa['entrada']}' no catalogo do perfil")
                continue
            if limpa.get("provider") and not any(
                    str(e.get("provider", "")).lower() == limpa["provider"].lower()
                    and str(e.get("modelo", "")) == limpa["modelo"] for e in catalogo):
                erros.append(f"{contexto}.{nome}: {limpa['provider']}/"
                             f"{limpa['modelo']} nao esta no catalogo do perfil")
                continue
        saida[nome] = limpa
    return saida


def regra_de_superficie(pcfg: dict[str, Any], superficie: str) -> dict[str, Any] | None:
    """A regra viva para esta superficie no perfil, ja resolvida a uma entrada.

    Devolve `None` quando nao ha regra ou quando ela foi anulada com
    `activo: false`. A resolucao para entrada do catalogo acontece aqui, na
    leitura — nao na gravacao — para a regra sobreviver a mudanca do catalogo.
    """
    nome = str(superficie or "").strip().lower()
    if not nome:
        return None
    regras = ((pcfg.get("analise") or {}).get("superficies") or {})
    regra = regras.get(nome)
    if not isinstance(regra, dict) or regra.get("activo") is False:
        return None
    catalogo = pcfg.get("catalogo") or []
    if regra.get("entrada"):
        for entrada in catalogo:
            if str(entrada.get("id")) == regra["entrada"]:
                return {"superficie": nome, "entrada": entrada,
                        "regra": regra, "motivo": f"superficie:{nome}"}
        return None
    if regra.get("provider"):
        for entrada in catalogo:
            if (str(entrada.get("provider", "")).lower() == regra["provider"].lower()
                    and str(entrada.get("modelo", "")) == regra["modelo"]):
                return {"superficie": nome, "entrada": entrada,
                        "regra": regra, "motivo": f"superficie:{nome}"}
        return None
    if regra.get("nivel"):
        return {"superficie": nome, "entrada": None, "regra": regra,
                "motivo": f"superficie:{nome}:nivel"}
    return None


def normalizar_limites(entrada: dict[str, Any], indice: int, erros: list[str]) -> dict[str, Any]:
    """Valida e normaliza os limites de uma rota (janela, modalidades, json, latencia)."""
    saida: dict[str, Any] = {}
    for chave in _LIMITES_INTERNOS:
        if chave not in entrada or entrada.get(chave) in (None, ""):
            continue
        bruto = entrada.get(chave)
        if bruto is None or bruto == "":
            continue
        texto = str(bruto).strip()
        try:
            valor = int(texto)
        except (TypeError, ValueError):
            try:
                valor = int(float(texto))
            except (TypeError, ValueError):
                erros.append(f"catalogo[{indice}].{chave} deve ser inteiro positivo (recebido {bruto!r})")
                continue
        if valor <= 0:
            erros.append(f"catalogo[{indice}].{chave} deve ser positivo (recebido {valor})")
            continue
        saida[chave] = valor
    if "modalidades" in entrada and entrada.get("modalidades") is not None:
        bruto_mods = entrada.get("modalidades")
        if isinstance(bruto_mods, str):
            bruto_mods = [bruto_mods]
        if not isinstance(bruto_mods, list):
            erros.append(f"catalogo[{indice}].modalidades deve ser lista ({', '.join(MODALIDADES)})")
        else:
            mods = list(dict.fromkeys(str(m).strip().lower() for m in bruto_mods if str(m).strip()))
            invalidas = [m for m in mods if m not in MODALIDADES]
            if invalidas:
                erros.append(f"catalogo[{indice}].modalidades invalidas: "
                             f"{', '.join(invalidas)} (use {', '.join(MODALIDADES)})")
            elif mods:
                saida["modalidades"] = mods
    if "json_nativo" in entrada and entrada.get("json_nativo") is not None:
        valor = entrada.get("json_nativo")
        if not isinstance(valor, bool):
            erros.append(f"catalogo[{indice}].json_nativo deve ser booleano (recebido {valor!r})")
        else:
            saida["json_nativo"] = valor
    return saida


def teto_latencia(pcfg: dict[str, Any] | None) -> int:
    """Teto de latencia declarado pelo perfil, em ms. 0 = sem teto.

    Le-se tolerante de proposito: um valor mal escrito nao pode desligar a
    triagem toda nem rebentar o turno. Fica 0 (sem teto) e o `validar` avisa.
    """
    bruto = (pcfg or {}).get("latencia_max_ms")
    if bruto in (None, ""):
        return 0
    try:
        return max(0, int(str(bruto).strip()))
    except (TypeError, ValueError):
        try:
            return max(0, int(float(str(bruto).strip())))
        except (TypeError, ValueError):
            return 0


def rota_serve(entrada: dict[str, Any], needs: dict[str, Any] | None) -> str:
    """'' se a rota serve o pedido; razao curta se nao serve ('' tambem sem needs).

    E um filtro de *impossibilidade*, nao de preferencia: so recusa o que a rota
    declarou nao conseguir. Sem `needs` devolve '' e a selecao fica como antes.
    """
    if not needs:
        return ""
    tokens = int(needs.get("tokens_entrada") or 0)
    contexto = entrada.get("contexto_max")
    if contexto and tokens > contexto:
        return f"contexto {tokens}>{contexto}"
    pedidas = set(needs.get("modalidades") or ())
    declaradas = entrada.get("modalidades")
    if pedidas and declaradas is not None:
        faltam = pedidas - set(declaradas)
        if faltam:
            return "sem modalidade: " + ",".join(sorted(faltam))
    if needs.get("json") and entrada.get("json_nativo") is False:
        return "sem json nativo"
    latencia = entrada.get("latencia_p95_ms")
    teto = int(needs.get("latencia_max_ms") or 0)
    if latencia and teto and latencia > teto:
        return f"latencia {latencia}>{teto}"
    return ""


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
    if entrada.get("custo") not in (None, ""):
        bruto_custo = entrada.get("custo")
        try:
            valor_custo = float(str(bruto_custo).strip())
        except (TypeError, ValueError):
            erros.append(f"catalogo[{indice}].custo deve ser numero (recebido {bruto_custo!r})")
            return None
        if valor_custo < 0:
            erros.append(f"catalogo[{indice}].custo deve ser >= 0 (recebido {valor_custo})")
            return None
        saida["custo"] = valor_custo
    if entrada.get("ordem") not in (None, ""):
        bruto_ordem = entrada.get("ordem")
        try:
            valor_ordem = int(str(bruto_ordem).strip())
        except (TypeError, ValueError):
            erros.append(f"catalogo[{indice}].ordem deve ser inteiro (recebido {bruto_ordem!r})")
            return None
        if valor_ordem < 0:
            erros.append(f"catalogo[{indice}].ordem deve ser >= 0 (recebido {valor_ordem})")
            return None
        saida["ordem"] = valor_ordem
    saida.update(normalizar_limites(entrada, indice, erros))
    for chave in ("nota",):
        if entrada.get(chave):
            saida[chave] = str(entrada[chave])
    return saida


def normalizar_estrategia(valor: Any, prefixo: str = "estrategia",
                          erros: list[str] | None = None
                          ) -> tuple[dict[str, str], list[str]]:
    """Normaliza o bloco de estrategia contra as listas fechadas.

    Aceita a forma nova (`{"modo": ..., "por": ...}`) e a antiga (string, com o
    `por` em `rodizio.por`) — a config viva foi gravada na antiga e um `gravar`
    sem esta conversao apagava a estrategia do perfil. Devolve **so** as chaves
    declaradas: a ausencia e o que significa "herda do global", e preencher aqui
    as que faltam transformaria toda a heranca numa copia.

    Chave desconhecida e erro: e a garantia de que a lista e fechada. Um
    `{"modo": "rodizio", "prioridade": 3}` typo ficava a ser ignorado em
    silencio e o utilizador julgava que tinha mudado alguma coisa.
    """
    if erros is None:
        erros = []
    if valor is None or valor == "":
        return {}, erros
    modo: Any = None
    por: Any = None
    if isinstance(valor, str):
        modo = valor
    elif isinstance(valor, dict):
        for chave in sorted(set(valor) - {"modo", "por"}):
            erros.append(f"{prefixo}.{chave} desconhecida "
                         f"(aceita: modo, por)")
        modo = valor.get("modo")
        por = valor.get("por")
    else:
        erros.append(f"{prefixo} tem de ser objecto com 'modo' e 'por' "
                     f"(ou a string antiga com o modo)")
        return {}, erros
    saida: dict[str, str] = {}
    if modo is not None and modo != "":
        m = str(modo).strip().lower()
        if m not in ESTRATEGIAS:
            erros.append(f"{prefixo}.modo invalido: {str(modo)!r} "
                         f"(aceita: {', '.join(ESTRATEGIAS)})")
        else:
            saida["modo"] = m
    if por is not None and por != "":
        p = str(por).strip().lower()
        if p not in POR:
            erros.append(f"{prefixo}.por invalido: {str(por)!r} "
                         f"(aceita: {', '.join(POR)})")
        else:
            saida["por"] = p
    return saida, erros


def opcoes_estrategia() -> dict[str, list[dict[str, str]]]:
    """Listas fechadas com rotulo, para o painel construir os `select`.

    Uma casa so: o painel nao repete os valores em JS, pede-os aqui. Um valor
    novo passa a aparecer no `select` por acrescento em `ESTRATEGIAS`/`POR`.
    """
    return {
        campo: [{"valor": v, "rotulo": ROTULOS_ESTRATEGIA[campo][v]} for v in valores]
        for campo, valores in (("modo", ESTRATEGIAS), ("por", POR))
    }


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
            elif chave == "pesos":
                # Medido a 2026-11-02: `pesos` passava intacto para a config e
                # um valor nao inteiro chegava ao turno, onde `soma += peso`
                # rebenta com TypeError a meio da decisao. Os pesos sao dados
                # aprendidos por `eval/aprender.py`, logo a validacao tem de
                # travar lixo aqui, antes de entrar em producao.
                if not isinstance(valor, dict):
                    erros.append("'analise.pesos' tem de ser um objecto")
                else:
                    limpos: dict[str, int] = {}
                    for sinal, peso in valor.items():
                        nome_sinal = str(sinal).strip()
                        if not _RE_SINAL.match(nome_sinal):
                            erros.append(f"'analise.pesos': sinal invalido '{nome_sinal}'")
                            continue
                        if isinstance(peso, bool) or not isinstance(peso, int):
                            erros.append(f"'analise.pesos.{nome_sinal}' tem de ser inteiro")
                            continue
                        if not -20 <= peso <= 20:
                            erros.append(f"'analise.pesos.{nome_sinal}' fora de [-20, 20]")
                            continue
                        limpos[nome_sinal] = peso
                    saida["analise"]["pesos"] = limpos
            elif chave == "fixar_nivel_por_turno":
                if isinstance(valor, bool):
                    saida["analise"][chave] = valor
                else:
                    erros.append("'analise.fixar_nivel_por_turno' tem de ser booleano")
            elif chave == "aprendiz":
                if not isinstance(valor, dict):
                    erros.append("'analise.aprendiz' tem de ser um objecto")
                else:
                    saida["analise"]["aprendiz"] = valor
            elif chave == "superficies":
                saida["analise"]["superficies"] = normalizar_superficies(
                    valor, "'analise.superficies'", erros)
            else:
                saida["analise"][chave] = valor

    # Estrategia global: listas fechadas (`rodizio`|`prioridade`,
    # `turno`|`sessao`). A ausencia do bloco nao e erro — vale o padrao; mas um
    # valor declarado fora da lista e erro, porque "ignorar em silencio" faz o
    # utilizador julgar que mudou a estrategia quando nada mudou.
    est_global, erros_est = normalizar_estrategia(cfg.get("estrategia"), "estrategia")
    erros.extend(erros_est)
    # So se grava o que foi declarado. Escrever aqui os valores por omissao
    # transformava "nao escolhi" numa escolha, e a origem passava a ser
    # `herdado` para toda a gente — o painel deixava de poder dizer "por
    # omissao", que e a informacao util quando nada foi decidido.
    if est_global:
        saida["estrategia"] = dict(est_global)

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
        # Estrategia do perfil: bloco opcional. Ausente = herda o global; com
        # chaves = sobrepoe-se chave a chave. Aceita a forma antiga (string mais
        # `rodizio.por`) e converte-a — sem isto, gravar apagava a estrategia de
        # um perfil que a tinha declarado antes desta versao.
        bruto_est = pcfg.get("estrategia")
        por_antigo = (pcfg.get("rodizio") or {}).get("por") \
            if isinstance(pcfg.get("rodizio"), dict) else None
        # Migracao da forma antiga (string + `rodizio.por`): a semente escrevia
        # `rodizio`/`sessao` em todos os perfis, portanto esse valor nao e uma
        # escolha — converter sem distinguir fixava cada perfil ao padrao.
        migrada = isinstance(bruto_est, str) or (bruto_est is None and por_antigo is not None)
        if isinstance(bruto_est, str):
            bruto_est = {"modo": bruto_est, "por": por_antigo}
        elif bruto_est is None and por_antigo is not None:
            bruto_est = {"por": por_antigo}
        est_limpa, erros_est = normalizar_estrategia(
            bruto_est, f"perfis.{nome}.estrategia")
        erros.extend(erros_est)
        # A estrategia declarada grava-se tal e qual, mesmo quando hoje coincide
        # com o global. A heranca e a AUSENCIA da chave; a presenca e uma
        # declaracao. A regra do delta (que se mantem na `analise`) engolia uma
        # escolha explicita: o painel respondia 200, o perfil continuava a
        # herdar, e uma alteracao posterior do global arrastava um perfil que o
        # dono tinha declarado independente. As paginas de edicao inicializam
        # sempre de `pcfg["estrategia"]` (nunca do valor efectivo), portanto nao
        # geram copias por acidente.
        est_declarada = {} if (migrada and est_limpa == dict(ESTRATEGIA_PADRAO)) \
            else dict(est_limpa)
        # `teto_latencia` e tolerante em tempo de execucao (0 = sem teto), mas
        # um valor declarado e invalido tem de aparecer no editor, senao o
        # utilizador grava um teto que nao existe e nunca sabe por que.
        if pcfg.get("latencia_max_ms") not in (None, "") and teto_latencia(pcfg) <= 0:
            erros.append(f"perfis.{nome}.latencia_max_ms deve ser inteiro positivo "
                         f"(recebido {pcfg.get('latencia_max_ms')!r})")
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
                    # Grava-se o que o perfil declara, mesmo que coincida hoje
                    # com o global: o painel abre em "herdar" quando a chave nao
                    # existe, por isso uma declaracao so aparece quando o dono a
                    # escolhe. Um bloco vazio continua a significar herdar.
                    if limpo:
                        panalise["classificador"] = limpo
                elif chave == "superficies":
                    # Guardado bruto: a validacao estreita precisa do
                    # catalogo, que so existe depois deste laco.
                    panalise["superficies"] = valor
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
        # Superficies: valida-se o que o perfil vai REALMENTE usar — o que ele
        # declara mais o que herda do global — contra o catalogo do perfil. Uma
        # regra global nao pode ser validada no global (o catalogo e por
        # perfil), e uma regra herdada que aponte para uma entrada que este
        # perfil nao tem nunca resolveria: o turno cairia na analise e ninguem
        # saberia por que. Por isso o erro aparece aqui, no perfil.
        global_sup = saida["analise"].get("superficies") or {}
        bruto_sup = panalise.pop("superficies", None)
        if bruto_sup is not None or global_sup:
            efectivas = dict(global_sup)
            if bruto_sup is not None:
                if isinstance(bruto_sup, dict):
                    efectivas.update({str(k).strip().lower(): v
                                      for k, v in bruto_sup.items()})
                else:
                    erros.append(f"perfis.{nome}.analise.superficies tem de ser "
                                 f"um objecto (superficie -> regra)")
            limpas_sup = normalizar_superficies(
                efectivas, f"perfis.{nome}.analise.superficies", erros,
                catalogo=catalogo)
            # Grava-se so o delta: o que e igual ao global nao vira copia (a
            # mesma armadilha das outras chaves da `analise`).
            delta = {k: v for k, v in limpas_sup.items()
                     if global_sup.get(k) != v}
            if delta:
                panalise["superficies"] = delta
        saida["perfis"][nome] = {
            "tarefas": tarefas,
            "activo": bool(pcfg.get("activo", True)),
            # Sem bloco declarado nao se grava `estrategia`: a ausencia da chave
            # e o que significa "herda o global" (e o que permite mudar o global
            # sem tocar nos perfis que nunca declararam nada).
            **(lambda e: {"estrategia": e} if e else {})(est_declarada),
            # Teto de latencia do perfil: so se grava quando declarado, pela
            # mesma razao que a `analise` — ausencia e "sem teto", e um valor
            # herdado do global fixaria o perfil ao numero do dia.
            **(lambda t: {"latencia_max_ms": t} if t else {})(teto_latencia(pcfg)),
            # Sem nada declarado nao se grava `analise` vazia: a ausencia da
            # chave e o que significa "herda tudo do global".
            **(lambda a: {"analise": a} if a else {})(panalise),
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
    # Uma so casa para o perfil: `seams.nome_perfil()` (env -> seam -> default).
    try:
        from . import seams as _seams  # type: ignore[import-not-found]
        return _seams.nome_perfil()
    except Exception:  # noqa: BLE001 — gestor carregado standalone, sem pacote
        import importlib.util as _ilu2
        _cache = globals().get("_SEAMS_G")
        if _cache is None:
            try:
                _spec = _ilu2.spec_from_file_location(
                    "vmm_seams_g", Path(__file__).with_name("seams.py"))
                _cache = _ilu2.module_from_spec(_spec)  # type: ignore[arg-type]
                _spec.loader.exec_module(_cache)  # type: ignore[union-attr]
                globals()["_SEAMS_G"] = _cache
            except Exception:  # noqa: BLE001
                return "default"
        try:
            return _cache.nome_perfil()
        except Exception:  # noqa: BLE001
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
                elif chave == "superficies" and isinstance(valor, dict):
                    # Uma regra global vale para todos, superficie a superficie:
                    # se o perfil declarasse `cron` e substituisse o bloco
                    # inteiro, as regras globais das outras superficies
                    # desapareciam sem ninguem as ter apagado.
                    base = dict(analise.get("superficies") or {})
                    base.update(copy.deepcopy(valor))
                    analise["superficies"] = base
                else:
                    analise[chave] = copy.deepcopy(valor)
        saida["analise"] = analise
    # Estrategia: o global vale para todos; o perfil sobrepoe-se **chave a
    # chave** (`modo` e `por` sao independentes — declarar so a rotacao nao
    # obriga a repetir o modo). A fundicao e na leitura, pela mesma razao da
    # `analise`: um perfil gravado antes de o global mudar nao pode ficar preso.
    global_est = cfg.get("estrategia")
    est: dict[str, str] = dict(ESTRATEGIA_PADRAO)
    if isinstance(global_est, dict):
        est.update({k: v for k, v in global_est.items() if k in ("modo", "por")})
    cru_est = saida.get("estrategia")
    if isinstance(cru_est, str):
        cru_est = {"modo": cru_est, "por": (saida.get("rodizio") or {}).get("por")}
    if isinstance(cru_est, dict):
        est.update({k: v for k, v in cru_est.items()
                    if k in ("modo", "por") and isinstance(v, str) and v})
    saida["estrategia"] = est
    # De onde veio o que esta em vigor: e o que o cartao mostra. Sem isto, um
    # perfil que herda parece ter escolhido, e o utilizador muda o global sem
    # perceber porque e que aquele perfil nao mudou.
    saida["estrategia_origem"] = ("proprio" if isinstance(cru_est, dict) and cru_est
                                  else "herdado" if isinstance(global_est, dict) and global_est
                                  else "padrao")
    saida.pop("rodizio", None)
    return saida


def estrategia_global(cfg: dict[str, Any]) -> dict[str, str]:
    """Estrategia do global, ja normalizada, com a origem (`global`/`padrao`)."""
    est = dict(ESTRATEGIA_PADRAO)
    bruto = cfg.get("estrategia")
    if isinstance(bruto, dict):
        est.update({k: v for k, v in bruto.items() if k in ("modo", "por")})
    return {"modo": est["modo"], "por": est["por"],
            "origem": "global" if isinstance(bruto, dict) and bruto else "padrao"}


def estrategia_efectiva(cfg: dict[str, Any], perfil: str) -> dict[str, str]:
    """O que este perfil usa: bloco herdado/proprio mais a origem."""
    pcfg = perfil_config(cfg, perfil) or {}
    est = pcfg.get("estrategia") or dict(ESTRATEGIA_PADRAO)
    return {"modo": str(est.get("modo") or "rodizio"),
            "por": str(est.get("por") or "sessao"),
            "origem": str(pcfg.get("estrategia_origem") or "padrao")}


def classificador_efectivo(cfg: dict[str, Any], perfil: str) -> dict[str, Any]:
    """O que decide a complexidade neste perfil: um modelo (qual) ou a heuristica.

    Uma casa so para a pergunta que o painel e o CLI fazem — o painel nao
    reimplementa a heranca nem o "e utilizavel?". `perfil_config` resolve a
    heranca (global -> perfil); `analise_modelo.normalizar` diz se a definicao
    serve. Devolve sempre as mesmas chaves, para o consumidor nao ter de saber
    distinguir "nao existe" de "existe e nao presta":

    - ``modo``: ``"modelo"`` ou ``"heuristica"``.
    - ``provider``/``modelo``/``esforco``: o que esta em vigor (vazio se heuristica).
    - ``origem``: ``"proprio"`` (o perfil declara o bloco), ``"herdado"`` (vem do
      global) ou ``"heuristica"`` (ninguem declarou nada).
    - ``motivo``: porque e que um bloco declarado nao esta a decidir. Vazio quando
      o modo e ``modelo``. **Nao se esconde**: um classificador configurado que
      falha a validacao parece ligado a quem so olha para o `activo`.
    """
    pcfg = perfil_config(cfg, perfil) or {}
    analise = pcfg.get("analise") or {}
    bruto = analise.get("classificador")
    definicao, erros = _analise_modelo().normalizar(bruto)

    # O bloco que o perfil declara tem de ser lido do perfil CRU: `perfil_config`
    # ja fundiu o global, e sem isto todo o perfil parecia "proprio".
    cru = ((cfg.get("perfis") or {}).get(perfil) or {}).get("analise")
    declara = isinstance(cru, dict) and "classificador" in cru
    tem_rota = bool(definicao.get("provider") and definicao.get("modelo"))

    if declara:
        origem = "proprio"
    elif tem_rota or definicao.get("activo"):
        origem = "herdado"
    else:
        origem = "heuristica"

    saida: dict[str, Any] = {
        "modo": "heuristica",
        "activo": False,
        "provider": "",
        "modelo": "",
        "esforco": "",
        "formato_json": "auto",
        "via": "chat",
        "confianca_min": 0.0,
        "estado": "medidas",
        "origem": origem,
        "motivo": "",
    }
    if definicao.get("activo") and tem_rota and not erros:
        saida.update(
            {
                "modo": "modelo",
                "activo": True,
                "provider": definicao["provider"],
                "modelo": definicao["modelo"],
                "esforco": definicao.get("esforco") or "",
                "formato_json": definicao.get("formato_json") or "auto",
                "via": definicao.get("via") or "chat",
                "confianca_min": float(definicao.get("confianca_min") or 0.0),
                "estado": definicao.get("estado") or "medidas",
            }
        )
        return saida
    if erros:
        saida["motivo"] = "; ".join(erros)
    elif tem_rota:
        saida["motivo"] = "desligado"
    return saida


def catalogo_utilizavel(pcfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in (pcfg.get("catalogo") or []) if isinstance(e, dict) and not e.get("desligado")]


def candidatos(catalogo: list[dict[str, Any]], nivel: str) -> list[dict[str, Any]]:
    return [e for e in catalogo if nivel in (e.get("complexidade") or [])]


def _chave_ordem(entrada: dict[str, Any]) -> tuple:
    """Chave total e determinista de um candidato: `ordem`, custo declarado, `id`.

    A `ordem` declarada **manda** sobre o custo, e a entrada que a declara ordena
    antes da que nao a declara: sem essa precedencia, um pago nunca podia ser
    escolhido a frente de um gratuito, por muito que o dono o escrevesse. Dentro
    do mesmo patamar, a entrada **sem** custo ordena depois das que o declaram —
    ler a ausencia como zero fazia o nao-classificado ganhar a todos os pagos.
    """
    custo = entrada.get("custo")
    if custo is None:
        patamar_custo = (1, 0.0)
    else:
        try:
            patamar_custo = (0, float(custo))
        except (TypeError, ValueError):
            patamar_custo = (1, 0.0)
    bruto_ordem = entrada.get("ordem")
    if bruto_ordem in (None, ""):
        return ((1, 0), patamar_custo, str(entrada.get("id") or ""))
    try:
        ordem = int(str(bruto_ordem).strip())
    except (TypeError, ValueError):
        return ((1, 0), patamar_custo, str(entrada.get("id") or ""))
    return ((0, ordem), patamar_custo, str(entrada.get("id") or ""))


def ordenar_candidatos(lista: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordem declarada dos candidatos: custo, `ordem` e `id`. Nao roda nada.

    O `id` fecha a chave: e unico e nao depende da posicao no ficheiro, portanto
    dois candidatos empatados resolvem-se sempre da mesma maneira.
    """
    return sorted(lista, key=_chave_ordem)


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
