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

    def test_selecao_global_por_config_e_interruptor_independente(self):
        chave = 'painel_graficos'
        antes = self.config.read_bytes()
        self.assertEqual(self.client.get('/estado').json().get(chave),
                         ['modelos', 'perfis', 'tarefas', 'superficies', 'origens'])
        self.assertEqual(self.config.read_bytes(), antes)
        for selecao in (['perfis', 'origens'], []):
            doc = self.client.get('/config').json()
            doc['config'][chave] = selecao
            r = self.client.post('/config', json=doc)
            self.assertEqual(r.status_code, 200, r.text)
            for activo in (False, True):
                estado = self.client.get('/estado').json()
                self.assertEqual(estado[chave], selecao)
                r = self.client.post('/painel-estatisticas', json={'rev': estado['rev'], 'activo': activo})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(json.loads(self.config.read_bytes())[chave], selecao)

    def test_editor_recusa_selecao_invalida_sem_gravar(self):
        doc = self.client.get('/config').json()
        antes = self.config.read_bytes()
        for valor in (None, 'perfis', ['inventado'], ['perfis', 'perfis'], [{}]):
            with self.subTest(valor=valor):
                doc['config']['painel_graficos'] = valor
                self.assertTrue(self.client.post('/validar', json=doc['config']).json()['erros'])
                self.assertEqual(self.client.post('/config', json=doc).status_code, 400)
                self.assertEqual(self.config.read_bytes(), antes)

    def test_filtros_globais_opcionais_e_concorrencia(self):
        antes = self.config.read_bytes()
        estado = self.client.get('/estado').json()
        self.assertEqual(estado.get('painel_filtros'), {'periodo': 'sempre', 'modelos': 'todos', 'limiar': 0})
        self.assertNotIn('painel_filtros', self.client.get('/config').json()['config'])
        self.assertEqual(self.config.read_bytes(), antes)
        for periodo, modelos in [('semana', 'acima'), ('mes', 'top5'), ('sempre', 'todos')]:
            valor = dict(periodo=periodo, modelos=modelos, limiar=5)
            corpo = dict(rev=estado['rev'], painel_filtros=valor)
            r = self.client.post('/painel-filtros', json=corpo)
            self.assertEqual(r.status_code, 200, r.text)
            estado = self.client.get('/estado').json()
            self.assertEqual(r.json(), dict(ok=True, rev=estado['rev'], painel_filtros=valor))
            self.assertEqual(estado['painel_filtros'], valor)
            self.assertEqual(json.loads(self.config.read_text())['painel_filtros'], valor)
            antes = self.config.read_bytes()
            conflito = self.client.post('/painel-filtros', json=corpo)
            self.assertEqual(conflito.status_code, 409)
            self.assertEqual(conflito.json()['rev'], estado['rev'])
            self.assertEqual(self.config.read_bytes(), antes)

    def test_filtros_recusados_na_rota_e_no_editor(self):
        antes = self.config.read_bytes()
        doc = self.client.get('/config').json()
        valor = dict(periodo='sempre', modelos='todos', limiar=0)
        for invalido in (None, [], {}, dict(valor, periodo=[]), dict(valor, modelos='seis'),
                         dict(valor, limiar=False), dict(valor, limiar=-1), dict(valor, extra=1)):
            with self.subTest(invalido=invalido):
                self.assertEqual(self.client.post('/painel-filtros', json=dict(rev=doc['rev'], painel_filtros=invalido)).status_code, 400)
                cfg = dict(doc['config'], painel_filtros=invalido)
                self.assertTrue(self.client.post('/validar', json=cfg).json()['erros'])
                self.assertEqual(self.client.post('/config', json=dict(config=cfg, rev=doc['rev'])).status_code, 400)
        for rev in (None, '', 2):
            self.assertEqual(self.client.post('/painel-filtros', json=dict(rev=rev, painel_filtros=valor)).status_code, 400)
        self.assertEqual(self.config.read_bytes(), antes)

    def test_limiar_inteiro_seguro_do_painel(self):
        doc = self.client.get('/config').json()
        antes = self.config.read_bytes()
        filtros = dict(periodo='semana', modelos='acima', limiar=2**53)
        cfg = dict(doc['config'], painel_filtros=filtros)
        self.assertTrue(self.client.post('/validar', json=cfg).json()['erros'])
        self.assertEqual(self.client.post('/config', json=dict(config=cfg, rev=doc['rev'])).status_code, 400)
        self.assertEqual(self.client.post('/painel-filtros', json=dict(rev=doc['rev'], painel_filtros=filtros)).status_code, 400)
        self.assertEqual(self.config.read_bytes(), antes)
        filtros['limiar'] = 2**53 - 1
        self.assertFalse(self.client.post('/validar', json=cfg).json()['erros'])
        self.assertEqual(self.client.post('/painel-filtros', json=dict(rev=doc['rev'], painel_filtros=filtros)).status_code, 200)
        self.assertEqual(self.client.get('/estado').json()['painel_filtros'], filtros)

    def test_editor_recusa_tipo_invalido(self):
        cfg = self.client.get('/config').json()['config']
        for valor in ('false', None, 0):
            r = self.client.post('/validar', json=dict(cfg, painel_estatisticas=valor))
            self.assertTrue(any('painel_estatisticas' in e for e in r.json()['erros']))


if __name__ == '__main__':
    unittest.main()
