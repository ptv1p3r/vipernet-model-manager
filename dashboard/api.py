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

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

# O ``api.py`` e importado por caminho; o pacote do plugin tem de estar no sys.path
# para que ``gestor`` resolva para o modulo do plugin e nao para outro homonimo.
_RAIZ_PLUGIN = Path(__file__).resolve().parents[1]
if str(_RAIZ_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_RAIZ_PLUGIN))

import gestor  # noqa: E402  (depende do sys.path acima)

router = APIRouter()

VERSAO_PAINEL = "3.3.0"
LIMITE_LOG_MAX = 500
LIMITE_LOG_OMISSAO = 50


# --------------------------------------------------------------------------- leitura


def _cfg() -> dict[str, Any]:
    cfg, _erros = gestor.carregar()
    return cfg


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


def _ultimo_por_perfil(limite_linhas: int = 4000) -> dict[str, dict[str, Any]]:
    """Ultimo evento ``resposta`` de cada perfil, numa so passagem do fim para o inicio."""
    caminho = gestor.caminho_ledger()
    if not caminho.exists():
        return {}
    achados: dict[str, dict[str, Any]] = {}
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
                if ev.get("evento") != "resposta":
                    continue
                p = ev.get("perfil") or ""
                if p and p not in achados:
                    achados[p] = {
                        "ts": ev.get("ts"),
                        "provider": ev.get("provider"),
                        "modelo": ev.get("modelo"),
                        "respondido_por": ev.get("respondido_por"),
                        "nivel": ev.get("nivel"),
                        "origem": ev.get("origem"),
                        "sessao": ev.get("sessao"),
                    }
    return achados


# --------------------------------------------------------------------------- rotas


@router.get("/estado")
def estado() -> dict[str, Any]:
    cfg = _cfg()
    perfis = cfg.get("perfis") or {}
    ultimos = _ultimo_por_perfil()

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
        saida_perfis[nome] = {
            "activo": bool(dados.get("activo", True)),
            "estrategia": dados.get("estrategia") or "rodizio",
            "rodizio": dados.get("rodizio") or {},
            "limiares": (dados.get("analise") or {}).get("limiares") or {},
            "tarefas": dados.get("tarefas") or {},
            "catalogo": catalogo,
            "ultimo": ultimos.get(nome),
        }

    ledger = gestor.caminho_ledger()
    return {
        "versao_painel": VERSAO_PAINEL,
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
def toggle(corpo: dict[str, Any]) -> JSONResponse:
    """Liga/desliga o plugin. ``ambito='global'`` (por omissao) ou ``'perfil'`` + ``perfil``."""
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


def _perfil_servido() -> Optional[str]:
    """Perfil que este processo serve, quando o host o expoe; ``None`` se nao souber."""
    for var in ("HERMES_PROFILE", "HERMES_PROFILE_NAME"):
        valor = (os.environ.get(var) or "").strip()
        if valor:
            return valor
    return None
