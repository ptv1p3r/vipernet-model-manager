"""Costuras para as APIs do Hermes de que o plugin depende.

Regra 9 do catalogo Hermes: um plugin estende o Hermes so por superficies
publicas, e `hermes plugins validate` avisa — e o revisor recusa — cada import
de modulo core nao documentado.

Este modulo tinha **um** import privado: `hermes_cli.runtime_provider.
resolve_runtime_provider`, usado para resolver credencial e endpoint de um
provider antes de trocar o modelo do agente vivo. Foi substituido por
superficies que o validador aceita. A tabela foi **medida**, nao suposta: a
sonda corre `private_import_findings` (a funcao do proprio validador) contra um
plugin falso com um import por candidato.

  agent.credential_pool.load_pool              PUBLICO  -> runtime_api_key,
                                                          runtime_base_url
  agent.secret_scope.get_secret                PUBLICO  (escopo do perfil)
  hermes_cli.auth.resolve_external_process_...  PUBLICO  (provider de subprocesso)
  hermes_cli.runtime_provider.resolve_...     BLOQUEADO (era o que havia)
  hermes_cli.model_switch.switch_model        BLOQUEADO (mesmo com credenciais
                                                resolvidas, nao serve)
  hermes_cli.providers.determine_api_mode     BLOQUEADO -> nao e' preciso:
      o `switch_model` do core resolve o `api_mode` quando vem vazio
      (`agent/agent_runtime_helpers.py:_resolve_switch_destination`:
      `if not api_mode: api_mode = determine_api_mode(...)`).

O que isto NAO resolve, e fica declarado: uma rota que muda de provider continua
a depender do `agent.switch_model` do anfitriao (metodo publico de um objecto
que o plugin recebe). Se o anfitriao deixar de o expor, o plugin degrada para a
rota da sessao em vez de falhar o turno.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 1. Credenciais de um provider (superficies publicas)
# ---------------------------------------------------------------------------

_LOAD_POOL: Any = None
_POOL_ERRO: str = ""


def _load_pool():
    """Importa `load_pool` ou devolve None com o motivo.

    So o sucesso e' memorizado: memorizar a falha tornaria a costura
    irrecuperavel dentro do processo (um teste que instale a pool depois do
    primeiro erro nunca mais a veria). Repetir o import e' uma consulta a
    `sys.modules`.
    """
    global _LOAD_POOL, _POOL_ERRO
    if _LOAD_POOL is not None:
        return _LOAD_POOL
    try:  # superficie publica — ver docstring do modulo
        from agent.credential_pool import load_pool as _f  # noqa: PLC0415

        _LOAD_POOL = _f
        _POOL_ERRO = ""
    except Exception as exc:  # noqa: BLE001
        _POOL_ERRO = f"{type(exc).__name__}: {exc}"
        _LOAD_POOL = None
    return _LOAD_POOL


def _da_pool(provider: str) -> dict[str, Any] | None:
    """Primeira entrada utilizavel da pool do provider.

    A ordem da pool e' significativa (e' a ordem de rotacao), logo nao se
    reordena: escolhe-se a primeira com chave de runtime. Devolver a chave e'
    o objectivo — o chamador entrega-a ao `switch_model` do anfitriao, que a
    guarda no agente e nao a persiste.
    """
    fn = _load_pool()
    if fn is None:
        return None
    pool = fn(provider)
    for entrada in pool.entries():
        chave = getattr(entrada, "runtime_api_key", "") or ""
        if not chave:
            continue
        base = (
            getattr(entrada, "runtime_base_url", "")
            or getattr(entrada, "base_url", "")
            or ""
        )
        return {
            "provider": (getattr(entrada, "provider", "") or provider).strip().lower(),
            "api_key": chave,
            "base_url": base,
            "api_mode": "",
            "source": getattr(entrada, "source", "") or "pool",
        }
    return None


def _de_subprocesso(provider: str) -> dict[str, Any] | None:
    """Providers servidos por subprocesso local (Codex / ChatGPT por subscricao).

    Chamado so como recurso: a pool cobre as chaves de API.
    """
    try:  # superficie publica
        from hermes_cli.auth import (  # noqa: PLC0415
            resolve_external_process_provider_credentials,
        )

        rt = resolve_external_process_provider_credentials(provider)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(rt, dict) or not rt:
        return None
    return {
        "provider": (rt.get("provider") or provider).strip().lower(),
        "api_key": rt.get("api_key") or "",
        "base_url": rt.get("base_url") or "",
        "api_mode": rt.get("api_mode") or "",
        "source": rt.get("source") or "external-process",
    }


def resolver_provider(provider: str, modelo: str) -> dict[str, Any]:
    """Devolve {provider, api_key, base_url, api_mode, source} para a rota.

    `modelo` fica na assinatura porque o chamador o conhece e porque a resolucao
    do anfitriao pode depender dele; estas vias publicas nao o usam.

    Levanta RuntimeError com a razao de cada via quando nenhuma resolve — o
    chamador trata isso como "rota indisponivel" e degrada, nunca inventa
    credencial.
    """
    motivos: list[str] = []
    for via in (_da_pool, _de_subprocesso):
        try:
            rota = via(provider)
        except Exception as exc:  # noqa: BLE001
            motivos.append(f"{via.__name__}: {type(exc).__name__}: {exc}")
            continue
        if rota and (rota.get("api_key") or rota.get("base_url")):
            return rota
        motivos.append(f"{via.__name__}: sem credencial")
    if _load_pool() is None:
        motivos.append(f"agent.credential_pool: {_POOL_ERRO}")
    raise RuntimeError(
        f"nenhuma credencial publica para {provider} ({'; '.join(motivos)})"
    )


def seam_disponivel() -> bool:
    """True se a via publica de credenciais esta utilizavel neste interpretador."""
    return _load_pool() is not None


def seam_erro() -> str:
    """Motivo pelo qual a via publica nao esta disponivel (vazio se esta)."""
    _load_pool()
    return _POOL_ERRO


# ---------------------------------------------------------------------------
# 2. Nome do perfil (so CLI: no gateway o perfil vem do agente vivo)
# ---------------------------------------------------------------------------

def nome_perfil() -> str:
    """Perfil servido: `HERMES_PROFILE`, senao `default`.

    Superficie publica — e uma variavel de ambiente, nao um modulo interno. No
    gateway o perfil chega por `gestor.perfil_atual(agente)`; este caminho
    serve o `vmm`, onde nao ha agente a quem perguntar.
    """
    import os

    return (os.environ.get("HERMES_PROFILE") or "").strip() or "default"
