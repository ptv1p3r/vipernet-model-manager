"""Provider custom (`providers.<name>`) — a credencial nao esta na pool.

O que se corrigiu (3.47.13), medido a 2026-11-08 no painel com `inception`:

A pool de credenciais (`agent.credential_pool`) so tem o que passou por
`hermes auth add`. Um provider custom, declarado em `providers.<name>` no
`config.yaml` com `base_url` e `key_env`, nao passa por la: a chave vive no
segredo dessa variavel. O resolvedor do plugin so olhava para a pool e para os
providers de subprocesso, portanto `inception/mercury-3` aparecia como «sem
credencial» com a chave carregada no processo — e a rota respondia 200 quando
chamada a mao (medido: 847 ms, `mercury-3`).

A via nova e a do proprio Hermes: `hermes_cli.config.get_compatible_custom_providers`
para o `base_url`/`key_env`, e `agent.secret_scope.get_secret` para o segredo
(regra 10: honra o perfil activo). `UnscopedSecretError` e' o processo de perfil
unico, em que a credencial entra pelo ambiente — unico caso em que se le
`os.environ`, igual ao que o nucleo faz em `_scoped_key_env`.

Uso: python3 test_sonda_provider_custom.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("vmm_seams_sonda", ROOT / "seams.py")
seams = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seams)


class _UnscopedSecretError(Exception):
    """Marca o mesmo caso que `agent.secret_scope.UnscopedSecretError`."""


def _instalar(entrada=None, segredos=None, erro=None):
    """Instala modulos falsos do nucleo em `sys.modules` e devolve-os.

    `seams` importa dentro da funcao, portanto o que estiver em `sys.modules`
    no momento da chamada e' o que vale. Nao se toca em rede nem em config real.
    """
    mod_cfg = types.ModuleType("hermes_cli.config")

    def get_compatible_custom_providers(config=None):
        valor = entrada(None) if callable(entrada) else entrada
        if valor is None:
            return []
        if isinstance(valor, tuple):  # forma antiga dos testes: (nome, entrada)
            valor = valor[1]
        return [valor]

    mod_cfg.get_compatible_custom_providers = get_compatible_custom_providers

    mod_scope = types.ModuleType("agent.secret_scope")
    mod_scope.UnscopedSecretError = _UnscopedSecretError

    def get_secret(nome, default=None):
        if erro is not None:
            raise erro
        return (segredos or {}).get(nome, default)

    mod_scope.get_secret = get_secret

    pacote_agent = types.ModuleType("agent")
    pacote_agent.__path__ = []
    pacote_cli = types.ModuleType("hermes_cli")
    pacote_cli.__path__ = []
    sys.modules["agent"] = pacote_agent
    sys.modules["agent.secret_scope"] = mod_scope
    sys.modules["hermes_cli"] = pacote_cli
    sys.modules["hermes_cli.config"] = mod_cfg
    return mod_cfg, mod_scope


class ProviderCustomTest(unittest.TestCase):
    def tearDown(self) -> None:
        for nome in ("hermes_cli.config", "hermes_cli", "agent.secret_scope", "agent"):
            sys.modules.pop(nome, None)

    def test_key_env_resolve(self) -> None:
        _instalar(
            entrada={
                "name": "Inception",
                "provider_key": "inception",
                "base_url": "https://api.inceptionlabs.ai/v1",
                "key_env": "INCEPTION_API_KEY",
                "api_mode": "chat_completions",
            },
            segredos={"INCEPTION_API_KEY": "chave-de-teste"},
        )
        rota = seams._de_config_custom("inception")
        self.assertIsNotNone(rota)
        self.assertEqual(rota["base_url"], "https://api.inceptionlabs.ai/v1")
        self.assertEqual(rota["api_key"], "chave-de-teste")
        self.assertEqual(rota["api_mode"], "chat_completions")
        self.assertEqual(rota["source"], "providers.inception/key_env")

    def test_nome_display_tambem_resolve(self) -> None:
        _instalar(entrada={"name": "Inception", "provider_key": "inception",
                           "base_url": "https://a/v1", "api_key": "inline"})
        self.assertIsNotNone(seams._de_config_custom("Inception"))

    def test_api_key_inline(self) -> None:
        _instalar(entrada={
            "name": "x", "provider_key": "x",
            "base_url": "https://exemplo/v1", "api_key": "inline"})
        rota = seams._de_config_custom("x")
        self.assertEqual(rota["source"], "providers.x")
        self.assertEqual(rota["api_key"], "inline")

    def test_sem_entrada_devolve_none(self) -> None:
        _instalar(entrada=None)
        self.assertIsNone(seams._de_config_custom("nao-existe"))

    def test_sem_chave_e_sem_endpoint_devolve_none(self) -> None:
        _instalar(entrada={"name": "x", "provider_key": "x"})
        self.assertIsNone(seams._de_config_custom("x"))

    def test_unscoped_cai_para_o_ambiente(self) -> None:
        _instalar(entrada={"name": "inc", "provider_key": "inc",
                           "base_url": "https://a/v1", "key_env": "VMM_CHAVE_TESTE"},
                  erro=_UnscopedSecretError("sem escopo"))
        rota = seams._de_config_custom("inc")
        self.assertIsNotNone(rota)
        self.assertEqual(rota["api_key"], "")  # o ambiente do teste nao a tem

    def test_nucleo_ausente_nao_rebenta(self) -> None:
        sys.modules.pop("hermes_cli.config", None)
        sys.modules.pop("hermes_cli", None)
        sys.modules.pop("agent.secret_scope", None)
        pacote = types.ModuleType("agent")
        pacote.__path__ = []
        sys.modules["agent"] = pacote
        self.assertIsNone(seams._de_config_custom("inception"))

    def test_via_esta_na_ordem_do_resolvedor(self) -> None:
        """A via nova tem de estar no laco do resolvedor, senao nada disto serve."""
        fonte = (ROOT / "seams.py").read_text()
        self.assertIn("for via in (_da_pool, _de_config_custom, _de_subprocesso):", fonte)


if __name__ == "__main__":
    unittest.main(verbosity=2)
