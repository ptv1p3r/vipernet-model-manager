#!/usr/bin/env python3
"""Coerencia entre o manifesto, o painel e a entrada de catalogo.

O defeito que este ficheiro prende apareceu **duas vezes** no projecto: a
entrada `plugin-catalog/vipernet-model-manager.yaml` a dizer uma versao e um
pin que o codigo ja' nao tinha, e nada a falhar. Um catalogo que anuncia
`version: 3.47.6` a pinar um commit de outra versao e' uma pagina que mente ao
revisor — e o validador do nucleo nao a apanha, porque os dois numeros sao
validos cada um por si.

O que fica preso, cada ponto com a sua guarda contra falso positivo:

1. A entrada existe e tem os campos que o loader le (`name`, `repo`, `sha`,
   `version`, `requires_hermes`, `capabilities`, `screenshots`).
2. `sha` e' 40 hex minusculas — ramos, tags e shas curtos sao recusados pelo
   loader do catalogo.
3. As tres `screenshots` existem, sao https e carregam **o mesmo sha** do pin:
   um bump de pin sem re-pinar as imagens serve a imagem do commit antigo.
4. `name`, `requires_hermes` e `capabilities` da entrada dizem o mesmo que o
   `plugin.yaml` — deriva aqui e' capacidade declarada que nao casa com a real.
5. A `version` da entrada nunca esta' **a frente** da versao do codigo. Ficar
   atras e' legitimo e esperado no `dev` (o pin so' avanca depois do merge); a
   frente e' impossivel e significa que alguem escreveu um numero a mao.
6. Quando o sha pinado existe neste repositorio, a `version` do `plugin.yaml`
   nesse commit tem de ser a `version` da entrada. E' a prova que fecha as
   outras: o pin e' um commit real e diz o que a entrada diz.

Uso: python3 test_catalogo.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ENTRADA = AQUI / "plugin-catalog" / "vipernet-model-manager.yaml"
MANIFESTO = AQUI / "plugin.yaml"
PAINEL = AQUI / "dashboard" / "manifest.json"

RE_SHA = re.compile(r"^[0-9a-f]{40}$")


def _sem_comentarios(texto: str) -> str:
    """Tira as linhas de comentario: a entrada e' muito comentada de proposito."""
    return "\n".join(l for l in texto.split("\n") if not l.lstrip().startswith("#"))


def _campo(texto: str, nome: str) -> str:
    """Valor de um campo escalar de topo. Falha alto quando nao existe."""
    m = re.search(rf"^{re.escape(nome)}:\s*(.+)$", texto, re.M)
    if not m:
        raise AssertionError(f"campo `{nome}` ausente da entrada de catalogo")
    return m.group(1).strip().strip('"').strip("'")


def _lista_bloco(texto: str, nome: str) -> list[str]:
    """Itens `- valor` que seguem `<nome>:` (lista simples, com ou sem recuo).

    O recuo importa: em `plugin.yaml` as listas estao na coluna 0, na entrada de
    catalogo vivem dentro de `capabilities:`. Uma procura ancorada na coluna 0
    encontrava uma lista vazia e o teste passava sem comparar nada.
    """
    m = re.search(rf"^[ \t]*{re.escape(nome)}:[ \t]*$\n((?:[ \t]+-.*\n?)+)", texto, re.M)
    if not m:
        return []
    return [l.strip()[1:].strip() for l in m.group(1).split("\n") if l.strip().startswith("-")]



def _versao(ficheiro: Path) -> str:
    texto = ficheiro.read_text(encoding="utf-8")
    m = re.search(r"^version:\s*(.+)$", texto, re.M)
    if not m:
        raise AssertionError(f"sem `version` em {ficheiro.name}")
    return m.group(1).strip().strip('"')


def _tuplo(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


class TestCatalogo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texto = _sem_comentarios(ENTRADA.read_text(encoding="utf-8"))
        cls.manifesto = MANIFESTO.read_text(encoding="utf-8")

    # 1. campos que o loader le
    def test_campos_obrigatorios(self):
        for campo in ("name", "repo", "sha", "version", "requires_hermes",
                      "maintainer", "tier", "category"):
            self.assertTrue(_campo(self.texto, campo), f"campo vazio: {campo}")
        self.assertTrue(_lista_bloco(self.texto, "screenshots"), "sem screenshots")
        self.assertRegex(self.texto, r"(?m)^[ \t]*capabilities:[ \t]*$", "sem bloco `capabilities`")


    def test_repo_e_https(self):
        self.assertTrue(_campo(self.texto, "repo").startswith("https://"),
                        "regra 2: o repo tem de ser um URL https que qualquer pessoa clone")

    # 2. pin de 40 hex
    def test_sha_de_40_hex(self):
        sha = _campo(self.texto, "sha")
        self.assertRegex(sha, RE_SHA, f"pin invalido: {sha!r}")

    # 3. capturas no mesmo sha
    def test_capturas_no_sha_do_pin(self):
        sha = _campo(self.texto, "sha")
        urls = _lista_bloco(self.texto, "screenshots")
        self.assertEqual(len(urls), 3, f"esperadas 3 capturas, encontradas {len(urls)}")
        for url in urls:
            self.assertTrue(url.startswith("https://"), url)
            self.assertIn(sha, url, "URL de captura sem o sha do pin (bump sem re-pin)")

    # 4. nada a divergir do manifesto
    def test_nome_e_requisitos_iguais_ao_manifesto(self):
        self.assertEqual(_campo(self.texto, "name"), _campo(self.manifesto, "name"))
        self.assertEqual(_campo(self.texto, "requires_hermes"),
                         _campo(self.manifesto, "requires_hermes"))

    def test_capacidades_iguais_ao_manifesto(self):
        for bloco in ("provides_hooks", "provides_middleware"):
            da_entrada = _lista_bloco(self.texto, bloco)
            do_manifesto = _lista_bloco(self.manifesto, bloco)
            self.assertEqual(sorted(da_entrada), sorted(do_manifesto),
                             f"`{bloco}` diverge entre a entrada e o plugin.yaml")

    # 5. a entrada nunca esta' a frente do codigo
    def test_entrada_nao_esta_a_frente_do_codigo(self):
        v_entrada = _campo(self.texto, "version")
        v_codigo = _versao(MANIFESTO)
        self.assertLessEqual(_tuplo(v_entrada), _tuplo(v_codigo),
                             f"entrada {v_entrada} a frente do codigo {v_codigo}")

    def test_painel_e_manifesto_nao_divergem(self):
        import json
        versao_painel = json.loads(PAINEL.read_text(encoding="utf-8"))["version"]
        self.assertEqual(versao_painel, _versao(MANIFESTO),
                         "dashboard/manifest.json e plugin.yaml dizem versoes diferentes")

    # 6. o pin existe e diz o mesmo
    def test_pin_existe_e_diz_o_mesmo(self):
        sha = _campo(self.texto, "sha")
        existe = subprocess.run(["git", "-C", str(AQUI), "cat-file", "-e", f"{sha}^{{commit}}"],
                                capture_output=True, text=True, check=False)
        if existe.returncode != 0:
            self.skipTest(f"{sha} nao esta' neste clone (esperado fora da linhagem publicada)")
        bruto = subprocess.run(["git", "-C", str(AQUI), "show", f"{sha}:plugin.yaml"],
                               capture_output=True, text=True, check=False)
        self.assertEqual(bruto.returncode, 0, "o pin nao tem plugin.yaml")
        m = re.search(r"^version:\s*(.+)$", bruto.stdout, re.M)
        if m is None:
            self.fail("plugin.yaml do pin sem version")
            return
        achado = m.group(1).strip().strip('"')
        self.assertEqual(achado, _campo(self.texto, "version"),
                         "a versao da entrada nao e' a versao do commit pinado")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
