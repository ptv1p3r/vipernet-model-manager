#!/usr/bin/env python3
"""Testes do custo por turno e por resultado (`custo.py`).

O caso que mais importa e o `test_tokens_em_falta_nao_e_preco_em_falta`: na
primeira versao, uma linha de `resposta` sem bloco `tokens` era contabilizada
como «modelo sem preco» e marcava turnos do `deepseek-flash` como se o preco
dele fosse desconhecido — falso, e capaz de zerar o denominador de custo sem
ninguem dar por isso.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

import custo as c  # noqa: E402


def _ledger(linhas):
    fd, nome = tempfile.mkstemp(suffix=".jsonl")
    with open(fd, "w", encoding="utf-8") as f:
        for l in linhas:
            f.write(json.dumps(l, ensure_ascii=False) + "\n")
    return Path(nome)


def _base_db(caminho: Path, registos):
    con = sqlite3.connect(caminho)
    con.execute(
        """create table session_model_usage (
               session_id text, model text, estimated_cost_usd real)"""
    )
    con.executemany("insert into session_model_usage values (?,?,?)", registos)
    con.commit()
    con.close()


class TestPrecos(unittest.TestCase):
    def test_preco_deepseek_reproduz_o_core(self):
        """O preco derivado tem de dar exactamente a conta do core.

        1 000 000 de input, 0 de cache e 0 de output = 0,15 USD.
        """
        tok = {"input": 1_000_000, "cache_read": 0, "output": 0}
        valor = c.custo_de("deepseek-flash", tok)
        assert valor is not None
        self.assertAlmostEqual(valor, 0.1500, places=6)
        tok = {"input": 0, "cache_read": 1_000_000, "output": 0}
        valor = c.custo_de("deepseek-flash", tok)
        assert valor is not None
        self.assertAlmostEqual(valor, 0.0030, places=6)
        tok = {"input": 0, "cache_read": 0, "output": 1_000_000}
        valor = c.custo_de("deepseek-flash", tok)
        assert valor is not None
        self.assertAlmostEqual(valor, 0.6000, places=6)

    def test_gratuito_custa_zero(self):
        tok = {"input": 5_000_000, "cache_read": 0, "output": 5_000_000}
        self.assertEqual(c.custo_de("thinkingmachines/inkling:free", tok), 0.0)

    def test_modelo_pago_desconhecido_nao_e_inventado(self):
        """Aplicar o preco do deepseek a um modelo pago daria um numero errado
        por ordens de grandeza, com aspecto de medido."""
        tok = {"input": 1000, "cache_read": 0, "output": 1000}
        self.assertIsNone(c.custo_de("openai/gpt-6.1-sol", tok))
        self.assertFalse(c.modelo_tem_preco("openai/gpt-6.1-sol"))

    def test_tokens_em_falta_nao_e_preco_em_falta(self):
        """Regressao: `None` por falta de tokens nao pode marcar o modelo."""
        self.assertIsNone(c.custo_de("deepseek-flash", None))
        self.assertTrue(c.modelo_tem_preco("deepseek-flash"))

    def test_cache_negativo_nao_existe(self):
        """Um provider que reporte input > prompt nao pode dar custo negativo."""
        tok = c._tokens({"input_tokens": 500, "prompt_tokens": 100, "output_tokens": 10})
        assert tok is not None
        self.assertEqual(tok["cache_read"], 0)

    def test_prompt_inclui_cache(self):
        tok = c._tokens({"input_tokens": 100, "prompt_tokens": 900, "output_tokens": 5})
        assert tok is not None
        self.assertEqual(tok, {"input": 100, "cache_read": 800, "output": 5})

    def test_bloco_ilegivel_devolve_none(self):
        self.assertIsNone(c._tokens("nao e dict"))
        self.assertIsNone(c._tokens({"input_tokens": "abc"}))


class TestTurnos(unittest.TestCase):
    def test_custo_e_efeito_no_mesmo_turno(self):
        L = _ledger([
            {"evento": "resposta", "turn_id": "t1", "sessao": "s1",
             "respondido_por": "deepseek-flash",
             "tokens": {"input_tokens": 1000, "prompt_tokens": 1000, "output_tokens": 100}},
            {"evento": "efeito", "turno": "t1", "efeito": "ok"},
            {"evento": "efeito", "turno": "t1", "efeito": "falha"},
        ])
        t = c.por_turno(L)
        self.assertIn("t1", t)
        self.assertEqual(t["t1"]["falhas"], 1)
        self.assertTrue(t["t1"]["tem_efeito"])
        self.assertTrue(t["t1"]["todos_com_preco"])
        self.assertIsNotNone(t["t1"]["custo"])
        L.unlink()

    def test_turno_sem_tokens_declara_subconta(self):
        L = _ledger([
            {"evento": "resposta", "turn_id": "t1", "sessao": "s1",
             "respondido_por": "deepseek-flash"},
        ])
        t = c.por_turno(L)
        self.assertEqual(t["t1"]["chamadas_sem_tokens"], 1)
        self.assertEqual(t["t1"]["modelos_sem_preco"], [])
        self.assertIsNotNone(t["t1"]["custo"])
        L.unlink()

    def test_turno_com_modelo_pago_sem_preco_fica_nulo(self):
        L = _ledger([
            {"evento": "resposta", "turn_id": "t1", "sessao": "s1",
             "respondido_por": "openai/gpt-6.1-sol",
             "tokens": {"input_tokens": 10, "prompt_tokens": 10, "output_tokens": 5}},
        ])
        t = c.por_turno(L)
        self.assertIsNone(t["t1"]["custo"])
        self.assertEqual(t["t1"]["modelos_sem_preco"], ["openai/gpt-6.1-sol"])
        L.unlink()

    def test_resumo_sem_turno_ok_nao_inventa_custo(self):
        L = _ledger([
            {"evento": "resposta", "turn_id": "t1", "sessao": "s1",
             "respondido_por": "deepseek-flash",
             "tokens": {"input_tokens": 1000, "prompt_tokens": 1000, "output_tokens": 10}},
            {"evento": "efeito", "turno": "t1", "efeito": "falha"},
        ])
        r = c.resumo(L)
        self.assertIsNone(r["custo_por_turno_ok_usd"])
        self.assertIsNotNone(r["custo_por_turno_falha_usd"])
        self.assertEqual(r["turnos_com_falha"], 1)
        L.unlink()


class TestAtribuicao(unittest.TestCase):
    def test_soma_atribuida_igual_ao_total_do_core(self):
        """O ponto da atribuicao: a soma dos turnos e o total do core, não um
        limite inferior de -4,7% por o ledger nao apanhar os tokens de raciocinio."""
        L = _ledger([
            {"evento": "resposta", "turn_id": "t1", "sessao": "s1",
             "respondido_por": "deepseek-flash",
             "tokens": {"input_tokens": 1000, "prompt_tokens": 1000, "output_tokens": 10}},
            {"evento": "resposta", "turn_id": "t2", "sessao": "s1",
             "respondido_por": "deepseek-flash",
             "tokens": {"input_tokens": 3000, "prompt_tokens": 3000, "output_tokens": 30}},
        ])
        db = Path(tempfile.mkdtemp()) / "state.db"
        _base_db(db, [("s1", "deepseek-flash", 9.0)])
        t = c.atribuido_por_turno(L, db)
        rec = t.pop("_reconciliacao")
        self.assertAlmostEqual(rec["desvio_usd"], 0.0, places=6)
        # t2 tem 3x os tokens de t1, logo 3x o custo
        self.assertAlmostEqual(t["t2"]["custo_atribuido"] / t["t1"]["custo_atribuido"],
                               3.0, places=3)
        self.assertAlmostEqual(t["t1"]["custo_atribuido"] + t["t2"]["custo_atribuido"],
                               9.0, places=6)
        L.unlink()

    def test_sessao_fora_do_core_nao_desaparece(self):
        L = _ledger([
            {"evento": "resposta", "turn_id": "t1", "sessao": "s-nova",
             "respondido_por": "deepseek-flash",
             "tokens": {"input_tokens": 1000, "prompt_tokens": 1000, "output_tokens": 10}},
        ])
        db = Path(tempfile.mkdtemp()) / "state.db"
        _base_db(db, [])
        t = c.atribuido_por_turno(L, db)
        t.pop("_reconciliacao")
        self.assertGreater(t["t1"]["custo_atribuido"], 0)
        L.unlink()


if __name__ == "__main__":
    unittest.main()
