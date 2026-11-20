"""Contrato HTTP da preferência global, com configuração descartável."""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from dashboard import api


class PainelEstatisticas(unittest.TestCase):
    def setUp(self):
        pasta = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, VMM_DIR=pasta))
        self.config = Path(pasta) / 'config.json'
        self.config.write_text('{"idioma":"pt","perfis":{"teste":{"catalogo":[]}}}')
        app = FastAPI()
        app.include_router(api.router)
        self.client = self.enterContext(TestClient(app))

    def test_omissao_sem_escrita(self):
        antes = self.config.read_bytes()
        self.assertIs(self.client.get('/estado').json().get('painel_estatisticas'), True)
        self.assertEqual(self.config.read_bytes(), antes)

    def test_desligar_conflito_e_religar(self):
        # Começar com uma config canónica para comparar todos os outros campos.
        api.gestor.gravar(self.client.get('/config').json()['config'])
        inicial = self.client.get('/config').json()
        rev = inicial['rev']
        for activo in (False, True):
            r = self.client.post('/painel-estatisticas', json={'rev': rev, 'activo': activo})
            self.assertEqual(r.status_code, 200, r.text)
            novo = self.client.get('/estado').json()
            self.assertEqual(r.json(), {'ok': True, 'painel_estatisticas': activo, 'rev': novo['rev']})
            self.assertNotEqual(rev, novo['rev'])
            self.assertIs(novo['painel_estatisticas'], activo)
            self.assertIs(json.loads(self.config.read_bytes())['painel_estatisticas'], activo)
            self.assertEqual(self.client.get('/config').json()['config'],
                             dict(inicial['config'], painel_estatisticas=activo))
            antes = self.config.read_bytes()
            conflito = self.client.post('/painel-estatisticas', json={'rev': rev, 'activo': not activo})
            self.assertEqual(conflito.status_code, 409)
            self.assertEqual(conflito.json()['rev'], novo['rev'])
            self.assertEqual(self.config.read_bytes(), antes)
            rev = novo['rev']

    def test_rejeita_corpo_invalido_sem_gravar(self):
        antes = self.config.read_bytes()
        rev = self.client.get('/estado').json()['rev']
        for corpo in ({'activo': True}, {'activo': True, 'rev': ''}, {'rev': rev},
                      *({'rev': rev, 'activo': v} for v in ('false', 0, 1, None))):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.client.post('/painel-estatisticas', json=corpo).status_code, 400)
                self.assertEqual(self.config.read_bytes(), antes)

    def test_rev_corresponde_ao_documento_normalizado(self):
        rev = self.client.get('/estado').json()['rev']
        r = self.client.post('/painel-estatisticas', json={'rev': rev, 'activo': False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['rev'], self.client.get('/config').json()['rev'])

    def test_editor_recusa_tipo_invalido(self):
        cfg = self.client.get('/config').json()['config']
        for valor in ('false', None, 0):
            r = self.client.post('/validar', json=dict(cfg, painel_estatisticas=valor))
            self.assertTrue(any('painel_estatisticas' in e for e in r.json()['erros']))


if __name__ == '__main__':
    unittest.main()
