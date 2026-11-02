"""Costuras unicas para as APIs do Hermes de que o plugin depende.

Regra 9 do catalogo Hermes: um plugin estende o Hermes so por superficies
publicas (hooks, middleware, ctx.register_*). Ha **uma** coisa que este plugin
precisa e que hoje nao tem superficie publica:

  resolver a credencial/endpoint de um provider para poder TROCAR o modelo do
  agente vivo (`resolve_runtime_provider`). Sem isto o plugin nao faz o que
  promete: o `agent.switch_model` precisa do cliente HTTP ja montado, e
  monta-lo por fora duplicaria a resolucao de chaves e ignoraria o escopo de
  segredos do perfil servido.

Ficam aqui, e nao no resto do plugin, dois efeitos uteis: ha **um** sitio a
mudar quando o seam publico existir, e o aviso do validador do catalogo
aponta para este ficheiro em vez de cinco.

As outras duas dependencias privadas que existiam aqui sairam:

  - `hermes_cli.profiles.get_active_profile_name` — substituida por
    `HERMES_PROFILE` (o plugin recebe o perfil do agente vivo; o env serve o
    CLI, onde nao ha agente);
  - `gateway.control_socket.reload_gateway_plugins` — o comando `vmm
    recarregar` deixou de existir. Recarregar plugins e operacao do anfitriao
    (`/reload-plugins`), nao de um plugin de routing.
"""

from __future__ import annotations

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
