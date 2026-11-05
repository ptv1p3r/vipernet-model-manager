"""Tarefa persistente por sessao (3.32.0) — a prova da correccao do drift.

O defeito: `analise.detectar_tarefa` le **so** a ultima mensagem do utilizador
(de proposito) e o pin dura um turno. O turno que traz "analise de arquitetura"
associava a rota certa; o seguinte ("continua", "agora escreve o parecer") nao
tinha nenhum dos termos, a associacao desaparecia e a complexidade escolhia
outro modelo — a conversa trocava de modelo a meio.

Este ficheiro prova seis coisas, cada uma com o seu caso negativo:

1. A tarefa atravessa turnos na mesma sessao, marcada `herdada` e com a idade
   contada — o rasto que permite medir se a heranca cola demais.
2. Sessao diferente nao herda nada; senao duas conversas partilhavam rota.
3. O prazo em turnos expira e a decisao volta a complexidade.
4. `complexidade: auto` sem tarefa no texto larga a associacao (unica saida
   manual) e o turno seguinte ja nao herda.
5. `activo: false` devolve o comportamento antigo: um turno, e nada mais.
6. A validacao recusa chave desconhecida e numeros fora dos limites, e o perfil
   herda do global chave a chave sem gravar uma copia do dia.

Uso: python3 test_tarefa_persistente.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("vmm_tarefa_persistente_test",
                                              ROOT / "__init__.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

TAREFA = "Faz uma análise de arquitetura."
SEGUINTE = "Diz apenas bom dia."


def config() -> dict:
    return {
        "activo": True,
        "analise": {"tarefa_persistente": {"activo": True, "expira_turnos": 6,
                                           "expira_min": 90}},
        "perfis": {"trabalho": {
            "activo": True, "estrategia": "prioridade",
            "tarefas": {"arquitetura": {
                "termos": ["analise de arquitetura", "architecture review"],
                "prioridade": 10}},
            "catalogo": [
                {"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
                 "complexidade": ["leve", "media"]},
                {"id": "pro", "provider": "deepseek", "modelo": "deepseek-v4-pro",
                 "complexidade": ["alta"]},
                {"id": "sol", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
                 "complexidade": ["alta"], "tarefas": ["arquitetura"]},
            ],
        }},
    }


class Agente:
    provider = "deepseek"
    model = "deepseek-flash"

    def switch_model(self, **kw):
        self.provider, self.model = kw["new_provider"], kw["new_model"]


class TarefaPersistente(unittest.TestCase):
    def setUp(self):
        self.agent = Agente()
        self.events: list[dict] = []
        self.cfg = config()
        p._historico.clear()
        p._contadores.clear()
        p._turno.clear()
        # Estado novo do 3.32.0: sem isto um teste herdava a tarefa do anterior,
        # que e exactamente o comportamento que se esta a medir.
        p._tarefa_sessao.clear()
        self.patches = [
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
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def run_turn(self, texto: str, *, sessao: str = "persist-test",
                 turno: str = "a1", history: list | None = None) -> dict:
        tid = f"{sessao}:{sessao}:{turno}"
        p.middleware_llm_request(
            request={"model": self.agent.model,
                     "messages": (history or []) + [{"role": "user", "content": texto}]},
            session_id=sessao, provider=self.agent.provider, turn_id=tid,
            api_call_count=1)
        # O desfecho fica na linha `resposta`, que e onde o rasto da decisao vive
        # desde que o `sem_troca` saiu.
        p.pos_resposta(session_id=sessao, model=self.agent.model,
                       provider=self.agent.provider,
                       response_model=self.agent.model, finish_reason="stop",
                       usage={"total_tokens": 10}, platform="api_server",
                       turn_id=tid)
        return self.events[-1]

    # 1 ------------------------------------------------------------------
    def test_tarefa_atravessa_turnos_na_mesma_sessao(self):
        e1 = self.run_turn(TAREFA, turno="a1")
        self.assertEqual(e1["origem"], "tarefa")
        self.assertEqual(e1["tarefa_origem"], "detectada")
        self.assertEqual(self.agent.model, "openai/gpt-6.1-sol")

        e2 = self.run_turn(SEGUINTE, turno="a2", history=[
            {"role": "user", "content": TAREFA},
            {"role": "assistant", "content": "Resultado da análise de arquitetura."}])
        self.assertEqual(self.agent.model, "openai/gpt-6.1-sol",
                         "o modelo da tarefa tem de sobreviver ao turno seguinte")
        self.assertEqual(e2["origem"], "tarefa")
        self.assertEqual(e2["tarefa"], "arquitetura")
        self.assertEqual(e2["tarefa_origem"], "herdada")
        self.assertEqual(e2["tarefa_idade_turnos"], 1)

    def test_idade_avanca_a_cada_turno_herdado(self):
        self.run_turn(TAREFA, turno="a1")
        e2 = self.run_turn(SEGUINTE, turno="a2")
        e3 = self.run_turn(SEGUINTE, turno="a3")
        self.assertEqual(e2["tarefa_idade_turnos"], 1)
        self.assertEqual(e3["tarefa_idade_turnos"], 2)

    # 2 ------------------------------------------------------------------
    def test_sessao_diferente_nao_herda(self):
        self.run_turn(TAREFA, sessao="A", turno="a1")
        self.assertEqual(self.agent.model, "openai/gpt-6.1-sol")
        e = self.run_turn(SEGUINTE, sessao="B", turno="b1")
        self.assertEqual(self.agent.model, "deepseek-flash")
        self.assertEqual(e["origem"], "analise")
        self.assertNotIn("tarefa", e)

    # 3 ------------------------------------------------------------------
    def test_prazo_em_turnos_expira(self):
        self.cfg["analise"]["tarefa_persistente"]["expira_turnos"] = 1
        self.run_turn(TAREFA, turno="a1")
        e2 = self.run_turn(SEGUINTE, turno="a2")
        self.assertEqual(e2["tarefa_origem"], "herdada")
        e3 = self.run_turn(SEGUINTE, turno="a3")
        self.assertEqual(e3["origem"], "analise")
        self.assertEqual(self.agent.model, "deepseek-flash")

    def test_prazo_em_minutos_expira(self):
        self.cfg["analise"]["tarefa_persistente"]["expira_min"] = 1
        self.run_turn(TAREFA, turno="a1")
        # Envelhece o registo sem mexer no relogio do processo.
        chave = "trabalho\x1fpersist-test"
        p._tarefa_sessao[chave]["ts"] -= 120
        e2 = self.run_turn(SEGUINTE, turno="a2")
        self.assertEqual(e2["origem"], "analise")
        self.assertNotIn(chave, p._tarefa_sessao)

    # 4 ------------------------------------------------------------------
    def test_marcador_auto_larga_a_tarefa(self):
        self.run_turn(TAREFA, turno="a1")
        self.run_turn(SEGUINTE + " complexidade: auto", turno="a2")
        resets = [e for e in self.events if e.get("evento") == "reset"]
        self.assertTrue(resets, "o marcador tem de produzir um evento de reset")
        self.assertTrue(resets[-1].get("tarefa_limpa"),
                        "o reset tem de declarar que largou a tarefa")
        e3 = self.run_turn(SEGUINTE, turno="a3")
        self.assertEqual(e3["origem"], "analise")
        self.assertEqual(self.agent.model, "deepseek-flash")

    # 5 ------------------------------------------------------------------
    def test_desligado_devolve_o_comportamento_antigo(self):
        self.cfg["analise"]["tarefa_persistente"]["activo"] = False
        self.run_turn(TAREFA, turno="a1")
        e2 = self.run_turn(SEGUINTE, turno="a2")
        self.assertEqual(e2["origem"], "analise")
        self.assertEqual(self.agent.model, "deepseek-flash")

    # 6 ------------------------------------------------------------------
    def test_validacao_recusa_chave_desconhecida(self):
        self.cfg["analise"]["tarefa_persistente"]["expira_turnso"] = 3
        _, erros = p.gestor.validar(self.cfg)
        self.assertTrue(any("expira_turnso" in e for e in erros), erros)

    def test_validacao_recusa_numeros_fora_dos_limites(self):
        for valor in (0, -1, 51):
            self.cfg["analise"]["tarefa_persistente"]["expira_turnos"] = valor
            _, erros = p.gestor.validar(self.cfg)
            self.assertTrue(any("expira_turnos fora de [1, 50]" in e for e in erros),
                            (valor, erros))
        self.cfg["analise"]["tarefa_persistente"]["expira_turnos"] = 60
        _, erros = p.gestor.validar(self.cfg)
        self.assertTrue(any("expira_turnos" in e for e in erros))

    def test_validacao_recusa_tipos_errados(self):
        self.cfg["analise"]["tarefa_persistente"]["expira_turnos"] = "6"
        _, erros = p.gestor.validar(self.cfg)
        self.assertTrue(any("expira_turnos tem de ser inteiro" in e for e in erros))
        self.cfg["analise"]["tarefa_persistente"]["expira_turnos"] = 6
        self.cfg["analise"]["tarefa_persistente"]["activo"] = "sim"
        _, erros = p.gestor.validar(self.cfg)
        self.assertTrue(any("activo tem de ser booleano" in e for e in erros))

    def test_perfil_herda_chave_a_chave(self):
        self.cfg["perfis"]["trabalho"]["analise"] = {
            "tarefa_persistente": {"expira_turnos": 2}}
        limpo, erros = p.gestor.validar(self.cfg)
        self.assertEqual(erros, [])
        pcfg = p.gestor.perfil_config(limpo, "trabalho")
        bloco = pcfg["analise"]["tarefa_persistente"]
        self.assertEqual(bloco["expira_turnos"], 2)
        self.assertTrue(bloco["activo"], "o activo do global tem de chegar ao perfil")
        self.assertEqual(bloco["expira_min"], 90)
        # O global fica no valor dele: o perfil nao o contaminou.
        self.assertEqual(limpo["analise"]["tarefa_persistente"]["expira_turnos"], 6)

    def test_perfil_nao_guarda_copia_do_global(self):
        self.cfg["perfis"]["trabalho"]["analise"] = {
            "tarefa_persistente": {"expira_turnos": 6}}
        limpo, erros = p.gestor.validar(self.cfg)
        self.assertEqual(erros, [])
        self.assertNotIn("tarefa_persistente",
                         limpo["perfis"]["trabalho"].get("analise") or {},
                         "repetir o valor global fixa o perfil ao dia")


if __name__ == "__main__":
    unittest.main(verbosity=2)
