#!/usr/bin/env python3
"""Testes do efeito por chamada (`resultado.py`).

O caso que mais importa e o `test_conteudo_com_exit_code_nao_conta`: o conteudo
de um `read_file` pode trazer a string `"exit_code"` e nao e uma medicao. Foi
exactamente esse o erro da primeira tentativa lexical, que dava 81% de falhas
no `terminal` por casar com JSONs escritos no proprio output.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

import resultado as r  # noqa: E402


def _efeito(texto):
    """`classificar` devolve `None` quando nao e medivel; nos testes de efeito
    isso e falha do teste, nao resultado a aceitar."""
    medido = r.classificar(texto)
    assert medido is not None, texto
    return medido["efeito"]


class TestClassificar(unittest.TestCase):
    def test_sucesso(self):
        self.assertEqual(r.classificar(json.dumps({"output": "x", "exit_code": 0, "error": None})),
                         {"exit_code": 0, "efeito": "ok"})

    def test_falha(self):
        self.assertEqual(r.classificar(json.dumps({"output": "", "exit_code": 2, "error": None})),
                         {"exit_code": 2, "efeito": "falha"})

    def test_codigo_negativo(self):
        self.assertEqual(_efeito('{"exit_code": -15}'), "falha")

    def test_exit_code_nulo(self):
        self.assertEqual(_efeito('{"exit_code": null}'), "desconhecido")

    def test_exit_code_em_texto(self):
        self.assertEqual(r.classificar('{"exit_code": "1"}'),
                         {"exit_code": 1, "efeito": "falha"})

    def test_exit_code_texto_invalido(self):
        self.assertEqual(_efeito('{"exit_code": "n/a"}'), "desconhecido")

    def test_sem_chave_nao_e_medivel(self):
        self.assertIsNone(r.classificar('{"content": "ola"}'))

    def test_conteudo_com_exit_code_nao_conta(self):
        # Forma real de um `read_file` que le um ficheiro que fala de exit_code.
        payload = {"content": 'x = {"exit_code": 1}\n' * 500, "total_lines": 500}
        self.assertIsNone(r.classificar(json.dumps(payload)))

    def test_erro_do_core_sem_parse(self):
        self.assertEqual(
            r.classificar("Error executing tool 'terminal': timed out after 180.0s"),
            {"exit_code": None, "efeito": "falha"})

    def test_cancelado(self):
        self.assertEqual(_efeito("[Tool execution cancelled — terminal was skipped. x]"),
                         "falha")

    def test_nao_json(self):
        self.assertIsNone(r.classificar("texto simples"))

    def test_json_lista(self):
        self.assertIsNone(r.classificar('[{"exit_code": 0}]'))

    def test_none_e_ditado(self):
        self.assertIsNone(r.classificar(None))
        self.assertIsNone(r.classificar(12345))

    def test_acima_do_limite_nao_e_medido(self):
        grande = '{"exit_code": 0, "output": "' + ("a" * (r.LIMITE_CHARS + 10)) + '"}'
        self.assertIsNone(r.classificar(grande))

    def test_booleano_nao_e_codigo(self):
        self.assertIsNone(r.classificar('{"exit_code": true}'))

    def test_json_malformado(self):
        self.assertIsNone(r.classificar('{"exit_code": 0'))

    def test_espacos_a_frente(self):
        self.assertEqual(_efeito('   \n {"exit_code": 0}'), "ok")


class TestEfeitoDoStatus(unittest.TestCase):
    def test_status_de_erro_conta(self):
        self.assertEqual((r.efeito_do_status("error") or {})["efeito"], "falha")
        self.assertEqual((r.efeito_do_status("blocked") or {})["efeito"], "falha")

    def test_status_de_sucesso_nao_e_medida(self):
        # Um `read_file` bem sucedido nao declara efeito: presumir `ok` a partir
        # do `status` gravaria ok para todas as chamadas e diluia o sinal.
        self.assertIsNone(r.efeito_do_status("ok"))
        self.assertIsNone(r.efeito_do_status("success"))
        self.assertIsNone(r.efeito_do_status("completed"))

    def test_status_desconhecido(self):
        self.assertIsNone(r.efeito_do_status("weird"))
        self.assertIsNone(r.efeito_do_status(None))
        self.assertIsNone(r.efeito_do_status(""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
