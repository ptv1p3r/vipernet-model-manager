#!/usr/bin/env python3
"""Testes da poda de backups de config (`gestor._podar_backups`).

O defeito medido a 2026-11-05: 21 ficheiros `config.json.bak-*` na directoria de
dados, porque `_escrever` copiava e nada apagava. A politica decidida a
2026-11-04 — 10 mais recentes mais um por semana — nunca tinha sido aplicada.

Os testes protegem as duas guardas que fazem a poda segura: o nome tem de casar
o carimbo que o proprio `_escrever` produz, e as copias nomeadas a mao (as
antigas `.bak-perfil-*`, `.bak-limiar-*`) nunca sao apagadas.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

import gestor  # noqa: E402


class TestPoda(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.caminho = self.pasta / "config.json"
        self.caminho.write_text("{}\n", encoding="utf-8")

    def _copia(self, carimbo: str, mtime: float | None = None) -> Path:
        p = self.pasta / f"config.json.bak-{carimbo}"
        p.write_text("{}\n", encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def test_abaixo_do_limite_nao_apaga_nada(self):
        for i in range(8):
            self._copia(f"2026110{i + 1}T120000")
        apagadas = gestor._podar_backups(self.caminho)
        self.assertEqual(apagadas, [])
        self.assertEqual(len(list(self.pasta.glob("config.json.bak-*"))), 8)

    def test_acima_do_limite_fica_com_dez(self):
        agora = time.time()
        for i in range(15):
            self._copia(f"20261105T{10 + i:02d}0000", agora - (15 - i) * 60)
        apagadas = gestor._podar_backups(self.caminho)
        restantes = sorted(p.name for p in self.pasta.glob("config.json.bak-*"))
        self.assertEqual(len(restantes), 10, restantes)
        self.assertEqual(len(apagadas), 5)
        # os mais antigos sao os que saem; os 10 ultimos ficam
        self.assertIn("config.json.bak-20261105T240000", restantes)
        self.assertNotIn("config.json.bak-20261105T100000", restantes)

    def test_guarda_uma_copia_por_semana(self):
        agora = time.time()
        # 12 copias antigas, uma por semana distinta, todas fora das 10 recentes:
        # nenhuma semana fica representada, logo o tecto de 8 semanas manda.
        t = agora - 80 * 24 * 3600
        for _ in range(12):
            carimbo = time.strftime("%Y%m%dT%H%M%S", time.localtime(t))
            self._copia(carimbo, t)
            t += 7 * 24 * 3600
        for i in range(12):
            self._copia(f"20261105T{10 + i:02d}0000", agora - (12 - i) * 60)
        gestor._podar_backups(self.caminho)
        restantes = list(self.pasta.glob("config.json.bak-*"))
        # 10 recentes + no maximo 8 semanas distintas
        self.assertLessEqual(len(restantes), 18, sorted(p.name for p in restantes))
        self.assertGreaterEqual(len(restantes), 10)
        # as 10 mais recentes estao todas presentes (i = 2..11)
        nomes = {p.name for p in restantes}
        for i in range(2, 12):
            self.assertIn(f"config.json.bak-20261105T{10 + i:02d}0000", nomes)

    def test_copias_nomeadas_a_mao_ficam(self):
        manual = self.pasta / "config.json.bak-perfil-trabalho-20261104"
        manual.write_text("{}\n", encoding="utf-8")
        limite = self.pasta / "config.json.bak-limiar-20261103"
        limite.write_text("{}\n", encoding="utf-8")
        agora = time.time()
        for i in range(15):
            self._copia(f"20261105T{10 + i:02d}0000", agora - (15 - i) * 60)
        apagadas = gestor._podar_backups(self.caminho)
        self.assertTrue(manual.exists(), "apagou uma copia nomeada a mao")
        self.assertTrue(limite.exists(), "apagou uma copia nomeada a mao")
        nomes = {p.name for p in apagadas}
        self.assertNotIn(manual.name, nomes)
        self.assertNotIn(limite.name, nomes)

    def test_a_copia_recem_criada_nunca_sai(self):
        agora = time.time()
        for i in range(14):
            self._copia(f"20261105T{10 + i:02d}0000", agora - (20 - i) * 60)
        nova = self._copia("20261105T234500", agora)
        apagadas = gestor._podar_backups(self.caminho)
        self.assertTrue(nova.exists())
        self.assertNotIn(nova, apagadas)

    def test_gravacao_real_poda(self):
        agora = time.time()
        for i in range(14):
            self._copia(f"20261105T{10 + i:02d}0000", agora - (20 - i) * 60)
        cfg, erros = gestor.validar({"perfis": {}})
        self.assertEqual(erros, [])
        gestor._escrever(cfg, self.caminho)
        restantes = list(self.pasta.glob("config.json.bak-*"))
        self.assertLessEqual(len(restantes), 11, [p.name for p in restantes])


if __name__ == "__main__":
    unittest.main()
