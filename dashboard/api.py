"""Vipernet Model Manager — backend do painel do dashboard.

Montado pelo host em ``/api/plugins/vipernet-model-manager/``. Le e escreve a MESMA
configuracao que o plugin do agente usa (``plugin-data/vipernet-model-manager/config.json``),
por via do ``gestor`` do plugin — nao ha uma segunda fonte de verdade: o painel nao
reimplementa validacao nem semantica, e tudo o que grava passa por ``gestor.gravar``
(validacao + backup + ``os.replace``).

Somente duas coisas sao escritas: o interruptor global e o interruptor de cada perfil.
O catalogo e o log sao leitura — mudar modelos e no ficheiro que o dono controla.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

# O ``api.py`` e importado por caminho; o pacote do plugin tem de estar no sys.path
# para que ``gestor`` resolva para o modulo do plugin e nao para outro homonimo.
_RAIZ_PLUGIN = Path(__file__).resolve().parents[1]
if str(_RAIZ_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_RAIZ_PLUGIN))

import gestor  # noqa: E402  (depende do sys.path acima)
import sombra as _sombra  # noqa: E402  (idem: o modulo vive na raiz do plugin)
import i18n as _i18n  # noqa: E402  (idem)

# Alias curto: as mensagens de recusa sao todas `t(...)`.
t = _i18n.t

router = APIRouter()

def _versao_painel() -> str:
    """Versao do painel lida do ``manifest.json`` — um numero, uma casa.

    Este valor ja esteve escrito a mao ao lado do manifest e divergiu: o
    endpoint respondia 3.3.0 com o manifest em 3.6.x, e quem le a resposta
    duvida de qual e o codigo que esta a correr. O manifest e a fonte.
    """
    try:
        manifesto = Path(__file__).resolve().parent / "manifest.json"
        dados = json.loads(manifesto.read_text(encoding="utf-8"))
        return str(dados.get("version") or "")
    except Exception:  # noqa: BLE001 - sem manifest o painel ainda responde
        return ""


GITHUB_AUTOR = "https://github.com/ptv1p3r"


def _meta_plugin() -> dict[str, str]:
    """Identidade do plugin para o painel: versao, autor e repositorio.

    A versao vive no ``plugin.yaml`` — o mesmo ficheiro que o loader le e que o
    catalogo verifica. O painel nao repete o numero: se o manifest e o plugin.yaml
    divergirem, o painel mostra os dois valores e a divergencia fica visivel em
    vez de escolher um deles em silencio.

    ``github`` e o **repositorio do plugin**, lido do campo ``homepage`` do
    ``plugin.yaml`` — uma so casa para o endereco, senao o painel e o manifesto
    dizem coisas diferentes. A chave chama-se ``github`` por compatibilidade com
    o loader, mas o valor e o repositorio real deste plugin: o Gitea interno
    (nao ha publicacao em GitHub). A submissao ao catalogo continua bloqueada
    por outra razao — a regra 2 do catalogo pede um repositorio publico,
    clonavel por https, que um Gitea em RFC1918 nao satisfaz. ``autor_github``
    fica separado: e o perfil do autor, nao o repositorio que se pina.
    """
    versao = ""
    autor = ""
    homepage = ""
    try:
        manifesto = Path(__file__).resolve().parent.parent / "plugin.yaml"
        for linha in manifesto.read_text(encoding="utf-8").splitlines():
            chave, _, valor = linha.partition(":")
            chave = chave.strip()
            if chave not in ("version", "author", "homepage"):
                continue
            # Aceita `author: Nome`, `author: "Nome"` e comentarios a direita.
            limpo = valor.strip().strip('"').strip("'")
            if chave == "version":
                versao = limpo
            elif chave == "author":
                autor = limpo
            else:
                homepage = limpo
    except Exception:  # noqa: BLE001 - sem plugin.yaml o painel ainda responde
        pass
    return {
        "versao": versao,
        "autor": autor,
        "github": homepage or GITHUB_AUTOR,
        "autor_github": GITHUB_AUTOR,
        # Divergencia entre os dois numeros e deriva, nao gosto: o painel diz.
        "versao_manifest": _versao_painel(),
    }


LIMITE_LOG_MAX = 500
LIMITE_LOG_OMISSAO = 50


# --------------------------------------------------------------------------- leitura


#: Erros da ultima leitura do config.
#:
#: `gestor.carregar()` devolve o config normalizado **e** a lista de erros. A
#: normalizacao descarta o que nao conhece, logo um ficheiro editado a mao (ou
#: escrito por uma versao futura) produzia um painel a mostrar uma config que
#: nao existe no disco, sem uma unica palavra de aviso. Engolir a lista era o
#: ultimo ponto cego da guarda de chaves: o `validar` passou a recusar, mas
#: ninguem via a recusa.
_ERROS_CONFIG: list[str] = []


def _cfg() -> dict[str, Any]:
    cfg, erros = gestor.carregar()
    _ERROS_CONFIG[:] = list(erros or [])
    return cfg


def _rev(cfg: dict[str, Any]) -> str:
    """Impressao digital do config, para o painel nao gravar por cima do que nao leu.

    Uma aba aberta ha meia hora contem o config de ha meia hora. Sem isto, um
    "Gravar" nessa aba repoe o passado inteiro — ja aconteceu: a escrita das
    11:27:52 repôs byte a byte o config que a pagina tinha carregado antes das
    11:18:47, apagando a correcao do perfil pessoal. O cliente envia o `rev` que
    leu; se nao for o actual, recusa-se a gravar e devolve o estado actual.

    A definicao vive no `gestor`: se o painel, o CLI e os scripts calculassem
    impressoes diferentes, um `rev` de um nao valia no outro.
    """
    return gestor.rev(cfg)


def _conflito(rev_cliente: str, rev_actual: str) -> bool:
    """Ha conflito quando o cliente declara uma versao e ela ja nao e a actual.

    Sem `rev` (cliente antigo) nao ha conflito: grava-se e registra-se a
    ausencia, para que a proxima escrita sem versao fique visivel no log em vez
    de ser invisivel.
    """
    return bool(rev_cliente) and rev_cliente != rev_actual


def _idade_s(ts: Any) -> Optional[float]:
    """Segundos desde `ts` (ISO com ou sem offset), ou ``None`` se ilegivel."""
    if not ts:
        return None
    try:
        texto = str(ts).replace("Z", "+00:00")
        quando = datetime.fromisoformat(texto)
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - quando).total_seconds())
    except Exception:  # noqa: BLE001 - um ts estragado nao pode derrubar a rota
        return None


def _config_mtime_iso() -> str:
    """Hora da ultima escrita do config.json, em ISO-8601 UTC.

    Serve para datar o que o motivo pode descrever: um motivo registado ANTES
    desta escrita nao pode estar a falar da config que esta em vigor agora.
    """
    try:
        ts = gestor.ficheiro_config().stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------- sombra
#
# O ensaio A/B vive em DOIS sitios diferentes, e o painel so mostrava um deles
# (nenhum, na verdade): o interruptor e a validade da sonda estao no `config.json`
# (bloco `sombra`), mas os MODELOS DOS BRACOS estao no pre-registo selado em
# `aprendiz/sombra/pre-registo-*.json`. Quem procurava a configuracao do teste no
# painel encontrava um editor de JSON e nada mais — a informacao existia e nao
# estava onde se edita.


def _dir_sombra() -> Path:
    return gestor.directorio() / "aprendiz" / "sombra"


def _pre_registo_actual() -> tuple[Optional[dict[str, Any]], str]:
    """Pre-registo mais recente e o caminho, ou ``(None, "")``.

    Le o ficheiro mais recente por ordem lexicografica (`pre-registo-AAAAMMDDx`)
    e verifica o `sha256` que ele proprio carrega. Um selo que nao bate nao e um
    pre-registo: e um ficheiro que alguem editou a mao depois de os numeros
    terem sido fixados, e usa-lo seria decidir com uma margem escolhida depois
    de ver os dados.

    Devolve tambem o caminho para que a escrita saiba que nome tem de superar
    na ordenacao — um ficheiro novo com nome anterior nunca seria lido.
    """
    base = _dir_sombra()
    try:
        ficheiros = sorted(base.glob("pre-registo-*.json"))
    except OSError:
        return None, ""
    for caminho in reversed(ficheiros):
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(dados, dict) and _sombra.verificar(dados):
            return dados, str(caminho)
    return None, ""


def _providers_disponiveis(cfg: dict[str, Any]) -> list[str]:
    """Providers que o catalogo conhece — lista fechada para o select.

    Sai do catalogo real de todos os perfis, nunca de uma lista escrita a mao:
    um provider que nao serve nenhuma entrada nao pode ser escolhido para um
    braco, porque o braco tem de cair numa entrada existente para o middleware
    o poder servir.
    """
    vistos: set[str] = set()
    for perfil in (cfg.get("perfis") or {}).values():
        if not isinstance(perfil, dict):
            continue
        for entrada in perfil.get("catalogo") or []:
            if not isinstance(entrada, dict):
                continue
            prov = str(entrada.get("provider") or "").strip().lower()
            if prov:
                vistos.add(prov)
    return sorted(vistos)


def _sombra_estado(cfg: dict[str, Any]) -> dict[str, Any]:
    """Bloco `sombra` para o painel: interruptor, sonda, bracos e selo.

    `selo_valido` e o campo que interessa: diz se o pre-registo ainda e o que
    foi selado. Um `activo: true` com `selo_valido: false` e um ensaio que corre
    com numeros que ja nao sao os declarados, e o painel tem de o mostrar em vez
    de apresentar os bracos como se estivessem aprovados.
    """
    bloco = cfg.get("sombra") if isinstance(cfg.get("sombra"), dict) else {}
    registo, caminho = _pre_registo_actual()

    # O ficheiro mais recente pode estar corrompido: le-se na mesma para que o
    # painel distinga "nao ha pre-registo" de "ha um e o selo nao bate".
    recente: Optional[dict[str, Any]] = None
    caminho_recente = ""
    try:
        ficheiros = sorted(_dir_sombra().glob("pre-registo-*.json"))
        if ficheiros:
            caminho_recente = str(ficheiros[-1])
            dados = json.loads(ficheiros[-1].read_text(encoding="utf-8"))
            recente = dados if isinstance(dados, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    vida: dict[str, Any] = {}
    try:
        vida = json.loads((_dir_sombra() / "vida.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        vida = {}

    # Fonte dos numeros do ensaio: o selo valido manda; se nao houver, mostra-se
    # o recente (para o painel poder dizer que existe um e o selo nao bate).
    numeros: dict[str, Any] = registo or recente or {}

    return {
        # Ausente nao e `false` por acidente: e "nao decidido". O middleware so
        # corre o ensaio com `activo` verdadeiro (mesma regra do `gestor`).
        "activo": bool(bloco.get("activo", False)),
        "vida_ttl_h": bloco.get("vida_ttl_h"),
        "bracos": (
            {"a": registo.get("braco_a"), "b": registo.get("braco_b")}
            if registo else {"a": None, "b": None}
        ),
        # O provider nao se adivinha: ou o desenho o declara, ou o painel diz
        # em voz alta que esta inferido. Um slug so nao identifica a rota, e a
        # heuristica antiga (`/` ou `:` -> openrouter) mandava um slug gratuito
        # contra a credencial errada.
        "providers": {
            "a": _sombra.provider_do_braco(registo, "a") if registo else "",
            "b": _sombra.provider_do_braco(registo, "b") if registo else "",
        },
        "providers_declarados": bool(
            registo
            and _sombra.provider_do_braco(registo, "a")
            and _sombra.provider_do_braco(registo, "b")
        ),
        "providers_disponiveis": _providers_disponiveis(cfg),
        "selo_valido": bool(registo),
        "selo": (registo or {}).get("sha256"),
        "pre_registo": caminho,
        "pre_registo_recente": caminho_recente,
        "pre_registo_recente_selado": (
            bool(recente) and _sombra.verificar(recente)
        ) if recente else None,
        "ensaio": numeros.get("ensaio"),
        "p0": numeros.get("p0"),
        "margem": numeros.get("margem_nao_inferioridade"),
        "n_por_braco": numeros.get("n_por_braco"),
        "dias_estimados": numeros.get("dias_estimados"),
        "vida": {
            "medida_ts": vida.get("ts") or vida.get("medida_ts") or "",
            "bracos": {
                "a": vida.get("braco_a") or {},
                "b": vida.get("braco_b") or {},
            },
            "todos_vivos": vida.get("todos_vivos"),
            "sha_registo": vida.get("sha_registo"),
        },
    }


# Um motivo de configuracao sem trafego que o renove envelhece: ao fim de duas
# horas ja nao e estado, e uma afirmacao de ontem sobre o sistema de hoje.
MOTIVO_VALIDADE_S = 7200


def _decidir_rasto(
    cl: Any,
    rasto: Any,
    motivo: str,
    motivo_ts: str,
    cfg_mtime: str = "",
) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """Separa o estado actual da historia — devolve (valido, motivo, historico).

    O cartao mostrou "sem efeito — provider_indisponivel" e, ao lado,
    "leu alta em 1355 ms": a leitura era de outro classificador (deepseek-flash
    das 11:23) e o erro era da config actual (inception). Duas verdades de
    momentos diferentes na mesma linha leem-se como uma mentira.

    Regras:
      * a leitura so e evidencia se for do MESMO provider/modelo configurado
        agora, e posterior ao ultimo motivo registado;
      * senao, a leitura vai para `historico` com a idade e o classificador que
        a produziu — historia rotulada, nao estado;
      * o motivo de configuracao so aparece quando nao ha leitura valida a
        desmenti-lo (uma config corrigida nao fica a mostrar o erro antigo).
    """
    cl = cl if isinstance(cl, dict) else {}
    rasto = rasto if isinstance(rasto, dict) else None
    modo = str(cl.get("modo") or "")
    mesmo = bool(
        rasto
        and modo == "modelo"
        and str(rasto.get("provider") or "") == str(cl.get("provider") or "")
        and str(rasto.get("modelo") or "") == str(cl.get("modelo") or "")
    )
    if mesmo and rasto is not None:
        ts_rasto = str(rasto.get("ts") or "")
        # ISO-8601 com o mesmo formato compara-se como texto; sem motivo nao ha
        # nada a desmentir.
        depois_do_motivo = (not motivo_ts) or ts_rasto >= str(motivo_ts)
    else:
        depois_do_motivo = False

    valido = mesmo and depois_do_motivo
    if valido:
        return True, "", None

    # O motivo so pode descrever a config que o produziu. Duas condicoes o
    # desqualificam: ter sido registado antes da ultima escrita do config.json
    # (a config mudou, portanto o motivo ja nao fala dela) e ter mais de
    # MOTIVO_VALIDADE_S sem um turno que o renove. Nos dois casos vai para
    # historico datado — nunca desaparece, deixa de se apresentar como estado.
    motivo_actual = ""
    motivo_antigo: Optional[dict[str, Any]] = None
    if motivo:
        idade = _idade_s(motivo_ts) if motivo_ts else None
        antes_da_config = bool(cfg_mtime) and bool(motivo_ts) and str(motivo_ts) < str(cfg_mtime)
        expirado = idade is not None and idade > MOTIVO_VALIDADE_S
        if antes_da_config or expirado:
            motivo_antigo = {
                "motivo": str(motivo),
                "ts": str(motivo_ts or ""),
                "idade_s": idade,
                "porque": ("anterior à config actual" if antes_da_config
                           else "sem confirmação há mais de " + str(MOTIVO_VALIDADE_S // 3600) + " h"),
            }
        else:
            motivo_actual = str(motivo)

    historico: Optional[dict[str, Any]] = None
    if rasto is not None or motivo_antigo is not None:
        historico = {
            "lido": (rasto or {}).get("lido"),
            "ms": (rasto or {}).get("ms"),
            "provider": (rasto or {}).get("provider"),
            "modelo": (rasto or {}).get("modelo"),
            "ts": (rasto or {}).get("ts"),
            "idade_s": _idade_s((rasto or {}).get("ts")) if rasto is not None else None,
            # Porque nao conta como estado: o classificador e outro, ou a
            # leitura e anterior ao erro que a config actual produz.
            "motivo": (("outro classificador" if not mesmo else "anterior ao erro actual")
                       if rasto is not None else ""),
            "motivo_antigo": motivo_antigo,
        }
    return False, motivo_actual, historico


def _registar_escrita(*, rev_antes: str, rev_depois: str, rev_cliente: str, conflito: bool, origem: str) -> None:
    """Registo append-only de cada escrita no config: quem, quando, sobre o que.

    A escrita das 11:27:52 nao tinha rasto nenhum — nao se sabia sequer se
    fora o painel, o CLI ou o ciclo. Passa a saber-se. Nunca le valores do
    config: so impressoes digitais e tamanho.
    """
    try:
        pasta = gestor.directorio()
        pasta.mkdir(parents=True, exist_ok=True)
        linha = {
            "ts": datetime.now(ZoneInfo("Europe/Lisbon")).isoformat(timespec="seconds"),
            "rev_antes": rev_antes,
            "rev_depois": rev_depois,
            "rev_cliente": rev_cliente,
            "conflito": bool(conflito),
            "sem_rev": not bool(rev_cliente),
            "origem": origem[:200],
        }
        with (pasta / "escritas.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - o log nao pode impedir a escrita pedida
        pass



def _verdadeiro(valor: Any) -> bool:
    """Interpreta um booleano vindo do painel, sem o confundir com texto.

    ``bool("false")`` e ``True`` — um painel que envie a string ``"false"``
    ligaria o ensaio em vez de o desligar. Aqui so ``true``/``1``/``sim`` contam.
    """
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return valor != 0
    if isinstance(valor, str):
        return valor.strip().lower() in ("true", "1", "sim", "yes", "on")
    return False


def _corpo_objeto(corpo: Any, *, obrigatorio: bool = True) -> Optional[dict[str, Any]]:
    """Normaliza o corpo do pedido para ``dict`` — ou recusa com 400 e a razao.

    O ``fetchJSON`` do SDK passa o ``init`` tal e qual ao ``fetch()``: se o painel
    lhe entregar um objecto JS, o corpo chega ao servidor como a string
    ``"[object Object]"`` (``Content-Type: text/plain``) e o FastAPI responde 422
    ``Input should be a valid dictionary`` — um erro que nao diz nada a quem esta
    a editar a configuracao. Aceita-se por isso dict, string JSON e lista, e o
    resto e recusado com uma mensagem que identifica o que foi recebido.

    ``obrigatorio=False`` devolve ``None`` para corpo ausente (validar o que esta).
    """
    if isinstance(corpo, dict):
        return corpo
    if isinstance(corpo, bytes):
        try:
            corpo = corpo.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            if obrigatorio:
                raise HTTPException(
                    status_code=400,
                    detail=t("corpo nao e um objecto JSON (recebido bytes, nao e utf-8)"),
                )
            return None
    if isinstance(corpo, str):
        try:
            lido = json.loads(corpo)
        except (json.JSONDecodeError, ValueError):
            lido = None
        if isinstance(lido, dict):
            return lido
        if isinstance(lido, list) and lido:
            return {"catalogo": lido}
        if obrigatorio:
            raise HTTPException(
                status_code=400,
                detail=t("corpo nao e um objecto JSON (recebido {valor!r})", valor=corpo[:60]),
            )
        return None
    if isinstance(corpo, list) and corpo:
        return {"catalogo": corpo}
    if corpo is None and not obrigatorio:
        return None
    raise HTTPException(
        status_code=400,
        detail=t("corpo nao e um objecto JSON (recebido o tipo {tipo})", tipo=type(corpo).__name__),
    )


def _ler_ledger(limite: int, perfil: Optional[str]) -> tuple[list[dict[str, Any]], int]:
    """Ultimas ``limite`` linhas de evento ``resposta``, mais novas primeiro.

    Le de tras para a frente em blocos para nao carregar um ledger grande: o ficheiro
    cresce a cada turno e o painel abre muitas vezes. Devolve tambem o total de
    eventos ``resposta`` lidos nesta janela (o total real nao e contado linha a linha).
    """
    caminho = gestor.caminho_ledger()
    if not caminho.exists():
        return [], 0

    saida: list[dict[str, Any]] = []
    vistos = 0
    bloco = 64 * 1024
    with caminho.open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        resto = b""
        while pos > 0 and len(saida) < limite:
            passo = min(bloco, pos)
            pos -= passo
            fh.seek(pos)
            dados = fh.read(passo) + resto
            linhas = dados.split(b"\n")
            resto = linhas.pop(0)
            for cru in reversed(linhas):
                if not cru.strip():
                    continue
                try:
                    ev = json.loads(cru)
                except Exception:
                    continue
                if ev.get("evento") != "resposta":
                    continue
                if perfil and ev.get("perfil") != perfil:
                    continue
                vistos += 1
                if len(saida) < limite:
                    saida.append(
                        {
                            "ts": ev.get("ts"),
                            "perfil": ev.get("perfil"),
                            "sessao": ev.get("sessao"),
                            "superficie": ev.get("superficie"),
                            "provider": ev.get("provider"),
                            "modelo": ev.get("modelo"),
                            "respondido_por": ev.get("respondido_por"),
                            "acesso": ev.get("acesso"),
                            "nivel": ev.get("nivel"),
                            "origem": ev.get("origem"),
                            "finish_reason": ev.get("finish_reason"),
                            "tokens": ev.get("tokens"),
                        }
                    )
    return saida, vistos


def _resumo_por_perfil(
    por_perfil: int = 25, limite_linhas: int = 20000
) -> dict[str, dict[str, Any]]:
    """Por perfil: ultimo turno, contagem de origens e ultimo rasto do classificador.

    Uma so passagem do fim para o inicio, com orcamento de linhas. A contagem de
    origens e o que distingue "tem modelo definido" de "o modelo esta mesmo a
    decidir": um classificador ligado que falha cai na heuristica e o painel so
    o mostra se olhar para o ledger, nao para a config.
    """
    caminho = gestor.caminho_ledger()
    if not caminho.exists():
        return {}
    achados: dict[str, dict[str, Any]] = {}
    ja_contados: set[tuple[Any, str]] = set()
    bloco = 64 * 1024
    lidas = 0
    with caminho.open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        resto = b""
        while pos > 0 and lidas < limite_linhas:
            passo = min(bloco, pos)
            pos -= passo
            fh.seek(pos)
            dados = fh.read(passo) + resto
            linhas = dados.split(b"\n")
            resto = linhas.pop(0)
            for cru in reversed(linhas):
                if not cru.strip():
                    continue
                lidas += 1
                if lidas > limite_linhas:
                    break
                try:
                    ev = json.loads(cru)
                except Exception:
                    continue
                p = ev.get("perfil") or ""
                if not p:
                    continue
                reg = achados.setdefault(
                    p,
                    {
                        "ultimo": None,
                        "origens": {},
                        "respostas": 0,
                        "classificador": None,
                        "classificador_erro": "",
                        "classificador_motivo": "",
                        "classificador_motivo_ts": "",
                        "classif_leituras": 0,
                        "classif_falhas": 0,
                        "classif_ultima_falha": "",
                    },
                )
                # O rasto do classificador viaja na linha `resposta` desde a 3.31.0
                # (antes vivia nos eventos de troca/sem_troca). Le-se de tras para a
                # frente, logo a primeira ocorrencia que se ve e a mais recente.
                if reg["classificador"] is None and isinstance(ev.get("classificador"), dict):
                    c = ev["classificador"]
                    reg["classificador"] = {
                        "provider": c.get("provider"),
                        "modelo": c.get("modelo"),
                        "lido": c.get("lido"),
                        "ms": c.get("ms") if c.get("ms") is not None else ev.get("ms"),
                        "cache": c.get("cache"),
                        "tokens_saida": c.get("tokens_saida"),
                        # Transporte de decisoes: a confianca e a evidencia que o
                        # cartao mostra ao lado do nivel, e sem ela o leitor nao
                        # sabe se a leitura foi um empate ou uma escolha clara.
                        "via": c.get("via") or "",
                        "confianca": c.get("confianca"),
                        "custo_usd": c.get("custo_usd"),
                        "falhou": c.get("falhou") or "",
                        "ts": ev.get("ts"),
                    }
                if not reg["classificador_erro"] and ev.get("classificador_erro"):
                    reg["classificador_erro"] = str(ev["classificador_erro"])
                if not reg["classificador_motivo"] and ev.get("classificador_motivo"):
                    reg["classificador_motivo"] = str(ev["classificador_motivo"])
                    # Quando apareceu este motivo: sem a hora nao se sabe se a
                    # leitura que esta ao lado veio antes ou depois do erro.
                    reg["classificador_motivo_ts"] = str(ev.get("ts") or "")
                # Taxa do classificador: um erro isolado nao e o mesmo que um
                # modelo a falhar metade das vezes, e o mesmo `ts` aparece em
                # varios eventos do turno (troca, sem_troca, resposta) — conta-se
                # uma vez por timestamp.
                if isinstance(ev.get("classificador"), dict):
                    c_ev = ev["classificador"]
                    tipo_ev = "ok" if c_ev.get("lido") else ("falha" if c_ev.get("falhou") else "")
                    marca = (ev.get("ts"), tipo_ev)
                    if tipo_ev and marca not in ja_contados:
                        ja_contados.add(marca)
                        if tipo_ev == "ok":
                            reg["classif_leituras"] += 1
                        else:
                            reg["classif_falhas"] += 1
                            if not reg["classif_ultima_falha"]:
                                reg["classif_ultima_falha"] = ev.get("ts") or ""
                if ev.get("evento") != "resposta":
                    continue
                if reg["respostas"] >= por_perfil:
                    continue
                reg["respostas"] += 1
                origem = str(ev.get("origem") or "—")
                reg["origens"][origem] = reg["origens"].get(origem, 0) + 1
                if reg["ultimo"] is None:
                    reg["ultimo"] = {
                        "ts": ev.get("ts"),
                        "provider": ev.get("provider"),
                        "modelo": ev.get("modelo"),
                        "respondido_por": ev.get("respondido_por"),
                        "nivel": ev.get("nivel"),
                        "origem": ev.get("origem"),
                        "sessao": ev.get("sessao"),
                    }
    for reg in achados.values():
        reg["janela"] = reg["respostas"]
    return achados


# --------------------------------------------------------------------------- rotas


@router.get("/estado")
def estado() -> dict[str, Any]:
    cfg = _cfg()
    perfis = cfg.get("perfis") or {}
    resumos = _resumo_por_perfil()

    saida_perfis: dict[str, Any] = {}
    for nome, dados in perfis.items():
        if not isinstance(dados, dict):
            continue
        catalogo = []
        for entrada in dados.get("catalogo") or []:
            if not isinstance(entrada, dict):
                continue
            catalogo.append(
                {
                    "id": entrada.get("id"),
                    "provider": entrada.get("provider"),
                    "modelo": entrada.get("modelo"),
                    "complexidade": entrada.get("complexidade") or [],
                    "tarefas": entrada.get("tarefas") or [],
                    "acesso": entrada.get("acesso") or "api",
                }
            )
        pcfg = gestor.perfil_config(cfg, nome) or {}
        resumo = resumos.get(nome) or {}
        # Estado actual vs historia: uma leitura so conta como evidencia se for
        # do classificador que esta configurado AGORA e posterior ao ultimo erro.
        cl_efectivo = gestor.classificador_efectivo(cfg, nome)
        rasto_valido, motivo_efectivo, historico = _decidir_rasto(
            cl_efectivo,
            resumo.get("classificador"),
            resumo.get("classificador_motivo") or "",
            resumo.get("classificador_motivo_ts") or "",
            _config_mtime_iso(),
        )
        saida_perfis[nome] = {
            "activo": bool(dados.get("activo", True)),
            # Estrategia em vigor + de onde vem (`proprio`/`herdado`/`padrao`).
            # O cartao mostra os valores que estao a decidir, nao o delta cru:
            # um perfil que herda tem de mostrar a estrategia do global, senao
            # parece que nao tem nenhuma.
            "estrategia": gestor.estrategia_efectiva(cfg, nome),
            # Efectivo, nao o cru do perfil: um perfil que herda os limiares do
            # global tem de mostrar os que estao a decidir, nao uma lista vazia.
            "limiares": (pcfg.get("analise") or {}).get("limiares") or {},
            "tarefas": dados.get("tarefas") or {},
            # Superficies de sessao: o contexto (`cron`, `api_server`, `cli`)
            # manda sobre a tarefa e sobre a analise. Vai o efectivo (perfil +
            # herdado do global) e a origem de cada regra, senao o cartao
            # mostra `cron` e o utilizador procura-a no perfil sem a encontrar.
            "superficies": {
                k: {**v, "origem_regra": ("perfil" if k in (
                    (dados.get("analise") or {}).get("superficies") or {}) else "global")}
                for k, v in ((pcfg.get("analise") or {}).get("superficies") or {}).items()
            },
            "catalogo": catalogo,
            "ultimo": resumo.get("ultimo"),
            # O indicador do cartao: modelo (qual) ou heuristica, e a evidencia
            # de que este perfil esta mesmo a usar aquilo que declara.
            "analise": {
                "classificador": cl_efectivo,
                "origens": resumo.get("origens") or {},
                "respostas": resumo.get("respostas", 0),
                # `rasto` e so o que vale como estado: mesma config, mais
                # recente que o erro. A leitura de outro classificador (ou
                # anterior ao erro) vai em `historico`, com a idade.
                "rasto": (resumo.get("classificador") if rasto_valido else None),
                "rasto_valido": rasto_valido,
                "historico": historico,
                # O erro so acompanha o cartao quando a ULTIMA leitura falhou.
                # Um erro de ha meia hora, ja recuperado, e historia — mostra-lo
                # ao lado de um rasto bom faz o cartao mentir.
                "erro": (resumo.get("classificador_erro") or "")
                        if (rasto_valido and (resumo.get("classificador") or {}).get("falhou")) else "",
                # Config errada nao e aviso de avaria: o provider nao existe no
                # perfil e o classificador nao decide ate a config mudar. O
                # cartao mostra-o como facto, nao como ⚠ — e so enquanto nao
                # houver leitura valida que o desminta.
                "motivo": motivo_efectivo,
                "taxa": {
                    "leituras": resumo.get("classif_leituras", 0),
                    "falhas": resumo.get("classif_falhas", 0),
                    "ultima_falha": resumo.get("classif_ultima_falha", ""),
                },
            },
        }

    ledger = gestor.caminho_ledger()
    return {
        "versao_painel": _versao_painel(),
        # Identidade: versao do plugin (do plugin.yaml), autor e repositorio
        # publico. E o que o painel mostra no cabecalho.
        "plugin": _meta_plugin(),
        "activo": bool(cfg.get("activo", True)),
        "registar": bool(cfg.get("registar", True)),
        # Estrategia global e as listas fechadas. O painel constroi os `select`
        # a partir daqui: se o backend acrescentar um valor, o painel mostra-o
        # sem alteracao, e nunca aparece um valor que a validacao recusaria.
        "estrategia_global": gestor.estrategia_global(cfg),
        # Ensaio A/B em sombra: interruptor e validade da sonda (config) e os
        # MODELOS DOS BRACOS (pre-registo selado). E o que faltava no painel: a
        # configuracao do teste existia em dois ficheiros e nao aparecia em
        # nenhum formulario.
        "sombra": _sombra_estado(cfg),
        # Lingua do painel, lida do config. O painel aplica-a antes de desenhar
        # o primeiro componente — sem isto abria em ingles e so mudava depois
        # de um segundo render, o que se via como um piscar de portugues.
        "idioma": gestor.idioma_efectivo(cfg),
        "opcoes": {
            "estrategia": gestor.opcoes_estrategia(),
            # Superficies que ja foram vistas neste ambiente. Nao e a lista
            # aceite (qualquer nome vale, um provider novo traz superficies
            # novas) — e a lista que evita gralhas obvias no painel.
            "superficies": list(gestor.SUPERFICIES_CONHECIDAS),
            "niveis": list(gestor.NIVEIS),
        },
        "rev": _rev(cfg),
        # Erros da leitura deste config. Vazio no caso normal. Nao-vazio quer
        # dizer que o ficheiro no disco tem algo que esta versao nao conhece e
        # que o painel esta a mostrar o valor normalizado, nao o do disco — o
        # utilizador tem de o ver antes de gravar por cima.
        "erros_config": list(_ERROS_CONFIG),
        "perfis": saida_perfis,
        "perfil_servido": _perfil_servido(),
        "ledger": {
            "caminho": str(ledger),
            "existe": ledger.exists(),
            "bytes": ledger.stat().st_size if ledger.exists() else 0,
            "mtime": ledger.stat().st_mtime if ledger.exists() else None,
        },
    }


@router.get("/log")
def log(
    limite: int = Query(LIMITE_LOG_OMISSAO, ge=1, le=LIMITE_LOG_MAX),
    perfil: Optional[str] = Query(None),
) -> dict[str, Any]:
    linhas, vistos = _ler_ledger(limite, perfil)
    return {"linhas": linhas, "pedidas": limite, "vistas": vistos, "perfil": perfil}


@router.post("/toggle")
def toggle(corpo: Any = Body(default=None)) -> JSONResponse:
    """Liga/desliga o plugin. ``ambito='global'`` (por omissao) ou ``'perfil'`` + ``perfil``."""
    corpo = _corpo_objeto(corpo)
    activo = corpo.get("activo")
    if not isinstance(activo, bool):
        raise HTTPException(status_code=400, detail=t("'activo' tem de ser booleano"))
    ambito = (corpo.get("ambito") or "global").strip().lower()
    nome = (corpo.get("perfil") or "").strip()
    if ambito not in ("global", "perfil"):
        raise HTTPException(status_code=400, detail=t("'ambito' tem de ser 'global' ou 'perfil'"))

    def mutar(cfg: dict[str, Any]) -> dict[str, Any]:
        """Muda so o interruptor — o resto do documento vem fresco do disco."""
        if ambito == "global":
            cfg["activo"] = activo
            return cfg
        perfis = cfg.get("perfis") or {}
        if nome not in perfis:
            raise HTTPException(status_code=404, detail=t("perfil {nome!r} nao existe na configuracao", nome=nome))
        perfis[nome]["activo"] = activo
        return cfg

    alvo = "global" if ambito == "global" else nome
    try:
        gestor.alterar(mutar, rev_esperado=str(corpo.get("rev") or "") or None)
    except gestor.Conflito as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 500 com a razao real, nao um OK mentiroso
        raise HTTPException(status_code=500, detail=t("nao gravou: {erro}", erro=f"{type(exc).__name__}: {exc}")) from exc

    # Releitura independente: o estado devolvido e o que ficou no disco, nao o que se quis escrever.
    confirmado = _cfg()
    if ambito == "global":
        lido = bool(confirmado.get("activo", True))
    else:
        lido = bool(((confirmado.get("perfis") or {}).get(alvo) or {}).get("activo", True))
    if lido != activo:
        raise HTTPException(
            status_code=500,
            detail=t("gravacao nao confirmada: pedido {pedido}, lido {lido}", pedido=activo, lido=lido),
        )
    return JSONResponse({"ok": True, "ambito": ambito, "alvo": alvo, "activo": lido})


@router.post("/idioma")
def idioma_post(corpo: Any = Body(default=None)) -> JSONResponse:
    """Muda a lingua do painel. Lista fechada (`en`|`pt`), escrita por chave.

    A lingua e uma preferencia de apresentacao, mas vive no config — e o unico
    sitio que o painel, o CLI e as sessoes do agente leem da mesma maneira. O
    `rev` e opcional: quem muda a lingua esta a mudar SO a lingua, e um conflito
    por a config ter mudado noutra chave so lhe pedia para repetir o gesto.
    """
    corpo = _corpo_objeto(corpo)
    codigo, erros = gestor.normalizar_idioma(corpo.get("idioma"))
    if erros or not codigo:
        raise HTTPException(status_code=400,
                            detail="; ".join(erros) or t("'idioma' ausente"))

    def mutar(cfg: dict[str, Any]) -> dict[str, Any]:
        cfg["idioma"] = codigo
        return cfg

    try:
        # Sem `rev`: o `alterar` ja parte do estado fresco do disco e este
        # mutador toca UMA chave, por isso nao ha nada para repor. Passar o rev
        # que a pagina tem fazia o gesto falhar com 409 quando a config mudou
        # noutra chave qualquer — medido no teste: uma mudanca de estrategia
        # noutra aba impedia o utilizador de mudar a lingua, e ele via 409 a
        # pedir para repetir um gesto que nao tem nada a ver.
        gestor.alterar(mutar)
    except gestor.Conflito as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 500 com a razao real, nao um OK mentiroso
        raise HTTPException(status_code=500,
                            detail=t("nao gravou: {erro}", erro=f"{type(exc).__name__}: {exc}")) from exc

    # Releitura independente: vale o que ficou no disco, nao o que se pediu.
    lido = gestor.idioma_efectivo(_cfg())
    if lido != codigo:
        raise HTTPException(status_code=500,
                            detail=t("gravacao nao confirmada: pedido {pedido}, lido {lido}", pedido=codigo, lido=lido))
    return JSONResponse({"ok": True, "idioma": lido})


@router.post("/estrategia")
def estrategia_post(corpo: Any = Body(default=None)) -> JSONResponse:
    """Muda a estrategia por `select` — valores fechados, um campo de cada vez.

    Nao reescreve a config inteira: le, altera so o bloco, valida e grava pelo
    `gestor` (backup, validacao e `os.replace`). E o que evita o acidente que o
    editor de JSON ja provocou: um painel que manda a config toda pode repor
    tudo o que outra sessao escreveu entretanto.

    `herdar=true` (com `ambito='perfil'`) apaga o bloco do perfil: a ausencia da
    chave e o unico gesto que exprime "volta a herdar" — sem ele, o perfil
    ficava preso ao valor do dia em que o escolheu.
    """
    corpo = _corpo_objeto(corpo)
    ambito = str(corpo.get("ambito") or "global").strip().lower()
    herdar = bool(corpo.get("herdar"))
    rev = str(corpo.get("rev") or "")
    campos = {k: corpo[k] for k in ("modo", "por") if k in corpo}
    if not herdar and not campos:
        raise HTTPException(status_code=400, detail=t("indique 'modo' e/ou 'por'"))
    nome = str(corpo.get("perfil") or "").strip()
    if ambito not in ("global", "perfil"):
        raise HTTPException(status_code=400, detail=t("'ambito' tem de ser 'global' ou 'perfil'"))

    def mutar(cfg: dict[str, Any]) -> dict[str, Any]:
        """Aplica o delta da estrategia sobre a config que esta no disco agora."""
        if ambito == "global":
            if herdar:
                cfg.pop("estrategia", None)
            else:
                est = gestor.estrategia_global(cfg)
                est.pop("origem", None)
                est.update(campos)
                cfg["estrategia"] = est
            return cfg
        perfis = cfg.get("perfis") or {}
        if nome not in perfis:
            raise HTTPException(status_code=404,
                                detail=t("perfil {nome!r} nao existe na configuracao", nome=nome))
        perfil = perfis[nome]
        if herdar:
            perfil.pop("estrategia", None)
            perfil.pop("rodizio", None)
        else:
            # Funde com o que ESTE perfil ja usa (herdado incluido) para que
            # mudar so a rotacao nao obrigue a repetir o modo; o `validar`
            # reduz depois a delta e o que e igual ao global nao vira copia.
            est = gestor.estrategia_efectiva(cfg, nome)
            est.pop("origem", None)
            est.update(campos)
            perfil["estrategia"] = est
            perfil.pop("rodizio", None)
        return cfg

    alvo = "global" if ambito == "global" else nome
    try:
        gestor.alterar(mutar, rev_esperado=rev or None)
    except gestor.Conflito:
        raise HTTPException(
            status_code=409,
            detail=t("a configuração mudou desde que este painel leu o estado"),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        # Erro de validacao (valor fora da lista, config inconsistente) e 400
        # com a razao, nao 500: o pedido e que estava errado, o servidor nao.
        raise HTTPException(status_code=400, detail=t("nao gravou: {erro}", erro=exc)) from exc

    # Releitura independente: o que se devolve e o que ficou no disco.
    confirmado = _cfg()
    if ambito == "global":
        lido = gestor.estrategia_global(confirmado)
    else:
        lido = gestor.estrategia_efectiva(confirmado, alvo)
    return JSONResponse({"ok": True, "ambito": ambito, "alvo": alvo,
                         "estrategia": lido, "rev": _rev(confirmado)})


@router.post("/superficies")
def superficies_post(corpo: Any = Body(default=None)) -> JSONResponse:
    """Liga uma superficie de sessao a uma rota, por perfil ou no global.

    A superficie (`cron`, `api_server`, `cli`, ...) e o contexto em que a sessao
    corre: e o unico critério que o core entrega de forma deterministica, e por
    isso ganha a tarefa e a analise. Sem esta regra, uma sessao de `cron` e
    decidida pela complexidade do texto — e o ledger mostra que 59% das
    execucoes `cron` caiam em `alta` e no modelo pago.

    `alvo` aceita as tres formas do contrato, e o `gestor` e que valida (uma
    entrada do catalogo, um `nivel`, ou um par `provider/modelo`):

    * ``entrada:<id>``  — preferida: `acesso` e `esforco` vem da entrada;
    * ``nivel:<leve|media|alta>``;
    * ``provider/modelo``;
    * ``""``            — apaga a regra (volta ao global, se houver);
    * ``nunca``         — regra `{activo: false}`: anula a herdada neste perfil.

    Grava so este ramo (le-altera-valida-grava), com `rev` para abortar (409)
    uma escolha feita sobre um estado que ja mudou.
    """
    corpo = _corpo_objeto(corpo)
    ambito = str(corpo.get("ambito") or "perfil").strip().lower()
    superficie = str(corpo.get("superficie") or "").strip().lower()
    alvo = str(corpo.get("alvo") or "").strip()
    if not superficie:
        raise HTTPException(status_code=400, detail=t("indique 'superficie'"))
    if ambito not in ("global", "perfil"):
        raise HTTPException(status_code=400, detail=t("'ambito' tem de ser 'global' ou 'perfil'"))

    cfg = _cfg()
    rev = str(corpo.get("rev") or "")
    if rev and _conflito(rev, _rev(cfg)):
        raise HTTPException(
            status_code=409,
            detail=t("a configuração mudou desde que este painel leu o estado"),
        )

    if alvo == "":
        regra: dict[str, Any] | None = None
    elif alvo.lower() in ("nunca", "off", "desligado"):
        regra = {"activo": False}
    elif alvo.startswith("entrada:"):
        regra = {"entrada": alvo.split(":", 1)[1].strip()}
    elif alvo.startswith("nivel:"):
        regra = {"nivel": alvo.split(":", 1)[1].strip().lower()}
    elif "/" in alvo:
        prov, mod = alvo.split("/", 1)
        regra = {"provider": prov.strip(), "modelo": mod.strip()}
    else:
        raise HTTPException(
            status_code=400,
            detail=t("'alvo' tem de ser entrada:<id>, nivel:<nivel>, provider/modelo, 'nunca' ou vazio (apagar)"),
        )

    nome = "global" if ambito == "global" else str(corpo.get("perfil") or "").strip()

    def mutar(cfg: dict[str, Any]) -> dict[str, Any]:
        """Muda so este ramo de superficies sobre a config fresca do disco."""
        if ambito == "global":
            destino = cfg.setdefault("analise", {}).setdefault("superficies", {})
        else:
            perfis = cfg.get("perfis") or {}
            if nome not in perfis:
                raise HTTPException(status_code=404,
                                    detail=t("perfil {nome!r} nao existe na configuracao", nome=nome))
            destino = (perfis[nome].setdefault("analise", {})
                       .setdefault("superficies", {}))
        if regra is None:
            destino.pop(superficie, None)
        else:
            destino[superficie] = regra
        return cfg

    try:
        gestor.alterar(mutar, rev_esperado=rev or None)
    except gestor.Conflito:
        raise HTTPException(
            status_code=409,
            detail=t("a configuração mudou desde que este painel leu o estado"),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=t("nao gravou: {erro}", erro=exc)) from exc

    # Releitura independente: devolve-se o que ficou no disco, nao o que se quis.
    # Le o bloco **efectivo** (global + delta do perfil), nao `regra_de_superficie`:
    # essa resolve a rota e devolve `None` para uma regra anulada, o que
    # confundiria "nao tem regra" com "anulada neste perfil".
    confirmado = _cfg()
    if ambito == "global":
        bloco = ((confirmado.get("analise") or {}).get("superficies") or {})
    else:
        bloco = (((gestor.perfil_config(confirmado, nome) or {}).get("analise") or {})
                 .get("superficies") or {})
    lido = dict(bloco.get(superficie) or {})
    return JSONResponse({
        "ok": (bool(lido) if regra is not None else not lido),
        "ambito": ambito, "perfil": None if ambito == "global" else nome,
        "superficie": superficie, "regra": lido,
        "rev": _rev(confirmado),
    })


@router.post("/sombra")
def sombra_post(corpo: Any = Body(default=None)) -> JSONResponse:
    """Liga/desliga o ensaio em sombra e fixa a validade da sonda de vida.

    Escreve so o bloco `sombra` do config — o mesmo que o CLI
    (`vmm sombra on|off`) e que o middleware le. Os MODELOS dos bracos nao se
    mudam aqui: vivem no pre-registo selado, e re-sela-lo e outro gesto (ver
    `POST /sombra/bracos`), porque mudar os bracos a meio invalida a comparacao
    que ja foi acumulada.
    """
    corpo = _corpo_objeto(corpo)
    rev = str(corpo.get("rev") or "")
    tem_activo = "activo" in corpo
    tem_ttl = "vida_ttl_h" in corpo
    if not tem_activo and not tem_ttl:
        raise HTTPException(status_code=400, detail=t("indique 'activo' e/ou 'vida_ttl_h'"))

    def mutar(cfg: dict[str, Any]) -> dict[str, Any]:
        bloco = cfg.get("sombra")
        bloco = dict(bloco) if isinstance(bloco, dict) else {}
        if tem_activo:
            bloco["activo"] = bool(corpo.get("activo"))
        if tem_ttl:
            bloco["vida_ttl_h"] = corpo.get("vida_ttl_h")
        cfg["sombra"] = bloco
        return cfg

    try:
        gestor.alterar(mutar, rev_esperado=rev or None)
    except gestor.Conflito:
        raise HTTPException(
            status_code=409,
            detail=t("a configuração mudou desde que este painel leu o estado"),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=t("nao gravou: {erro}", erro=exc)) from exc

    confirmado = _cfg()
    return JSONResponse({"ok": True, "sombra": _sombra_estado(confirmado),
                         "rev": _rev(confirmado)})


def _proximo_nome_pre_registo() -> str:
    """Nome do proximo pre-registo, garantidamente posterior na ordenacao.

    A leitura usa ``sorted(glob(...))[-1]``, logo o nome nao e decorativo: um
    ficheiro novo com nome que ordene ANTES do actual nunca seria lido e o
    painel gravaria com sucesso um ensaio que ninguem corre. Os nomes seguem
    ``pre-registo-AAAAMMDD`` e depois letras (``...b``); a proxima letra livre
    e a escolha. Uma data com sufixo vazio conta como anterior a ``a``.
    """
    hoje = datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%Y%m%d")
    prefixo = f"pre-registo-{hoje}"
    usado = ""
    try:
        for caminho in _dir_sombra().glob("pre-registo-*.json"):
            nome = caminho.stem
            if not nome.startswith(prefixo):
                continue
            sufixo = nome[len(prefixo):]
            if sufixo > usado:
                usado = sufixo
    except OSError:
        usado = ""
    if not usado:
        return f"{prefixo}a"
    # Incrementa a ultima letra; `z` passa a `za` (mantem a ordenacao).
    ultima = usado[-1]
    if ultima < "z":
        return f"{prefixo}{usado[:-1]}{chr(ord(ultima) + 1)}"
    return f"{prefixo}{usado}a"


@router.post("/sombra/bracos")
def sombra_bracos_post(corpo: Any = Body(default=None)) -> JSONResponse:
    """Re-sela o pre-registo com outros bracos (modelos) para o ensaio A/B.

    Este gesto reabre o ensaio: os numeros que ja estavam selados deixam de
    valer, e os turnos recolhidos com os bracos antigos nao sao comparaveis com
    os novos. Por isso exige ``confirmar_reabertura: true`` — sem ele recusa,
    em vez de apagar em silencio o desenho que o dono aprovou.

    ``p0``, ``design_effect`` e ``chamadas_por_dia`` sao herdados do pre-registo
    actual quando nao vierem no corpo: sao valores MEDIDOS (48582 chamadas de
    ferramenta), e reescreve-los a mao mudaria o tamanho da amostra sem que
    ninguem tivesse medido nada.
    """
    corpo = _corpo_objeto(corpo)
    if not _verdadeiro(corpo.get("confirmar_reabertura")):
        raise HTTPException(
            status_code=409,
            detail=t("reabrir o ensaio invalida a amostra ja recolhida; reenvie com 'confirmar_reabertura: true'"),
        )

    actual, caminho_actual = _pre_registo_actual()
    if actual is None:
        actual = {}

    braco_a = str(corpo.get("braco_a") or "").strip()
    braco_b = str(corpo.get("braco_b") or "").strip()
    if not braco_a or not braco_b:
        raise HTTPException(status_code=400,
                            detail=t("indique 'braco_a' (pago) e 'braco_b' (gratuito)"))
    if braco_a == braco_b:
        raise HTTPException(status_code=400,
                            detail=t("os dois bracos nao podem ser o mesmo modelo"))

    # Um slug so nao identifica a rota. O provider entra no selo: ou vem dito,
    # ou sai do catalogo por consulta exacta (zero ou duas entradas recusam em
    # vez de escolher uma em silencio). O que nao se faz e adivinhar pelo nome
    # — foi assim que um slug gratuito foi sondado contra a credencial errada.
    cfg, _ = gestor.carregar()
    catalogo: dict[str, set[str]] = {}
    for perfil in (cfg.get("perfis") or {}).values():
        if not isinstance(perfil, dict):
            continue
        for entrada in perfil.get("catalogo") or []:
            if not isinstance(entrada, dict):
                continue
            modelo_e = str(entrada.get("modelo") or "").strip().lower()
            prov_e = str(entrada.get("provider") or "").strip().lower()
            if modelo_e and prov_e:
                catalogo.setdefault(modelo_e, set()).add(prov_e)
    disponiveis = _providers_disponiveis(cfg)

    def _provider(campo_modelo: str, campo_provider: str,
                  modelo: str) -> tuple[str, str]:
        declarado = str(corpo.get(campo_provider) or "").strip().lower()
        candidatos = sorted(catalogo.get(modelo.strip().lower()) or set())
        if declarado:
            if declarado not in disponiveis:
                raise HTTPException(
                    status_code=400,
                    detail=t("'{campo_provider}' = '{declarado}' nao serve nenhuma entrada do catalogo; disponiveis: {disponiveis}",
                                campo_provider=campo_provider, declarado=declarado,
                                disponiveis=disponiveis or ['(nenhum)']))
            if declarado not in candidatos:
                raise HTTPException(
                    status_code=400,
                    detail=t("'{campo_provider}': o par {declarado}/{modelo} nao existe no catalogo{sufixo}",
                                campo_provider=campo_provider, declarado=declarado, modelo=modelo,
                                sufixo=(f" (esse modelo esta em {candidatos})" if candidatos else "")))
            return declarado, "declarado"
        if len(candidatos) == 1:
            return candidatos[0], "catalogo"
        if not candidatos:
            raise HTTPException(
                status_code=400,
                detail=t("'{campo_modelo}': o modelo '{modelo}' nao esta em nenhum catalogo; sem entrada o middleware nao o pode servir",
                        campo_modelo=campo_modelo, modelo=modelo))
        raise HTTPException(
            status_code=400,
            detail=t("'{campo_modelo}': '{modelo}' existe em {candidatos}; indique '{campo_provider}' para desempatar",
                    campo_modelo=campo_modelo, modelo=modelo, candidatos=candidatos,
                    campo_provider=campo_provider))

    provider_a, origem_a = _provider("braco_a", "braco_a_provider", braco_a)
    provider_b, origem_b = _provider("braco_b", "braco_b_provider", braco_b)

    def num(campo: str, omissao: Any) -> Any:
        valor = corpo.get(campo, omissao)
        try:
            return float(valor)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=t("'{campo}' tem de ser um numero", campo=campo)) from None

    p0 = num("p0", actual.get("p0", 0.0463))
    margem = num("margem", actual.get("margem_nao_inferioridade", 0.01))
    deff = num("design_effect", actual.get("design_effect", 3.08))
    por_dia = num("chamadas_por_dia", actual.get("chamadas_por_dia", 16194.0))
    if not (0.0 <= p0 <= 1.0):
        raise HTTPException(status_code=400, detail=t("'p0' tem de estar em [0, 1]"))
    if not (0.0 < margem <= 1.0):
        raise HTTPException(status_code=400,
                            detail=t("'margem' tem de estar em ]0, 1]"))
    if deff <= 0:
        raise HTTPException(status_code=400,
                            detail=t("'design_effect' tem de ser positivo"))

    registo = _sombra.desenhar(
        caro=braco_a,
        barato=braco_b,
        provider_caro=provider_a,
        provider_barato=provider_b,
        p0=p0,
        margem=margem,
        deff=deff,
        chamadas_por_dia=por_dia,
        medida=str(corpo.get("medida") or actual.get("medida_primaria")
                   or "taxa de falha por chamada de ferramenta medida por exit_code"),
        nota=str(corpo.get("nota") or (
            f"re-selado pelo painel; substitui {Path(caminho_actual).name}"
            if caminho_actual else "selado pelo painel")),
    )
    if not _sombra.verificar(registo):
        raise HTTPException(status_code=500,
                            detail=t("o pre-registo nao ficou selado; nada gravado"))

    destino = _dir_sombra() / f"{_proximo_nome_pre_registo()}.json"
    escrito = _sombra.escrever(registo, destino)

    # Releitura independente: o que se devolve e o que ficou no disco.
    confirmado = _cfg()
    estado = _sombra_estado(confirmado)
    return JSONResponse({
        "ok": True,
        "pre_registo": escrito,
        "anterior": caminho_actual or None,
        "sombra": estado,
        # De onde veio o provider de cada braco: dito no corpo ou lido do
        # catalogo. Nunca "adivinhado pelo nome do modelo".
        "providers_origem": {"a": origem_a, "b": origem_b},
        "rev": _rev(confirmado),
    })


@router.get("/perfis")
def perfis() -> dict[str, Any]:
    cfg = _cfg()
    return {"perfis": sorted((cfg.get("perfis") or {}).keys())}


# --------------------------------------------------------------------------- config ---------------------------------------------------------------------------

@router.get("/config")
def config_get() -> dict[str, Any]:
    """Config completa (normalizada) **e a versao em que foi lida**.

    O envelope `{"config": ..., "rev": ...}` e o que permite ao painel gravar
    sem repor o passado: o editor devolve o `rev` que recebeu aqui.
    """
    cfg, _ = gestor.carregar()
    return {"config": cfg, "rev": _rev(cfg)}

@router.post("/config")
def config_post(request: Request, corpo: Any = Body(default=None)) -> JSONResponse:
    """Substitui a config toda via gestor.gravar — mesma semantica que `vmm` CLI.

    Aceita dois corpos:

    * ``{"config": {...}, "rev": "<rev lido>"}`` — o painel; se o `rev` nao for
      o actual responde **409** e nao grava nada (uma aba antiga nao apaga o
      trabalho das outras);
    * o config cru — CLI e clientes antigos; grava, mas fica registado em
      `escritas.log` como `sem_rev`, porque uma escrita sem versao e
      precisamente a que nao se consegue explicar depois.
    """
    corpo = _corpo_objeto(corpo)
    if not corpo:
        raise HTTPException(status_code=400, detail=t("JSON inválido: não é um objecto"))
    envelope = isinstance(corpo.get("config"), dict)
    novo = corpo["config"] if envelope else corpo
    rev_cliente = str(corpo.get("rev") or "") if envelope else ""
    if not (novo.get("perfis") or {}):
        raise HTTPException(
            status_code=400,
            detail=t("config sem nenhum perfil: recusado (o plugin ficaria sem rotas)"),
        )
    if envelope and not rev_cliente:
        # Documento inteiro sem versao de origem e uma escrita as cegas: reporia
        # o passado sobre o que outra sessao escreveu entretanto. O painel manda
        # sempre o `rev` que leu; quem escreve por API tem de o mandar tambem.
        raise HTTPException(
            status_code=400,
            detail=t("falta 'rev': substituir a config inteira exige a versão que foi lida (POST /config devolve-a em 'rev')"),
        )
    actual, _ = gestor.carregar()
    rev_actual = _rev(actual)
    try:
        origem = "%s %s" % (request.client.host if request and request.client else "-",
                            (request.headers.get("user-agent") if request else "") or "-")
    except Exception:  # noqa: BLE001
        origem = "-"
    if _conflito(rev_cliente, rev_actual):
        # Nao grava: devolve o estado actual para o cliente recarregar o editor.
        _registar_escrita(
            rev_antes=rev_actual, rev_depois="", rev_cliente=rev_cliente,
            conflito=True, origem=origem,
        )
        return JSONResponse(
            status_code=409,
            content={
                "erro": "config desactualizado: o editor foi aberto noutra versao",
                "rev": rev_actual,
                "config": actual,
            },
        )
    try:
        # `rev_esperado` repete a verificacao DENTRO da trava: entre a leitura
        # acima e a escrita ha uma janela, e e nela que duas escritas simultaneas
        # perdiam uma delas. Aqui a segunda leva `Conflito` em vez de ganhar.
        gestor.gravar(novo, rev_esperado=rev_actual if rev_cliente else None)
    except gestor.Conflito as exc:
        _registar_escrita(
            rev_antes=rev_actual, rev_depois="", rev_cliente=rev_cliente,
            conflito=True, origem=origem,
        )
        return JSONResponse(
            status_code=409,
            content={"erro": "config mudou durante a gravação", "rev": exc.rev_actual},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    cfg, _ = gestor.carregar()
    rev_novo = _rev(cfg)
    _registar_escrita(
        rev_antes=rev_actual, rev_depois=rev_novo, rev_cliente=rev_cliente,
        conflito=False, origem=origem,
    )
    return JSONResponse(
        status_code=200,
        content={"ok": True, "rev": rev_novo, "config": cfg, "sem_rev": not bool(rev_cliente)},
    )


@router.get("/circuit-breaker")
def circuit_breaker_get(perfil: str = Query(default="")) -> dict[str, Any]:
    """Config do circuit breaker efetiva para um perfil (com heranca do global)."""
    cfg, _ = gestor.carregar()
    if perfil:
        pcfg = gestor.perfil_config(cfg, perfil)
        if pcfg:
            analise = pcfg.get("analise") or {}
            cb = analise.get("circuit_breaker") or {}
            # Mostra a origem de cada valor
            global_analise = cfg.get("analise") or {}
            global_cb = global_analise.get("circuit_breaker") or {}
            result = {}
            for key in ["ativo", "limite_falhas", "janela_minutos", "multiplicador_cooldown"]:
                result[key] = {
                    "valor": cb.get(key, global_cb.get(key, gestor.CIRCUIT_BREAKER_PADRAO[key])),
                    "origem": "perfil" if key in cb else ("global" if key in global_cb else "padrao")
                }
            return {"perfil": perfil, "circuit_breaker": result}
    # Se nao tem perfil ou perfil nao gerido, devolve o global
    global_analise = cfg.get("analise") or {}
    global_cb = global_analise.get("circuit_breaker") or {}
    result = {}
    for key in ["ativo", "limite_falhas", "janela_minutos", "multiplicador_cooldown"]:
        result[key] = {
            "valor": global_cb.get(key, gestor.CIRCUIT_BREAKER_PADRAO[key]),
            "origem": "global" if key in global_cb else "padrao"
        }
    return {"perfil": "global", "circuit_breaker": result}


@router.post("/circuit-breaker")
async def circuit_breaker_post(request: Request, corpo: Any = Body(default=None)) -> JSONResponse:
    """Atualiza o bloco circuit_breaker (global ou de um perfil).

    Corpo esperado:
    - `perfil` (opcional): string. Se ausente ou vazio, edita o global.
    - `circuit_breaker`: object com as chaves a atualizar (todas opcionais).
    - `rev`: string (obrigatorio se edita config completa via envelope, mas aqui
      fazemos merge parcial entao usamos o rev atual da config).
    """
    corpo = _corpo_objeto(corpo)
    if not corpo:
        raise HTTPException(status_code=400, detail=t("JSON invalido: nao e um objecto"))
    
    perfil = str(corpo.get("perfil") or "").strip()
    cb_novo = corpo.get("circuit_breaker")
    if not isinstance(cb_novo, dict):
        raise HTTPException(status_code=400, detail=t("campo 'circuit_breaker' deve ser um objecto"))
    
    # Valida chaves
    chaves_validas = {"ativo", "limite_falhas", "janela_minutos", "multiplicador_cooldown"}
    chaves_invalidas = set(cb_novo.keys()) - chaves_validas
    if chaves_invalidas:
        raise HTTPException(status_code=400, detail=t("chaves invalidas em circuit_breaker: {chaves}", chaves=sorted(chaves_invalidas)))
    
    # Carrega config atual
    cfg, _ = gestor.carregar()
    rev_atual = _rev(cfg)
    
    if perfil:
        # Edita perfil
        perfis = cfg.get("perfis") or {}
        if perfil not in perfis:
            raise HTTPException(status_code=404, detail=t("perfil {nome!r} nao existe", nome=perfil))
        pcfg = perfis[perfil]
        analise = pcfg.get("analise")
        if not isinstance(analise, dict):
            analise = {}
        cb_atual = analise.get("circuit_breaker")
        if not isinstance(cb_atual, dict):
            cb_atual = {}
        # Merge: so atualiza chaves fornecidas
        cb_atual.update(cb_novo)
        analise["circuit_breaker"] = cb_atual
        pcfg["analise"] = analise
        perfis[perfil] = pcfg
        cfg["perfis"] = perfis
    else:
        # Edita global
        global_analise = cfg.get("analise") or {}
        cb_atual = global_analise.get("circuit_breaker")
        if not isinstance(cb_atual, dict):
            cb_atual = {}
        cb_atual.update(cb_novo)
        global_analise["circuit_breaker"] = cb_atual
        cfg["analise"] = global_analise
    
    # Grava via gestor.gravar (validacao + backup + atomic)
    try:
        origem = "%s %s" % (request.client.host if request and request.client else "-",
                            (request.headers.get("user-agent") if request else "") or "-")
    except Exception:
        origem = "-"
    try:
        gestor.gravar(cfg, rev_esperado=rev_atual)
    except gestor.Conflito as exc:
        return JSONResponse(
            status_code=409,
            content={"erro": "config mudou durante a gravacao", "rev": exc.rev_actual},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    
    cfg_novo, _ = gestor.carregar()
    rev_novo = _rev(cfg_novo)
    _registar_escrita(
        rev_antes=rev_atual, rev_depois=rev_novo, rev_cliente=rev_atual,
        conflito=False, origem=origem,
    )
    return JSONResponse(
        status_code=200,
        content={"ok": True, "rev": rev_novo, "config": cfg_novo},
    )

@router.post("/validar")
def validar_post(corpo: Any = Body(default=None)) -> dict[str, Any]:
    """Valida a config sem a escrever; devolve o config normalizado e uma lista de erros.

    Corpo ausente valida o que esta gravado; corpo presente e ilegivel e recusado
    com 400 — devolver 200 nesse caso diria "valido" sobre um texto que o painel
    nem conseguiu ler.
    """
    if corpo is None:
        cfg, erros = gestor.carregar()
        return {"config": cfg, "erros": erros}
    cfg, erros = gestor.validar(_corpo_objeto(corpo, obrigatorio=True))
    if not (cfg.get("perfis") or {}):
        erros = list(erros) + ["config sem nenhum perfil: o plugin ficaria sem rotas"]
    return {"config": cfg, "erros": erros}


def _perfil_servido() -> Optional[str]:
    """Perfil que este processo serve, quando o host o expoe; ``None`` se nao souber.

    `HERMES_PROFILE` e a via publica. `HERMES_HOME` e um **caminho**
    (`/opt/data`), nao um nome — so vale quando aponta para
    `.../profiles/<nome>`, senao devolvia-se um directoria como se fosse perfil.
    """
    nome = (os.environ.get("HERMES_PROFILE") or "").strip()
    if nome:
        return nome
    casa = (os.environ.get("HERMES_HOME") or "").strip().rstrip("/")
    if casa and os.path.basename(os.path.dirname(casa)) == "profiles":
        return os.path.basename(casa)
    return None


# ------------------------------------------------------------------ um modelo
# Editar UMA rota do catalogo numa pagina propria. O gesto e cirurgico: le-se a
# config, substitui-se (ou acrescenta-se) essa entrada e volta-se a gravar pelo
# `gestor`. O browser nunca reescreve a config inteira — senao uma pagina de
# modelo aberta ha dez minutos repunha tudo o que outra sessao escreveu, que e
# exactamente o defeito que o `rev` existe para travar.

#: Listas FECHADAS de cada campo de uma rota. Vem do `gestor` (a autoridade da
#: validacao), nao de uma copia escrita aqui nem no JS: o que o painel mostra
#: como escolha e o que o `gravar` aceita.
def _opcoes_modelo() -> dict[str, Any]:
    # Providers oferecidos ao painel: os que ja existem no ficheiro — em
    # qualquer perfil e nos classificadores. Lista fechada, mas derivada da
    # config: o plugin nao inventa providers que o dono nao usa.
    provs: set[str] = set()
    try:
        cfg = _cfg()
    except Exception:
        cfg = {}
    perfis_cfg = cfg.get("perfis") or {}
    for bloco in perfis_cfg.values():
        if not isinstance(bloco, dict):
            continue
        for m in bloco.get("catalogo") or []:
            if isinstance(m, dict) and m.get("provider"):
                provs.add(str(m["provider"]))
        cl = ((bloco.get("analise") or {}).get("classificador") or {})
        if isinstance(cl, dict) and cl.get("provider"):
            provs.add(str(cl["provider"]))
    cl_global = ((cfg.get("analise") or {}).get("classificador") or {})
    if isinstance(cl_global, dict) and cl_global.get("provider"):
        provs.add(str(cl_global["provider"]))
    return {
        "niveis": list(gestor.NIVEIS),
        "acessos": list(gestor.ACESSOS),
        "esforcos": list(gestor.ESFORCOS),
        "modalidades": list(gestor.MODALIDADES),
        "providers": sorted(provs),
    }


def _perfil_da_config(cfg: dict[str, Any], perfil: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Devolve ``(bloco do perfil, catalogo)`` ou 404 — um perfil que nao existe
    nao pode receber um modelo por gralha de nome."""
    dados = (cfg.get("perfis") or {}).get(perfil)
    if not isinstance(dados, dict):
        conhecidos = ", ".join(sorted((cfg.get("perfis") or {}).keys())) or "nenhum"
        raise HTTPException(status_code=404,
                            detail=t("perfil {nome!r} nao existe na configuracao (tem: {conhecidos})", nome=perfil, conhecidos=conhecidos))
    catalogo = dados.get("catalogo")
    return dados, (catalogo if isinstance(catalogo, list) else [])


def _entrada_por_id(catalogo: list[dict[str, Any]], id_: str) -> Optional[int]:
    for indice, entrada in enumerate(catalogo):
        if isinstance(entrada, dict) and str(entrada.get("id")) == str(id_):
            return indice
    return None


@router.get("/modelo")
def modelo_get(perfil: str = Query(...), id: Optional[str] = Query(None)) -> dict[str, Any]:
    """Uma rota do catalogo, crua e completa, para a pagina de edicao.

    O `/estado` mostra o catalogo resumido (o que o cartao precisa). Aqui vem a
    entrada inteira — esforco, limites, modalidades, json nativo, nota — que e o
    que a pagina edita. Sem `id` devolve o modelo em branco para criar.
    """
    cfg = _cfg()
    dados, catalogo = _perfil_da_config(cfg, perfil)
    tarefas = dados.get("tarefas") or {}
    opcoes = _opcoes_modelo()
    opcoes["tarefas"] = [
        {"valor": nome, "rotulo": nome + " · prioridade " + str((tarefas.get(nome) or {}).get("prioridade", 0))}
        for nome in sorted(tarefas.keys())
    ]
    opcoes["ids"] = [str(e.get("id")) for e in catalogo if isinstance(e, dict)]
    opcoes["perfis"] = sorted((cfg.get("perfis") or {}).keys())

    if id:
        indice = _entrada_por_id(catalogo, id)
        if indice is None:
            raise HTTPException(status_code=404,
                                detail=t("modelo {id!r} nao existe no catalogo de {perfil!r}", id=id, perfil=perfil))
        return {"perfil": perfil, "id": id, "novo": False,
                "entrada": catalogo[indice], "opcoes": opcoes, "rev": _rev(cfg)}

    return {
        "perfil": perfil, "id": None, "novo": True,
        "entrada": {"id": "", "provider": "", "modelo": "",
                    "complexidade": [], "acesso": "api", "tarefas": []},
        "opcoes": opcoes, "rev": _rev(cfg),
    }


@router.post("/modelo")
def modelo_post(corpo: Any = Body(default=None)) -> JSONResponse:
    """Valida (``validar: true``) ou grava uma rota do catalogo de um perfil.

    Gravar substitui a entrada no lugar quando ela existe (a ordem do catalogo
    e a ordem de `prioridade`, logo nao pode mudar sem intencao) e acrescenta ao
    fim quando e nova. O id e a chave: um id ja usado por outra entrada e
    recusado, porque as superficies e as tarefas lhe chamam pelo nome.
    """
    corpo = _corpo_objeto(corpo)
    perfil = str(corpo.get("perfil") or "").strip()
    entrada = corpo.get("entrada")
    if not perfil:
        raise HTTPException(status_code=400, detail=t("falta 'perfil'"))
    if not isinstance(entrada, dict):
        raise HTTPException(status_code=400, detail=t("'entrada' tem de ser um objecto"))
    apenas_validar = bool(corpo.get("validar"))
    id_original = str(corpo.get("id") or "").strip() or None
    novo_id = str(entrada.get("id") or "").strip()
    if not novo_id:
        raise HTTPException(status_code=400,
                            detail=t("o modelo precisa de um id (e por ele que as superficies e as tarefas lhe chamam)"))

    rev = str(corpo.get("rev") or "")

    def mutar(cfg: dict[str, Any]) -> dict[str, Any]:
        """Substitui/acrescenta a rota no catalogo, sobre a config fresca.

        As verificacoes (id inexistente, id duplicado) correm aqui dentro, contra
        o disco de agora — nao contra o que a pagina leu. Se outra sessao
        acrescentou a mesma rota entretanto, a colisao e apanhada na mesma.
        """
        dados, catalogo = _perfil_da_config(cfg, perfil)
        posicao = _entrada_por_id(catalogo, id_original) if id_original else None
        if id_original and posicao is None:
            raise HTTPException(status_code=404,
                                detail=t("modelo {id!r} ja nao existe no catalogo de {perfil!r}", id=id_original, perfil=perfil))
        colisao = _entrada_por_id(catalogo, novo_id)
        if colisao is not None and colisao != posicao:
            raise HTTPException(status_code=400,
                                detail=t("ja existe um modelo com o id {id!r} no perfil {perfil!r}", id=novo_id, perfil=perfil))
        nova_lista = list(catalogo)
        limpa = dict(entrada)
        if posicao is None:
            nova_lista.append(limpa)
        else:
            nova_lista[posicao] = limpa
        dados["catalogo"] = nova_lista
        return cfg

    # Validar nunca escreve: corre o mesmo `validar` do `gravar` sobre a config
    # em memoria e devolve os erros — o botao "Validar" tem de poder dizer nao
    # sem deixar rasto no ficheiro.
    if apenas_validar:
        try:
            cfg = _cfg()
            mutar(cfg)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code,
                                content={"ok": False, "detail": exc.detail,
                                         "erros": [str(exc.detail)]})
        _, erros = gestor.validar(cfg)
        return JSONResponse(content={"ok": not erros, "erros": list(erros),
                                     "perfil": perfil, "id": novo_id})

    try:
        novo, rev_novo = gestor.alterar(mutar, rev_esperado=rev or None)
    except gestor.Conflito as exc:
        return JSONResponse(status_code=409, content={
            "ok": False,
            "detail": t("a configuração mudou desde que esta página a leu"),
            "rev": exc.rev_actual,
        })
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code,
                            content={"ok": False, "detail": exc.detail})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={
            "ok": False,
            "detail": t("nao gravou: {erro}", erro=str(exc)),
            "erros": [p for p in str(exc).split("; ") if p],
        })

    # Releitura independente: o que se devolve e o que ficou no ficheiro, nao o
    # que se julgou ter escrito.
    confirmado = _cfg()
    _, catalogo_lido = _perfil_da_config(confirmado, perfil)
    indice = _entrada_por_id(catalogo_lido, novo_id)
    if indice is None:  # pragma: no cover - so se o `gravar` mentir
        raise HTTPException(status_code=500, detail=t("gravou mas a rota não aparece na releitura"))
    return JSONResponse(content={
        "ok": True, "perfil": perfil, "id": novo_id,
        "entrada": catalogo_lido[indice], "rev": _rev(confirmado),
    })


# --------------------------------------------------------------- um perfil
# Uma pagina por perfil, a semelhanca da pagina de um modelo. O que se edita
# aqui e o que o perfil DECLARA — nao o catalogo (esse tem pagina propria, por
# modelo) nem as superficies (tem bloco proprio no editor de configuracao).
#
# A gravacao e cirurgica: toca so nas chaves do perfil que esta pagina mostra.
# Reescrever o bloco inteiro a partir do formulario apagaria o `catalogo` e as
# `superficies` que outra pagina (ou outra sessao) escreveu entretanto.

#: Superficies que a pagina oferece no `select`. A lista do gestor e a mesma que
#: o middleware usa; aqui so se acrescenta o rotulo.
_ROTULOS_SUPERFICIE = {
    "cron": "cron (execução agendada)",
    "api_server": "api_server (API/chat)",
    "cli": "cli (terminal)",
    "whatsapp": "whatsapp",
    "subagent": "subagent",
    "tui": "tui",
    "oneshot": "oneshot",
}


def _opcoes_perfil(cfg: dict[str, Any], perfil: str) -> dict[str, Any]:
    """Listas FECHADAS que a pagina do perfil mostra.

    Uma casa so: os valores saem do `gestor`/`analise_modelo`, nunca de uma
    copia em JS. Acrescentar um nivel em `gestor.NIVEIS` faz aparecer a opcao
    sem tocar no bundle — e, o que importa mais, o `select` nao pode oferecer
    um valor que a validacao recusa.
    """
    modelo = gestor._analise_modelo()
    pcfg = (cfg.get("perfis") or {}).get(perfil) or {}
    # Providers a partir do que a config ja usa: nao se inventa um provider
    # que o perfil nao tem credencial para servir.
    providers = sorted({
        str(e.get("provider"))
        for p in (cfg.get("perfis") or {}).values()
        for e in (p.get("catalogo") or [])
        if e.get("provider")
    })
    superficies = sorted(set(gestor.SUPERFICIES_CONHECIDAS))
    return {
        "activo": [{"valor": True, "rotulo": "activo"},
                   {"valor": False, "rotulo": "desligado"}],
        "niveis": [{"valor": v, "rotulo": v} for v in gestor.NIVEIS],
        "acessos": [{"valor": v, "rotulo": v} for v in gestor.ACESSOS],
        "esforcos": [{"valor": v, "rotulo": v} for v in gestor.ESFORCOS],
        "modalidades": [{"valor": v, "rotulo": v} for v in gestor.MODALIDADES],
        "providers": [{"valor": v, "rotulo": v} for v in providers],
        "vias": [{"valor": v, "rotulo": v} for v in sorted(set(modelo.VIAS.values()))],
        "presets": [{"valor": v, "rotulo": v} for v in sorted(modelo.PRESETS)],
        "estados": [{"valor": v, "rotulo": v} for v in modelo.ESTADOS_DECISAO],
        "formatos_json": [{"valor": v, "rotulo": v} for v in ("auto", "on", "off")],
        "estrategia": gestor.opcoes_estrategia(),
        "superficies": [
            {"valor": s, "rotulo": _ROTULOS_SUPERFICIE.get(s, s)} for s in superficies
        ],
        # Tarefas declaradas NESTE perfil: e o unico universo valido para
        # associar uma rota a uma tarefa (a validacao recusa um id que nao
        # exista aqui).
        "tarefas": [{"valor": t, "rotulo": t} for t in sorted((pcfg.get("tarefas") or {}))],
    }


@router.get("/perfil")
def perfil_get(nome: str = Query(...)) -> dict[str, Any]:
    """O bloco que este perfil DECLARA, mais o que esta em vigor.

    Devolve duas coisas distintas de proposito: `declarado` (o que o ficheiro
    tem para este perfil, cru) e `efectivo` (o que o middleware usa, ja com a
    heranca do global resolvida). Sem esta separacao o formulario nao sabia
    distinguir \"herdo\" de \"declaro o mesmo valor\", e gravar punha uma copia
    que fixava o perfil ao valor do dia.
    """
    cfg = _cfg()
    perfis = cfg.get("perfis") or {}
    if nome not in perfis:
        raise HTTPException(status_code=404, detail=t("perfil {nome!r} nao existe", nome=nome))
    cru = perfis.get(nome) or {}
    _analise_cru = cru.get("analise")
    analise: dict[str, Any] = _analise_cru if isinstance(_analise_cru, dict) else {}
    estrategia_cru = cru.get("estrategia")
    est_efectiva = gestor.estrategia_efectiva(cfg, nome)
    cls_efectivo = gestor.classificador_efectivo(cfg, nome)

    # So o que esta DECLARADO: uma chave ausente e "herda", e e isso que o
    # formulario tem de mostrar como vazio, nao o valor herdado ja resolvido.
    declarado: dict[str, Any] = {
        "activo": bool(cru.get("activo", True)),
        "tarefas": copy.deepcopy(cru.get("tarefas") or {}),
        "classificador": copy.deepcopy(analise.get("classificador")) if "classificador" in analise else None,
        "estrategia": copy.deepcopy(estrategia_cru) if estrategia_cru else None,
        # So o declarado: `None` = herda o global. O valor herdado vai em
        # `global`, nao aqui — dentro do bloco, gravar punha uma copia que
        # fixava o perfil ao valor do dia.
        "tarefa_persistente": (copy.deepcopy(analise.get("tarefa_persistente"))
                               if isinstance(analise.get("tarefa_persistente"), dict) else None),
        "latencia_max_ms": cru.get("latencia_max_ms") or 0,
    }
    # O classificador global, para o formulario poder mostrar o que se herda.
    global_cls = copy.deepcopy((cfg.get("analise") or {}).get("classificador") or {})
    # O bloco global desta materia ja com os valores por omissao: e isso que o
    # perfil herda, e e isso que o formulario mostra a quem escolhe "herdar".
    tp_global = dict(gestor.TAREFA_PERSISTENTE_PADRAO)
    tp_global.update(copy.deepcopy((cfg.get("analise") or {}).get("tarefa_persistente") or {}))
    return {
        "perfil": nome,
        "rev": _rev(cfg),
        "declarado": declarado,
        "efectivo": {
            "classificador": cls_efectivo,
            "estrategia": est_efectiva,
            "tarefa_persistente": gestor.tarefa_persistente_efectiva(cfg, nome),
            "n_modelos": len(cru.get("catalogo") or []),
        },
        "global": {"classificador": global_cls,
                   "estrategia": copy.deepcopy(cfg.get("estrategia") or gestor.ESTRATEGIA_PADRAO),
                   "tarefa_persistente": tp_global},
        "opcoes": _opcoes_perfil(cfg, nome),
    }


@router.post("/perfil")
def perfil_post(corpo: Any = Body(default=None)) -> JSONResponse:
    """Grava (ou valida) o bloco deste perfil, sem tocar no resto.

    `validar: true` corre o mesmo caminho e para antes do `gravar` — nao deixa
    rasto no ficheiro. Sem isso, o botao \"Validar\" teria de escrever para saber
    se o que o dono escreveu serve, que e o oposto de validar.
    """
    obj = _corpo_objeto(corpo)
    if obj is None:                      # `obrigatorio=True` ja recusou acima
        return JSONResponse(status_code=400, content={"ok": False, "detail": t("falta o corpo")})
    nome = str(obj.get("perfil") or "").strip()
    if not nome:
        return JSONResponse(status_code=400, content={"ok": False, "detail": t("falta 'perfil'")})
    apenas_validar = bool(obj.get("validar"))

    cfg = _cfg()
    if nome not in (cfg.get("perfis") or {}):
        return JSONResponse(status_code=404, content={"ok": False, "detail": t("perfil {p0} nao existe", p0=repr(nome))})

    rev_cliente = str(obj.get("rev") or "")
    rev_antes = _rev(cfg)
    if rev_cliente and _conflito(rev_cliente, rev_antes):
        return JSONResponse(status_code=409, content={
            "ok": False, "rev": rev_antes, "detail": t("a configuração mudou noutro sítio — recarregue"),
        })

    # Trabalha-se sobre uma copia: a validacao devolve uma config NOVA e o que
    # nao for tocado tem de sobreviver tal e qual (catalogo, superficies, pesos).
    novo = copy.deepcopy(cfg)
    pcfg = novo["perfis"][nome]

    if "activo" in obj:
        pcfg["activo"] = bool(obj.get("activo"))

    if "tarefas" in obj:
        tarefas = obj.get("tarefas")
        if tarefas is None:
            tarefas = {}
        if not isinstance(tarefas, dict):
            return JSONResponse(status_code=400, content={
                "ok": False, "detail": t("'tarefas' tem de ser um objecto (id -> {termos, prioridade})")})
        # Normaliza as listas de termos: aceita lista ou texto separado por
        # virgulas (o formulario escreve texto), e descarta vazios.
        limpas: dict[str, Any] = {}
        for tid, regra in tarefas.items():
            chave = str(tid).strip()
            if not chave:
                continue
            if not isinstance(regra, dict):
                regra = {}
            termos = regra.get("termos", [])
            if isinstance(termos, str):
                termos = [t.strip() for t in termos.split(",")]
            termos = [str(t).strip() for t in (termos or []) if str(t).strip()]
            prioridade = regra.get("prioridade", 0)
            try:
                prioridade = int(prioridade)
            except (TypeError, ValueError):
                prioridade = 0
            limpas[chave] = {"termos": list(dict.fromkeys(termos)), "prioridade": prioridade}
        pcfg["tarefas"] = limpas

    if "latencia_max_ms" in obj:
        try:
            teto = int(obj.get("latencia_max_ms") or 0)
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={
                "ok": False, "detail": t("'latencia_max_ms' tem de ser inteiro (0 = sem teto)")})
        if teto > 0:
            pcfg["latencia_max_ms"] = teto
        else:
            pcfg.pop("latencia_max_ms", None)

    analise: dict[str, Any] = pcfg.get("analise") if isinstance(pcfg.get("analise"), dict) else {}
    analise = copy.deepcopy(analise)
    mexeu_analise = False

    if "classificador" in obj:
        valor = obj.get("classificador")
        if valor is None or valor == "" or valor == "herdar":
            analise.pop("classificador", None)      # volta a herdar o global
        else:
            analise["classificador"] = valor        # o `normalizar` valida-o depois
        mexeu_analise = True

    # A tarefa persistente segue a mesma regra, mas o delta e chave a chave: o
    # `validar` deita fora as chaves iguais ao global, para um perfil que
    # declare o mesmo valor nao ficar preso ao valor do dia.
    if "tarefa_persistente" in obj:
        valor = obj.get("tarefa_persistente")
        if valor is None or valor == "" or valor == "herdar":
            analise.pop("tarefa_persistente", None)  # volta a herdar o global
        elif not isinstance(valor, dict):
            return JSONResponse(status_code=400, content={
                "ok": False,
                "detail": t("'tarefa_persistente' tem de ser um objecto "
                           "({activo, expira_turnos, expira_min}) ou \"herdar\"")})
        else:
            analise["tarefa_persistente"] = valor    # o `validar` fecha as chaves
        mexeu_analise = True

    if mexeu_analise:
        if analise:
            pcfg["analise"] = analise
        else:
            pcfg.pop("analise", None)               # ausencia = herda tudo

    if "estrategia" in obj:
        valor = obj.get("estrategia")
        if valor is None or valor == "" or valor == "herdar":
            pcfg.pop("estrategia", None)
        else:
            pcfg["estrategia"] = valor

    limpa, erros = gestor.validar(novo)
    if erros:
        return JSONResponse(status_code=400, content={"ok": False, "erros": erros,
                                                      "detail": "; ".join(erros)})

    if apenas_validar:
        return JSONResponse(content={"ok": True, "validado": True, "perfil": nome,
                                     "erros": [], "rev": rev_antes})

    try:
        # O documento foi montado sobre a leitura desta requisicao; `rev_esperado`
        # garante, ja dentro da trava, que ninguem escreveu entretanto — sem isso
        # esta escrita reporia o estado lido ha milissegundos.
        gestor.gravar(limpa, rev_esperado=rev_antes or None)
    except gestor.Conflito as exc:
        return JSONResponse(status_code=409, content={
            "ok": False, "detail": t("a configuração mudou desde que esta página a leu"),
            "rev": exc.rev_actual,
        })
    except ValueError as exc:
        return JSONResponse(status_code=400, content={
            "ok": False, "detail": t("nao gravou: {erro}", erro=str(exc)),
            "erros": [p for p in str(exc).split("; ") if p],
        })

    # Releitura independente: a resposta diz o que ficou no ficheiro.
    confirmado = _cfg()
    rev_depois = _rev(confirmado)
    _registar_escrita(rev_antes=rev_antes, rev_depois=rev_depois,
                      rev_cliente=rev_cliente, conflito=False, origem="perfil")
    cru = (confirmado.get("perfis") or {}).get(nome) or {}
    return JSONResponse(content={
        "ok": True,
        "perfil": nome,
        "rev": rev_depois,
        "activo": bool(cru.get("activo", True)),
        "tarefas": cru.get("tarefas") or {},
        "efectivo": {
            "classificador": gestor.classificador_efectivo(confirmado, nome),
            "estrategia": gestor.estrategia_efectiva(confirmado, nome),
        },
    })
