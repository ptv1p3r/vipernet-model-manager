#!/usr/bin/env python3
"""Testes do veredicto de entrega (`veredicto.py`).

O caso que mais importa e o `test_pass_exige_todas_as_fontes`: o agregado so
pode dizer `pass` quando todas as fontes que falaram dizem `pass`. Uma tarefa
com o gate verde e a releitura não feita não esta fechada, e um agregado
permissivo transformaria «não medi» em «está bem».
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

import veredicto as v  # noqa: E402


def _ledger():
    fd, nome = tempfile.mkstemp(suffix=".jsonl")
    import os
    os.close(fd)
    return Path(nome)


class TestValidacao(unittest.TestCase):
    def test_estado_invalido(self):
        self.assertTrue(v.validar_veredicto("talvez", "gate", "prova"))

    def test_fonte_invalida(self):
        erros = v.validar_veredicto("pass", "porque-sim", "prova")
        self.assertTrue(any("fonte" in e for e in erros))

    def test_evidencia_vazia(self):
        erros = v.validar_veredicto("pass", "gate", "   ")
        self.assertTrue(any("evidencia vazia" in e for e in erros))

    def test_evidencia_longa(self):
        self.assertTrue(v.validar_veredicto("pass", "gate", "x" * 600))

    def test_valido_nao_tem_erros(self):
        self.assertEqual(v.validar_veredicto("pass", "gate", "PASS 19/19"), [])

    def test_registar_recusa_antes_de_escrever(self):
        L = _ledger()
        with self.assertRaises(ValueError):
            v.registar(L, tarefa="x", estado="pass", fonte="inventada", evidencia="y")
        self.assertEqual(v.resumo(L)["veredictos"], 0, "nao pode ter escrito nada")
        L.unlink()

    def test_tarefa_vazia(self):
        L = _ledger()
        with self.assertRaises(ValueError):
            v.registar(L, tarefa="", estado="pass", fonte="gate", evidencia="y")
        L.unlink()


class TestRegisto(unittest.TestCase):
    def test_escrita_e_leitura(self):
        L = _ledger()
        v.registar(L, tarefa="t", estado="pass", fonte="gate",
                   evidencia="PASS", perfil="default")
        todos = v.veredictos(L)
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0]["estado"], "pass")
        self.assertEqual(todos[0]["perfil"], "default")
        L.unlink()

    def test_ultimo_por_tarefa_usa_o_mais_recente(self):
        """Uma fonte pode corrigir-se: o que vale e o ultimo, os outros ficam
        como historia no ledger."""
        L = _ledger()
        v.registar(L, tarefa="t", estado="falha", fonte="gate", evidencia="1 falha",
                   agora="2026-11-05T01:00:00+00:00")
        v.registar(L, tarefa="t", estado="pass", fonte="gate", evidencia="corrigido",
                   agora="2026-11-05T02:00:00+00:00")
        u = v.ultimo_por_tarefa(L)
        self.assertEqual(u["t/gate"]["estado"], "pass")
        self.assertEqual(len(v.veredictos(L)), 2, "a historia nao e apagada")
        L.unlink()


class TestAgregado(unittest.TestCase):
    def test_sem_veredicto_tem_nome_proprio(self):
        L = _ledger()
        r = v.estado_da_tarefa(L, "nunca-medida")
        self.assertEqual(r["agregado"], "sem_veredicto")
        L.unlink()

    def test_pass_exige_todas_as_fontes(self):
        L = _ledger()
        v.registar(L, tarefa="t", estado="pass", fonte="gate", evidencia="ok")
        v.registar(L, tarefa="t", estado="pass", fonte="releitura", evidencia="ok")
        self.assertEqual(v.estado_da_tarefa(L, "t")["agregado"], "pass")
        L.unlink()

    def test_falha_derruba_o_agregado(self):
        L = _ledger()
        v.registar(L, tarefa="t", estado="pass", fonte="gate", evidencia="ok")
        v.registar(L, tarefa="t", estado="falha", fonte="releitura", evidencia="divergiu")
        self.assertEqual(v.estado_da_tarefa(L, "t")["agregado"], "falha")
        L.unlink()

    def test_parcial(self):
        L = _ledger()
        v.registar(L, tarefa="t", estado="pass", fonte="gate", evidencia="ok")
        v.registar(L, tarefa="t", estado="parcial", fonte="ha", evidencia="faltou releitura")
        self.assertEqual(v.estado_da_tarefa(L, "t")["agregado"], "parcial")
        L.unlink()

    def test_tarefas_nao_se_misturam(self):
        L = _ledger()
        v.registar(L, tarefa="a", estado="pass", fonte="gate", evidencia="ok")
        v.registar(L, tarefa="b", estado="falha", fonte="gate", evidencia="nao")
        self.assertEqual(v.estado_da_tarefa(L, "a")["agregado"], "pass")
        self.assertEqual(v.estado_da_tarefa(L, "b")["agregado"], "falha")
        L.unlink()


if __name__ == "__main__":
    unittest.main()
