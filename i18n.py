"""i18n do vipernet-model-manager — ingles (padrao) e portugues de Portugal.

O codigo foi escrito em portugues, portanto a **chave** do dicionario e o texto
portugues (com marcadores `{nome}`) e o **valor** e o ingles. Consequencias, que
sao o desenho e nao um efeito secundario:

* em `pt` devolve-se a propria chave ja formatada — o comportamento historico
  mantem-se byte a byte, e nenhuma traducao mal feita pode regredir o portugues;
* em `en` (padrao) procura-se o valor; **em falta devolve-se a chave** e a falha
  fica registada em `MISSES`, para um teste a poder exigir cobertura total em vez
  de deixar portugues escapar para uma sessao inglesa;
* nao se inventa texto: sem traducao, o utilizador ve a frase original.

A lingua vem da chave `idioma` da config viva (a mesma que o painel grava), com
cache por `mtime` — uma sessao de CLI curta nao paga uma leitura por mensagem e
uma alteracao no painel entra no pedido seguinte.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

NOME = "vipernet-model-manager"
IDIOMAS = ("en", "pt")
IDIOMA_PADRAO = "en"
NOMES_IDIOMA = {"en": "English", "pt": "Português (Portugal)"}

# Falhas de traducao observadas: `t()` acrescenta aqui a chave em falta. Vazio e
# o estado correto em `en`; o teste de cobertura parte daqui e do scan estatico.
MISSES: set[str] = set()

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"mtime": None, "idioma": IDIOMA_PADRAO, "caminho": None}
# Lingua fixada por testes. `None` = seguir a config (o normal).
_FORCAR: str | None = None


def normalizar(codigo: Any) -> str:
    """Reduz o codigo a `en`/`pt`. Formas regionais (`pt-PT`, `pt_BR`) colapsam."""
    if not isinstance(codigo, str):
        return IDIOMA_PADRAO
    baixo = codigo.strip().lower().replace("_", "-")
    if not baixo:
        return IDIOMA_PADRAO
    base = baixo.split("-", 1)[0]
    return base if base in IDIOMAS else IDIOMA_PADRAO


def _caminho_config() -> Path:
    """Mesma resolucao que `gestor.directorio()`, para nao haver duas verdades."""
    env = (os.environ.get("VMM_DIR") or "").strip()
    if env:
        return Path(env).expanduser() / "config.json"
    try:
        from hermes_constants import get_default_hermes_root  # type: ignore

        base = Path(get_default_hermes_root())
    except Exception:
        base = Path(os.environ.get("HERMES_HOME") or "/opt/data")
    return base / "plugin-data" / NOME / "config.json"


def idioma() -> str:
    """Lingua em vigor, lida da config viva. Nunca levanta e nunca bloqueia."""
    if _FORCAR is not None:
        return _FORCAR
    caminho = _caminho_config()
    try:
        mtime = caminho.stat().st_mtime_ns
    except OSError:
        with _LOCK:
            _CACHE.update({"mtime": None, "idioma": IDIOMA_PADRAO, "caminho": caminho})
        return IDIOMA_PADRAO

    with _LOCK:
        if _CACHE["mtime"] == mtime and _CACHE["caminho"] == caminho:
            return str(_CACHE["idioma"])

    codigo = IDIOMA_PADRAO
    try:
        import json

        with caminho.open(encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict):
            codigo = normalizar(cfg.get("idioma"))
    except Exception:
        codigo = IDIOMA_PADRAO

    with _LOCK:
        _CACHE.update({"mtime": mtime, "idioma": codigo, "caminho": caminho})
    return codigo


def definir_para_testes(codigo: str | None) -> None:
    """Fixa a lingua sem tocar na config (`None` volta a seguir a config)."""
    global _FORCAR
    _FORCAR = None if codigo is None else normalizar(codigo)


def t(chave: str, /, **valores: Any) -> str:
    """Traduz a chave (texto portugues) e formata os marcadores.

    Em `pt` devolve a chave — o texto historico, tal como estava no codigo.
    Em `en` devolve o ingles; sem traducao devolve a chave e registra a falta.
    """
    codigo = idioma()
    if codigo == "pt":
        texto = chave
    else:
        ingles = _EN.get(chave)
        if ingles is None:
            MISSES.add(chave)
            texto = chave
        else:
            texto = ingles
    if not valores:
        return texto
    try:
        return texto.format(**valores)
    except (KeyError, IndexError, ValueError):
        # Um marcador trocado na traducao nao pode derrubar o comando: cai para a
        # chave formatada, que e a frase que o autor escreveu.
        try:
            return chave.format(**valores)
        except Exception:
            return texto


def todas_as_chaves() -> set[str]:
    """Chaves com traducao — usado pelo teste de cobertura."""
    return set(_EN)


# ---------------------------------------------------------------------------
# Dicionario. Chave = texto portugues (com `{marcadores}`); valor = ingles.
# Agrupado por superficie para se poder ver o que falta de relance.
# ---------------------------------------------------------------------------

_EN: dict[str, str] = {
    # -- API do painel: recusas que o utilizador le no ecra ------------------
    "corpo nao e um objecto JSON (recebido bytes, nao e utf-8)":
        "body is not a JSON object (got bytes, not utf-8)",
    "corpo nao e um objecto JSON (recebido {valor!r})":
        "body is not a JSON object (got {valor!r})",
    "corpo nao e um objecto JSON (recebido o tipo {tipo})":
        "body is not a JSON object (got type {tipo})",
    "'activo' tem de ser booleano": "'activo' must be a boolean",
    "'ambito' tem de ser 'global' ou 'perfil'": "'ambito' must be 'global' or 'perfil'",
    "perfil {nome!r} nao existe na configuracao":
        "profile {nome!r} does not exist in the configuration",
    "perfil {nome!r} nao existe na configuracao (tem: {conhecidos})":
        "profile {nome!r} does not exist in the configuration (has: {conhecidos})",
    "perfil {nome!r} nao existe": "profile {nome!r} does not exist",
    "nao gravou: {erro}": "did not save: {erro}",
    "gravacao nao confirmada: pedido {pedido}, lido {lido}":
        "write not confirmed: requested {pedido}, read back {lido}",
    "'idioma' ausente": "'idioma' missing",
    "indique 'modo' e/ou 'por'": "provide 'modo' and/or 'por'",
    "a configuração mudou desde que este painel leu o estado":
        "the configuration changed since this panel read the state",
    "indique 'superficie'": "provide 'superficie'",
    "'alvo' tem de ser entrada:<id>, nivel:<nivel>, provider/modelo, 'nunca' ou vazio (apagar)":
        "'alvo' must be entrada:<id>, nivel:<nivel>, provider/modelo, 'nunca' or empty (clear)",
    "indique 'activo' e/ou 'vida_ttl_h'": "provide 'activo' and/or 'vida_ttl_h'",
    "reabrir o ensaio invalida a amostra ja recolhida; reenvie com 'confirmar_reabertura: true'":
        ("reopening the trial invalidates the sample already collected; "
         "resend with 'confirmar_reabertura: true'"),
    "indique 'braco_a' (pago) e 'braco_b' (gratuito)":
        "provide 'braco_a' (paid) and 'braco_b' (free)",
    "os dois bracos nao podem ser o mesmo modelo":
        "the two arms cannot be the same model",
    "'{campo_provider}' = '{declarado}' nao serve nenhuma entrada do catalogo; disponiveis: {disponiveis}":
        ("'{campo_provider}' = '{declarado}' is served by no catalogue entry; "
         "available: {disponiveis}"),
    "'{campo_provider}': o par {declarado}/{modelo} nao existe no catalogo{sufixo}":
        "'{campo_provider}': the pair {declarado}/{modelo} does not exist in the catalogue{sufixo}",
    "'{campo_modelo}': o modelo '{modelo}' nao esta em nenhum catalogo; "
    "sem entrada o middleware nao o pode servir":
        ("'{campo_modelo}': model '{modelo}' is in no catalogue; without an entry "
         "the middleware cannot serve it"),
    "'{campo_modelo}': '{modelo}' existe em {candidatos}; indique '{campo_provider}' para desempatar":
        ("'{campo_modelo}': '{modelo}' exists in {candidatos}; "
         "state '{campo_provider}' to break the tie"),
    "'{campo}' tem de ser um numero": "'{campo}' must be a number",
    "'p0' tem de estar em [0, 1]": "'p0' must be within [0, 1]",
    "'margem' tem de estar em ]0, 1]": "'margem' must be within ]0, 1]",
    "'design_effect' tem de ser positivo": "'design_effect' must be positive",
    "o pre-registo nao ficou selado; nada gravado":
        "the pre-registration was not sealed; nothing saved",
    "JSON inválido: não é um objecto": "Invalid JSON: not an object",
    "JSON invalido: nao e um objecto": "Invalid JSON: not an object",
    "config sem nenhum perfil: recusado (o plugin ficaria sem rotas)":
        "config with no profiles: refused (the plugin would be left without routes)",
    "falta 'rev': substituir a config inteira exige a versão que foi lida (POST /config devolve-a em 'rev')":
        ("missing 'rev': replacing the whole config requires the version that was read "
         "(POST /config returns it in 'rev')"),
    "campo 'circuit_breaker' deve ser um objecto":
        "'circuit_breaker' field must be an object",
    "chaves invalidas em circuit_breaker: {chaves}":
        "invalid keys in circuit_breaker: {chaves}",
    "modelo {id!r} nao existe no catalogo de {perfil!r}":
        "model {id!r} does not exist in the catalogue of {perfil!r}",
    "falta 'perfil'": "missing 'perfil'",
    "'entrada' tem de ser um objecto": "'entrada' must be an object",
    "o modelo precisa de um id (e por ele que as superficies e as tarefas lhe chamam)":
        "the model needs an id (surfaces and tasks refer to it by that id)",
    "modelo {id!r} ja nao existe no catalogo de {perfil!r}":
        "model {id!r} no longer exists in the catalogue of {perfil!r}",
    "ja existe um modelo com o id {id!r} no perfil {perfil!r}":
        "a model with id {id!r} already exists in profile {perfil!r}",
    "gravou mas a rota não aparece na releitura":
        "saved but the route does not appear on read-back",

    # -- Ajuda do CLI (`vmm --help`) ----------------------------------------
    # Texto auto-contido: nao se mistura com as mensagens do validador, por isso
    # e traduzivel sem deixar uma frase a meio numa lingua e a outra na outra.
    "(implicito) listar em vez de escrever": "(implicit) list instead of writing",
    "actua so neste perfil (por omissao: global)": "act only on this profile (default: global)",
    "apagar o bloco e voltar a herdar o global": "delete the block and inherit the global again",
    "backups do config: contagem e poda da rotina": "config backups: count and routine pruning",
    "baixo|medio|alto, ou '-' para nao declarar": "baixo|medio|alto, or '-' to leave undeclared",
    "caminhos da config e do ledger": "config and ledger paths",
    "classificar um texto e ver a rota": "classify a text and show the route",
    "compactar de facto": "compact for real",
    "contagens por estado e fonte": "counts by state and source",
    "criar/substituir uma regra": "create/replace a rule",
    "cron, api_server, cli, whatsapp...": "cron, api_server, cli, whatsapp...",
    "cron, api_server, cli, whatsapp... ou * para todas":
        "cron, api_server, cli, whatsapp... or * for all",
    "desempate entre custos iguais (o id fecha o empate)":
        "tie-break between equal costs (the id settles it)",
    "dias de detalhe a manter (padrao: config)": "days of detail to keep (default: config)",
    "dizer se um MODELO pode servir uma superficie (on|off|herdar)":
        "say whether a MODEL may serve a surface (on|off|herdar)",
    "ensaio A/B: pre-registo, vida dos bracos, interruptor":
        "A/B trial: pre-registration, arm liveness, switch",
    "entrada:<id> | nivel:<leve|media|alta> | provider/modelo":
        "entrada:<id> | nivel:<leve|media|alta> | provider/modelo",
    "esforco de raciocinio da rota (omisso: nao declarado)":
        "route reasoning effort (omitted: undeclared)",
    "esforco do classificador: baixo|medio|alto ('-' limpa)":
        "classifier effort: baixo|medio|alto ('-' clears)",
    "estado a registar; sem isto o comando so le":
        "state to record; without it the command only reads",
    "estado agregado de --tarefa, todas as fontes":
        "aggregated state of --tarefa, all sources",
    "estado global e por perfil": "global and per-profile state",
    "ficheiro de config alternativo (testes)": "alternative config file (tests)",
    "gerir perfis": "manage profiles",
    "gerir rotas (provider+modelo+complexidade)":
        "manage routes (provider+model+complexity)",
    "id do modelo no catalogo (ou provider/modelo)":
        "model id in the catalogue (or provider/model)",
    "ledger: tamanho e compactacao do detalhe antigo":
        "ledger: size and compaction of old detail",
    "ler o ledger": "read the ledger",
    "leve,media,alta (lista separada por virgulas)":
        "leve,media,alta (comma-separated list)",
    "ligar uma superficie de sessao (cron, api_server...) a uma rota":
        "bind a session surface (cron, api_server...) to a route",
    "modelo que classifica leve/media/alta": "model that classifies leve/media/alta",
    "modelo que decide a complexidade": "model that decides complexity",
    "mudar o esforco de uma rota existente": "change the effort of an existing route",
    "nome da tarefa (obrigatorio para escrever)": "task name (required to write)",
    "on (pode servir) | off (nao serve) | herdar (volta ao perfil)":
        "on (may serve) | off (does not serve) | herdar (back to the profile)",
    "par chave=valor adicional (repetivel)": "extra key=value pair (repeatable)",
    "perfil a que o veredicto pertence": "profile the verdict belongs to",
    "perfis geridos e existentes": "managed and existing profiles",
    "piso de confianca da decisao (0-1; 0 aceita tudo)":
        "decision confidence floor (0-1; 0 accepts everything)",
    "podar de facto": "prune for real",
    "prova concreta, curta (obrigatoria para escrever)":
        "proof, short, concrete (required to write)",
    "provider do modelo classificador": "classifier model provider",
    "quantos datados ficam (padrao: config)":
        "how many dated backups to keep (default: config)",
    "quantos listar (padrao 20)": "how many to list (default 20)",
    "quantos por semana ISO para la dos primeiros":
        "how many per ISO week beyond the first ones",
    "quem escreveu a config e se o disco veio desse caminho":
        "who wrote the config and whether the disk came from that path",
    "remover a regra do perfil": "remove the profile rule",
    "resolver credenciais de cada rota": "resolve the credentials of each route",
    "response_format json_object: auto (pede e degrada), on, off":
        "response_format json_object: auto (request and degrade), on, off",
    "saida em JSON": "JSON output",
    "sem isto, mexe-se na estrategia global":
        "without it, the global strategy is changed",
    "sessao a que o veredicto pertence": "session the verdict belongs to",
    "so se aplica a rodizio": "only applies to rodizio",
    "textos a classificar (cmd=lote)": "texts to classify (cmd=lote)",
    "transporte: chat|decisoes ('-' repoe chat)":
        "transport: chat|decisoes ('-' restores chat)",
    "turn_id a que o veredicto pertence": "turn_id the verdict belongs to",
    "ver as regras de um perfil (ou de todos)": "show one profile's rules (or all)",
    "ver ou mudar a estrategia (global ou por perfil)":
        "show or change the strategy (global or per profile)",
    "veredicto de entrega: registar (com --estado) ou ler (sem ele)":
        "delivery verdict: record (with --estado) or read (without it)",
    "{nome} o plugin": "{nome} the plugin",
    "rota de acesso; por omissao infere do provider (openai-codex=subscricao, restantes=api)":
        ("access route; by default inferred from the provider "
         "(openai-codex=subscricao, restantes=api)"),
    "preco por milhao de tokens; 0 e gratis. Sem custo a rota ordena depois das que o declaram":
        ("price per million tokens; 0 is free. Without a cost the route orders "
         "after those that declare one"),
    "quem afirma: gate, releitura, testes, ha, jira, wiki, git, skill, manual":
        "who asserts it: gate, releitura, testes, ha, jira, wiki, git, skill, manual",
}
