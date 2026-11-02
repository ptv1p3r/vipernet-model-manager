#!/usr/bin/env python3
"""Testes do ciclo de aprendizagem — sem rede, sem tocar na config viva.

O que se prova aqui e o que pode mentir em silencio: a metrica, o kappa (que ja
devolveu acordo simples disfarcado de kappa quando estava preso a tres classes),
os portoes (que tem de fechar), o treino (que tem de melhorar num caso separavel)
e a leitura do JSON do modelo (que ja leu o corpo da API como se fosse o
conteudo e rotulou tudo `None`).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL.parent))
sys.path.insert(0, str(EVAL))

import aprender as A  # noqa: E402


class TestMetricas(unittest.TestCase):
    def test_macro_f1_perfeito(self):
        pares = [("leve", "leve"), ("media", "media"), ("alta", "alta")]
        m = A.metricas(pares)
        self.assertEqual(m["macro_f1"], 1.0)
        self.assertEqual(m["acerto"], 1.0)

    def test_macro_f1_conta_por_classe(self):
        # leve: prec 1,0 / cob 0,5 -> 0,667; media: 0; alta: prec 0,5 / cob 1,0 -> 0,667
        pares = [("leve", "leve"), ("leve", "media"), ("media", "alta"), ("alta", "alta")]
        m = A.metricas(pares)
        self.assertAlmostEqual(m["macro_f1"], 0.4445, places=4)
        self.assertEqual(m["por_classe"]["media"]["f1"], 0.0)
        self.assertEqual(m["por_classe"]["alta"]["suporte"], 1)

    def test_vazio_nao_divide_por_zero(self):
        self.assertEqual(A.metricas([])["n"], 0)


class TestKappa(unittest.TestCase):
    def test_acordo_perfeito(self):
        a = [{"id": "1", "rotulo": "leve"}, {"id": "2", "rotulo": "alta"}]
        self.assertEqual(A.kappa(a, a)["kappa"], 1.0)

    def test_vocabulario_binario(self):
        """Regressao: preso a NIVEIS, um juiz binario devolvia acordo como kappa."""
        a = [{"id": "1", "rotulo": "sim"}, {"id": "2", "rotulo": "sim"},
             {"id": "3", "rotulo": "nao"}, {"id": "4", "rotulo": "nao"}]
        b = [{"id": "1", "rotulo": "sim"}, {"id": "2", "rotulo": "nao"},
             {"id": "3", "rotulo": "nao"}, {"id": "4", "rotulo": "nao"}]
        k = A.kappa(a, b)
        # acordo 0,75; esperado 0,5 -> kappa 0,5 (com kappa preso a NIVEIS dava 0,75)
        self.assertEqual(k["acordo"], 0.75)
        self.assertEqual(k["kappa"], 0.5)

    def test_so_ids_comuns(self):
        a = [{"id": "1", "rotulo": "leve"}]
        b = [{"id": "2", "rotulo": "leve"}]
        self.assertEqual(A.kappa(a, b)["n"], 0)


class TestFronteiras(unittest.TestCase):
    def test_limiares(self):
        lim = {"media": 3, "alta": 6}
        self.assertEqual(A.decidir_score(0, lim), "leve")
        self.assertEqual(A.decidir_score(2, lim), "leve")
        self.assertEqual(A.decidir_score(3, lim), "media")
        self.assertEqual(A.decidir_score(5, lim), "media")
        self.assertEqual(A.decidir_score(6, lim), "alta")
        self.assertEqual(A.decidir_score(99, lim), "alta")


class TestConteudoJson(unittest.TestCase):
    def test_le_content_da_api(self):
        """Regressao: `resposta["json"]` e o corpo da API, nao o conteudo."""
        corpo = {"json": {"choices": [{"message": {"content": '{"nivel":"alta"}'}}]}}
        self.assertEqual(A.conteudo_json(corpo)["nivel"], "alta")

    def test_desembrulha_cerca(self):
        corpo = {"json": {"choices": [{"message": {"content": '```json\n{"nivel":"leve"}\n```'}}]}}
        self.assertEqual(A.conteudo_json(corpo)["nivel"], "leve")

    def test_lixo_devolve_vazio(self):
        self.assertEqual(A.conteudo_json({"json": {"choices": []}}), {})
        self.assertEqual(A.conteudo_json({}), {})
        corpo = {"json": {"choices": [{"message": {"content": "nao sei"}}]}}
        self.assertEqual(A.conteudo_json(corpo), {})

    def test_corpo_da_api_nao_e_confundido_com_conteudo(self):
        corpo = {"json": {"id": "x", "object": "chat.completion"}}
        self.assertEqual(A.conteudo_json(corpo), {})


class TestTreino(unittest.TestCase):
    def test_melhora_num_conjunto_separavel(self):
        """Pesos errados de proposito: o treino tem de os corrigir."""
        from analise import PESOS

        casos = [
            {"texto": "bom dia", "rotulo": "leve"},
            {"texto": "obrigado", "rotulo": "leve"},
            {"texto": "desenha a arquitetura do sistema e justifica o trade-off", "rotulo": "alta"},
            {"texto": "otimiza o algoritmo de ordenacao", "rotulo": "alta"},
        ]
        limiares = {"media": 3, "alta": 6}
        zeros = {k: 0 for k in PESOS}
        antes = A.avaliar_pesos(zeros, casos, limiares)
        novos, r = A.treinar(zeros, casos, limiares, passes=1)
        self.assertGreaterEqual(r["depois"]["macro_f1"], antes["macro_f1"])
        self.assertTrue(any(novos[k] != 0 for k in novos), "o treino nao mexeu em nenhum peso")

    def test_nao_quebra_com_zero_casos(self):
        from analise import PESOS

        novos, r = A.treinar(dict(PESOS), [], {"media": 3, "alta": 6}, passes=1)
        self.assertEqual(novos, dict(PESOS))
        self.assertEqual(r["depois"]["n"], 0)


class TestPortoes(unittest.TestCase):
    @staticmethod
    def _m(macro, media_f1, leve_f1=0.5, alta_f1=0.5):
        return {"macro_f1": macro, "acerto": macro, "n": 50,
                "por_classe": {"leve": {"f1": leve_f1}, "media": {"f1": media_f1},
                               "alta": {"f1": alta_f1}}}

    def test_fecha_com_kappa_baixo(self):
        passou, razoes = A.portoes({"n": 90, "kappa": 0.35}, self._m(0.5, 0.4),
                                   self._m(0.9, 0.9), 0.55, 0.02)
        self.assertFalse(passou)
        self.assertTrue(any("kappa" in r for r in razoes))

    def test_fecha_com_calibracao_insuficiente(self):
        passou, razoes = A.portoes({"n": 5, "kappa": 0.9}, self._m(0.5, 0.4),
                                   self._m(0.9, 0.9), 0.55, 0.02)
        self.assertFalse(passou)
        self.assertTrue(any("insuficiente" in r for r in razoes))

    def test_fecha_com_ganho_pequeno(self):
        passou, razoes = A.portoes({"n": 90, "kappa": 0.8}, self._m(0.70, 0.5),
                                   self._m(0.71, 0.6), 0.55, 0.02)
        self.assertFalse(passou)
        self.assertTrue(any("ganho" in r for r in razoes))

    def test_fecha_se_media_desce(self):
        passou, razoes = A.portoes({"n": 90, "kappa": 0.8}, self._m(0.50, 0.60),
                                   self._m(0.60, 0.55), 0.55, 0.02)
        self.assertFalse(passou)
        self.assertTrue(any("media" in r for r in razoes))

    def test_abre_quando_tudo_melhora(self):
        passou, razoes = A.portoes({"n": 90, "kappa": 0.8}, self._m(0.50, 0.40),
                                   self._m(0.62, 0.60), 0.55, 0.02)
        self.assertTrue(passou, razoes)


class TestColheita(unittest.TestCase):
    def test_exclui_gold_e_o_que_ja_foi_usado(self):
        pool = [{"id": "a" * 12, "texto": "bom dia equipa", "comprimento": 14},
                {"id": "b" * 12, "texto": "x" * 100, "comprimento": 100}]
        original_pool, original_cursor = A._pool, A.CURSOR
        try:
            A._pool = lambda: pool  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as d:
                A.CURSOR = Path(d) / "cursor.json"
                A.CURSOR.write_text(json.dumps({"usados": ["b" * 12]}), encoding="utf-8")
                casos, meta = A.colher(10, 1)
                self.assertEqual(meta["frescos"], 1)
                self.assertEqual([c["id"] for c in casos], ["a" * 12])
        finally:
            A._pool, A.CURSOR = original_pool, original_cursor

    def test_sem_novidades_nao_quebra(self):
        original_pool, original_cursor = A._pool, A.CURSOR
        try:
            A._pool = lambda: []  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as d:
                A.CURSOR = Path(d) / "cursor.json"
                casos, meta = A.colher(10, 1)
                self.assertEqual(casos, [])
                self.assertIn("motivo", meta)
        finally:
            A._pool, A.CURSOR = original_pool, original_cursor

    def test_deterministico_na_mesma_semente(self):
        pool = [{"id": f"{i:012d}", "texto": "y" * (20 + i), "comprimento": 20 + i}
                for i in range(40)]
        original_pool, original_cursor = A._pool, A.CURSOR
        try:
            A._pool = lambda: pool  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as d:
                A.CURSOR = Path(d) / "cursor.json"
                c1, _ = A.colher(8, 7)
                c2, _ = A.colher(8, 7)
                self.assertEqual([c["id"] for c in c1], [c["id"] for c in c2])
        finally:
            A._pool, A.CURSOR = original_pool, original_cursor


class TestReversao(unittest.TestCase):
    def test_sem_historico_recusa(self):
        original = A.HISTORICO
        try:
            with tempfile.TemporaryDirectory() as d:
                A.HISTORICO = Path(d) / "historico.jsonl"
                r = A.reverter()
                self.assertFalse(r["ok"])
                self.assertIn("erro", r)
        finally:
            A.HISTORICO = original


class TestVariantes(unittest.TestCase):
    def test_variantes_declaradas_e_json(self):
        modelo = A._modelo()
        self.assertEqual(sorted(modelo.PROMPTS), ["v1", "v2", "v3"])
        # O provider exige a palavra "json" no prompt para aceitar response_format.
        for nome, prompt in modelo.PROMPTS.items():
            self.assertIn("json", prompt.lower(), nome)

    def test_variante_invalida_e_recusada(self):
        modelo = A._modelo()
        _, erros = modelo.normalizar({"provider": "p", "modelo": "m", "prompt_variante": "v9"})
        self.assertTrue(any("prompt_variante" in e for e in erros))

    def test_variante_valida_sobrevive(self):
        modelo = A._modelo()
        d, erros = modelo.normalizar({"provider": "p", "modelo": "m", "prompt_variante": "v3"})
        self.assertEqual(erros, [])
        self.assertEqual(d["prompt_variante"], "v3")


if __name__ == "__main__":
    unittest.main(verbosity=1)
