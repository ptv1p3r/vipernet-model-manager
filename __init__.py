"""vipernet-model-manager — o provider e o modelo do turno seguem a complexidade,
dentro do catalogo que o dono declarou para o perfil que esta a ser servido.

O que faz, por turno:

1. identifica o **perfil** do turno (o gateway multiplexado serve `default`,
   `pessoal` e `trabalho` no mesmo processo — o perfil nao se adivinha pelo texto);
2. se o perfil nao estiver em `perfis`, ou estiver desligado, ou o interruptor
   global estiver em baixo: **nao toca no pedido** e regista o facto;
3. analisa a conversa (`analise.decidir`) e obtem `leve`|`media`|`alta`;
4. escolhe no catalogo **desse perfil** a entrada cuja `complexidade` cobre o
   nivel, segundo `estrategia` (`rodizio`|`prioridade`), saltando entradas cuja
   credencial nao resolve no escopo do perfil;
5. troca o agente vivo (`agent.switch_model`): cliente HTTP, chave, base_url,
   api_mode e contabilidade de custo mudam juntos.

Regras de desenho:

- **A middleware `llm_request` e o unico ponto que ve o pedido e o agente.** O
  frame de `agent.turn_api_request.build_api_request` tem o agente vivo; o
  `session_id` do frame coincide com o do turno (validado nesta instalacao).
- **Nada e inventado.** Fora do catalogo do perfil nao ha rotas; uma entrada sem
  credencial e saltada e fica registada. Sem decisao, o pedido segue intacto.
- **Fail-open com rasto.** Qualquer excepcao devolve `None` e escreve em
  `uso.err`; um `None` silencioso seria indistinguivel de "nao havia nada a fazer".
- **Sem texto de conversa no ledger.** So perfil, nivel, score, nomes de sinais,
  providers, modelos, tempos e o modelo que o provider diz que respondeu.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

try:  # carregado como pacote do Hermes
    from . import gestor
    from . import analise as _analise_mod
    from . import analise_modelo as _analise_modelo_mod
except Exception:  # import directo (selftest / CLI fora do Hermes)
    import importlib.util as _ilu

    _aqui = Path(__file__).resolve().parent

    def _mod(nome: str):
        spec = _ilu.spec_from_file_location(f"vmm_{nome}", _aqui / f"{nome}.py")
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        # Registar em sys.modules: sem isso `importlib.reload` (o hot reload do
        # analisador) falha com "module not in sys.modules" e o plugin fica
        # preso a versao carregada no arranque.
        sys.modules.setdefault(spec.name, mod)  # type: ignore[union-attr]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    gestor = _mod("gestor")
    _analise_mod = _mod("analise")
    _analise_modelo_mod = _mod("analise_modelo")

NIVEIS = _analise_mod.NIVEIS

logger = logging.getLogger(__name__)


def _versao_do_manifesto() -> str:
    """Versao declarada em ``plugin.yaml``, lida uma vez no import.

    Vai em **cada** evento de decisao do ledger como ``motor``: e a unica forma
    de responder "que codigo decidiu este turno?" quando o processo ja tem o
    modulo em memoria e o ficheiro em disco ja mudou. Sem isto, provar que uma
    recarga entrou exigia adivinhar por mudanca de comportamento.
    """
    try:
        for linha in (Path(__file__).resolve().parent / "plugin.yaml").read_text(
                encoding="utf-8").splitlines():
            if linha.startswith("version:"):
                return linha.split(":", 1)[1].strip().strip('"\'')
    except Exception:  # noqa: BLE001 - metadado ausente nunca parte um turno
        logger.debug("plugin.yaml sem version legivel", exc_info=True)
    return ""


_VERSAO_MOTOR = _versao_do_manifesto()

_FICHEIRO_ANALISE = Path(_analise_mod.__file__ or "analise.py").resolve()
_MTIME_ANALISE = [0]
_FICHEIRO_ANALISE_MODELO = Path(_analise_modelo_mod.__file__ or "analise_modelo.py").resolve()
_MTIME_ANALISE_MODELO = [0]


def _recarregar_modulo(mod: Any, ficheiro: Path, cache: list[int], rotulo: str) -> Any:
    """Devolve a versao mais recente do modulo se o ficheiro mudou no disco.

    O gateway importa o plugin uma vez e congela as funcoes em memoria: uma
    melhoria so entrava com restart do gateway, que corta as sessoes vivas.
    Aqui o modulo e reexecutado quando o `mtime` muda, para entrar no turno
    seguinte.

    Nao se usa `importlib.reload`: exige que um finder reencontre o modulo a
    partir do `sys.path`, e estes modulos vem de uma directoria que nao esta
    la — reload falhava com `spec not found` e deixava o modulo preso no
    arranque. Reexecuta-se o spec original.

    Fail-open: se a recarga falhar, devolve-se o modulo em memoria e registra-se.
    """
    try:
        mtime = ficheiro.stat().st_mtime_ns
    except OSError:
        return mod
    if mtime == cache[0]:
        return mod
    try:
        spec = mod.__spec__
        if spec is None or spec.loader is None:
            raise ImportError(f"{rotulo} sem spec carregavel")
        recente = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = recente
        spec.loader.exec_module(recente)
        cache[0] = mtime
        logger.info("%s recarregado (ficheiro mudou)", rotulo)
        return recente
    except Exception:  # noqa: BLE001 - nunca partir um turno por causa disto
        logger.warning("falha a recarregar %s; mantem a versao em memoria",
                       rotulo, exc_info=True)
        return mod


def _recarregar_analise_se_mudou() -> None:
    """`analise.py` — o analisador heuristico."""
    global NIVEIS, _analise_mod
    antes = _analise_mod
    _analise_mod = _recarregar_modulo(antes, _FICHEIRO_ANALISE, _MTIME_ANALISE, "analisador")
    if _analise_mod is not antes:
        NIVEIS = _analise_mod.NIVEIS


def _recarregar_analise_modelo_se_mudou() -> None:
    """`analise_modelo.py` — o classificador por modelo.

    Muda com mais frequencia que o resto (e o que se afina), e sem isto cada
    melhoria exigia reiniciar o gateway.
    """
    global _analise_modelo_mod
    _analise_modelo_mod = _recarregar_modulo(
        _analise_modelo_mod, _FICHEIRO_ANALISE_MODELO, _MTIME_ANALISE_MODELO, "classificador")


NOME = gestor.NOME
_AMBIENTE_DESLIGADO = ("VMM_DESATIVADO", "VIPERNET_MODEL_MANAGER_OFF")

_TTL_CONFIG = 5.0
_TTL_ROTA = 600.0
_TTL_MAU = 120.0
# Limiares usados so para localizar a zona morta em `herdar_se_indeciso` quando
# o perfil nao os declara; os efectivos vem sempre do config.
_LIMIARES_SEGURANCA = {"media": 3, "alta": 6}

_lock = threading.Lock()
_log_lock = threading.Lock()
_cfg: dict[str, Any] = {"mtime": -1.0, "dados": gestor.SEED, "lido": 0.0, "caminho": ""}
_rotas: dict[tuple[str, str, str], dict[str, Any]] = {}
_historico: dict[str, dict[str, Any]] = {}
_contadores: dict[str, int] = {}
_maus: set[tuple[str, str, str]] = set()
_avisados: set[tuple[str, str, str]] = set()
_maus_expira: dict[tuple[str, str, str], float] = {}
_ultimo_perfil: dict[str, str] = {}
_ultima_decisao: dict[str, dict[str, Any]] = {}
# Decisao fixada por turno: nivel, tarefa, origem e rota do primeiro pedido.
# A chave e o `turn_id` do core (sessao:sessao:8hex) ou, sem ele, um hash do
# texto — nunca o texto, para o ledger continuar sem conversa.
_turno: dict[str, dict[str, Any]] = {}
_TURNO_MAX = 500

#: Identidade desta instancia do modulo. Uma recarga de plugins faz
#: `sys.modules.pop` e reimporta, logo `_turno` nasce vazio a meio do que pode
#: ser o mesmo turno. Sem marca, o evento desse pedido fica `pin: novo` e a
#: prova aritmetica (`eventos - turnos == reutilizados`) falha por um em cada
#: recarga — medido a 2026-11-03, passo 37 do turno `…5797c44f`.
_INSTANCIA = uuid.uuid4().hex[:6]
_inst_decidiu = False


def _primeira_decisao_da_instancia() -> bool:
    """Marca e devolve se esta e a primeira decisao desta instancia."""
    global _inst_decidiu
    antes = _inst_decidiu
    _inst_decidiu = True
    return not antes
# Resultado do turno anterior, por sessao: alimenta o nivel do turno seguinte
# (A). Guarda so o nivel efectivo, o finish_reason e os tokens de saida.
_resultado: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- estado


def _agora() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Europe/Lisbon")).isoformat(timespec="seconds")


def _config() -> dict[str, Any]:
    """Config do disco, com cache curta por `mtime` (o dono edita-a a quente)."""
    agora = time.monotonic()
    caminho = gestor.ficheiro_config()
    try:
        mtime = caminho.stat().st_mtime
    except OSError:
        return gestor.carregar(caminho)[0]
    with _lock:
        if (_cfg["mtime"] == mtime and _cfg["caminho"] == str(caminho)
                and (agora - _cfg["lido"]) < _TTL_CONFIG):
            return _cfg["dados"]
    dados, erros = gestor.carregar(caminho)
    for erro in erros:
        _falha(erro)
    with _lock:
        _cfg.update({"mtime": mtime, "dados": dados, "lido": agora,
                     "caminho": str(caminho)})
    return dados


def _append_linha(caminho: Path, linha: str) -> None:
    """Uma linha por evento, num unico `write` com `O_APPEND`.

    A escrita e atomica para linhas pequenas mesmo com varios processos a
    acrescentar em simultaneo (o CLI e o gateway correm em processos
    diferentes). O ficheiro nasce `0600`; nada de segredos, mas por higiene.
    """
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        fd = os.open(str(caminho), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return
    try:
        os.write(fd, linha.encode("utf-8"))
    finally:
        os.close(fd)


def _registar(entrada: dict[str, Any], *, registar: bool = True) -> None:
    """Ledger append-only; nunca leva texto de conversa nem credenciais."""
    if not registar:
        return
    try:
        linha = json.dumps({"ts": _agora(), **entrada}, ensure_ascii=False) + "\n"
        with _log_lock:
            _append_linha(gestor.caminho_ledger(), linha)
    except Exception:
        pass


def _falha(motivo: str) -> None:
    try:
        with _log_lock:
            _append_linha(gestor.caminho_erros(), f"{_agora()} {motivo}\n")
    except Exception:
        logger.warning("vipernet-model-manager: falha ao registar erro: %s", motivo)


def _lembrar_decisao(sessao: str, **campos: Any) -> None:
    """Guarda a decisao mais recente da sessao para a correlacionar na resposta."""
    if not sessao:
        return
    with _lock:
        _ultima_decisao[sessao] = campos
        if len(_ultima_decisao) > 500:
            _ultima_decisao.clear()


def _uma_vez(chave: tuple[str, str, str]) -> bool:
    """True na primeira vez que esta chave aparece nesta sessao (evita spam de ledger)."""
    with _lock:
        if chave in _avisados:
            return False
        _avisados.add(chave)
        if len(_avisados) > 2000:
            _avisados.clear()
        return True


def chave_do_turno(sessao: str, turn_id: Any = "",
                   mensagens: list[Any] | None = None) -> tuple[str, str]:
    """Chave estavel do turno e a sua fonte (`turn_id` ou `hash`).

    O core entrega `turn_id` em `sessao:sessao:8hex` (medido a 2026-11-02 em
    167 turnos, zero sem ele). Sem `turn_id` — outra superficie, outra versao —
    deriva-se do que **nao muda dentro do turno**: numero de mensagens de
    utilizador e hash da ultima. So o hash entra na chave: o texto da conversa
    nunca chega ao ledger.
    """
    sessao = sessao or ""
    tid = str(turn_id or "").strip()
    if tid:
        return f"{sessao}\x1f{tid}", "turn_id"
    n_utilizador = 0
    ultima = ""
    for msg in mensagens or []:
        if not isinstance(msg, dict) or (msg.get("role") or "") != "user":
            continue
        n_utilizador += 1
        conteudo = msg.get("content")
        if isinstance(conteudo, str):
            ultima = conteudo
        elif isinstance(conteudo, list):
            try:
                ultima = json.dumps(conteudo, ensure_ascii=False, sort_keys=True)
            except Exception:  # noqa: BLE001 — conteudo exotico: fica o contador
                ultima = ""
    resumo = hashlib.sha256(f"{sessao}\x1f{n_utilizador}\x1f{ultima}".encode("utf-8"))
    return f"{sessao}\x1f{resumo.hexdigest()[:16]}", "hash"


def _pin_ler(chave: str) -> dict[str, Any] | None:
    with _lock:
        guardado = _turno.get(chave)
        return dict(guardado) if guardado else None


def _pin_gravar(chave: str, valores: dict[str, Any]) -> None:
    with _lock:
        _turno[chave] = dict(valores)
        if len(_turno) > _TURNO_MAX:
            _turno.clear()


# --------------------------------------------------------------------------- rotas


def resolver_rota(perfil: str, provider: str, modelo: str) -> dict[str, Any]:
    """Credenciais/endpoint/wire para (perfil, provider, modelo), com cache.

    A chave do cache inclui o perfil: o mesmo provider resolve para chaves
    diferentes conforme o escopo de segredos do perfil servido.
    """
    chave = (perfil, provider.strip().lower(), modelo)
    agora = time.monotonic()
    with _lock:
        guardado = _rotas.get(chave)
        if guardado and guardado["expira"] > agora:
            if "erro" in guardado:
                raise RuntimeError(guardado["erro"])
            return dict(guardado["rota"])
    from . import seams as _seams

    try:
        rota = _seams.resolver_provider(provider, modelo)
        if not rota["api_key"]:
            raise RuntimeError(f"provider {provider} sem credencial no perfil {perfil}")
    except Exception as exc:
        with _lock:
            _rotas[chave] = {"expira": agora + _TTL_ROTA,
                             "erro": f"{type(exc).__name__}: {exc}"}
        raise
    with _lock:
        _rotas[chave] = {"expira": agora + _TTL_ROTA, "rota": rota}
    return dict(rota)


def _estrategia_do_perfil(pcfg: dict[str, Any]) -> tuple[str, str]:
    """`(modo, por)` da estrategia em vigor neste perfil.

    O bloco chega normalizado por `gestor.perfil_config`; a leitura da forma
    antiga (string em `estrategia` e `por` em `rodizio`) fica aqui para que uma
    config passada directamente a `escolher()` — testes, import — nao caia em
    `rodizio` por omissao e ignore em silencio um `prioridade` declarado.
    """
    est = pcfg.get("estrategia")
    por_antigo = (pcfg.get("rodizio") or {}).get("por") \
        if isinstance(pcfg.get("rodizio"), dict) else None
    if isinstance(est, dict):
        return (str(est.get("modo") or "rodizio"),
                str(est.get("por") or "sessao"))
    if isinstance(est, str) and est.strip():
        return (est.strip().lower(), str(por_antigo or "sessao"))
    return ("rodizio", str(por_antigo or "sessao"))


def _entrada_do_catalogo(pcfg: dict[str, Any], entrada_id: Any) -> dict[str, Any] | None:
    """Entrada do catalogo pelo id; `None` se o dono a removeu ou renomeou."""
    alvo = str(entrada_id or "")
    for entrada in gestor.catalogo_utilizavel(pcfg):
        if str(entrada.get("id") or "") == alvo:
            return entrada
    return None


def escolher(pcfg: dict[str, Any], nivel: str, *, perfil: str, sessao: str) -> dict[str, Any] | None:
    catalogo = gestor.catalogo_utilizavel(pcfg)
    lista = gestor.ordenar_candidatos(gestor.candidatos(catalogo, nivel))
    if not lista:
        return None
    modo, por = _estrategia_do_perfil(pcfg)
    if modo != "rodizio" or len(lista) == 1:
        return lista[0]
    chave = gestor.chave_rodizio(perfil, sessao, nivel)
    with _lock:
        indice = gestor.indice_rodizio(chave, len(lista), por=por, perfil=perfil,
                                       sessao=sessao, nivel=nivel, contadores=_contadores,
                                       ids=[str(e.get("id") or "") for e in lista])
    return lista[indice % len(lista)]


def escolher_com_rota(pcfg: dict[str, Any], nivel: str, *, perfil: str,
                      sessao: str, tarefa: str | None = None,
                      needs: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Primeira entrada utilizavel do nivel, na ordem da estrategia; devolve as falhadas.

    A ordem e construida **uma vez** e percorrida: repetir `escolher()` a cada
    falha prendia o turno para sempre quando a estrategia e `prioridade` — a
    mesma entrada voltava a sair e o ciclo dava a volta sem tentar a seguinte.

    `needs` (janela, modalidades, JSON, latencia) filtra antes de tentar
    credenciais: uma rota que declarou nao caber nao se tenta, e a razao fica
    no registo como `inviavel` em vez de `sem_rota`.
    """
    catalogo = gestor.catalogo_utilizavel(pcfg)
    lista = ([e for e in catalogo if tarefa in e.get("tarefas", [])] if tarefa
             else gestor.candidatos(catalogo, nivel))
    if not lista:
        return None, None, []
    falhadas: list[str] = []
    if needs:
        viaveis = []
        for entrada_n in lista:
            motivo = gestor.rota_serve(entrada_n, needs)
            if motivo:
                falhadas.append(f"{entrada_n['id']}:inviavel:{motivo}")
            else:
                viaveis.append(entrada_n)
        if not viaveis:
            return None, None, falhadas
        lista = viaveis
    # Ordem declarada (custo, `ordem`, `id`) antes de rodar: sem isto o
    # desempate era a posicao no ficheiro. A rotacao fica intacta.
    lista = gestor.ordenar_candidatos(lista)
    modo, por = _estrategia_do_perfil(pcfg)
    if modo == "rodizio" and len(lista) > 1:
        chave = gestor.chave_rodizio(perfil, sessao, nivel)
        with _lock:
            indice = gestor.indice_rodizio(chave, len(lista), por=por, perfil=perfil,
                                           sessao=sessao, nivel=nivel, contadores=_contadores,
                                           ids=[str(e.get("id") or "") for e in lista])
        ordenada = lista[indice % len(lista):] + lista[:indice % len(lista)]
    else:
        ordenada = list(lista)
    for entrada in ordenada:
        chave = (perfil, entrada["provider"].lower(), entrada["modelo"])
        with _lock:
            recente = _maus_expira.get(chave, 0.0) > time.monotonic()
        if recente:
            falhadas.append(f"{entrada['id']}:falhou_ha_pouco")
            continue
        try:
            return entrada, resolver_rota(perfil, entrada["provider"], entrada["modelo"]), falhadas
        except Exception as exc:
            falhadas.append(f"{entrada['id']}:{type(exc).__name__}")
            _registar({"evento": "sem_rota", "perfil": perfil, "sessao": sessao,
                       "nivel": nivel, "entrada": entrada["id"],
                       "erro": f"{type(exc).__name__}: {exc}"[:200]})
    return None, None, falhadas


# --------------------------------------------------------------------------- realimentacao


_TRUNCADO = {"length", "max_tokens", "max_output_tokens"}


def _resultado_do_turno_anterior(sessao: str) -> dict[str, Any]:
    with _lock:
        return dict(_resultado.get(sessao) or {})


def ajustar_por_resultado(sessao: str, nivel: str) -> tuple[str, str]:
    """A: sobe o nivel quando o turno anterior foi truncado no mesmo nivel.

    `finish_reason=length` e prova medida de que o trabalho nao coube no
    modelo: repetir o mesmo nivel no turno seguinte repete a truncagem. So
    sobe um nivel, e nunca acima de `alta`. A descida continua a ser feita
    pela histerese (`descer_apos`) — nao se desce por palpite.
    """
    if not sessao or not nivel:
        return nivel, ""
    antes = _resultado_do_turno_anterior(sessao)
    if not antes or antes.get("nivel") != nivel:
        return nivel, ""
    motivo = str(antes.get("finish_reason") or "").strip().lower()
    saida = antes.get("output_tokens")
    truncado = motivo in _TRUNCADO or (isinstance(saida, int) and saida >= 4000)
    if not truncado:
        return nivel, ""
    ordem = {n: i for i, n in enumerate(NIVEIS)}
    if ordem.get(nivel, 0) >= len(NIVEIS) - 1:
        return nivel, "ja_no_topo"
    return NIVEIS[ordem[nivel] + 1], f"turno_anterior_truncado:{motivo or saida}"


def degradar_nivel(pcfg: dict[str, Any], nivel: str, *, perfil: str,
                   sessao: str, needs: dict[str, Any] | None = None) -> tuple[str | None, dict[str, Any] | None,
                                      dict[str, Any] | None, list[str]]:
    """C: desce um nivel quando o decidido nao tem rota utilizavel.

    Melhor servir no nivel imediatamente abaixo, com registo, do que nao
    decidir nada: um `sem_rota` silencioso fica com o modelo da sessao, que
    pode ser o mais caro do catalogo. Nunca sobe — so desce.
    """
    ordem = {n: i for i, n in enumerate(NIVEIS)}
    i = ordem.get(nivel, 0) - 1
    while i >= 0:
        abaixo = NIVEIS[i]
        entrada, rota, falhadas = escolher_com_rota(
            pcfg, abaixo, perfil=perfil, sessao=sessao, needs=needs)
        if entrada is not None and rota is not None:
            return abaixo, entrada, rota, falhadas
        i -= 1
    return None, None, None, []


def escolher_por_janela(pcfg: dict[str, Any], needs: dict[str, Any] | None, *,
                        perfil: str = "", sessao: str = "") -> tuple[dict[str, Any] | None,
                                                                    dict[str, Any] | None, list[str]]:
    """Ultimo recurso: a rota viavel com a maior janela declarada.

    Descer de nivel nao resolve um pedido que nao cabe: se o texto ocupa mais do
    que qualquer janela do nivel, o nivel nao e o problema. Procura-se entao em
    todo o catalogo, o que tambem serve quando as rotas do nivel nao tem
    credencial e outra tem.
    """
    catalogo = gestor.catalogo_utilizavel(pcfg)
    viaveis, falhadas = [], []
    for entrada in catalogo:
        motivo = gestor.rota_serve(entrada, needs)
        if motivo:
            falhadas.append(f"{entrada['id']}:inviavel:{motivo}")
        else:
            viaveis.append(entrada)
    if not viaveis:
        return None, None, falhadas
    viaveis.sort(key=lambda e: int(e.get("contexto_max") or 0), reverse=True)
    for entrada in viaveis:
        try:
            return entrada, resolver_rota(perfil, entrada["provider"], entrada["modelo"]), falhadas
        except Exception as exc:
            falhadas.append(f"{entrada['id']}:{type(exc).__name__}")
    return None, None, falhadas


def herdar_se_indeciso(perfil: str, sessao: str, nivel: str,
                       decisao: dict[str, Any],
                       limiares: dict[str, Any] | None) -> tuple[str, str]:
    """D: na zona morta do limiar, herda o nivel do turno anterior.

    Um score encostado ao limiar de `media` com um unico sinal independente e
    um ponto de decisao arbitrario: oscilar aqui troca de provider a cada
    turno e cada troca invalida o cache do provider (custo real). Herdar o
    nivel anterior reduz a oscilacao sem inventar um nivel novo.
    """
    if not sessao or not nivel:
        return nivel, ""
    if nivel != "media":
        return nivel, ""
    score = decisao.get("score")
    if not isinstance(score, int):
        return nivel, ""
    lim = dict(_LIMIARES_SEGURANCA)
    for chave, valor in (limiares or {}).items():
        if chave == "media_sinais":
            continue
        try:
            lim[chave] = int(valor)
        except (TypeError, ValueError):
            continue
    margem = max(1, int(lim.get("media", 3)))
    # zona morta: no primeiro ponto acima do limiar de media
    if score > margem:
        return nivel, ""
    sinais = list(decisao.get("sinais") or [])
    if len(_analise_mod.sinais_independentes(sinais)) >= 2:
        return nivel, ""
    with _lock:
        antes = (_historico.get(f"{perfil}\x1f{sessao}") or {}).get("nivel")
    if antes in (None, "", "media"):
        return nivel, ""
    return antes, f"herdado_da_zona_morta:{antes}"


def aplicar_histerese(perfil: str, sessao: str, nivel: str, *, descer_apos: int = 2) -> str:
    """Sobe ja; desce so depois de `descer_apos` avaliacoes abaixo do nivel actual."""
    if not nivel:
        return nivel
    chave = f"{perfil}\x1f{sessao}"
    with _lock:
        antes = _historico.get(chave)
        if antes is None:
            _historico[chave] = {"nivel": nivel, "abaixo": 0}
            return nivel
        ordem = {n: i for i, n in enumerate(NIVEIS)}
        if ordem[nivel] >= ordem.get(antes["nivel"], 0):
            _historico[chave] = {"nivel": nivel, "abaixo": 0}
            return nivel
        antes["abaixo"] += 1
        if antes["abaixo"] >= max(1, int(descer_apos or 1)):
            _historico[chave] = {"nivel": nivel, "abaixo": 0}
            return nivel
        return antes["nivel"]


# --------------------------------------------------------------------------- agente


def agente_vivo(session_id: str = "", profundidade: int = 40) -> Any:
    """O agente do turno, encontrado na pilha da middleware.

    `llm_request` corre dentro de `build_api_request(agent, ...)`: o frame do
    chamador tem o agente. Sem agente compativel, o chamador cai para o modo
    antigo (reescrever so o nome do modelo, so quando o destino pertence a rota
    actual).
    """
    import sys

    f = sys._getframe(1)
    vistos = 0
    candidato = None
    while f is not None and vistos < profundidade:
        obj = f.f_locals.get("agent")
        if obj is not None and hasattr(obj, "switch_model"):
            sid = getattr(obj, "session_id", "") or ""
            if not session_id or sid == session_id:
                return obj
            candidato = candidato or obj
        f = f.f_back
        vistos += 1
    return candidato


def _mensagens(pedido: dict[str, Any]) -> list[Any]:
    for chave in ("messages", "input"):
        valor = pedido.get(chave)
        if isinstance(valor, list):
            return valor
    return []


#: Esforco de raciocinio por rota. `agent.reasoning_config` e estado do agente,
#: nao do turno: sem guardar o valor de origem, uma rota com esforco contaminava
#: todas as seguintes (o `switch_model` volta a resolver do `config.yaml`, mas
#: nao restaura um valor que ja nao esteja la).
_esforco_base: dict[str, Any] = {}
_ESFORCO_AUSENTE = object()
#: Campos de raciocinio que o payload pode ja trazer da rota anterior. Ao
#: aplicar esforco saem todos: a forma do provider antigo (o `reasoning` do
#: OpenRouter) e recusada pelo novo (o deepseek quer `thinking` + `reasoning_effort`).
_CAMPOS_ESFORCO_TOPO = ("reasoning_effort", "verbosity")
_CAMPOS_ESFORCO_EXTRA = ("reasoning", "thinking")


def _aplicar_esforco(agente: Any, entrada: dict[str, Any], sessao: str) -> str:
    """Escreve o esforco da rota no agente; devolve o valor aplicado ('' = nenhum).

    Chamar **depois** de `switch_model`: a troca volta a resolver
    `reasoning_config` do `config.yaml` e apagaria o valor que aqui se pusesse.
    """
    chave = f"{sessao}\x1f{id(agente)}"
    esforco, _ = gestor.normalizar_esforco(entrada.get("esforco"))
    with _lock:
        if len(_esforco_base) > 500:
            _esforco_base.clear()
        base = _esforco_base.get(chave, _ESFORCO_AUSENTE)
    if esforco:
        wire = gestor.esforco_wire(esforco)
        if not wire:
            return ""
        with _lock:
            if base is _ESFORCO_AUSENTE:
                try:
                    _esforco_base[chave] = copy.deepcopy(
                        getattr(agente, "reasoning_config", None))
                except Exception:  # noqa: BLE001
                    _esforco_base[chave] = None
        try:
            agente.reasoning_config = {"enabled": True, "effort": wire}
        except Exception:  # noqa: BLE001 — agente sem o atributo: fail-open
            _falha("esforco: agente recusou reasoning_config")
            return ""
        return esforco
    if base is not _ESFORCO_AUSENTE:
        try:
            agente.reasoning_config = base
        except Exception:  # noqa: BLE001
            pass
        with _lock:
            _esforco_base.pop(chave, None)
    return ""


def _pedido_com_esforco(pedido: dict[str, Any], entrada: dict[str, Any],
                        rota: dict[str, Any], sessao: str = "") -> tuple[dict[str, Any], str]:
    """Payload deste turno com o esforco na forma do provider de destino.

    O `api_kwargs` ja foi construido antes da middleware com a rota anterior,
    logo mudar o agente so vale para o turno seguinte: sem esta escrita, o
    primeiro turno de uma rota nova saia com o esforco antigo.
    """
    esforco, _ = gestor.normalizar_esforco(entrada.get("esforco"))
    if not esforco:
        return pedido, ""
    provider = str(rota.get("provider") or entrada.get("provider") or "")
    extra, topo = gestor.extras_esforco(provider, entrada.get("modelo", ""),
                                        esforco, session_id=sessao)
    if not (extra or topo):
        return pedido, ""
    novo = dict(pedido)
    for chave in _CAMPOS_ESFORCO_TOPO:
        novo.pop(chave, None)
    corpo_extra = novo.get("extra_body")
    corpo_extra = dict(corpo_extra) if isinstance(corpo_extra, dict) else {}
    for chave in _CAMPOS_ESFORCO_EXTRA:
        corpo_extra.pop(chave, None)
    corpo_extra.update(extra)
    if corpo_extra:
        novo["extra_body"] = corpo_extra
    elif "extra_body" in novo:
        novo.pop("extra_body", None)
    novo.update(topo)
    return novo, esforco


def _trocar(agente: Any, entrada: dict[str, Any], rota: dict[str, Any],
            perfil: str, sessao: str) -> dict[str, Any]:
    alvo_provider = rota["provider"] or entrada["provider"].lower()
    alvo_modelo = entrada["modelo"]
    agente.switch_model(
        new_model=alvo_modelo, new_provider=alvo_provider,
        api_key=rota.get("api_key", ""), base_url=rota.get("base_url", ""),
        api_mode=rota.get("api_mode", ""),
    )
    with _lock:
        _maus_expira.pop((perfil, alvo_provider.lower(), alvo_modelo), None)
    return {"provider": alvo_provider, "modelo": alvo_modelo}


# --------------------------------------------------------------------------- middleware


def middleware_llm_request(
    request: Any = None,
    original_request: Any = None,  # noqa: ARG001 — contrato da middleware
    session_id: str = "",
    model: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    turn_id: Any = None,
    api_request_id: Any = None,
    api_call_count: Any = 0,
    platform: str | None = None,
    **_: Any,
) -> dict[str, Any] | None:
    """Decide a rota do turno e troca-a no agente; `None` = pedido intacto."""
    inicio = time.monotonic()
    try:
        if not isinstance(request, dict):
            return None
        for nome_env in _AMBIENTE_DESLIGADO:
            if str(os.environ.get(nome_env) or "").strip().lower() in ("1", "on", "true", "sim"):
                return None
        config = _config()
        registar = bool(config.get("registar", True))
        # Diagnostico opt-in (`VMM_DEBUG_SUP=1`): regista o que o core entrega
        # a middleware. Existe porque um kwarg ausente faz a regra de
        # superficie nunca disparar e o sintoma (rota escolhida pela analise)
        # nao se distingue de "nao ha regra". Desligado por omissao.
        if str(os.environ.get("VMM_DEBUG_SUP") or "").strip() in ("1", "on", "true"):
            try:
                with open(gestor.directorio() / "debug-sup.jsonl", "a",
                          encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "platform": platform, "session_id": session_id,
                        "model": model, "provider": provider,
                        "turn_id": str(turn_id)[-12:] if turn_id else None,
                    }, ensure_ascii=False) + "\n")
            except Exception:  # noqa: BLE001
                pass
        if not config.get("activo", True):
            if _uma_vez(("desligado", session_id, "")):
                _registar({"evento": "desligado", "sessao": session_id}, registar=registar)
            return None
        agente = agente_vivo(session_id)
        perfil = gestor.perfil_atual(agente)
        with _lock:
            if session_id:
                _ultimo_perfil[session_id] = perfil
                if len(_ultimo_perfil) > 500:
                    _ultimo_perfil.clear()
        pcfg = gestor.perfil_config(config, perfil)
        if pcfg is None:
            if _uma_vez(("perfil_nao_gerido", session_id, perfil)):
                _registar({"evento": "perfil_nao_gerido", "perfil": perfil,
                           "sessao": session_id}, registar=registar)
            return None
        if not pcfg.get("activo", True):
            if _uma_vez(("perfil_desligado", session_id, perfil)):
                _registar({"evento": "perfil_desligado", "perfil": perfil,
                           "sessao": session_id}, registar=registar)
            return None
        analise = pcfg.get("analise") or {}
        # Superficie: o core entrega `platform` a esta middleware
        # (`turn_api_request.py`: `platform=agent.platform or ""`). E o unico
        # contexto de sessao deterministico que existe — `cron`, `api_server`,
        # `cli`. Medido a 2026-11-03 em 7118 respostas: `api_server` 6700,
        # `cron` 173, `cli` 27. Uma regra de superficie ganha a tarefa (que se
        # adivinha por palavra) e a analise de complexidade (que se estima);
        # so o marcador explicito do utilizador manda mais.
        superficie = (platform or "").strip().lower()
        fonte_sup = "kwarg" if superficie else ""
        if not superficie and agente is not None:
            superficie = str(getattr(agente, "platform", "") or "").strip().lower()
            fonte_sup = "agente" if superficie else ""
        regra_sup = gestor.regra_de_superficie(pcfg, superficie) if superficie else None
        nome_sup = superficie
        _recarregar_analise_se_mudou()
        _recarregar_analise_modelo_se_mudou()
        mensagens = _mensagens(request)
        # O nivel decide-se UMA vez por turno. Um turno faz varios pedidos (laco
        # de ferramentas) e cada pedido mudava a janela de mensagens, voltava a
        # correr o classificador (nao deterministico) e somava na histerese:
        # medido a 2026-11-02, 63 de 167 turnos (37,7%) trocavam de nivel a
        # meio. A chave vem do `turn_id` que o core ja entrega.
        fixar_turno = bool(analise.get("fixar_nivel_por_turno", True))
        chave_turno, fonte_turno = chave_do_turno(session_id, turn_id, mensagens)
        passo = api_call_count if isinstance(api_call_count, int) else 0
        identificador_turno = chave_turno.rsplit("\x1f", 1)[-1]
        pin = _pin_ler(chave_turno) if fixar_turno else None
        # Uma ordem explicita do utilizador (`complexidade: alta` na mensagem)
        # ganha a tudo, inclusive a regra de superficie. Calculado nos dois
        # ramos (pin e decisao nova) porque o bloco que fixa a entrada da
        # superficie corre depois dos dois.
        marcador_manda = False
        if pin is not None and not pin.get("nivel"):
            pin = None
        if pin is not None and fonte_turno != "turn_id":
            # Sem `turn_id` a chave e um hash, e dois turnos seguidos com o mesmo
            # texto colidem. `api_call_count` reinicia a cada turno, logo so se
            # reutiliza quando o passo avancou: e a unica prova de que ainda
            # estamos no mesmo turno.
            if passo <= int(pin.get("passo") or 0):
                pin = None
        if pin is not None:
            # Reutilizacao: nao corre decidir, detectar_tarefa, classificar,
            # herdar_se_indeciso, ajustar_por_resultado nem aplicar_histerese.
            # O score/sinais do primeiro pedido nao se repetem — sao o rasto da
            # decisao, e nao houve reavaliacao nenhuma neste pedido.
            nivel = str(pin.get("nivel") or "")
            tarefa = str(pin.get("tarefa") or "")
            associada = pin.get("associada") or None
            marcador_manda = (pin.get("origem") == "marcador")
            base = {"perfil": perfil, "sessao": session_id, "nivel": nivel,
                    "motor": _VERSAO_MOTOR, "inst": _INSTANCIA,
                    "origem": pin.get("origem") or "",
                    "tarefa": tarefa, "score": None, "sinais": None,
                    "turno": identificador_turno, "passo": passo,
                    "pin": "reutilizado", "fonte_turno": fonte_turno,
                    "superficie": superficie or None,
                    "superficie_fonte": fonte_sup or None,
                    "plataforma_kw": (platform or "").strip().lower() or None,
                    "kw_platform": platform is not None}
            if pin.get("classificador") and (pin["classificador"] or {}).get("lido"):
                # So a leitura se herda. Uma falha nao se herda: o cartao conta
                # uma falha por (ts, tipo) e cada passo tem o seu `ts`, logo o
                # mesmo TimeoutError do passo 1 era contado uma vez por passo.
                base["classificador"] = pin["classificador"]
            if pin.get("classificador_erro"):
                # A falha aconteceu no PRIMEIRO pedido do turno e ja foi
                # registada la. Repehti-la em cada passo fazia um turno de 14
                # pedidos parecer 14 falhas — medido a 2026-11-03: 14 eventos,
                # um turno, o cartao a dizer "falhou ha instantes" durante tres
                # minutos e a taxa do classificador a mentir. O erro propaga-se
                # como referencia (o cartao continua a saber que o classificador
                # esta em baixo neste turno), mas nao como uma falha nova.
                base["classificador_erro_herdado"] = pin["classificador_erro"]
        else:
            decisao = _analise_mod.decidir(
                mensagens,
                limiares=analise.get("limiares"),
                janela=int(analise.get("janela_mensagens", 8) or 8),
                maximo=int(analise.get("varredura_max_mensagens", 60) or 60),
                pesos=analise.get("pesos"),
            )
            tarefa = _analise_mod.detectar_tarefa(mensagens, pcfg.get("tarefas") or {})
            associada = tarefa if any(tarefa in e.get("tarefas", [])
                                     for e in gestor.catalogo_utilizavel(pcfg)) else None
            # A superficie manda sobre a tarefa e sobre a analise, mas nunca
            # sobre um marcador explicito: se o utilizador escreveu
            # `complexidade: alta` na mensagem, e ele que decide.
            superficie_manda = regra_sup is not None and decisao.get("origem") != "marcador"
            marcador_manda = decisao.get("origem") == "marcador"
            if superficie_manda:
                nivel_sup = regra_sup["regra"].get("nivel")
                if nivel_sup:
                    decisao = {**decisao, "nivel": nivel_sup,
                               "origem": "superficie", "score": None}
                else:
                    # Entrada fixada: a tarefa nao pode escolher outra rota.
                    associada = None
            # Classificador por modelo: entra quando o turno nao tem ordem explicita
            # (`complexidade: alta` na mensagem), quando nenhuma tarefa manda no
            # turno (a tarefa e a prioridade) e quando o perfil o tem ligado com
            # rota declarada. Falha -> heuristica, com rasto no ledger.
            classificador: dict[str, Any] | None = None
            classificador_erro = ""
            classificador_motivo = ""
            if not associada and not superficie_manda and decisao.get("origem") != "marcador":
                definicao = _analise_modelo_mod.efectiva(analise)
                if definicao is not None:
                    resultado_m = _analise_modelo_mod.classificar(
                        mensagens, definicao,
                        lambda p, m: resolver_rota(perfil, p, m), perfil=perfil)
                    tudo = dict(resultado_m or {})
                    classificador = tudo.get("classificador")
                    if tudo.get("erro"):
                        classificador_erro = str(tudo["erro"])
                    elif tudo.get("sem_efeito"):
                        # Config errada (provider inexistente no perfil, ou
                        # credencial ausente). Nao e avaria: o cartao diz
                        # "sem efeito" com o motivo, e o turno segue pela
                        # heuristica sem alarme.
                        classificador_motivo = str(tudo.get("motivo") or tudo["sem_efeito"])
                    elif tudo.get("nivel"):
                        decisao = {**decisao, "nivel": tudo["nivel"], "origem": "modelo",
                                   "score": None}
            nivel = decisao["nivel"] or ("leve" if associada else "")
            base = {"perfil": perfil, "sessao": session_id, "nivel": nivel,
                    "motor": _VERSAO_MOTOR, "inst": _INSTANCIA,
                    "origem": "tarefa" if associada else decisao["origem"],
                    "tarefa": tarefa, "score": decisao.get("score"),
                    "sinais": decisao.get("sinais"),
                    "turno": identificador_turno, "passo": passo,
                    "pin": "novo", "fonte_turno": fonte_turno,
                    "superficie": superficie or None,
                    "superficie_fonte": fonte_sup or None,
                    "plataforma_kw": (platform or "").strip().lower() or None,
                    "kw_platform": platform is not None}
            primeira_da_instancia = _primeira_decisao_da_instancia()
            if fixar_turno and passo > 1 and primeira_da_instancia:
                # Nao e oscilacao do classificador: o store de pins vive na
                # memoria do modulo e uma recarga apaga-o a meio do turno. A
                # marca separa "perdi o pin" de "decidi outra vez", senao a
                # prova aritmetica do pin acusa um turno instavel que nao
                # existe. O nivel mantem-se o que a decisao der agora.
                base["excecao"] = "recarga"
            if classificador:
                base["classificador"] = classificador
            if classificador_erro:
                base["classificador_erro"] = classificador_erro
            if classificador_motivo:
                base["classificador_motivo"] = classificador_motivo
            if not nivel:
                _lembrar_decisao(session_id, perfil=perfil, nivel=nivel, tarefa=tarefa,
                                 origem=base["origem"], acesso="", entrada="")
                _registar({**base, "evento": "reset"}, registar=registar)
                return None
            if associada:
                # A tarefa nao contamina a histerese: a proxima tarefa e reavaliada ja.
                with _lock:
                    _historico.pop(f"{perfil}\x1f{session_id}", None)
            else:
                # D: zona morta do limiar -> herda o nivel anterior em vez de oscilar.
                # Nao se aplica a decisao por modelo: a zona morta e um artefacto do
                # limiar heuristico, e o modelo nao decide por pontuacao.
                if decisao.get("origem") == "modelo":
                    motivo_d = ""
                else:
                    nivel, motivo_d = herdar_se_indeciso(
                        perfil, session_id, nivel, decisao, analise.get("limiares"))
                if motivo_d:
                    base["indecisao"] = motivo_d
                # A: turno anterior truncado no mesmo nivel -> sobe um nivel.
                nivel, motivo_a = ajustar_por_resultado(session_id, nivel)
                if motivo_a:
                    base["realimentacao"] = motivo_a
                nivel = aplicar_histerese(perfil, session_id, nivel,
                                          descer_apos=int(analise.get("descer_apos", 2) or 2))
            base["nivel"] = nivel
            if fixar_turno:
                # A rota entra aqui no primeiro pedido que a escolher com sucesso;
                # `escolher_com_rota` continua a ser a unica autoridade sobre ela.
                _pin_gravar(chave_turno, {
                    "nivel": nivel, "tarefa": tarefa, "associada": associada,
                    "origem": base["origem"], "passo": passo,
                    "classificador": base.get("classificador"),
                    "classificador_erro": base.get("classificador_erro"),
                })
        _lembrar_decisao(session_id, perfil=perfil, nivel=nivel, tarefa=tarefa,
                         origem=base["origem"], acesso="", entrada="")
        if not gestor.catalogo_utilizavel(pcfg):
            _registar({**base, "evento": "sem_catalogo"}, registar=registar)
            return None
        # Triagem do pedido: o que ele *exige* sobrepoe-se a pontuacao. Texto
        # grande nao e "mais complexo", mas um `leve` que nao cabe na janela
        # nao serve por muito leve que seja — e uma imagem nao se resolve com
        # um modelo que nao a aceita.
        needs = _analise_mod.necessidades(
            _mensagens(request), request,
            limiar_contexto=int(analise.get("contexto_grande_tokens") or 24000))
        needs["latencia_max_ms"] = gestor.teto_latencia(pcfg)
        if needs.get("contexto_grande"):
            # Regista sempre que o contexto passa o limiar, mesmo quando o nivel
            # ja era media/alta: se so ficar registado quando promove um `leve`,
            # um turno de 80k tokens nao deixa rasto nenhum de que a triagem
            # correu — e deixa de ser possivel provar que esta viva.
            base["triagem"] = f"contexto {needs['tokens_entrada']}tok"
            if nivel == "leve":
                nivel = "media"
                base["nivel"] = nivel
                # Num turno ja fixado, subir o nivel aqui e uma necessidade do
                # pedido (nao coube), nao oscilacao: fica declarado como tal.
                base["excecao"] = "capacidade"
                base["excecao_motivo"] = "contexto_nao_cabe_no_nivel_pinado"
        if needs.get("modalidades") or needs.get("json"):
            base["exige"] = {k: needs[k] for k in ("modalidades", "json") if needs.get(k)}
        # Rota fixada no turno: reutiliza-se enquanto for viavel. So cai para a
        # escolha normal quando a rota morreu (falhou ha pouco, credencial) ou
        # deixou de servir (contexto/modalidade/json/latencia) — e isso fica
        # marcado com `excecao`, senao "estavel" seria uma mentira confortavel.
        entrada = rota = None
        falhadas: list[str] = []
        if pin is not None and pin.get("entrada"):
            candidata = _entrada_do_catalogo(pcfg, pin["entrada"])
            if candidata is not None:
                motivo_cap = gestor.rota_serve(candidata, needs) if needs else ""
                chave_rota = (perfil, str(candidata["provider"]).lower(),
                              candidata["modelo"])
                with _lock:
                    morta = _maus_expira.get(chave_rota, 0.0) > time.monotonic()
                if motivo_cap:
                    base["excecao"] = "capacidade"
                    base["excecao_motivo"] = f"rota_pinada:{motivo_cap}"
                elif morta:
                    base["excecao"] = "rota"
                    base["excecao_motivo"] = "rota_pinada_falhou_ha_pouco"
                else:
                    try:
                        entrada = candidata
                        rota = resolver_rota(perfil, candidata["provider"],
                                             candidata["modelo"])
                        base["rota_pin"] = "reutilizada"
                    except Exception as exc:  # noqa: BLE001
                        base["excecao"] = "rota"
                        base["excecao_motivo"] = f"rota_pinada:{type(exc).__name__}"
                        entrada = rota = None
        if (entrada is None or rota is None) and not marcador_manda \
                and regra_sup is not None \
                and regra_sup.get("entrada") is not None:
            # Superficie com entrada fixada: e uma ordem, nao uma preferencia.
            # So cede a capacidade (o pedido nao cabe no modelo) ou a uma rota
            # que falhou ha pouco — e nos dois casos fica marcado com
            # `excecao`, senao parecia que a regra nunca disparou.
            candidata = regra_sup["entrada"]
            motivo_cap = gestor.rota_serve(candidata, needs) if needs else ""
            chave_rota = (perfil, str(candidata["provider"]).lower(),
                          candidata["modelo"])
            with _lock:
                morta = _maus_expira.get(chave_rota, 0.0) > time.monotonic()
            if motivo_cap:
                base["excecao"] = "capacidade"
                base["excecao_motivo"] = f"superficie:{nome_sup}:{motivo_cap}"
            elif morta:
                base["excecao"] = "rota"
                base["excecao_motivo"] = f"superficie:{nome_sup}:falhou_ha_pouco"
            else:
                try:
                    entrada = candidata
                    rota = resolver_rota(perfil, candidata["provider"],
                                         candidata["modelo"])
                    base["origem"] = "superficie"
                    base["superficie_regra"] = regra_sup["motivo"]
                except Exception as exc:  # noqa: BLE001
                    base["excecao"] = "rota"
                    base["excecao_motivo"] = f"superficie:{type(exc).__name__}"
                    entrada = rota = None
        if entrada is None or rota is None:
            entrada, rota, falhadas = escolher_com_rota(
                pcfg, nivel, perfil=perfil, sessao=session_id, tarefa=associada,
                needs=needs)
        if associada and (entrada is None or rota is None):
            base["origem"] = "analise_fallback_tarefa"
            base["falhadas_tarefa"] = falhadas
            entrada, rota, falhadas = escolher_com_rota(
                pcfg, nivel, perfil=perfil, sessao=session_id, needs=needs)
        if entrada is None or rota is None:
            # C: sem rota no nivel decidido, desce um nivel em vez de nao decidir.
            if not associada:
                nivel_ef, entrada_ef, rota_ef, falhadas_ef = degradar_nivel(
                    pcfg, nivel, perfil=perfil, sessao=session_id, needs=needs)
                if entrada_ef is not None and rota_ef is not None:
                    base["degradado_de"] = nivel
                    base["nivel_efetivo"] = nivel_ef
                    base["falhadas_nivel"] = falhadas
                    nivel = nivel_ef
                    entrada, rota, falhadas = entrada_ef, rota_ef, falhadas_ef
            if entrada is None or rota is None:
                # Ultimo recurso, e so quando o problema e **capacidade**: uma
                # rota que nao cabe nao se resolve descendo de nivel, mas um
                # nivel sem rota (ou sem credencial) resolve-se ficando sem
                # troca — ir buscar outra rota qualquer seria decidir por fora
                # da complexidade.
                if any(":inviavel:" in f for f in falhadas):
                    entrada_j, rota_j, falhadas_j = escolher_por_janela(
                        pcfg, needs, perfil=perfil, sessao=session_id)
                    if entrada_j is not None and rota_j is not None:
                        base["escolhido_por_janela"] = entrada_j["id"]
                        entrada, rota = entrada_j, rota_j
                        falhadas = falhadas + falhadas_j
            if entrada is None or rota is None:
                _registar({**base, "evento": "sem_rota", "falhadas": falhadas}, registar=registar)
                return None
        if fixar_turno:
            # A rota do turno fica fixada no mesmo pin: os pedidos seguintes
            # reutilizam-na sem repetir rodizio, estrategia nem credenciais.
            guardado = _pin_ler(chave_turno) or {}
            guardado.update({"nivel": nivel, "entrada": entrada["id"],
                             "provider": rota["provider"],
                             "modelo": entrada["modelo"], "passo": passo})
            _pin_gravar(chave_turno, guardado)
        _lembrar_decisao(session_id, perfil=perfil, nivel=nivel, tarefa=tarefa,
                         origem=base["origem"], acesso=entrada.get("acesso", ""),
                         entrada=entrada["id"], escolhido_provider=rota["provider"],
                         escolhido_modelo=entrada["modelo"])
        atual = {"provider": (provider or "").strip().lower(),
                 "modelo": request.get("model") or model or ""}
        if agente is None:
            # Sem agente nao ha troca de provider: so reescreve o nome, e so quando
            # o destino pertence a rota actual — o limite da v1, mantido como rede.
            if rota["provider"].lower() != atual["provider"] or entrada["modelo"] == atual["modelo"]:
                _registar({**base, "evento": "sem_agente", "entrada": entrada["id"],
                           "acesso": entrada.get("acesso", ""),
                           "de": atual}, registar=registar)
                return None
            pedido, esforco_fio = _pedido_com_esforco(
                {**request, "model": entrada["modelo"]}, entrada, rota, session_id)
            _registar({**base, "evento": "troca_so_modelo", "de": atual["modelo"],
                       "para": entrada["modelo"], "entrada": entrada["id"],
                       "acesso": entrada.get("acesso", ""),
                       "esforco": esforco_fio or "nao_aplicado",
                       "ms": round((time.monotonic() - inicio) * 1000, 1)}, registar=registar)
            return {"request": pedido, "source": NOME,
                    "reason": f"{perfil}:complexidade={nivel}->{entrada['modelo']}"}
        destino = {"provider": (getattr(agente, "provider", "") or "").lower(),
                   "modelo": getattr(agente, "model", "") or ""}
        if destino["provider"] == rota["provider"].lower() and destino["modelo"] == entrada["modelo"]:
            # A rota ja e a do turno, mas o esforco pode nao ser: aplica-se (e
            # repoe-se quando a entrada nao declara nenhum).
            esforco_agente = _aplicar_esforco(agente, entrada, session_id)
            pedido, esforco_fio = _pedido_com_esforco(request, entrada, rota, session_id)
            _registar({**base, "evento": "sem_troca", "rota": destino,
                       "entrada": entrada["id"], "acesso": entrada.get("acesso", ""),
                       "esforco": esforco_agente or "nao_declarado",
                       "ms": round((time.monotonic() - inicio) * 1000, 1)}, registar=registar)
            if esforco_fio:
                return {"request": pedido, "source": NOME,
                        "reason": f"{perfil}:complexidade={nivel}:esforco={esforco_fio}"}
            return None
        try:
            aplicado = _trocar(agente, entrada, rota, perfil, session_id)
        except Exception as exc:
            with _lock:
                _maus_expira[(perfil, entrada["provider"].lower(), entrada["modelo"])] = \
                    time.monotonic() + _TTL_MAU
            _registar({**base, "evento": "falha_troca", "entrada": entrada["id"],
                       "acesso": entrada.get("acesso", ""),
                       "de": destino, "erro": f"{type(exc).__name__}: {exc}"[:300]},
                      registar=registar)
            return None
        # Depois da troca: o `switch_model` re-resolve `reasoning_config` do
        # `config.yaml` e apagaria um valor posto antes.
        esforco_agente = _aplicar_esforco(agente, entrada, session_id)
        pedido, esforco_fio = _pedido_com_esforco(
            {**request, "model": entrada["modelo"]}, entrada, rota, session_id)
        _registar({**base, "evento": "troca", "entrada": entrada["id"], "de": destino,
                   "acesso": entrada.get("acesso", ""),
                   "para": aplicado, "fonte_credencial": rota.get("source", ""),
                   "esforco": esforco_agente or "nao_declarado",
                   **({"preteridas": list(dict.fromkeys(falhadas))} if falhadas else {}),
                   "ms": round((time.monotonic() - inicio) * 1000, 1)}, registar=registar)
        if entrada["modelo"] != request.get("model") or esforco_fio:
            return {"request": pedido, "source": NOME,
                    "reason": f"{perfil}:complexidade={nivel}->{aplicado['provider']}:{entrada['modelo']}"}
        return None
    except Exception:
        _falha("middleware llm_request: " + traceback.format_exc(limit=4).replace("\n", " | "))
        return None


# --------------------------------------------------------------------------- prova


def pos_resposta(
    session_id: str = "",
    model: str = "",
    response_model: Any = None,
    finish_reason: Any = None,
    usage: Any = None,
    provider: str = "",
    platform: str = "",
    api_request_id: Any = None,
    turn_id: Any = None,
    task_id: Any = None,
    api_mode: str = "",
    **_: Any,
) -> None:
    """Prova independente do que respondeu: o provider e o modelo **efectivos**
    do agente no momento da resposta (nao o escolhido no catalogo), o
    `response_model` que o provider devolveu, e o contexto da decisao
    (perfil, sessao, superficie, acesso, tarefa/complexidade) mais a
    correlacao de `request_id`/`turn_id`/`task_id` que o hook entrega.

    Nada aqui e inventado a partir da config: `escolhido` e o que a middleware
    decidiu; `provider`/`modelo` sao o que o agente tinha de facto.
    """
    try:
        config = _config()
        if not bool(config.get("registar", True)):
            return
        with _lock:
            decisao = dict(_ultima_decisao.get(session_id) or {})
            perfil = decisao.get("perfil") or _ultimo_perfil.get(session_id) or ""
        agente = agente_vivo(session_id) if not perfil else None
        if not perfil:
            perfil = gestor.perfil_atual(agente)
        superficie = (platform or "").strip()
        if not superficie and agente is not None:
            superficie = (getattr(agente, "platform", "") or "").strip()
        superficie = superficie or "desconhecido"
        efectivo_provider = (provider or "").strip().lower()
        efectivo_modelo = (model or "").strip()
        acesso = decisao.get("acesso") or ""
        if not acesso and efectivo_provider:
            acesso = gestor.acesso_por_provider(efectivo_provider)
        entrada: dict[str, Any] = {
            "evento": "resposta",
            "perfil": perfil or "desconhecido",
            "sessao": session_id,
            "superficie": superficie,
            "provider": efectivo_provider or "desconhecido",
            "modelo": efectivo_modelo or "desconhecido",
            "respondido_por": response_model,
            "finish_reason": finish_reason,
            "api_mode": api_mode or "",
            "acesso": acesso or "desconhecido",
            # O motor e a instancia vao tambem na linha da resposta: quem le o
            # registo procura "que modelo respondeu e com que codigo", e ter de
            # juntar com o evento de decisao pelo `turno` para saber a versao
            # fazia a linha parecer de outro plugin.
            "motor": _VERSAO_MOTOR,
            "inst": _INSTANCIA,
        }
        if api_request_id is not None:
            entrada["request_id"] = api_request_id
        if turn_id is not None:
            entrada["turn_id"] = turn_id
        if task_id is not None:
            entrada["task_id"] = task_id
        if decisao.get("nivel"):
            entrada["nivel"] = decisao["nivel"]
        if decisao.get("tarefa"):
            entrada["tarefa"] = decisao["tarefa"]
        if decisao.get("origem"):
            entrada["origem"] = decisao["origem"]
        if decisao.get("escolhido_provider"):
            entrada["escolhido"] = {
                "provider": decisao["escolhido_provider"],
                "modelo": decisao.get("escolhido_modelo", ""),
            }
        if isinstance(usage, dict):
            entrada["tokens"] = {k: usage.get(k) for k in
                                 ("input_tokens", "output_tokens", "prompt_tokens",
                                  "completion_tokens", "total_tokens")
                                 if usage.get(k) is not None}
        # A: guarda o resultado para o turno seguinte poder ver se o nivel chegou.
        if session_id and decisao.get("nivel"):
            saida = None
            if isinstance(usage, dict):
                saida = usage.get("output_tokens", usage.get("completion_tokens"))
            with _lock:
                _resultado[session_id] = {
                    "nivel": decisao.get("nivel"),
                    "finish_reason": finish_reason,
                    "output_tokens": saida if isinstance(saida, int) else None,
                }
                if len(_resultado) > 500:
                    _resultado.clear()
        _registar(entrada, registar=True)
    except Exception:
        return


# --------------------------------------------------------------------------- registo


def register(ctx: Any) -> None:
    try:
        ctx.register_middleware("llm_request", middleware_llm_request)
        ctx.register_hook("post_api_request", pos_resposta)
        gestor.garantir()  # cria o config por omissao para o dono o editar
        _config()  # aquece a cache com o que esta no disco
    except Exception:
        _falha("register: " + traceback.format_exc(limit=4).replace("\n", " | "))
        raise
