#!/usr/bin/env python3
"""Testes do ensaio em sombra (`sombra.py`).

O caso que mais importa e o `test_pre_registo_alterado_e_recusado`: uma margem
escolhida depois de ver os dados transforma qualquer resultado em «promover». O
sha256 sela os numeros antes, e a decisao recusa correr sobre um registo mexido.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

import sombra as s  # noqa: E402


def _registo(margem=0.01, p0=0.0463, deff=3.08):
    return s.desenhar(
        caro="caro", barato="barato", p0=p0, margem=margem, deff=deff,
        chamadas_por_dia=16000.0,
    )


class TestAmostra(unittest.TestCase):
    def test_margem_menor_pede_mais_amostra(self):
        n1 = s.n_por_braco(0.05, 0.01, 1.0)
        n2 = s.n_por_braco(0.05, 0.005, 1.0)
        self.assertGreater(n2, n1)

    def test_deff_inflaciona(self):
        n_sem = s.n_por_braco(0.05, 0.01, 1.0)
        n_com = s.n_por_braco(0.05, 0.01, 3.0)
        self.assertAlmostEqual(n_com / n_sem, 3.0, places=1)

    def test_ordem_de_grandeza_conhecida(self):
        """p=5%, margem 1pp, deff 1: a formula classica da ~6 000 por braco."""
        n = s.n_por_braco(0.05, 0.01, 1.0)
        self.assertTrue(5500 < n < 6800, n)

    def test_entradas_invalidas(self):
        with self.assertRaises(ValueError):
            s.n_por_braco(0, 0.01)
        with self.assertRaises(ValueError):
            s.n_por_braco(0.05, 0)


class TestSelo(unittest.TestCase):
    def test_selo_valido_no_nascimento(self):
        self.assertTrue(s.verificar(_registo()))

    def test_pre_registo_alterado_e_recusado(self):
        r = _registo()
        r["margem_nao_inferioridade"] = 0.10  # margem conveniente
        self.assertFalse(s.verificar(r))
        with self.assertRaises(ValueError):
            s.decidir(r, falhas_a=100, n_a=20000, falhas_b=150, n_b=20000)


class TestDecisao(unittest.TestCase):
    def test_nao_inferior_promove(self):
        r = _registo()
        d = s.decidir(r, falhas_a=927, n_a=18536, falhas_b=930, n_b=18536)
        self.assertEqual(d["decisao"], "promover")

    def test_barato_muito_pior_mantem(self):
        r = _registo()
        d = s.decidir(r, falhas_a=927, n_a=18536, falhas_b=1400, n_b=18536)
        self.assertEqual(d["decisao"], "manter")

    def test_ambiguo_e_indeterminado_e_nao_promove(self):
        r = _registo()
        # diferenca ~1pp com n grande: o IC cruza a margem
        d = s.decidir(r, falhas_a=927, n_a=18536, falhas_b=1100, n_b=18536)
        self.assertEqual(d["decisao"], "indeterminado")

    def test_amostra_curta_nunca_promove(self):
        """Mesmo com numeros favoraveis, abaixo do n pre-registado nao se decide."""
        r = _registo()
        d = s.decidir(r, falhas_a=10, n_a=200, falhas_b=10, n_b=200)
        self.assertIn("amostra abaixo", d["decisao"])
        self.assertFalse(d["amostra_suficiente"])

    def test_intervalo_e_coerente(self):
        r = _registo()
        d = s.decidir(r, falhas_a=927, n_a=18536, falhas_b=930, n_b=18536)
        inf, sup = d["ic95_unilateral"]
        self.assertLess(inf, d["diferenca"])
        self.assertGreater(sup, d["diferenca"])


class TestFicheiro(unittest.TestCase):
    def test_escrita_sela_no_disco(self):
        import tempfile

        r = _registo()
        destino = Path(tempfile.mkdtemp()) / "pre.json"
        info = s.escrever(r, destino)
        self.assertTrue(info["selo_valido"])
        self.assertGreater(info["bytes"], 100)
        relido = json.loads(destino.read_text(encoding="utf-8"))
        self.assertEqual(relido["n_por_braco"], r["n_por_braco"])


if __name__ == "__main__":
    unittest.main()
