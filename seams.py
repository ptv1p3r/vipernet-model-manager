"""Costuras unicas para as APIs do Hermes de que o plugin depende.

Regra 9 do catalogo Hermes: um plugin estende o Hermes so por superficies
publicas (hooks, middleware, ctx.register_*). Ha duas coisas que este plugin
precisa e que hoje nao tem superficie publica:

  1. resolver a credencial/endpoint de um provider para poder TROCAR o modelo
     do agente vivo (`resolve_runtime_provider`). Sem isto o plugin nao faz o
     que promete: o `agent.switch_model` precisa do cliente HTTP ja montado.
  2. recarregar os plugins do gateway (`gateway.control_socket`), usado apenas
     pelo comando `vmm recarregar`.

Concentrar aqui os imports tem dois efeitos: ha **um** sitio a mudar quando o
seam publico existir, e o aviso do validador do catalogo aponta para este
ficheiro em vez de cinco.

Se um dia o Hermes expuser um seam publico (issue pedida), substitui-se o
corpo destas funcoes e o resto do plugin nao muda.
"""

from __future__ import annotations

import os
from typing import Any

# ---------------------------------------------------------------------------
# 1. Credenciais de um provider para trocar o modelo do agente vivo
# ---------------------------------------------------------------------------

_RT: Any = None
_RT_ERRO: str = ""


def _runtime_provider():
    """Importa `resolve_runtime_provider` uma vez, ou devolve None com motivo."""
    global _RT, _RT_ERRO
    if _RT is not None or _RT_ERRO:
        return _RT
    try:  # seam privado — ver docstring do modulo
        from hermes_cli.runtime_provider import (  # noqa: PLC0415
            resolve_runtime_provider as _f,
        )

        _RT = _f
    except Exception as exc:  # noqa: BLE001
        _RT_ERRO = f"{type(exc).__name__}: {exc}"
        _RT = None
    return _RT


def resolver_provider(provider: str, modelo: str) -> dict[str, Any]:
    """Devolve {provider, api_key, base_url, api_mode, source} para a rota.

    Levanta RuntimeError com a razao quando o seam nao existe ou o provider
    nao tem credencial no perfil servido.
    """
    fn = _runtime_provider()
    if fn is None:
        raise RuntimeError(f"seam resolve_runtime_provider indisponivel: {_RT_ERRO}")
    rt = fn(requested=provider, target_model=modelo)
    return {
        "provider": (rt.get("provider") or provider).strip().lower(),
        "api_key": rt.get("api_key") or "",
        "base_url": rt.get("base_url") or "",
        "api_mode": rt.get("api_mode") or "",
        "source": rt.get("source") or "",
    }


def seam_disponivel() -> bool:
    """True se o seam de credenciais esta utilizavel neste interpretador."""
    return _runtime_provider() is not None


def seam_erro() -> str:
    """Motivo pelo qual o seam nao esta disponivel (vazio se esta)."""
    _runtime_provider()
    return _RT_ERRO


# ---------------------------------------------------------------------------
# 2. Nome do perfil servido
# ---------------------------------------------------------------------------

def nome_perfil() -> str:
    """Perfil servido, sem depender de um import privado quando o env basta.

    Ordem: HERMES_PROFILE -> import privado (hermes_cli.profiles) -> "default".
    """
    env = (os.environ.get("HERMES_PROFILE") or "").strip()
    if env:
        return env
    try:  # seam privado — so quando o env nao o diz
        from hermes_cli.profiles import get_active_profile_name  # noqa: PLC0415

        return str(get_active_profile_name() or "default")
    except Exception:  # noqa: BLE001
        return "default"


# ---------------------------------------------------------------------------
# 3. Recarregar plugins do gateway (so CLI)
# ---------------------------------------------------------------------------

def recarregar_gateway(home) -> dict[str, Any] | None:
    """Pede ao gateway para redescobrir plugins. None se nada respondeu."""
    from gateway.control_socket import reload_gateway_plugins  # noqa: PLC0415

    return reload_gateway_plugins(home)
