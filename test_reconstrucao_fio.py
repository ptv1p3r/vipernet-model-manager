"""Reconstrucao do payload quando a troca de rota muda de fio (3.47.24).

O defeito que este ficheiro existe para impedir, medido a 2026-11-19 no perfil
`default`: a middleware corre depois de o `api_kwargs` estar construido para a
rota anterior (`agent/turn_api_request.py`). Quando a troca muda de fio, o corpo
segue na forma antiga e o SDK do destino recusa-o **antes de sair**. Com a base
`gpt-6.2-astra` (`codex_responses`) a cair no `deepseek-flash` ou no
`north-mini-code:free` (chat/completions) o erro era

    TypeError: Missing required arguments; Expected either ('messages' and
    'model') or ('messages', 'model' and 'stream') arguments to be given

seis vezes em doze turnos trocados entre 2026-11-18 e 2026-11-19. No `default`
era isso que impedia o flash de ficar em primeiro no catalogo: a config ficava
escrita e o primeiro turno de cada sessao morria.

O que se prova aqui, com os transports **reais** do core (ida e volta):

1. `codex_responses` -> chat: um payload Responses convertido produz as mesmas
   mensagens chat que a conversa original produzia, com o prompt de sistema
   vindo de `instructions`.
2. `anthropic_messages` -> chat: `system` e os blocos `tool_use`/`tool_result`
   sobrevivem a conversao.
3. `_payload_na_forma_do_destino` devolve um corpo com `messages` e sem
   `input`/`instructions`, e regista `fio.reconstruido`.
4. Sem mudanca de fio nao se reconstroi nada (o payload sai intacto).

Uso: python3 test_reconstrucao_fio.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

CORE = Path("/opt/hermes")
if CORE.is_dir() and str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))


def _carregar_plugin():
    spec = importlib.util.spec_from_file_location(
        "vmm_reconstrucao", RAIZ / "__init__.py",
        submodule_search_locations=[str(RAIZ)])
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["vmm_reconstrucao"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


try:
    vmm = _carregar_plugin()
except Exception as exc:  # pragma: no cover - ambiente sem o core
    raise SystemExit(f"nao foi possivel carregar o plugin: {type(exc).__name__}: {exc}")

from agent.transports.anthropic import AnthropicTransport  # noqa: E402
from agent.transports.chat_completions import ChatCompletionsTransport  # noqa: E402
from agent.transports.codex import ResponsesApiTransport  # noqa: E402

FERRAMENTAS = [{
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "corre um comando",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string"}},
                       "required": ["command"]},
    },
}]

CONVERSA = [
    {"role": "system", "content": "Es o assistente de teste. Responde em pt-PT."},
    {"role": "user", "content": "Le /tmp/x e diz o que tem."},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "terminal:0", "type": "function",
         "function": {"name": "terminal", "arguments": '{"command": "cat /tmp/x"}'}}]},
    {"role": "tool", "tool_call_id": "terminal:0", "content": "a.txt\nb.txt"},
    {"role": "assistant", "content": "Tem dois ficheiros: a.txt e b.txt."},
    {"role": "user", "content": "E qual e o maior?"},
]


def _chat(payload_mensagens, modelo="deepseek-flash"):
    """Payload chat/completions a partir de mensagens, com o transport do core."""
    return ChatCompletionsTransport().build_kwargs(
        model=modelo, messages=payload_mensagens, tools=FERRAMENTAS,
        base_url="https://api.deepseek.com/v1", provider_name="deepseek",
        model_lower=modelo)


def _responses(modelo="gpt-6.2-astra"):
    return ResponsesApiTransport().build_kwargs(
        model=modelo, messages=CONVERSA, tools=FERRAMENTAS,
        base_url="https://foundry.example/openai/v1", max_tokens=4096,
        is_codex_backend=False, is_xai_responses=False, provider="foundry-edp-gpt")


def _anthropic(modelo="claude-fable-5-2"):
    return AnthropicTransport().build_kwargs(
        model=modelo, messages=CONVERSA, tools=FERRAMENTAS,
        max_tokens=4096)


class _AgenteFalso:
    """O pedaco do agente que a reconstrucao usa: o construtor do core.

    Já trocado, como no gateway: o `api_mode` e o do destino e o modelo e o da
    rota escolhida. O construtor delega no transport real do fio de destino.
    """

    def __init__(self, modo: str, modelo: str, mensagens_observadas: list):
        self.api_mode = modo
        self.model = modelo
        self.provider = "deepseek"
        self._mensagens = mensagens_observadas

    def _build_api_kwargs(self, mensagens, tools_for_api=None):
        self._mensagens.extend(mensagens)
        return _chat(mensagens, self.model)


class ReconstrucaoDoFio(unittest.TestCase):
    def test_responses_para_chat_mantem_conversa_e_sistema(self):
        antigo = _responses()
        self.assertIn("input", antigo)
        self.assertNotIn("messages", antigo)

        convertidas = vmm._mensagens_de_responses({**antigo, "model": "deepseek-flash"})
        self.assertEqual(convertidas[0]["role"], "system")
        self.assertIn("pt-PT", convertidas[0]["content"])

        observadas: list = []
        agente = _AgenteFalso("chat_completions", "deepseek-flash", observadas)
        novo, fio = vmm._payload_na_forma_do_destino(
            agente, antigo, "codex_responses", "chat_completions")

        self.assertIsNotNone(fio, "a troca de fio tinha de reconstruir o payload")
        self.assertEqual(fio["de"], "codex_responses")
        self.assertEqual(fio["para"], "chat_completions")
        self.assertTrue(fio["reconstruido"])
        self.assertIn("messages", novo)
        self.assertNotIn("input", novo)
        self.assertNotIn("instructions", novo)
        self.assertEqual(novo.get("model"), "deepseek-flash")

        # A conversa reconstruida e a mesma que sairia da conversa original.
        esperado = _chat(CONVERSA)["messages"]
        obtido = novo["messages"]
        self.assertEqual(len(obtido), len(esperado))
        for indice, (a, b) in enumerate(zip(esperado, obtido)):
            self.assertEqual(a.get("role"), b.get("role"),
                             f"papel da mensagem {indice}")
            self.assertEqual(a.get("content"), b.get("content"),
                             f"conteudo da mensagem {indice}")
            self.assertEqual(bool(a.get("tool_calls")), bool(b.get("tool_calls")),
                             f"ferramentas da mensagem {indice}")
            for ca, cb in zip(a.get("tool_calls") or [], b.get("tool_calls") or []):
                self.assertEqual(ca["id"], cb["id"])
                self.assertEqual(ca["function"], cb["function"])

    def test_anthropic_para_chat_mantem_sistema_e_ferramentas(self):
        antigo = _anthropic()
        self.assertIn("system", antigo)
        self.assertNotIn("messages", {k: v for k, v in antigo.items() if k == "input"})

        convertidas = vmm._mensagens_de_anthropic(antigo)
        self.assertEqual(convertidas[0]["role"], "system")
        tipos = [m.get("role") for m in convertidas]
        self.assertIn("tool", tipos)

        observadas: list = []
        agente = _AgenteFalso("chat_completions", "deepseek-flash", observadas)
        novo, fio = vmm._payload_na_forma_do_destino(
            agente, antigo, "anthropic_messages", "chat_completions")
        self.assertIsNotNone(fio)
        mensagens = novo["messages"]
        self.assertEqual(mensagens[0]["role"], "system")
        self.assertIn("pt-PT", mensagens[0]["content"])
        chamadas = [tc for m in mensagens for tc in (m.get("tool_calls") or [])]
        self.assertTrue(chamadas, "a chamada de ferramenta perdeu-se")
        self.assertEqual(chamadas[0]["function"]["name"], "terminal")
        resultados = [m for m in mensagens if m.get("role") == "tool"]
        self.assertTrue(resultados, "o resultado da ferramenta perdeu-se")
        self.assertIn("a.txt", resultados[0]["content"])

    def test_mesmo_fio_nao_reconstroi(self):
        pedido = _chat(CONVERSA)
        observadas: list = []
        agente = _AgenteFalso("chat_completions", "deepseek-flash", observadas)
        novo, fio = vmm._payload_na_forma_do_destino(
            agente, pedido, "chat_completions", "chat_completions")
        self.assertIsNone(fio)
        self.assertIs(novo, pedido)
        self.assertEqual(observadas, [], "nao devia chamar o construtor")

    def test_destino_sem_construtor_devolve_intacto(self):
        pedido = _responses()
        novo, fio = vmm._payload_na_forma_do_destino(
            object(), pedido, "codex_responses", "chat_completions")
        self.assertIsNone(fio)
        self.assertIs(novo, pedido)

    def test_responses_sem_instrucoes_nao_inventa_sistema(self):
        pedido = _responses()
        pedido.pop("instructions", None)
        convertidas = vmm._mensagens_de_responses(pedido)
        self.assertTrue(convertidas)
        self.assertNotEqual(convertidas[0]["role"], "system")

    def test_itens_desconhecidos_caem_sem_partir_a_conversa(self):
        pedido = _responses()
        pedido["input"] = [
            {"type": "reasoning", "encrypted_content": "xxx"},
            {"type": "message", "role": "user", "content": "Ola"},
            {"type": "web_search_call", "id": "ws_1", "status": "completed"},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Bom dia"}]},
        ]
        convertidas = vmm._mensagens_de_responses(pedido)
        papeis = [m["role"] for m in convertidas]
        self.assertEqual(papeis, ["system", "user", "assistant"])
        self.assertEqual(convertidas[-1]["content"], "Bom dia")


if __name__ == "__main__":
    unittest.main(verbosity=2)
