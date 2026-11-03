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

    ``github`` e o repositorio publico do autor. O repositorio que o catalogo
    aceita tem de ser publico, clonavel por https em GitHub ou GitLab [1]; o
    Gitea interno continua a ser o espelho de trabalho, nao o endereco de
    submissao.
    """
    versao = ""
    autor = ""
    try:
        manifesto = Path(__file__).resolve().parent.parent / "plugin.yaml"
        for linha in manifesto.read_text(encoding="utf-8").splitlines():
            chave, _, valor = linha.partition(":")
            chave = chave.strip()
            if chave not in ("version", "author"):
                continue
            # Aceita `author: Nome` e `author: "Nome"`.
            limpo = valor.strip().strip('"').strip("'")
            if chave == "version":
                versao = limpo
            else:
                autor = limpo
    except Exception:  # noqa: BLE001 - sem plugin.yaml o painel ainda responde
        pass
    return {
        "versao": versao,
        "autor": autor,
        "github": GITHUB_AUTOR,
        # Divergencia entre os dois numeros e deriva, nao gosto: o painel diz.
        "versao_manifest": _versao_painel(),
    }


LIMITE_LOG_MAX = 500
LIMITE_LOG_OMISSAO = 50


# --------------------------------------------------------------------------- leitura


def _cfg() -> dict[str, Any]:
    cfg, _erros = gestor.carregar()
    return cfg


def _rev(cfg: dict[str, Any]) -> str:
    """Impressao digital do config, para o painel nao gravar por cima do que nao leu.

    Uma aba aberta ha meia hora contem o config de ha meia hora. Sem isto, um
    "Gravar" nessa aba repoe o passado inteiro — ja aconteceu: a escrita das
    11:27:52 repôs byte a byte o config que a pagina tinha carregado antes das
    11:18:47, apagando a correcao do perfil pessoal. O cliente envia o `rev` que
    leu; se nao for o actual, recusa-se a gravar e devolve o estado actual.
    """
    canonico = json.dumps(cfg, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16]


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
                    detail=f"corpo nao e um objecto JSON (recebido bytes, nao e utf-8)",
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
                detail=f"corpo nao e um objecto JSON (recebido {corpo[:60]!r})",
            )
        return None
    if isinstance(corpo, list) and corpo:
        return {"catalogo": corpo}
    if corpo is None and not obrigatorio:
        return None
    raise HTTPException(
        status_code=400,
        detail=f"corpo nao e um objecto JSON (recebido o tipo {type(corpo).__name__})",
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
                # O rasto do classificador vive nos eventos de troca/sem_troca, que
                # sao anteriores a `resposta` no ficheiro; a primeira ocorrencia que
                # se ve aqui e a mais recente.
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
            "estrategia": dados.get("estrategia") or "rodizio",
            "rodizio": dados.get("rodizio") or {},
            # Efectivo, nao o cru do perfil: um perfil que herda os limiares do
            # global tem de mostrar os que estao a decidir, nao uma lista vazia.
            "limiares": (pcfg.get("analise") or {}).get("limiares") or {},
            "tarefas": dados.get("tarefas") or {},
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
        raise HTTPException(status_code=400, detail="'activo' tem de ser booleano")
    ambito = (corpo.get("ambito") or "global").strip().lower()

    cfg = _cfg()
    if ambito == "global":
        cfg["activo"] = activo
        alvo = "global"
    elif ambito == "perfil":
        nome = (corpo.get("perfil") or "").strip()
        perfis = cfg.get("perfis") or {}
        if nome not in perfis:
            raise HTTPException(status_code=404, detail=f"perfil {nome!r} nao existe na configuracao")
        perfis[nome]["activo"] = activo
        alvo = nome
    else:
        raise HTTPException(status_code=400, detail="'ambito' tem de ser 'global' ou 'perfil'")

    try:
        gestor.gravar(cfg)
    except Exception as exc:  # noqa: BLE001 - 500 com a razao real, nao um OK mentiroso
        raise HTTPException(status_code=500, detail=f"nao gravou: {type(exc).__name__}: {exc}") from exc

    # Releitura independente: o estado devolvido e o que ficou no disco, nao o que se quis escrever.
    confirmado = _cfg()
    if ambito == "global":
        lido = bool(confirmado.get("activo", True))
    else:
        lido = bool(((confirmado.get("perfis") or {}).get(alvo) or {}).get("activo", True))
    if lido != activo:
        raise HTTPException(
            status_code=500,
            detail=f"gravacao nao confirmada: pedido {activo}, lido {lido}",
        )
    return JSONResponse({"ok": True, "ambito": ambito, "alvo": alvo, "activo": lido})


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
        raise HTTPException(status_code=400, detail="JSON inválido: não é um objecto")
    envelope = isinstance(corpo.get("config"), dict)
    novo = corpo["config"] if envelope else corpo
    rev_cliente = str(corpo.get("rev") or "") if envelope else ""
    if not (novo.get("perfis") or {}):
        raise HTTPException(
            status_code=400,
            detail="config sem nenhum perfil: recusado (o plugin ficaria sem rotas)",
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
        # gestor.gravar valida, faz backup e escreve com os.replace.
        gestor.gravar(novo)
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
