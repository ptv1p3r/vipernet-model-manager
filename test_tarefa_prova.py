"""Tarefa detectada — a **prova** da deteccao e o fim do termo generico sozinho.

O que se corrigiu (3.47.10), medido a 2026-11-08 no registo de utilizacao do
perfil `default`:

1. A linha dizia `origem: tarefa` e nada mais. Nao se sabia qual tarefa, se
   vinha deste turno ou de um anterior, nem **que termo** a fixou — uma
   atribuicao que ninguem consegue auditar. Agora o ledger leva o termo, a lista
   de termos encontrados, o numero de caracteres analisados e o `sha256` do
   texto (ja sem contexto injectado), e a heranca transporta essa prova.
2. `sensor`, `entidade` e `automação` sao termos **genericos**: aparecem em
   trabalho de plugin, de codigo e de documentacao. Sozinhos associavam a
   tarefa e mandavam o turno para o modelo dela. Um generico so conta quando
   outro termo da mesma tarefa o acompanha.

Uso: python3 test_tarefa_prova.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VMM_IDIOMA", "pt")

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("vmm_tarefa_prova_test", ROOT / "__init__.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

analise = p._analise_mod

REGRAS = {
    "home-assistant": {"termos": ["home assistant", "sensor", "entidade", "automação"],
                       "prioridade": 8},
    "arquitetura": {"termos": ["análise de arquitetura"], "prioridade": 10},
}


def msg(texto: str) -> list[dict]:
    return [{"role": "user", "content": texto}]


class Detector(unittest.TestCase):
    # --- termo generico nao associa sozinho -------------------------------
    def test_generico_sozinho_nao_detecta(self):
        for texto in ("o sensor de versao do plugin esta errado",
                      "a entidade nova nao aparece no painel",
                      "corrige a automação do registo"):
            self.assertIsNone(analise.detectar_tarefa(msg(texto), REGRAS), texto)

    def test_generico_mais_especifico_detecta(self):
        d = analise.detectar_tarefa_detalhe(
            msg("o sensor do home assistant esta unavailable"), REGRAS)
        self.assertIsNotNone(d)
        self.assertEqual(d["tarefa"], "home-assistant")
        self.assertEqual(sorted(d["termos"]), ["home assistant", "sensor"])

    def test_dois_genericos_detectam(self):
        d = analise.detectar_tarefa_detalhe(
            msg("a entidade e o sensor do quadro"), REGRAS)
        self.assertIsNotNone(d)
        self.assertEqual(d["tarefa"], "home-assistant")
        self.assertTrue(d["generico"])

    # --- a prova ----------------------------------------------------------
    def test_prova_traz_termo_e_digest(self):
        texto = "revê a análise de arquitetura do módulo"
        d = analise.detectar_tarefa_detalhe(msg(texto), REGRAS)
        self.assertEqual(d["tarefa"], "arquitetura")
        self.assertEqual(d["termo"], "análise de arquitetura")
        self.assertFalse(d["generico"])
        self.assertEqual(len(d["texto_sha"]), 16)
        self.assertGreater(d["texto_chars"], 0)
        # Repetivel: o mesmo texto da o mesmo digest, e e o digest do texto
        # normalizado que o detector leu (minusculas, sem acentos, sem contexto).
        self.assertEqual(d, analise.detectar_tarefa_detalhe(msg(texto), REGRAS))

    def test_contexto_injectado_nao_serve_de_prova(self):
        # O caso do dono: um turno de trabalho no model manager com memoria
        # injectada a falar de Home Assistant nao e um turno de Home Assistant.
        texto = ("fecha o PR do plugin\n\n"
                 "<memory-context>\n## Mnemosyne Context\n"
                 "[2026-10-07] home assistant: o sensor e a entidade da bateria\n"
                 "</memory-context>")
        self.assertIsNone(analise.detectar_tarefa(msg(texto), REGRAS))
        d = analise.detectar_tarefa_detalhe(msg(texto), REGRAS)
        self.assertIsNone(d)

    def test_documento_anexado_nao_classifica_o_pedido(self):
        # Wrapper de pesquisa do core: o pedido vem depois do `</context>`.
        # O documento anexado falava de Home Assistant inteiro; o pedido nao.
        contexto = ("### Task:\nResponde usando o contexto.\n<context>\n"
                    "<source id=\"1\">O sensor do home assistant e a entidade da "
                    "automação da bateria.</source>\n</context>\n\n"
                    "corrige o 409 ao gravar a lingua no painel")
        self.assertIsNone(analise.detectar_tarefa(msg(contexto), REGRAS))

    def test_pedido_depois_do_contexto_continua_a_contar(self):
        contexto = ("### Task:\nResponde usando o contexto.\n<context>\n"
                    "<source id=\"1\">documentacao irrelevante</source>\n</context>\n\n"
                    "o sensor do home assistant ficou unavailable")
        d = analise.detectar_tarefa_detalhe(msg(contexto), REGRAS)
        self.assertEqual(d["tarefa"], "home-assistant")
        self.assertEqual(d["termo"], "home assistant")
        # O digest cobre so o pedido: 0 documento nao entra na prova.
        self.assertLess(d["texto_chars"], len(contexto))


class Config:
    """Config minima com as duas tarefas e um catalogo por tarefa."""

    def build(self) -> dict:
        return {
            "activo": True,
            "analise": {"tarefa_persistente": {"activo": True, "expira_turnos": 6,
                                               "expira_min": 90}},
            "perfis": {"trabalho": {
                "activo": True, "estrategia": "prioridade",
                "tarefas": REGRAS,
                "catalogo": [
                    {"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
                     "complexidade": ["leve", "media"]},
                    {"id": "pro", "provider": "deepseek", "modelo": "deepseek-v4-pro",
                     "complexidade": ["alta"]},
                    {"id": "ha", "provider": "deepseek", "modelo": "deepseek-v4-pro",
                     "complexidade": ["leve", "media", "alta"],
                     "tarefas": ["home-assistant"]},
                ],
            }},
        }


class Agente:
    provider = "deepseek"
    model = "deepseek-flash"

    def switch_model(self, **kw):
        self.provider, self.model = kw["new_provider"], kw["new_model"]


class ProvaNoRegisto(unittest.TestCase):
    def setUp(self):
        self.agent = Agente()
        self.events: list[dict] = []
        self.cfg = Config().build()
        p._historico.clear()
        p._contadores.clear()
        p._turno.clear()
        p._tarefa_sessao.clear()
        for item in [
            patch.object(p, "_config",
                         side_effect=lambda: p.gestor.validar(self.cfg)[0]),
            patch.object(p.gestor, "perfil_atual", return_value="trabalho"),
            patch.object(p, "agente_vivo", return_value=self.agent),
            patch.object(p, "_registar",
                         side_effect=lambda e, **kw: self.events.append(e)),
            patch.object(p, "resolver_rota",
                         side_effect=lambda perfil, provider, modelo: {
                             "provider": provider, "api_key": "",
                             "base_url": "", "api_mode": ""}),
        ]:
            m = item.start()
            self.addCleanup(item.stop)

    def run_turn(self, texto: str, *, turno: str) -> dict:
        tid = f"prova:{turno}"
        p.middleware_llm_request(
            request={"model": self.agent.model, "messages": msg(texto)},
            session_id="prova", provider=self.agent.provider, turn_id=tid,
            api_call_count=1)
        p.pos_resposta(session_id="prova", model=self.agent.model,
                       provider=self.agent.provider,
                       response_model=self.agent.model, finish_reason="stop",
                       usage={"total_tokens": 10}, platform="api_server",
                       turn_id=tid)
        return self.events[-1]

    def test_deteccao_leva_a_prova_ao_ledger(self):
        e = self.run_turn("controla o sensor do home assistant", turno="t1")
        self.assertEqual(e["tarefa"], "home-assistant")
        self.assertEqual(e["tarefa_origem"], "detectada")
        self.assertEqual(e["tarefa_termo"], "home assistant")
        self.assertIn("sensor", e["tarefa_termos"])
        self.assertEqual(len(e["tarefa_texto_sha"]), 16)
        self.assertGreater(e["tarefa_texto_chars"], 0)
        self.assertIn(e["nivel_origem"], ("analise", "modelo", "marcador", "superficie"))

    def test_heranca_transporta_a_prova_do_turno_que_detectou(self):
        e1 = self.run_turn("controla o sensor do home assistant", turno="t1")
        e2 = self.run_turn("agora escreve o resumo", turno="t2")
        self.assertEqual(e2["tarefa"], "home-assistant")
        self.assertEqual(e2["tarefa_origem"], "herdada")
        self.assertEqual(e2["tarefa_termo"], e1["tarefa_termo"])
        self.assertEqual(e2["tarefa_texto_sha"], e1["tarefa_texto_sha"],
                         "a heranca tem de manter o digest do turno que detectou")

    def test_turno_de_plugin_nao_e_home_assistant(self):
        e = self.run_turn("corrige o campo sensor do formulario do provider no plugin",
                          turno="t1")
        # A ausencia de tarefa nao se escreve no ledger: o campo simplesmente
        # nao existe. O que se exige e que o turno nao seja atribuido a ela.
        self.assertIsNone(e.get("tarefa"))
        self.assertIsNone(e.get("tarefa_termo"))
        self.assertNotEqual(e.get("origem"), "tarefa")


if __name__ == "__main__":
    unittest.main(verbosity=2)
