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

import contextlib
import copy
import difflib
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

NOME = "vipernet-model-manager"


#: Traducao das mensagens: chave = texto portugues, valor = ingles. A lingua vem
#: da config viva e, em `pt`, `t()` devolve a propria chave — logo o texto
#: historico mantem-se byte a byte. O modulo resolve-se por tres vias porque
#: este ficheiro e carregado de duas formas (pacote do Hermes e
#: `spec_from_file_location`, com a directoria fora do `sys.path`), e tem de ser
#: **uma so** instancia: duas davam duas linguas no mesmo processo, e um teste
#: que fixa a lingua numa delas nao teria efeito na outra.
def _carregar_i18n():
    ja = sys.modules.get("i18n")
    if ja is not None and hasattr(ja, "t") and hasattr(ja, "IDIOMAS"):
        return ja
    try:  # carregado como pacote do Hermes
        from . import i18n as mod

        return mod
    except Exception:
        pass
    try:  # CLI, testes e dashboard: a directoria esta no `sys.path`
        import i18n as mod

        return mod
    except Exception:
        pass
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("i18n", Path(__file__).resolve().parent / "i18n.py")
    mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["i18n"] = mod  # nome fixo: garante uma unica instancia
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_i18n = _carregar_i18n()


def _t(chave: str, /, **valores: Any) -> str:
    """Mensagem do validador na lingua activa (ver `i18n.t`)."""
    return _i18n.t(chave, **valores)

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

# Tarefa persistente por sessao (3.32.0). O detector
# (`analise.detectar_tarefa`) le **so** a ultima mensagem do utilizador — de
# proposito, para nao classificar historico nem contexto injectado — e o pin
# fixa a decisao por turno. Juntas, as duas coisas davam isto: o turno que traz
# "analise de arquitetura" associava a rota certa, e o turno seguinte
# ("continua", "agora escreve o parecer") nao tinha nenhum dos termos, a
# associacao desaparecia e quem decidia era a complexidade. O turno trocava de
# modelo a meio da conversa.
#
# `expira_turnos` e `expira_min` contam a partir da ultima **deteccao**, nunca
# da ultima heranca: se herdar renovasse o prazo, um "sim" de oito em oito
# turnos mantinha um modelo pago vivo indefinidamente e ninguem dava por isso.
TAREFA_PERSISTENTE_PADRAO: dict[str, Any] = {
    "activo": True,
    "expira_turnos": 6,
    "expira_min": 90,
}
TAREFA_PERSISTENTE_CHAVES = frozenset(TAREFA_PERSISTENTE_PADRAO)

# Circuit breaker / failure tracking: regra "se modelo falha X vezes em Y minutos,
# aumentar cooldown em Z%". Configuravel global e por perfil (heranca chave a chave).
CIRCUIT_BREAKER_PADRAO: dict[str, Any] = {
    "ativo": True,
    "limite_falhas": 3,
    "janela_minutos": 5,
    "multiplicador_cooldown": 1.5,
}
_CHAVES_CIRCUIT_BREAKER = frozenset(CIRCUIT_BREAKER_PADRAO)

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
    # A tarefa associada vive na **sessao**, com prazo — ver
    # `TAREFA_PERSISTENTE_PADRAO`. Herda-se do global chave a chave, como a
    # estrategia: declarar so o prazo nao obriga a repetir o `activo`.
    "tarefa_persistente": dict(TAREFA_PERSISTENTE_PADRAO),
    # Circuit breaker: regra de falhas repetidas -> cooldown progressivo.
    # Herda-se do global chave a chave (ativo, limite_falhas, janela_minutos,
    # multiplicador_cooldown). Ausente = herda do global.
    "circuit_breaker": dict(CIRCUIT_BREAKER_PADRAO),
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


# ------------------------------------------------------------------ listas fechadas
# Chaves aceites em cada nivel do config. Uma chave fora destas listas e ERRO e
# nao um descarte silencioso. Medido a 2026-11-04, antes desta guarda: dez casos
# — `classificador` no topo, `perfís` com acento, `modelo` declarado no perfil,
# `latencia_max` sem o `_ms`, `cust` por `custo` na entrada, `superficie` no
# singular — passavam com ZERO erros e o valor declarado desaparecia na
# gravacao. O utilizador julgava ter mudado uma definicao que nunca chegou ao
# disco, que e a pior classe de defeito deste ficheiro: silencioso e com
# aspecto de sucesso.
#
# `_CHAVES_ANALISE` deriva de `ANALISE_PADRAO` de proposito: uma chave nova
# acrescentada la entra aqui sozinha, e um teste prende as duas para nao
# divergirem. As listas do classificador vivem em `analise_modelo` (`CHAVES`),
# derivadas do `PADRAO` dele pela mesma razao.
_CHAVES_CONFIG = frozenset({"versao", "activo", "registar", "analise",
                            "estrategia", "retencao", "sombra", "idioma",
                            "perfis"})
_CHAVES_PERFIL = frozenset({"activo", "estrategia", "analise", "catalogo",
                            "tarefas", "latencia_max_ms", "rodizio"})
_CHAVES_ANALISE = frozenset(set(ANALISE_PADRAO) | {"pesos", "aprendiz",
                                                  "classificador", "circuit_breaker"})
# `limiares` so tem dois limiares: sao os que `analise.nivel_de_score` le.
LIMIARES_CHAVES = frozenset({"media", "alta"})

# Retencao: quantos dias de detalhe ficam no ledger e quantos backups do config
# se guardam. Fica no config para o painel e o CLI lerem o mesmo numero — um
# limite escrito em dois sitios divergem sempre.
RETENCAO_PADRAO: dict[str, int] = {
    "dias_completo": 7,
    "backups_manter": 10,
    "backups_semanais": 1,
}
_CHAVES_RETENCAO = frozenset(RETENCAO_PADRAO)

_CHAVES_TAREFA = frozenset({"termos", "prioridade"})
_CHAVES_ENTRADA = frozenset({
    "id", "provider", "modelo", "complexidade", "niveis", "acesso",
    "esforco", "tarefas", "desligado", "custo", "ordem", "nota",
    "superficies", "modalidades", "json_nativo",
}) | frozenset(_LIMITES_INTERNOS)


def _recusar_desconhecidas(mapa: Any, permitidas: frozenset[str],
                           prefixo: str, erros: list[str]) -> None:
    """Um erro por cada chave fora da lista fechada, com sugestao do typo.

    A sugestao vem de `difflib` porque o caso real e o engano de uma letra
    (`cust` por `custo`, `latencia_max` por `latencia_max_ms`): com a chave
    certa ao lado, o erro corrige-se sozinho em vez de obrigar a ler a lista
    inteira. Nao levanta e nao escreve: so acrescenta a `erros`.
    """
    if not isinstance(mapa, dict):
        return
    for chave in sorted(set(mapa) - set(permitidas), key=str):
        # Corte a 0,75 medido a 2026-11-04: as sugestoes uteis ficam todas acima
        # (`cust`->`custo` 0,889, `janela_msgs`->`janela_mensagens` 0,815) e a
        # unica falsa (`superficies`->`perfis`, 0,706) cai. Uma sugestao errada
        # custa mais do que nenhuma, porque manda mudar a chave certa.
        perto = difflib.get_close_matches(str(chave), sorted(permitidas),
                                          n=1, cutoff=0.75)
        if perto:
            dica = _t("; queria dizer '{p0}'?", p0=perto[0])
        else:
            lista = ", ".join(sorted(permitidas))
            if len(lista) > 140:
                lista = lista[:137] + "..."
            dica = _t(" (aceita: {p0})", p0=lista)
        erros.append(_t("{p0}.{p1} desconhecida{p2}", p0=prefixo, p1=chave, p2=dica))


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
        erros.append(_t("{p0} tem de ser um objecto (superficie -> regra)", p0=contexto))
        return {}
    saida: dict[str, dict[str, Any]] = {}
    for bruto, regra in valor.items():
        nome = str(bruto or "").strip().lower()
        if not nome:
            erros.append(_t("{p0}: superficie com nome vazio", p0=contexto))
            continue
        if not isinstance(regra, dict):
            erros.append(_t("{p0}.{p1} tem de ser um objecto", p0=contexto, p1=nome))
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
                erros.append(_t("{p0}.{p1}: superficie parecida com '{p2}' — corrija o nome ou use uma superficie real", p0=contexto, p1=nome, p2=parecida[0]))
                continue
        limpa: dict[str, Any] = {}
        if regra.get("entrada") not in (None, ""):
            limpa["entrada"] = str(regra["entrada"]).strip()
        if regra.get("nivel") not in (None, ""):
            nivel = str(regra["nivel"]).strip().lower()
            if nivel not in ("leve", "media", "alta"):
                erros.append(_t("{p0}.{p1}.nivel invalido: {p2}", p0=contexto, p1=nome, p2=nivel))
            else:
                limpa["nivel"] = nivel
        provider = str(regra.get("provider") or "").strip()
        modelo = str(regra.get("modelo") or "").strip()
        if provider or modelo:
            if not (provider and modelo):
                erros.append(_t("{p0}.{p1}: provider e modelo vao juntos", p0=contexto, p1=nome))
            else:
                limpa["provider"] = provider
                limpa["modelo"] = modelo
        if not limpa:
            erros.append(_t("{p0}.{p1}: sem entrada, nivel nem provider/modelo", p0=contexto, p1=nome))
            continue
        if catalogo is not None:
            if limpa.get("entrada") and not any(
                    str(e.get("id")) == limpa["entrada"] for e in catalogo):
                erros.append(_t("{p0}.{p1}: entrada desconhecida '{p2}' no catalogo do perfil", p0=contexto, p1=nome, p2=limpa['entrada']))
                continue
            if limpa.get("provider") and not any(
                    str(e.get("provider", "")).lower() == limpa["provider"].lower()
                    and str(e.get("modelo", "")) == limpa["modelo"] for e in catalogo):
                erros.append(_t("{p0}.{p1}: {p2}/{p3} nao esta no catalogo do perfil", p0=contexto, p1=nome, p2=limpa['provider'], p3=limpa['modelo']))
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


# Chave que vale como "todas as outras" dentro do mapa de superficie de um
# modelo: com `"*": false` o modelo so serve as superficies que nomear a `true`.
CORINGA_SUPERFICIE = "*"


def normalizar_superficies_modelo(
    valor: Any,
    contexto: str,
    erros: list[str],
) -> dict[str, bool]:
    """Valida `catalogo[i].superficies`: superficie -> permitido (booleano).

    Duas camadas, com proposito diferente, e por isso com forma diferente:

    - No **perfil** (`analise.superficies`) a regra diz *quem serve* a
      superficie: mapeia superficie -> rota (entrada, niveau ou provider/modelo).
    - No **modelo** (`catalogo[i].superficies`) a regra diz *se pode servir*:
      mapeia superficie -> booleano. E um filtro de elegibilidade, nao um router.
      Um modelo nunca aponta para outro — isso seria uma terceira copia da
      decisao, e o desempate e do perfil.

    Heranca: o modelo que nao declara `superficies` herda o comportamento do
    perfil (a regra do perfil decide a rota e o modelo nao restringe). Uma
    superficie ausente do mapa declarado tambem herda; para inverter o sentido
    usa-se a chave `*` (`{"*": false, "cron": true}` = so cron).

    Nome parecido com uma superficie real e erro, e nao aviso: uma regra para
    `crom` nunca dispara e o silencio e o pior resultado — o utilizador fica
    convencido de ter limitado o cron.
    """
    if isinstance(valor, list):
        # Acucar: lista de superficies permitidas vale por todas a `true`.
        valor = {str(v): True for v in valor if str(v).strip()}
    if not isinstance(valor, dict):
        erros.append(_t("{p0}.superficies tem de ser um objecto (superficie -> true/false)", p0=contexto))
        return {}
    saida: dict[str, bool] = {}
    for bruto, permitido in valor.items():
        nome = str(bruto or "").strip().lower()
        if not nome:
            erros.append(_t("{p0}.superficies: superficie com nome vazio", p0=contexto))
            continue
        if not isinstance(permitido, bool):
            erros.append(_t("{p0}.superficies.{p1} tem de ser true ou false (recebido {p2})", p0=contexto, p1=nome, p2=repr(permitido)))
            continue
        if nome != CORINGA_SUPERFICIE and nome not in SUPERFICIES_CONHECIDAS \
                and nome.isalpha():
            parecida = difflib.get_close_matches(
                nome, SUPERFICIES_CONHECIDAS, n=1, cutoff=0.75)
            if parecida:
                erros.append(_t("{p0}.superficies.{p1}: superficie parecida com '{p2}' — corrija o nome ou use uma superficie real", p0=contexto, p1=nome, p2=parecida[0]))
                continue
        saida[nome] = permitido
    return saida


def serve_superficie(entrada: dict[str, Any], superficie: str) -> bool:
    """O modelo aceita servir esta superficie? Ausente = herda (sim).

    Le-se de forma tolerante: um mapa mal escrito nao pode calar o turno todo.
    So o `false` explicito exclui — a ausencia e heranca, nao uma proibicao.
    """
    nome = str(superficie or "").strip().lower()
    if not nome:
        return True
    mapa = entrada.get("superficies")
    if not isinstance(mapa, dict) or not mapa:
        return True
    if nome in mapa:
        return bool(mapa[nome])
    if CORINGA_SUPERFICIE in mapa:
        return bool(mapa[CORINGA_SUPERFICIE])
    return True


def superficies_do_modelo(pcfg: dict[str, Any], entrada_id: str) -> dict[str, bool] | None:
    """O mapa declarado por um modelo, ou `None` quando herda do perfil."""
    for entrada in (pcfg.get("catalogo") or []):
        if isinstance(entrada, dict) and str(entrada.get("id")) == str(entrada_id):
            mapa = entrada.get("superficies")
            return dict(mapa) if isinstance(mapa, dict) and mapa else None
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
                erros.append(_t("catalogo[{p0}].{p1} deve ser inteiro positivo (recebido {p2})", p0=indice, p1=chave, p2=repr(bruto)))
                continue
        if valor <= 0:
            erros.append(_t("catalogo[{p0}].{p1} deve ser positivo (recebido {p2})", p0=indice, p1=chave, p2=valor))
            continue
        saida[chave] = valor
    if "modalidades" in entrada and entrada.get("modalidades") is not None:
        bruto_mods = entrada.get("modalidades")
        if isinstance(bruto_mods, str):
            bruto_mods = [bruto_mods]
        if not isinstance(bruto_mods, list):
            erros.append(_t("catalogo[{p0}].modalidades deve ser lista ({p1})", p0=indice, p1=', '.join(MODALIDADES)))
        else:
            mods = list(dict.fromkeys(str(m).strip().lower() for m in bruto_mods if str(m).strip()))
            invalidas = [m for m in mods if m not in MODALIDADES]
            if invalidas:
                erros.append(_t("catalogo[{p0}].modalidades invalidas: {p1} (use {p2})", p0=indice, p1=', '.join(invalidas), p2=', '.join(MODALIDADES)))
            elif mods:
                saida["modalidades"] = mods
    if "json_nativo" in entrada and entrada.get("json_nativo") is not None:
        valor = entrada.get("json_nativo")
        if not isinstance(valor, bool):
            erros.append(_t("catalogo[{p0}].json_nativo deve ser booleano (recebido {p1})", p0=indice, p1=repr(valor)))
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
        erros.append(_t("catalogo[{p0}] nao e um objecto", p0=indice))
        return None
    _recusar_desconhecidas(entrada, _CHAVES_ENTRADA, f"catalogo[{indice}]", erros)
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
        erros.append(_t("catalogo[{p0}] sem provider/modelo", p0=indice))
        return None
    if not niveis:
        erros.append(_t("catalogo[{p0}] ({p1}:{p2}) sem complexidade", p0=indice, p1=provider, p2=modelo))
        return None
    if invalidos:
        erros.append(_t("catalogo[{p0}] complexidade invalida: {p1}", p0=indice, p1=', '.join(invalidos)))
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
            erros.append(_t("catalogo[{p0}] ({p1}:{p2}) acesso invalido: '{p3}' (use api|subscricao)", p0=indice, p1=provider, p2=modelo, p3=acesso_bruto))
            return None
    if acesso == "subscricao" and provider.lower() not in _PROVIDERS_SUBSCRICAO:
        erros.append(_t("catalogo[{p0}] ({p1}:{p2}) acesso 'subscricao' exige provider openai-codex", p0=indice, p1=provider, p2=modelo))
        return None
    if acesso == "api" and provider.lower() in _PROVIDERS_SUBSCRICAO:
        erros.append(_t("catalogo[{p0}] ({p1}:{p2}) provider openai-codex e uma subscricao; use acesso 'subscricao'", p0=indice, p1=provider, p2=modelo))
        return None
    saida["acesso"] = acesso
    # esforco de raciocinio: opcional e por rota. Ausente nao e "medio" — e
    # "nao declarado", para que uma rota que nao precise nao arraste o esforco
    # de outra.
    esforco, erro_esforco = normalizar_esforco(entrada.get("esforco"))
    if erro_esforco:
        erros.append(_t("catalogo[{p0}] ({p1}:{p2}) {p3}", p0=indice, p1=provider, p2=modelo, p3=erro_esforco))
        return None
    if esforco:
        saida["esforco"] = esforco
    tarefas = entrada.get("tarefas", [])
    if not isinstance(tarefas, list) or any(not isinstance(t, str) or not t.strip() for t in tarefas):
        erros.append(_t("catalogo[{p0}].tarefas deve ser lista de identificadores", p0=indice))
    else:
        saida["tarefas"] = list(dict.fromkeys(t.strip() for t in tarefas))
    if entrada.get("desligado"):
        saida["desligado"] = True
    if entrada.get("custo") not in (None, ""):
        bruto_custo = entrada.get("custo")
        try:
            valor_custo = float(str(bruto_custo).strip())
        except (TypeError, ValueError):
            erros.append(_t("catalogo[{p0}].custo deve ser numero (recebido {p1})", p0=indice, p1=repr(bruto_custo)))
            return None
        if valor_custo < 0:
            erros.append(_t("catalogo[{p0}].custo deve ser >= 0 (recebido {p1})", p0=indice, p1=valor_custo))
            return None
        saida["custo"] = valor_custo
    if entrada.get("ordem") not in (None, ""):
        bruto_ordem = entrada.get("ordem")
        try:
            valor_ordem = int(str(bruto_ordem).strip())
        except (TypeError, ValueError):
            erros.append(_t("catalogo[{p0}].ordem deve ser inteiro (recebido {p1})", p0=indice, p1=repr(bruto_ordem)))
            return None
        if valor_ordem < 0:
            erros.append(_t("catalogo[{p0}].ordem deve ser >= 0 (recebido {p1})", p0=indice, p1=valor_ordem))
            return None
        saida["ordem"] = valor_ordem
    saida.update(normalizar_limites(entrada, indice, erros))
    # Superficies do modelo: filtro de elegibilidade por superficie de sessao.
    # Ausente = herda do perfil (o modelo nao restringe nada).
    bruto_sup = entrada.get("superficies")
    if bruto_sup not in (None, {}, []):
        mapa = normalizar_superficies_modelo(
            bruto_sup, f"catalogo[{indice}] ({provider}:{modelo})", erros)
        if mapa:
            saida["superficies"] = mapa
    for chave in ("nota",):
        if entrada.get(chave):
            saida[chave] = str(entrada[chave])
    return saida


def normalizar_retencao(valor: Any, prefixo: str = "retencao",
                        erros: list[str] | None = None
                        ) -> tuple[dict[str, int], list[str]]:
    """Normaliza o bloco de retencao: tres inteiros, listas fechadas.

    So devolve o que foi declarado — ausente significa "vale o padrao", como na
    estrategia. Um `0` explicito no `dias_completo` e legitimo (nao compacta
    nada); o que nao e legitimo e um valor que nao seja inteiro nao negativo, ou
    um `backups_manter` a zero, que apagaria todos os backups de rotina.
    """
    if erros is None:
        erros = []
    if valor is None or valor == "":
        return {}, erros
    if not isinstance(valor, dict):
        erros.append(_t("{p0} tem de ser um objecto com {p1}", p0=prefixo, p1=sorted(_CHAVES_RETENCAO)))
        return {}, erros
    _recusar_desconhecidas(valor, _CHAVES_RETENCAO, prefixo, erros)
    saida: dict[str, int] = {}
    minimos = {"dias_completo": 0, "backups_manter": 1, "backups_semanais": 0}
    for chave in sorted(_CHAVES_RETENCAO):
        if chave not in valor:
            continue
        bruto = valor[chave]
        if isinstance(bruto, bool) or not isinstance(bruto, (int, str)):
            erros.append(_t("{p0}.{p1} tem de ser um inteiro (veio {p2})", p0=prefixo, p1=chave, p2=repr(bruto)))
            continue
        try:
            numero = int(bruto)
        except (TypeError, ValueError):
            erros.append(_t("{p0}.{p1} tem de ser um inteiro (veio {p2})", p0=prefixo, p1=chave, p2=repr(bruto)))
            continue
        if numero < minimos[chave]:
            erros.append(_t("{p0}.{p1} tem de ser >= {p2} (veio {p3})", p0=prefixo, p1=chave, p2=minimos[chave], p3=numero))
            continue
        saida[chave] = numero
    return saida, erros


def normalizar_tarefa_persistente(valor: Any,
                                 prefixo: str = "analise.tarefa_persistente",
                                 erros: list[str] | None = None
                                 ) -> dict[str, Any]:
    """Normaliza o bloco da tarefa persistente; devolve **so** o declarado.

    Devolver so o declarado e o que mantem a heranca: em `perfil_config` uma
    chave ausente cai no global, e preencher aqui as que faltam transformava
    todo o perfil numa copia fixa do dia em que foi gravado.

    Chave desconhecida e erro. Os numeros tem limites porque `0` e o valor que
    parece inocente e desliga a heranca com o `activo` ainda a `true`: o dono
    julgaria que a tarefa persistia enquanto o modelo continuava a mudar a meio
    da conversa — o defeito que este bloco existe para corrigir.
    """
    if erros is None:
        erros = []
    if valor is None or valor == "":
        return {}
    if not isinstance(valor, dict):
        erros.append(_t("{p0} tem de ser um objecto", p0=prefixo))
        return {}
    _recusar_desconhecidas(valor, TAREFA_PERSISTENTE_CHAVES, prefixo, erros)
    limite = {"expira_turnos": 50, "expira_min": 1440}
    saida: dict[str, Any] = {}
    for chave, valor_chave in valor.items():
        if chave not in TAREFA_PERSISTENTE_CHAVES:
            continue
        if chave == "activo":
            if isinstance(valor_chave, bool):
                saida[chave] = valor_chave
            else:
                erros.append(_t("{p0}.activo tem de ser booleano", p0=prefixo))
            continue
        if isinstance(valor_chave, bool) or not isinstance(valor_chave, int):
            erros.append(_t("{p0}.{p1} tem de ser inteiro", p0=prefixo, p1=chave))
            continue
        if not 1 <= valor_chave <= limite[chave]:
            erros.append(_t("{p0}.{p1} fora de [1, {p2}]", p0=prefixo, p1=chave, p2=limite[chave]))
            continue
        saida[chave] = valor_chave
    return saida


# Idioma do painel: lista fechada de duas linguas. O ingles e o padrao — uma
# instalacao nova abre em ingles. O valor vive no config (e nao no browser) para
# o painel, o CLI e qualquer sessao lerem o mesmo; e um codigo fora da lista e
# erro, nao um silencioso "volta ao padrao", que deixaria o utilizador a julgar
# que mudou a lingua quando nada mudou.
IDIOMAS = ("en", "pt")
IDIOMA_PADRAO = "en"


def normalizar_idioma(valor: Any) -> tuple[str | None, list[str]]:
    """Valida o codigo de lingua. Devolve `(codigo, erros)`; `None` = ausente.

    Aceita as formas regionais (`pt-PT`, `en-US`, `pt_BR`) e reduz a lingua
    base: o painel nao tem variantes, e recusar `pt-PT` obrigava o utilizador a
    adivinhar que tinha de escrever `pt`.
    """
    erros: list[str] = []
    if valor is None or valor == "":
        return None, erros
    if not isinstance(valor, str):
        erros.append(_t("idioma tem de ser texto (aceita: {p0})", p0=', '.join(IDIOMAS)))
        return None, erros
    base = valor.strip().lower().replace("_", "-").split("-")[0]
    if base not in IDIOMAS:
        erros.append(_t("idioma invalido: {p0} (aceita: {p1})", p0=repr(valor), p1=', '.join(IDIOMAS)))
        return None, erros
    return base, erros


def idioma_efectivo(cfg: dict[str, Any] | None) -> str:
    """Lingua em vigor: a declarada, ou o padrao. Nunca levanta."""
    if not isinstance(cfg, dict):
        return IDIOMA_PADRAO
    codigo, erros = normalizar_idioma(cfg.get("idioma"))
    if erros or not codigo:
        return IDIOMA_PADRAO
    return codigo


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
            erros.append(_t("{p0}.{p1} desconhecida (aceita: modo, por)", p0=prefixo, p1=chave))
        modo = valor.get("modo")
        por = valor.get("por")
    else:
        erros.append(_t("{p0} tem de ser objecto com 'modo' e 'por' (ou a string antiga com o modo)", p0=prefixo))
        return {}, erros
    saida: dict[str, str] = {}
    if modo is not None and modo != "":
        m = str(modo).strip().lower()
        if m not in ESTRATEGIAS:
            erros.append(_t("{p0}.modo invalido: {p1} (aceita: {p2})", p0=prefixo, p1=repr(str(modo)), p2=', '.join(ESTRATEGIAS)))
        else:
            saida["modo"] = m
    if por is not None and por != "":
        p = str(por).strip().lower()
        if p not in POR:
            erros.append(_t("{p0}.por invalido: {p1} (aceita: {p2})", p0=prefixo, p1=repr(str(por)), p2=', '.join(POR)))
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


SOMBRA_PADRAO: dict[str, Any] = {"activo": False, "vida_ttl_h": 24}
_CHAVES_SOMBRA = frozenset(SOMBRA_PADRAO)


def normalizar_sombra(valor: Any,
                      campo: str = "sombra") -> tuple[dict[str, Any], list[str]]:
    """Bloco do ensaio em sombra: interruptor e validade da sonda de vida.

    Mesma regra da estrategia e da retencao — so se grava o que foi declarado.
    `activo` ausente **nao** e `false` por acidente: e "nao decidido", e o ensaio
    so corre com `activo` verdadeiro. `vida_ttl_h` tem tecto de uma semana: uma
    sonda esquecida nao pode autorizar para sempre modelos que ja morreram — foi
    assim que um slug gratuito retirado pela OpenRouter ia entrar em trafego.
    """
    erros: list[str] = []
    if valor is None:
        return {}, erros
    if not isinstance(valor, dict):
        return {}, [_t("{p0} nao e um objecto", p0=campo)]
    _recusar_desconhecidas(valor, _CHAVES_SOMBRA, campo, erros)
    saida: dict[str, Any] = {}
    if "activo" in valor:
        saida["activo"] = bool(valor["activo"])
    if "vida_ttl_h" in valor:
        try:
            ttl = float(valor["vida_ttl_h"])
        except (TypeError, ValueError):
            erros.append(_t("{p0}.vida_ttl_h nao e um numero", p0=campo))
        else:
            if not 1 <= ttl <= 168:
                erros.append(_t("{p0}.vida_ttl_h fora de 1..168 horas", p0=campo))
            else:
                saida["vida_ttl_h"] = int(ttl) if ttl.is_integer() else ttl
    return saida, erros


def normalizar_circuit_breaker(valor: Any,
                                prefixo: str = "circuit_breaker",
                                erros: list[str] | None = None
                                ) -> tuple[dict[str, Any], list[str]]:
    """Normaliza o bloco do circuit breaker: valida chaves e limites.

    Mesma regra da estrategia/retencao/sombra — so se grava o que foi declarado.
    A ausencia da chave no ficheiro = herda do global/padrao; a presenca da
    chave = declaracao explicita. Um valor igual ao global nao e declaracao
    (a regra do delta na validacao do perfil trata disso).
    """
    if erros is None:
        erros = []
    if valor is None or valor == "":
        return {}, erros
    if not isinstance(valor, dict):
        return {}, [_t("{p0} tem de ser um objecto com {p1}", p0=prefixo, p1=sorted(_CHAVES_CIRCUIT_BREAKER))]
    _recusar_desconhecidas(valor, _CHAVES_CIRCUIT_BREAKER, prefixo, erros)
    saida: dict[str, Any] = {}
    if "ativo" in valor:
        if isinstance(valor["ativo"], bool):
            saida["ativo"] = bool(valor["ativo"])
        else:
            erros.append(_t("{p0}.ativo tem de ser booleano", p0=prefixo))
    if "limite_falhas" in valor:
        if isinstance(valor["limite_falhas"], bool) or not isinstance(valor["limite_falhas"], int):
            erros.append(_t("{p0}.limite_falhas tem de ser inteiro", p0=prefixo))
        elif not 1 <= valor["limite_falhas"] <= 50:
            erros.append(_t("{p0}.limite_falhas fora de 1..50 (veio {p1})", p0=prefixo, p1=valor['limite_falhas']))
        else:
            saida["limite_falhas"] = int(valor["limite_falhas"])
    if "janela_minutos" in valor:
        if isinstance(valor["janela_minutos"], bool) or not isinstance(valor["janela_minutos"], int):
            erros.append(_t("{p0}.janela_minutos tem de ser inteiro", p0=prefixo))
        elif not 1 <= valor["janela_minutos"] <= 1440:
            erros.append(_t("{p0}.janela_minutos fora de 1..1440 (veio {p1})", p0=prefixo, p1=valor['janela_minutos']))
        else:
            saida["janela_minutos"] = int(valor["janela_minutos"])
    if "multiplicador_cooldown" in valor:
        if isinstance(valor["multiplicador_cooldown"], bool) or not isinstance(valor["multiplicador_cooldown"], (int, float)):
            erros.append(_t("{p0}.multiplicador_cooldown tem de ser numero", p0=prefixo))
        elif not 1.0 <= float(valor["multiplicador_cooldown"]) <= 10.0:
            erros.append(_t("{p0}.multiplicador_cooldown fora de 1.0..10.0 (veio {p1})", p0=prefixo, p1=valor['multiplicador_cooldown']))
        else:
            saida["multiplicador_cooldown"] = float(valor["multiplicador_cooldown"])
    return saida, erros


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
        return copy.deepcopy(SEED), [_t("config nao e um objecto")]
    _recusar_desconhecidas(cfg, _CHAVES_CONFIG, "config", erros)
    # `versao` tambem nao se reescreve em silencio: um ficheiro de uma versao
    # futura aberto e gravado aqui perdia as chaves que esta versao desconhece,
    # e o utilizador ficava com uma copia mutilada sem um unico erro.
    _versao = cfg.get("versao")
    if _versao not in (None, "", 3, "3"):
        erros.append(_t("config.versao {p0} nao suportada (este plugin le a versao 3)", p0=repr(_versao)))
    saida: dict[str, Any] = {
        "versao": 3,
        "activo": bool(cfg.get("activo", True)),
        "registar": bool(cfg.get("registar", True)),
        "analise": copy.deepcopy(ANALISE_PADRAO),
        "perfis": {},
    }
    analise = cfg.get("analise") or {}
    if isinstance(analise, dict):
        _recusar_desconhecidas(analise, _CHAVES_ANALISE, "analise", erros)
        for chave, valor in analise.items():
            if chave == "limiares" and isinstance(valor, dict):
                # Fechado a `media`/`alta`, que e o que `nivel_de_score` le.
                # Um `leve` aqui era aceite e nunca decidia nada.
                _recusar_desconhecidas(valor, LIMIARES_CHAVES,
                                       "analise.limiares", erros)
                saida["analise"]["limiares"].update(
                    {k: v for k, v in valor.items() if k in LIMIARES_CHAVES})
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
                    erros.append(_t("'analise.pesos' tem de ser um objecto"))
                else:
                    limpos: dict[str, int] = {}
                    for sinal, peso in valor.items():
                        nome_sinal = str(sinal).strip()
                        if not _RE_SINAL.match(nome_sinal):
                            erros.append(_t("'analise.pesos': sinal invalido '{p0}'", p0=nome_sinal))
                            continue
                        if isinstance(peso, bool) or not isinstance(peso, int):
                            erros.append(_t("'analise.pesos.{p0}' tem de ser inteiro", p0=nome_sinal))
                            continue
                        if not -20 <= peso <= 20:
                            erros.append(_t("'analise.pesos.{p0}' fora de [-20, 20]", p0=nome_sinal))
                            continue
                        limpos[nome_sinal] = peso
                    saida["analise"]["pesos"] = limpos
            elif chave == "fixar_nivel_por_turno":
                if isinstance(valor, bool):
                    saida["analise"][chave] = valor
                else:
                    erros.append(_t("'analise.fixar_nivel_por_turno' tem de ser booleano"))
            elif chave == "aprendiz":
                if not isinstance(valor, dict):
                    erros.append(_t("'analise.aprendiz' tem de ser um objecto"))
                else:
                    saida["analise"]["aprendiz"] = valor
            elif chave == "superficies":
                saida["analise"]["superficies"] = normalizar_superficies(
                    valor, "'analise.superficies'", erros)
            elif chave == "tarefa_persistente":
                # Funde sobre o padrao: um global que declare so o prazo mantem
                # o `activo` do padrao, que e o que o dono espera.
                saida["analise"]["tarefa_persistente"].update(
                    normalizar_tarefa_persistente(
                        valor, "'analise.tarefa_persistente'", erros))
            elif chave == "circuit_breaker":
                # Circuit breaker: valida e normaliza (so o que foi declarado).
                limpo, erros_cb = normalizar_circuit_breaker(valor, "'analise.circuit_breaker'")
                saida["analise"]["circuit_breaker"].update(limpo)
                erros.extend(erros_cb)
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

    # Retencao: bloco opcional, mesma regra da estrategia — so se grava o que
    # foi declarado, e a ausencia vale o padrao.
    retencao, erros_ret = normalizar_retencao(cfg.get("retencao"), "retencao")
    erros.extend(erros_ret)
    if retencao:
        saida["retencao"] = dict(retencao)

    # Ensaio em sombra: bloco opcional, mesma regra da estrategia e da retencao.
    # Sem esta fiacao o interruptor era escrito e deitado fora na leitura, e o
    # ensaio aparecia "ligado" no ficheiro e desligado no turno.
    sombra, erros_sombra = normalizar_sombra(cfg.get("sombra"))
    erros.extend(erros_sombra)
    if sombra:
        saida["sombra"] = dict(sombra)

    # Idioma do painel: chave opcional, mesma regra — so se grava o que foi
    # declarado, e a ausencia vale o ingles. Sem esta fiacao o seletor do painel
    # escrevia a chave e a leitura deitava-a fora, e o painel voltava a ingles
    # no render seguinte como se nada tivesse sido escolhido.
    idioma, erros_idioma = normalizar_idioma(cfg.get("idioma"))
    erros.extend(erros_idioma)
    if idioma:
        saida["idioma"] = idioma

    perfis = cfg.get("perfis")
    if not isinstance(perfis, dict):
        erros.append(_t("'perfis' ausente ou nao e um objecto"))
        return saida, erros
    for nome, pcfg in perfis.items():
        nome = str(nome).strip()
        if not nome:
            erros.append(_t("perfil com nome vazio"))
            continue
        if not isinstance(pcfg, dict):
            erros.append(_t("perfis.{p0} nao e um objecto", p0=nome))
            continue
        _recusar_desconhecidas(pcfg, _CHAVES_PERFIL, f"perfis.{nome}", erros)
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
            erros.append(_t("perfis.{p0}.latencia_max_ms deve ser inteiro positivo (recebido {p1})", p0=nome, p1=repr(pcfg.get('latencia_max_ms'))))
        # O perfil grava SO o que declara: a heranca faz-se na leitura
        # (`perfil_config`). Gravar uma copia do global fixava o perfil ao valor
        # do dia em que foi gravado — um global que mudasse depois deixava de
        # chegar-lhe. Isto vale para a `analise` inteira, nao so para o
        # classificador: qualquer chave herdada e a mesma armadilha.
        panalise: dict[str, Any] = {}
        if isinstance(pcfg.get("analise"), dict):
            _recusar_desconhecidas(pcfg["analise"], _CHAVES_ANALISE,
                                   f"perfis.{nome}.analise", erros)
            for chave, valor in pcfg["analise"].items():
                if chave == "limiares" and isinstance(valor, dict):
                    _recusar_desconhecidas(valor, LIMIARES_CHAVES,
                                           f"perfis.{nome}.analise.limiares",
                                           erros)
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
                elif chave == "tarefa_persistente":
                    # Chave a chave, e so o que difere do global: repetir o
                    # valor global era fixar o perfil ao dia (a mesma armadilha
                    # dos `limiares`).
                    limpo_tp = normalizar_tarefa_persistente(
                        valor, f"perfis.{nome}.analise.tarefa_persistente", erros)
                    globais_tp = saida["analise"].get("tarefa_persistente") or {}
                    declarados_tp = {k: v for k, v in limpo_tp.items()
                                     if globais_tp.get(k) != v}
                    if declarados_tp:
                        panalise["tarefa_persistente"] = declarados_tp
                elif chave == "circuit_breaker":
                    # Circuit breaker do perfil: so o delta (chaves que diferem do global).
                    limpo_cb = normalizar_circuit_breaker(
                        valor, f"perfis.{nome}.analise.circuit_breaker", erros)[0]
                    globais_cb = saida["analise"].get("circuit_breaker") or {}
                    declarados_cb = {k: v for k, v in limpo_cb.items()
                                     if globais_cb.get(k) != v}
                    if declarados_cb:
                        panalise["circuit_breaker"] = declarados_cb
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
            erros.append(_t("perfis.{p0}.catalogo ausente ou nao e lista", p0=nome))
        else:
            for i, entrada in enumerate(pcfg["catalogo"]):
                limpa = _normalizar_entrada(entrada, i, erros)
                if limpa is None:
                    continue
                if limpa["id"] in vistos:
                    erros.append(_t("perfis.{p0}: id duplicado '{p1}'", p0=nome, p1=limpa['id']))
                    continue
                vistos.add(limpa["id"])
                catalogo.append(limpa)
        tarefas = {}
        regras = pcfg.get("tarefas", {})
        if not isinstance(regras, dict):
            erros.append(_t("perfis.{p0}.tarefas deve ser objecto", p0=nome))
            regras = {}
        for tid, regra in regras.items():
            if not isinstance(tid, str) or not tid.strip() or not isinstance(regra, dict):
                erros.append(_t("perfis.{p0}.tarefas: regra invalida", p0=nome))
                continue
            _recusar_desconhecidas(regra, _CHAVES_TAREFA,
                                   f"perfis.{nome}.tarefas.{tid}", erros)
            termos = regra.get("termos", [])
            prioridade = regra.get("prioridade", 0)
            if (not isinstance(termos, list) or not termos or
                    any(not isinstance(t, str) or not t.strip() for t in termos) or
                    type(prioridade) is not int):
                erros.append(_t("perfis.{p0}.tarefas.{p1}: termos/prioridade invalidos", p0=nome, p1=tid))
                continue
            tarefas[tid] = {"termos": list(dict.fromkeys(t.strip() for t in termos)),
                            "prioridade": prioridade}
        for entrada in catalogo:
            for tid in entrada.get("tarefas", []):
                if tid not in tarefas:
                    erros.append(_t("perfis.{p0}: tarefa desconhecida '{p1}' em {p2}", p0=nome, p1=tid, p2=entrada['id']))
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
                    erros.append(_t("perfis.{p0}.analise.superficies tem de ser um objecto (superficie -> regra)", p0=nome))
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
                return copy.deepcopy(SEED), [_t("config nao e um objecto")]
        else:
            return copy.deepcopy(SEED), [_t("config nao e um objecto")]
    return validar(bruto)


def _registar_quem(caminho: Path, antes: bytes, depois: bytes) -> None:
    """Auditoria de autoria: atribui cada escrita a um processo.

    Nasceu de um incidente sem rasto (nove escritas em dois minutos, cada uma a
    reduzir uma rota ao seu nivel classificado): o `escritas.log` do painel so
    cobre o painel, o CLI e os scripts gravavam sem deixar autor. Aqui fica o
    pid, a linha de comando e os tres frames de cima da pilha.

    Nunca le valores do config — so tamanhos e hashes. Falha em silencio: um
    registo de auditoria nao pode impedir a escrita que foi pedida.
    """
    try:
        # `sys.orig_argv` (3.10+) tem a linha de comando completa sem ler /proc
        # de outro processo — o validador do catalogo marca leituras a /proc.
        cmdline = " ".join(str(a) for a in (getattr(sys, "orig_argv", None) or sys.argv))
    except Exception:  # noqa: BLE001
        cmdline = ""
    try:
        pilha = [
            "%s:%s:%s" % (Path(str(f.filename)).name, f.lineno, f.name)
            for f in traceback.extract_stack()[-6:-1]
        ]
        linha = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "cmdline": cmdline[:300],
            "pilha": pilha,
            "antes_sha": hashlib.sha256(antes).hexdigest()[:16] if antes else "",
            "depois_sha": hashlib.sha256(depois).hexdigest()[:16],
            "antes_b": len(antes),
            "depois_b": len(depois),
        }
        with (caminho.parent / "escritas-quem.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
        _podar_registo(caminho.parent / "escritas-quem.log")
    except Exception:  # noqa: BLE001
        pass


#: Tecto do log de auditoria. Uma linha tem ~200 B; 256 KiB dao ~1300 escritas,
#: muito acima do pior incidente medido (nove escritas em dois minutos).
_LIMITE_LOG_AUDITORIA = 256 * 1024
_LINHAS_LOG_AUDITORIA = 400


def _podar_registo(registo: Path) -> None:
    """Corta o log de auditoria a 400 linhas quando passa de 256 KiB.

    A cadeia de hashes so precisa do ultimo elo para dizer se a config foi
    escrita pelo caminho unico; as linhas antigas sao contexto forense. Sem
    tecto, um escritor em laco fazia crescer isto sem fim, no mesmo diretorio
    onde ja houve 119 backups a encher o disco.
    """
    try:
        if registo.stat().st_size <= _LIMITE_LOG_AUDITORIA:
            return
        linhas = registo.read_text(encoding="utf-8").splitlines()
        if len(linhas) <= _LINHAS_LOG_AUDITORIA:
            return
        tmp = registo.with_suffix(registo.suffix + ".tmp")
        tmp.write_text("\n".join(linhas[-_LINHAS_LOG_AUDITORIA:]) + "\n",
                       encoding="utf-8")
        os.replace(tmp, registo)
    except OSError:
        pass


def _reconciliar(saida: dict[str, Any], registo: Path) -> None:
    """Separa quebra **explicada** de quebra **por explicar**.

    Uma escrita fora do caminho unico deixa o registo com um salto entre o
    `depois_sha` de uma linha e o `antes_sha` da seguinte. Ha duas causas
    possiveis e o registo sozinho nao as distingue: corrupcao (alguem mexeu no
    ficheiro ou no log) ou uma escrita manual conhecida e reversivel.

    O ficheiro ``escritas-quem.reconciliacao.json`` ao lado do registo declara
    as quebras conhecidas. Cada declaracao tem de trazer a **testemunha** — os
    tres shas medidos naquela linha. Sem isso, uma entrada generica desculparia
    tambem as quebras futuras, e o alarme morria por excesso de confianca.

    O que fica: `quebras` continua a listar **todas** (a evidencia nao se apaga),
    `quebras_reconciliadas` as que teem declaracao com testemunha igual, e
    `quebras_nao_explicadas` as restantes — so estas fazem a auditoria falhar.
    """
    saida["quebras_nao_explicadas"] = list(saida["quebras"])
    ficheiro = registo.parent / "escritas-quem.reconciliacao.json"
    try:
        dados = json.loads(ficheiro.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return
    declaradas = dados.get("quebras") if isinstance(dados, dict) else None
    if not isinstance(declaradas, list):
        return
    for item in declaradas:
        if not isinstance(item, dict):
            continue
        bruto = item.get("linha")
        if bruto is None or isinstance(bruto, bool):
            continue
        try:
            numero = int(bruto)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        testemunha = saida["_testemunhas"].get(numero)
        if not testemunha:
            continue
        if (str(item.get("antes_sha") or "") != testemunha["antes_sha"]
                or str(item.get("depois_sha") or "") != testemunha["depois_sha"]
                or str(item.get("ultimo_depois_sha") or "") != testemunha["ultimo_depois_sha"]):
            # Declaracao que nao casa com o medido nao desculpa nada.
            continue
        if numero in saida["quebras_nao_explicadas"]:
            saida["quebras_nao_explicadas"].remove(numero)
            saida["quebras_reconciliadas"].append(numero)


def verificar_cadeia(caminho: Path | None = None) -> dict[str, Any]:
    """Diz se a config no disco foi escrita pelo caminho unico.

    O `escritas-quem.log` atribui cada escrita — mas so as que passam por
    `_escrever`. Uma edicao a mao do JSON, um `sed`, um script que escreva o
    ficheiro diretamente nao deixam linha nenhuma, e ate agora isso lia-se como
    "ninguem escreveu": o log provava autoria quando havia escrita, nunca
    provava ausencia de escrita por fora.

    A cadeia fecha esse buraco com o que ja estava gravado: cada linha leva
    `depois_sha` e a seguinte leva o mesmo valor em `antes_sha`. Se o sha dos
    bytes vivos nao for o `depois_sha` da ultima linha, o ficheiro foi mexido
    fora do caminho unico — ou o log foi apagado. E a diferenca entre "o
    utilizador mudou pelo painel" (linha 1 a 11 do log real: `api.py:modelo_post`,
    pid do gateway) e "alguem escreveu o JSON por fora" (nenhuma linha).
    """
    caminho = Path(caminho) if caminho else ficheiro_config()
    registo = caminho.parent / "escritas-quem.log"
    saida: dict[str, Any] = {
        "config": str(caminho),
        "registo": str(registo),
        "existe": registo.exists(),
        "linhas": 0,
        "ilegiveis": 0,
        "quebras": [],
        "quebras_reconciliadas": [],
        "quebras_nao_explicadas": [],
        "_testemunhas": {},
        "ultimo_depois_sha": "",
        "vivo_sha": "",
        "cadeia_continua": True,
        "fora_da_cadeia": False,
    }
    try:
        saida["vivo_sha"] = hashlib.sha256(caminho.read_bytes()).hexdigest()[:16]
    except OSError:
        saida["vivo_sha"] = ""
    if not registo.exists():
        # Sem registo nao ha como atribuir: se ha config, ela veio de fora.
        saida["fora_da_cadeia"] = bool(saida["vivo_sha"])
        return saida
    anterior = ""
    try:
        linhas = registo.read_text(encoding="utf-8").splitlines()
    except OSError:
        return saida
    for numero, linha in enumerate(linhas, 1):
        try:
            dado = json.loads(linha)
        except (json.JSONDecodeError, ValueError):
            saida["ilegiveis"] += 1
            continue
        if not isinstance(dado, dict):
            saida["ilegiveis"] += 1
            continue
        saida["linhas"] += 1
        antes = str(dado.get("antes_sha") or "")
        depois = str(dado.get("depois_sha") or "")
        # A primeira escrita legitima de todas tem `antes_sha` vazio.
        if anterior and antes and antes != anterior:
            saida["quebras"].append(numero)
            # Guarda a testemunha da quebra: e o que impede uma reconciliacao
            # generica de desculpar uma quebra que ainda nao aconteceu.
            saida["_testemunhas"][numero] = {
                "antes_sha": antes,
                "depois_sha": depois,
                "ultimo_depois_sha": anterior,
            }
        anterior = depois
    saida["ultimo_depois_sha"] = anterior
    _reconciliar(saida, registo)
    saida["cadeia_continua"] = not saida["quebras_nao_explicadas"]
    saida["fora_da_cadeia"] = bool(anterior) and saida["vivo_sha"] != anterior
    saida.pop("_testemunhas", None)
    return saida


def rev(cfg: dict[str, Any]) -> str:
    """Impressao digital canonica da config, partilhada por todos os escritores.

    Chaves ordenadas e separadores fixos: o mesmo conteudo em qualquer ordem de
    insercao da a mesma impressao. E a unica definicao — o painel, o CLI e os
    scripts comparam pelo mesmo numero, senao um `rev` de um nao vale no outro.
    """
    canonico = json.dumps(cfg, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16]


class Conflito(RuntimeError):
    """A config mudou no disco entre a leitura do chamador e a sua escrita.

    Levar `Conflito` em vez de gravar e o que impede a perda silenciosa: o
    chamador leu um documento, outro escritor mudou uma chave, e gravar o
    documento inteiro reporia o passado. Quem apanha isto rele e reaplica o
    delta — nunca descarta o que o outro escreveu.
    """

    def __init__(self, rev_actual: str, mensagem: str = "") -> None:
        super().__init__(mensagem or f"a configuracao mudou no disco (rev {rev_actual})")
        self.rev_actual = rev_actual


@contextlib.contextmanager
def _trava(caminho: Path):
    """Trava exclusiva por ficheiro de config (bloqueia escritores concorrentes).

    O `flock` e do kernel e vive no ficheiro `.lock` ao lado do config, nao no
    config: o `os.replace` troca o inode do config e uma trava no inode antigo
    deixaria de proteger. E reentrante no mesmo processo por ficheiro aberto,
    portanto `alterar` pode chamar `_escrever` sem se auto-bloquear.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    ficheiro = caminho.with_suffix(".json.lock")
    with ficheiro.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _podar_backups(caminho: Path, manter: int = 10,
                   semanas: int = 8) -> list[Path]:
    """Poda os backups de config: `manter` recentes mais um por semana.

    Politica decidida a 2026-11-04 e ate agora nunca implementada: cada gravacao
    copiava o ficheiro e nada apagava, e a directoria chegou a 21 copias. O que
    se mantem e o que serve para repor: as ultimas `manter` gravacoes (o
    historico curto que se usa a seguir a um erro) e a copia mais recente de
    cada uma das ultimas `semanas` semanas ISO (a rede para "voltar a semana
    passada").

    O tecto de semanas e deliberado: "uma por semana" sem limite cresce 52
    ficheiros por ano e volta ao mesmo problema por outra porta. Com 10 + 8 o
    maximo e 18.

    Duas guardas: so se apagam ficheiros com o carimbo exato que `_escrever`
    produz (`<nome>.bak-AAAAMMDDTHHMMSS`), para nao tocar em copias nomeadas a
    mao, e o ficheiro recem-criado nunca sai porque esta entre os mais recentes.
    """
    padrao = re.compile(rf"^{re.escape(caminho.name)}\.bak-\d{{8}}T\d{{6}}$")
    copias = [p for p in caminho.parent.glob(caminho.name + ".bak-*")
              if p.is_file() and padrao.match(p.name)]
    copias.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    if len(copias) <= manter:
        return []
    manter_set = {p.name for p in copias[:manter]}
    semanas_vistas: list[str] = [
        time.strftime("%G-%V", time.localtime(p.stat().st_mtime))
        for p in copias[:manter]
    ]
    for copia in copias[manter:]:
        chave = time.strftime("%G-%V", time.localtime(copia.stat().st_mtime))
        if chave in semanas_vistas:
            continue
        if len(semanas_vistas) >= manter + semanas:
            continue
        semanas_vistas.append(chave)
        manter_set.add(copia.name)
    apagadas: list[Path] = []
    for copia in copias:
        if copia.name in manter_set:
            continue
        try:
            copia.unlink()
            apagadas.append(copia)
        except OSError:
            continue
    return apagadas


def _escrever(cfg: dict[str, Any], caminho: Path) -> Path:
    """Valida, faz backup, escreve em `.tmp` e troca com `os.replace`."""
    limpo, erros = validar(cfg)
    if erros:
        raise ValueError("; ".join(erros))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    antes = b""
    if caminho.exists():
        carimbo = time.strftime("%Y%m%dT%H%M%S")
        shutil.copy2(caminho, caminho.with_suffix(f".json.bak-{carimbo}"))
        # Poda logo a seguir a copiar: a politica "10 mais um semanal" so vale
        # se for aplicada na gravacao. Deixar a limpeza para um job externo
        # significa que o dia em que o job nao correr volta o lixo todo.
        with contextlib.suppress(Exception):
            _podar_backups(caminho)
        try:
            antes = caminho.read_bytes()
        except OSError:
            antes = b""
    texto = json.dumps(limpo, ensure_ascii=False, indent=2) + "\n"
    tmp = caminho.with_suffix(".json.tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, caminho)
    _registar_quem(caminho, antes, texto.encode("utf-8"))
    return caminho


def gravar(cfg: dict[str, Any], caminho: Path | None = None,
           rev_esperado: str | None = None) -> Path:
    """Grava o documento inteiro, com trava e verificacao de versao opcional.

    Usar so quando a intencao E o documento inteiro (semente, editor de JSON,
    importacao). Para mudar uma chave, `alterar` — que relê o disco dentro da
    trava e aplica o delta ao estado actual, em vez de repor o que o chamador
    tinha em maos.
    """
    caminho = Path(caminho) if caminho else ficheiro_config()
    with _trava(caminho):
        if rev_esperado:
            actual, _erros = carregar(caminho)
            if rev(actual) != rev_esperado:
                raise Conflito(rev(actual))
        return _escrever(cfg, caminho)


def alterar(mutador, caminho: Path | None = None,
            rev_esperado: str | None = None) -> tuple[dict[str, Any], str]:
    """Muda chaves sobre o estado fresco do disco, dentro da trava.

    Este e o caminho sancionado de escrita por chave: o `mutador` recebe a config
    que esta no disco agora (nao a que o chamador leu ha um minuto), muda so o
    que lhe compete e devolve-a. Uma escrita concorrente noutra chave sobrevive
    porque nunca e substituida — so a chave que este mutador toca e reescrita.

    `rev_esperado` acrescenta a deteccao de conflito de intencao: se o documento
    mudou desde que o chamador o leu, levanta `Conflito` em vez de aplicar um
    delta calculado sobre um estado que ja nao existe.

    Devolve `(config_nova, rev_nova)`.
    """
    caminho = Path(caminho) if caminho else ficheiro_config()
    with _trava(caminho):
        cfg, erros = carregar(caminho)
        if erros:
            raise ValueError("; ".join(erros))
        actual = rev(cfg)
        if rev_esperado and rev_esperado != actual:
            raise Conflito(actual)
        resultado = mutador(cfg)
        novo = resultado if isinstance(resultado, dict) else cfg
        _escrever(novo, caminho)
        return novo, rev(novo)


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
        return "", _t(
            "esforco invalido: '{p0}' (use {p1}; low/medium/high tambem sao aceites)",
            p0=bruto,
            p1=", ".join(ESFORCOS),
        )
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
                elif chave == "tarefa_persistente" and isinstance(valor, dict):
                    # Chave a chave, como a estrategia: um perfil que declare
                    # so o prazo herda o `activo` do global.
                    base = dict(analise.get("tarefa_persistente") or {})
                    base.update(valor)
                    analise["tarefa_persistente"] = base
                elif chave == "circuit_breaker" and isinstance(valor, dict):
                    # Circuit breaker: heranca chave a chave (mesma regra da tarefa_persistente).
                    base = dict(analise.get("circuit_breaker") or {})
                    base.update(valor)
                    analise["circuit_breaker"] = base
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


def tarefa_persistente_efectiva(cfg: dict[str, Any], perfil: str) -> dict[str, Any]:
    """O bloco da tarefa persistente em vigor neste perfil, com a origem.

    Tres camadas pela mesma regra da estrategia: o perfil sobrepoe-se ao global
    **chave a chave**, e o que ninguem declarar fica no valor por omissao. A
    origem e o que o painel mostra ao lado do bloco — sem ela, um perfil que
    herda parece ter escolhido, e o dono muda o global sem saber o que mudou.
    """
    pcfg = perfil_config(cfg, perfil) or {}
    bloco = (pcfg.get("analise") or {}).get("tarefa_persistente")
    saida: dict[str, Any] = dict(TAREFA_PERSISTENTE_PADRAO)
    if isinstance(bloco, dict):
        saida.update({k: bloco[k] for k in TAREFA_PERSISTENTE_CHAVES if k in bloco})
    nome_cfg = str(pcfg.get("origem_config") or perfil)
    cru = (((cfg.get("perfis") or {}).get(nome_cfg) or {}).get("analise") or {})
    proprio = cru.get("tarefa_persistente") if isinstance(cru, dict) else None
    global_tp = (cfg.get("analise") or {}).get("tarefa_persistente")
    # O `validar` materializa o bloco global com os valores por omissao, logo
    # "existe no ficheiro" nao quer dizer "foi declarado". Se o resultado e
    # igual ao padrao, a origem honesta e `padrao` — dizer `global` faria o
    # painel anunciar uma escolha que ninguem fez.
    global_efectivo = dict(TAREFA_PERSISTENTE_PADRAO)
    if isinstance(global_tp, dict):
        global_efectivo.update({k: global_tp[k] for k in TAREFA_PERSISTENTE_CHAVES if k in global_tp})
    if isinstance(proprio, dict) and proprio:
        origem = "proprio"
    elif global_efectivo != dict(TAREFA_PERSISTENTE_PADRAO):
        origem = "global"
    else:
        origem = "padrao"
    saida["origem"] = origem
    return saida


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


def _hash_candidato(semente: str, entrada_id: str) -> str:
    """Hash estavel de (semente, id). Nao depende do numero de candidatos."""
    return hashlib.md5(f"{semente}\x1f{entrada_id}".encode("utf-8")).hexdigest()


def indice_consistente(perfil: str, sessao: str, nivel: str, ids: list[str]) -> int:
    """Hash consistente: o candidato com o menor md5(semente|id).

    Ao contrario de `semente % n`, acrescentar ou remover um modelo nao muda o
    divisor de ninguem: so se deslocam as sessoes que caem perto da entrada nova.
    """
    if not ids:
        return 0
    semente = f"{perfil}|{sessao}|{nivel}"
    melhor, melhor_hash = 0, None
    for posicao, entrada_id in enumerate(ids):
        h = _hash_candidato(semente, str(entrada_id))
        if melhor_hash is None or h < melhor_hash:
            melhor_hash, melhor = h, posicao
    return melhor


def indice_rodizio(chave: str, n: int, *, por: str, perfil: str, sessao: str,
                   nivel: str, contadores: dict[str, int],
                   ids: list[str] | None = None) -> int:
    """Indice estavel: por sessao usa hash, por turno incrementa. Nunca negativo.

    Com `ids` preenchido e `por="sessao"`, usa hash consistente em vez de modulo;
    sem `ids` mantem a formula antiga, para nao partir quem so conhece `n`.
    """
    if n <= 0:
        return 0
    if por == "sessao":
        if ids:
            return indice_consistente(perfil, sessao, nivel, ids) % n
        semente = int(hashlib.md5(f"{perfil}|{sessao}|{nivel}".encode("utf-8")).hexdigest()[:8], 16)
        return semente % n
    atual = contadores.get(chave, 0)
    contadores[chave] = atual + 1
    return atual % n


def assinatura(cfg: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
