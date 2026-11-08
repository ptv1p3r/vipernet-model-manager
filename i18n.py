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

import contextvars
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

#: Via deterministica, por ambiente, para testes e para uma sessao pontual de
#: CLI. Existe porque `definir_para_testes` so tem efeito no modulo ja carregado,
#: e ha ficheiros de teste que carregam o `gestor` (e portanto o `i18n`) dentro de
#: um metodo: a ordem de import deixava esses testes a medir a lingua errada.
#: Aceita as formas regionais (`pt-PT`, `pt_BR`), como a config.
ENV_IDIOMA = "VMM_IDIOMA"

#: Lingua do **pedido em curso**, posta pela dependencia do painel a partir dos
#: cabecalhos `X-VMM` / `VMM_IDIOMA`. Um `ContextVar` e o que torna isto seguro
#: com pedidos concorrentes em linguas diferentes: o valor vive no contexto da
#: tarefa (e, medido, atravessa o threadpool do anyio), nao no modulo.
_PEDIDO: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vmm_idioma_pedido", default=None
)


def definir_do_pedido(codigo: Any) -> None:
    """Fixa a lingua para o pedido em curso. `None`/invalido limpa (segue a config)."""
    if codigo is None:
        _PEDIDO.set(None)
        return
    bruto = str(codigo).strip().lower().replace("_", "-")
    base = bruto.split("-", 1)[0]
    _PEDIDO.set(base if base in IDIOMAS else None)


def idioma_do_pedido() -> str | None:
    """Lingua pedida pelos cabecalhos, ou `None` se o pedido nao a declarou."""
    return _PEDIDO.get()


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
    """Lingua em vigor: `_FORCAR`, depois o pedido, depois `VMM_IDIOMA`, depois a config.

    O pedido vem antes do ambiente porque e o sinal mais especifico: o ambiente
    e um valor de processo (testes, CLI), o cabecalho e a escolha daquele
    utilizador naquele pedido.
    """
    if _FORCAR is not None:
        return _FORCAR
    pedido = _PEDIDO.get()
    if pedido is not None:
        return pedido
    env = (os.environ.get(ENV_IDIOMA) or "").strip().lower()
    if env:
        base = env.replace("_", "-").split("-")[0]
        if base in IDIOMAS:
            return base
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
    "ligar o plugin": "turn the plugin on",
    "desligar o plugin": "turn the plugin off",
    "mostrar esta ajuda e sair": "show this help message and exit",
    "rota de acesso; por omissao infere do provider (openai-codex=subscricao, restantes=api)":
        ("access route; by default inferred from the provider "
         "(openai-codex=subscricao, restantes=api)"),
    "preco por milhao de tokens; 0 e gratis. Sem custo a rota ordena depois das que o declaram":
        ("price per million tokens; 0 is free. Without a cost the route orders "
         "after those that declare one"),
    "quem afirma: gate, releitura, testes, ha, jira, wiki, git, skill, manual":
        "who asserts it: gate, releitura, testes, ha, jira, wiki, git, skill, manual",
    # -- Validador do gestor (75 mensagens) -------------------------------
    # Chave = texto portugues, valor = ingles. Sao as recusas que sobem a
    # API em `detail=str(exc)` e a saida do CLI; ficaram de fora da primeira
    # passagem i18n e davam metade da linha em cada lingua.
    # As palavras da config (`catalogo`, `acesso`, `superficie`, `entrada`,
    # `nivel`, `tarefa`, `modo`, `por`, `termos`, `prioridade`) NAO se
    # traduzem: sao identificadores que o utilizador escreve no JSON, e
    # traduzi-las mandava-o escrever uma chave que o plugin nao le.
    " (aceita: {p0})": " (accepts: {p0})",
    # Achadas na verificacao de cobertura (fora do `erros.append`: devolvidas na
    # lista, ou no normalizador de esforco, e saem para as mesmas superficies).
    "{p0} nao e um objecto": "{p0} is not an object",
    "config nao e um objecto": "config is not an object",
    "esforco invalido: '{p0}' (use {p1}; low/medium/high tambem sao aceites)": "invalid esforco: '{p0}' (use {p1}; low/medium/high are also accepted)",
    # Recusas da camada HTTP do painel (dashboard/api.py). Sao a mesma superficie
    # da `detail` que ja leva as mensagens do validador: ficarem em portugues dava
    # uma linha inglesa com uma recusa portuguesa ao lado.
    "a configuração mudou desde que esta página a leu": "the configuration changed since this page loaded it",
    "a configuração mudou noutro sítio — recarregue": "the configuration changed elsewhere — reload it",
    "falta o corpo": "missing body",
    "falta 'perfil'": "missing 'perfil'",
    "perfil {p0} nao existe": "perfil {p0} does not exist",
    "'tarefas' tem de ser um objecto (id -> {termos, prioridade})": "'tarefas' must be an object (id -> {termos, prioridade})",
    "'latencia_max_ms' tem de ser inteiro (0 = sem teto)": "'latencia_max_ms' must be an integer (0 = no cap)",
    "'tarefa_persistente' tem de ser um objecto ({activo, expira_turnos, expira_min}) ou \"herdar\"": "'tarefa_persistente' must be an object ({activo, expira_turnos, expira_min}) or \"herdar\"",
    "'analise.aprendiz' tem de ser um objecto": "'analise.aprendiz' must be an object",
    "'analise.fixar_nivel_por_turno' tem de ser booleano": "'analise.fixar_nivel_por_turno' must be a boolean",
    "'analise.pesos' tem de ser um objecto": "'analise.pesos' must be an object",
    "'analise.pesos': sinal invalido '{p0}'": "'analise.pesos': invalid sign '{p0}'",
    "'analise.pesos.{p0}' fora de [-20, 20]": "'analise.pesos.{p0}' out of range [-20, 20]",
    "'analise.pesos.{p0}' tem de ser inteiro": "'analise.pesos.{p0}' must be an integer",
    "'perfis' ausente ou nao e um objecto": "'perfis' missing or not an object",
    "; queria dizer '{p0}'?": "; did you mean '{p0}'?",
    "catalogo[{p0}] ({p1}:{p2}) acesso 'subscricao' exige provider openai-codex": "catalogo[{p0}] ({p1}:{p2}) acesso 'subscricao' requires provider openai-codex",
    "catalogo[{p0}] ({p1}:{p2}) acesso invalido: '{p3}' (use api|subscricao)": "catalogo[{p0}] ({p1}:{p2}) invalid acesso: '{p3}' (use api|subscricao)",
    "catalogo[{p0}] ({p1}:{p2}) provider openai-codex e uma subscricao; use acesso 'subscricao'": "catalogo[{p0}] ({p1}:{p2}) provider openai-codex is a subscription; use acesso 'subscricao'",
    "catalogo[{p0}] ({p1}:{p2}) sem complexidade": "catalogo[{p0}] ({p1}:{p2}) has no complexidade",
    "catalogo[{p0}] ({p1}:{p2}) {p3}": "catalogo[{p0}] ({p1}:{p2}) {p3}",
    "catalogo[{p0}] complexidade invalida: {p1}": "catalogo[{p0}] invalid complexidade: {p1}",
    "catalogo[{p0}] nao e um objecto": "catalogo[{p0}] is not an object",
    "catalogo[{p0}] sem provider/modelo": "catalogo[{p0}] has no provider/modelo",
    "catalogo[{p0}].custo deve ser >= 0 (recebido {p1})": "catalogo[{p0}].custo must be >= 0 (got {p1})",
    "catalogo[{p0}].custo deve ser numero (recebido {p1})": "catalogo[{p0}].custo must be a number (got {p1})",
    "catalogo[{p0}].json_nativo deve ser booleano (recebido {p1})": "catalogo[{p0}].json_nativo must be a boolean (got {p1})",
    "catalogo[{p0}].modalidades deve ser lista ({p1})": "catalogo[{p0}].modalidades must be a list ({p1})",
    "catalogo[{p0}].modalidades invalidas: {p1} (use {p2})": "catalogo[{p0}] invalid modalidades: {p1} (use {p2})",
    "catalogo[{p0}].ordem deve ser >= 0 (recebido {p1})": "catalogo[{p0}].ordem must be >= 0 (got {p1})",
    "catalogo[{p0}].ordem deve ser inteiro (recebido {p1})": "catalogo[{p0}].ordem must be an integer (got {p1})",
    "catalogo[{p0}].tarefas deve ser lista de identificadores": "catalogo[{p0}].tarefas must be a list of identifiers",
    "catalogo[{p0}].{p1} deve ser inteiro positivo (recebido {p2})": "catalogo[{p0}].{p1} must be a positive integer (got {p2})",
    "catalogo[{p0}].{p1} deve ser positivo (recebido {p2})": "catalogo[{p0}].{p1} must be positive (got {p2})",
    "config.versao {p0} nao suportada (este plugin le a versao 3)": "config.versao {p0} not supported (this plugin reads version 3)",
    "idioma invalido: {p0} (aceita: {p1})": "invalid idioma: {p0} (accepts: {p1})",
    "idioma tem de ser texto (aceita: {p0})": "idioma must be text (accepts: {p0})",
    "perfil com nome vazio": "profile with an empty name",
    "perfis.{p0} nao e um objecto": "perfis.{p0} is not an object",
    "perfis.{p0}.analise.superficies tem de ser um objecto (superficie -> regra)": "perfis.{p0}.analise.superficies must be an object (superficie -> rule)",
    "perfis.{p0}.catalogo ausente ou nao e lista": "perfis.{p0}.catalogo missing or not a list",
    "perfis.{p0}.latencia_max_ms deve ser inteiro positivo (recebido {p1})": "perfis.{p0}.latencia_max_ms must be a positive integer (got {p1})",
    "perfis.{p0}.tarefas deve ser objecto": "perfis.{p0}.tarefas must be an object",
    "perfis.{p0}.tarefas.{p1}: termos/prioridade invalidos": "perfis.{p0}.tarefas.{p1}: invalid termos/prioridade",
    "perfis.{p0}.tarefas: regra invalida": "perfis.{p0}.tarefas: invalid rule",
    "perfis.{p0}: id duplicado '{p1}'": "perfis.{p0}: duplicate id '{p1}'",
    "perfis.{p0}: tarefa desconhecida '{p1}' em {p2}": "perfis.{p0}: unknown tarefa '{p1}' in {p2}",
    "{p0} tem de ser objecto com 'modo' e 'por' (ou a string antiga com o modo)": "{p0} must be an object with 'modo' and 'por' (or the old string with the mode)",
    "{p0} tem de ser um objecto": "{p0} must be an object",
    "{p0} tem de ser um objecto (superficie -> regra)": "{p0} must be an object (superficie -> rule)",
    "{p0} tem de ser um objecto com {p1}": "{p0} must be an object with {p1}",
    "{p0}.activo tem de ser booleano": "{p0}.activo must be a boolean",
    "{p0}.ativo tem de ser booleano": "{p0}.ativo must be a boolean",
    "{p0}.janela_minutos fora de 1..1440 (veio {p1})": "{p0}.janela_minutos out of range 1..1440 (got {p1})",
    "{p0}.janela_minutos tem de ser inteiro": "{p0}.janela_minutos must be an integer",
    "{p0}.limite_falhas fora de 1..50 (veio {p1})": "{p0}.limite_falhas out of range 1..50 (got {p1})",
    "{p0}.limite_falhas tem de ser inteiro": "{p0}.limite_falhas must be an integer",
    "{p0}.modo invalido: {p1} (aceita: {p2})": "{p0}.invalid modo: {p1} (accepts: {p2})",
    "{p0}.multiplicador_cooldown fora de 1.0..10.0 (veio {p1})": "{p0}.multiplicador_cooldown out of range 1.0..10.0 (got {p1})",
    "{p0}.multiplicador_cooldown tem de ser numero": "{p0}.multiplicador_cooldown must be a number",
    "{p0}.por invalido: {p1} (aceita: {p2})": "{p0}.invalid por: {p1} (accepts: {p2})",
    "{p0}.superficies tem de ser um objecto (superficie -> true/false)": "{p0}.superficies must be an object (superficie -> true/false)",
    "{p0}.superficies.{p1} tem de ser true ou false (recebido {p2})": "{p0}.superficies.{p1} must be true or false (got {p2})",
    "{p0}.superficies.{p1}: superficie parecida com '{p2}' — corrija o nome ou use uma superficie real": "{p0}.superficies.{p1}: superficie looks like '{p2}' — fix the name or use a real superficie",
    "{p0}.superficies: superficie com nome vazio": "{p0}.superficies: superficie with an empty name",
    "{p0}.vida_ttl_h fora de 1..168 horas": "{p0}.vida_ttl_h out of range 1..168 hours",
    "{p0}.vida_ttl_h nao e um numero": "{p0}.vida_ttl_h is not a number",
    "{p0}.{p1} desconhecida (aceita: modo, por)": "{p0}.{p1} is unknown (accepts: modo, por)",
    "{p0}.{p1} desconhecida{p2}": "{p0}.{p1} is unknown{p2}",
    "{p0}.{p1} fora de [1, {p2}]": "{p0}.{p1} out of range [1, {p2}]",
    "{p0}.{p1} tem de ser >= {p2} (veio {p3})": "{p0}.{p1} must be >= {p2} (got {p3})",
    "{p0}.{p1} tem de ser inteiro": "{p0}.{p1} must be an integer",
    "{p0}.{p1} tem de ser um inteiro (veio {p2})": "{p0}.{p1} must be an integer (got {p2})",
    "{p0}.{p1} tem de ser um objecto": "{p0}.{p1} must be an object",
    "{p0}.{p1}.nivel invalido: {p2}": "{p0}.{p1}.invalid nivel: {p2}",
    "{p0}.{p1}: entrada desconhecida '{p2}' no catalogo do perfil": "{p0}.{p1}: unknown entrada '{p2}' in the profile catalogo",
    "{p0}.{p1}: provider e modelo vao juntos": "{p0}.{p1}: provider and modelo go together",
    "{p0}.{p1}: sem entrada, nivel nem provider/modelo": "{p0}.{p1}: no entrada, no nivel and no provider/modelo",
    "{p0}.{p1}: superficie parecida com '{p2}' — corrija o nome ou use uma superficie real": "{p0}.{p1}: superficie looks like '{p2}' — fix the name or use a real superficie",
    "{p0}.{p1}: {p2}/{p3} nao esta no catalogo do perfil": "{p0}.{p1}: {p2}/{p3} is not in the profile catalogo",
    "{p0}: superficie com nome vazio": "{p0}: superficie with an empty name",
    # -- CLI: saida dos comandos (vmm) -------------------------------------
    # Valores de dominio que o CLI imprime como rotulo (nao sao chaves de config).
    'texto': 'text',
    'nao declarado': 'not declared',
    '\nglobal: {v0} · {v1}/{v2}':
        '\nglobal: {v0} · {v1}/{v2}',
    '\n{v0}/{v1} · {v2} pedido(s) · {v3} ms · formato={v4} · pedido={v5} · tokens {v6}→{v7}':
        '\n{v0}/{v1} · {v2} request(s) · {v3} ms · format={v4} · request={v5} · tokens {v6}→{v7}',
    '\n{v0}/{v1} · {v2} pedido(s) · {v3} ms · formato={v4} · pedido={v5} · tokens {v6}→{v7} · {v8}':
        '\n{v0}/{v1} · {v2} request(s) · {v3} ms · format={v4} · request={v5} · tokens {v6}→{v7} · {v8}',
    '  aviso: nao foi possivel montar o contexto do perfil: {v0}':
        '  warning: could not build the profile context: {v0}',
    '  braco {v0}: {v1} (HTTP {v2}) {v3}':
        '  arm {v0}: {v1} (HTTP {v2}) {v3}',
    '  dobradas {v0} linhas de {v1} dias em {v2} resumos':
        '  folded {v0} rows from {v1} days into {v2} summaries',
    '  nada a compactar (corte {v0})':
        '  nothing to compact (cutoff {v0})',
    '  politica: detalhe dos ultimos {v0} dias (correr --aplicar para compactar)':
        '  policy: detail for the last {v0} days (run --aplicar to compact)',
    '  politica: ficam {v0} + {v1}/semana (correr --aplicar para podar)':
        '  policy: keeps {v0} + {v1}/week (run --aplicar to prune)',
    '  removidos {v0} de {v1} datados ({v2} MB livres), ficaram {v3}':
        '  removed {v0} of {v1} dated ({v2} MB freed), {v3} left',
    '  semanais preservados: {v0}':
        '  weekly preserved: {v0}',
    '  nada a podar':
        '  nothing to prune',
    '  {v0} MB -> {v1} MB ({v2})':
        '  {v0} MB -> {v1} MB ({v2})',
    '  {v0} MB, {v1} linhas ({v2} de detalhe, {v3} resumos)':
        '  {v0} MB, {v1} rows ({v2} detail, {v3} summaries)',
    '  {v0:14s} (superficie -> permitido)':
        '  {v0:14s} (surface -> allowed)',
    '  {v0} datados, {v1} a mao, {v2} MB':
        '  {v0} dated, {v1} manual, {v2} MB',
    '  {v0} linhas ilegiveis preservadas':
        '  {v0} unreadable rows preserved',
    '  {v0}: FALHA {v1}: {v2}':
        '  {v0}: FAILED {v1}: {v2}',
    '  {v0}: {v1}, {v2} rotas':
        '  {v0}: {v1}, {v2} routes',
    '  {v0}: {v1}; acesso={v2}; base_url={v3}; fonte={v4}; chave={v5} B':
        '  {v0}: {v1}; access={v2}; base_url={v3}; source={v4}; key={v5} B',
    'AUSENTE':
        'MISSING',
    'DESLIGADO':
        'OFF',
    'MORTO':
        'DEAD',
    'NAO':
        'NO',
    'VEREDITO: OK com quebras declaradas — a config viva veio do caminho unico; a historia tem escritas manuais reconciliadas':
        'VERDICT: OK with declared breaks — the live config came from the single path; the history has reconciled manual writes',
    'VEREDITO: cadeia quebrada SEM explicacao nas linhas {v0}':
        'VERDICT: chain broken WITHOUT explanation at rows {v0}',
    'VEREDITO: escrita FORA do caminho unico (o config vivo nao e o que o ultimo escritor deixou)':
        'VERDICT: write OUTSIDE the single path (the live config is not what the last writer left)',
    'VEREDITO: sem registo de escritas e sem config — nada a atribuir':
        'VERDICT: no write log and no config — nothing to attribute',
    '[{v0}] estrategia={v1} rotacao_por={v2} origem={v3} activo={v4}':
        '[{v0}] strategy={v1} rotation_by={v2} origin={v3} active={v4}',
    'analise; sem associacao':
        'analysis; unassociated',
    'aviso: contexto por perfil indisponivel ({v0}); a resolver no contexto do processo':
        'warning: per-profile context unavailable ({v0}); resolving in the process context',
    'aviso: {v0}':
        'warning: {v0}',
    'backups em {v0}':
        'backups at {v0}',
    'braco a = {v0} | braco b = {v1}':
        'arm a = {v0} | arm b = {v1}',
    'config:  {v0}':
        'config:  {v0}',
    'config: {v0}':
        'config: {v0}',
    'desligado':
        'off',
    'ensaio em sombra: {v0}':
        'shadow trial: {v0}',
    'ensaio: {v0} | unidade: {v1}':
        'trial: {v0} | unit: {v1}',
    'erro: {v0}':
        'error: {v0}',
    'estrategia global: {v0} ({v1})':
        'global strategy: {v0} ({v1})',
    'estrategia global: {v0} por {v1} ({v2})':
        'global strategy: {v0} by {v1} ({v2})',
    'existentes que nao estao na config (nao geridos):':
        'existing entries not in the config (unmanaged):',
    'global: {v0} / {v1} ({v2})':
        'global: {v0} / {v1} ({v2})',
    'global: {v0} · registar: {v1}':
        'global: {v0} · record: {v1}',
    'interruptor: {v0}':
        'switch: {v0}',
    'ledger {v0}':
        'ledger {v0}',
    'ledger {v0} (inexistente)':
        'ledger {v0} (missing)',
    'ligado':
        'on',
    'linhas:  {v0}':
        'rows:  {v0}',
    'linhas:  {v0} ({v1} ilegiveis)':
        'rows:  {v0} ({v1} unreadable)',
    'n por braco = {v0} | margem = {v1}':
        'n per arm = {v0} | margin = {v1}',
    'nao':
        'no',
    'nivel: {v0} (origem={v1}, score={v2}, sinais={v3})':
        'level: {v0} (origin={v1}, score={v2}, signals={v3})',
    'o gateway le a config a quente (cache de 5 s); nao e preciso reiniciar.':
        'the gateway reads the config hot (5 s cache); no restart needed.',
    "o perfil que quiser desligar um global ligado declara {'activo': false} no seu bloco.":
        "a profile wanting to turn off a global that is on declares {'activo': false} in its block.",
    "perfil '{v0}' acrescentado (copia de '{v1}')":
        "profile '{v0}' added (copy of '{v1}')",
    "perfil '{v0}' acrescentado (sem rotas)":
        "profile '{v0}' added (no routes)",
    "perfil '{v0}' removido (deixa de ser gerido)":
        "profile '{v0}' removed (no longer managed)",
    'perfil: {v0}':
        'profile: {v0}',
    'perfis geridos: {v0}':
        'managed profiles: {v0}',
    'pre-registo: {v0} | selo ok | sha {v1}':
        'pre-registration: {v0} | seal ok | sha {v1}',
    'reconciliadas: linhas {v0} (quebra declarada com testemunha igual)':
        'reconciled: rows {v0} (break declared with matching witness)',
    'registo: {v0} ({v1})':
        'log: {v0} ({v1})',
    "rota '{v0}' acrescentada a {v1} para {v2} ({v3})":
        "route '{v0}' added to {v1} for {v2} ({v3})",
    "rota '{v0}' de {v1}: esforco={v2}":
        "route '{v0}' of {v1}: effort={v2}",
    "rota '{v0}' removida de {v1}":
        "route '{v0}' removed from {v1}",
    "rota: nenhuma — o catalogo de {v0} nao cobre '{v1}'":
        "route: none — the {v0} catalogue does not cover '{v1}'",
    'sem classificador definido: a complexidade e decidida pela heuristica.':
        'no classifier defined: complexity is decided by the heuristic.',
    'sem veredictos registados':
        'no verdicts recorded',
    'sim':
        'yes',
    'tarefa: {v0} (origem={v1})':
        'task: {v0} (origin={v1})',
    'todos vivos: {v0} | gravado em {v1}':
        'all alive: {v0} | written at {v1}',
    'ultimo:  {v0}':
        'last:  {v0}',
    'um braco sem vida medido mantem o ensaio parado (vmm sombra vida).':
        'an arm without a measured lifetime keeps the trial stopped (vmm sombra vida).',
    'valores possiveis: modo={v0} por={v1}':
        'possible values: mode={v0} by={v1}',
    'veredicto registado: {v0} = {v1} (fonte {v2})':
        'verdict recorded: {v0} = {v1} (source {v2})',
    'vida: nunca medida — o ensaio nao aplica sem ela (vmm sombra vida)':
        'lifetime: never measured — the trial does not apply without it (vmm sombra vida)',
    'vida: {v0} | todos vivos: {v1}':
        'lifetime: {v0} | all alive: {v1}',
    'vivo':
        'alive',
    'vivo:    {v0}':
        'alive:    {v0}',
    'vmm: {v0}':
        'vmm: {v0}',
    '{v0}: classificador {v1}':
        '{v0}: classifier {v1}',
    '{v0}: classificador {v1} ({v2}/{v3}, esforco={v4})':
        '{v0}: classifier {v1} ({v2}/{v3}, effort={v4})',
    "{v0}: regra da superficie '{v1}' removida":
        "{v0}: surface rule '{v1}' removed",
    '{v0}: sem regras de superficie':
        '{v0}: no surface rules',
    "{v0}: superficie '{v1}' -> {v2}":
        "{v0}: surface '{v1}' -> {v2}",
    '{v0}: voltou a herdar -> {v1} / {v2}':
        '{v0}: back to inheriting -> {v1} / {v2}',
    "{v0}: {v1} superficie '{v2}' -> {v3}":
        "{v0}: {v1} surface '{v2}' -> {v3}",
    # -- CLI, 2.a passagem: mensagens de _erro, tabelas e palavras de estado --
    'proprio': 'own',
    'herdado': 'inherited',
    '--agregado exige --tarefa':
        '--agregado requires --tarefa',
    '--confianca-min deve estar entre 0 e 1':
        '--confianca-min must be between 0 and 1',
    '--confianca-min deve ser um numero entre 0 e 1':
        '--confianca-min must be a number between 0 and 1',
    '--dias tem de ser >= 0':
        '--dias must be >= 0',
    '--formato deve ser auto, on ou off':
        '--formato must be auto, on or off',
    '--manter tem de ser >= 1':
        '--manter must be >= 1',
    '--semanais tem de ser >= 0':
        '--semanais must be >= 0',
    '--via deve ser chat ou decisoes':
        '--via must be chat or decisoes',
    'a escrita nao ficou no disco (mapa ainda presente)':
        'the write did not land on disk (map still present)',
    'a escrita nao ficou no disco (valor diferente)':
        'the write did not land on disk (different value)',
    'activo':
        'active',
    'comando desconhecido: {v0}':
        'unknown command: {v0}',
    'config com erros: {v0}':
        'config with errors: {v0}',
    'config recusada: {v0}':
        'config refused: {v0}',
    'estrategia':
        'strategy',
    'global':
        'global',
    'herda do perfil':
        'inherits from the profile',
    'ligar exige --provider e --modelo':
        'turning on requires --provider and --modelo',
    'lote exige pelo menos um texto':
        'batch requires at least one text',
    'modelo':
        'model',
    "modelo '{v0}' nao existe em {v1}: {v2}":
        "model '{v0}' does not exist in {v1}: {v2}",
    "modelo '{v0}' vem do catalogo global, nao de {v1}: declare-o no perfil antes de o limitar":
        "model '{v0}' comes from the global catalogo, not from {v1}: declare it in the profile before limiting it",
    'niveis':
        'levels',
    'nivel invalido: {v0}':
        'invalid level: {v0}',
    'origem':
        'origin',
    'perfil':
        'profile',
    "perfil '{v0}' ja existe":
        "profile '{v0}' already exists",
    "perfil '{v0}' nao e gerido por este config":
        "profile '{v0}' is not managed by this config",
    "perfil '{v0}' nao esta na config (vmm perfis)":
        "profile '{v0}' is not in the config (vmm perfis)",
    "perfil '{v0}' nao existe":
        "profile '{v0}' does not exist",
    "perfil '{v0}' nao existe (vmm perfil add {v1})":
        "profile '{v0}' does not exist (vmm perfil add {v1})",
    "perfil '{v0}' sem classificador por modelo ({v1})":
        "profile '{v0}' without a per-model classifier ({v1})",
    "perfil de origem '{v0}' nao existe":
        "source profile '{v0}' does not exist",
    'perfil {v0}':
        'profile {v0}',
    'perfis.{v0}.analise nao e um objecto':
        'perfis.{v0}.analise is not an object',
    'perfis.{v0}.analise.superficies nao e um objecto':
        'perfis.{v0}.analise.superficies is not an object',
    'pre-registo ilegivel: {v0}':
        'unreadable pre-registration: {v0}',
    'provider':
        'provider',
    'credenciais do provider indisponiveis: {v0}':
        'provider credentials unavailable: {v0}',
    "rota '{v0}' nao existe em {v1}":
        "route '{v0}' does not exist in {v1}",
    'rota: {v0}: {v1}':
        'route: {v0}: {v1}',
    'rotacao':
        'rotation',
    'rotas':
        'routes',
    'seams.py nao encontrado junto de cli.py':
        'seams.py not found next to cli.py',
    'selo do pre-registo nao confere — recusado':
        'pre-registration seal does not match — refused',
    'sem choices':
        'no choices',
    'sem ledger em {v0}':
        'no ledger at {v0}',
    'sem pre-registo em aprendiz/sombra':
        'no pre-registration in aprendiz/sombra',
    'superficie vazia':
        'superficie is empty',
    "valor invalido '{v0}' (use on|off|herdar)":
        "invalid value '{v0}' (use on|off|herdar)",
    'veredicto recusado: {v0}':
        'verdict refused: {v0}',
    "{v0}: superficie '{v1}' nao tem regra propria":
        "{v0}: superficie '{v1}' has no rule of its own",
    # Mensagens do backend do painel que chegavam ao ecra sem traducao.
    "anterior à config actual":
        "older than the current config",
    "sem confirmação há mais de {v0} h":
        "unconfirmed for over {v0} h",
    "outro classificador":
        "a different classifier",
    "anterior ao erro actual":
        "older than the current error",
    "config desactualizado: o editor foi aberto noutra versao, e a config já mudou no disco":
        "config out of date: the editor was opened on another version and the config has changed on disk",
    "config mudou durante a gravação":
        "config changed while saving",
    "config sem nenhum perfil: o plugin ficaria sem rotas":
        "config with no profile: the plugin would be left without routes",
    # Sonda de ligação da página de um modelo (3.47.12).
    "faltam 'provider' e 'modelo' para testar a ligação":
        "missing 'provider' and 'model' to test the connection",
    "sem credencial para {provider} neste processo: {motivo}":
        "no credential for {provider} in this process: {motivo}",
    "o provider {provider} não declara endpoint HTTP (é servido por subprocesso)":
        "provider {provider} declares no HTTP endpoint (it is served by a subprocess)",
}
