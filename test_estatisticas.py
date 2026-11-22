"""Contrato HTTP das estatísticas, sem escrever no ledger do plugin."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from dashboard import api


class Estatisticas(unittest.TestCase):
    def setUp(self):
        pasta = self.enterContext(TemporaryDirectory())
        self.ledger = Path(pasta) / 'uso.jsonl'
        self.enterContext(patch.object(api.gestor, 'caminho_ledger', return_value=self.ledger))
        app = FastAPI()
        app.include_router(api.router)
        self.client = self.enterContext(TestClient(app))

    def obter(self):
        r = self.client.get('/estatisticas')
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_so_respostas_retidas(self):
        eventos = [
            dict(evento='resumo_dia', eventos={'resposta': 100}, ts='2026-01-01'),
            dict(evento='erro', respondido_por='ignorar'),
            dict(evento='resposta', modelo='pedido', respondido_por='efectivo', perfil='pessoal',
                 tarefa='arquitetura', task_id='id-1', superficie='cron', origem='superficie',
                 turno='mesmo', ts='2026-06-01T10:00:00+00:00'),
            dict(evento='resposta', modelo='pedido', task_id='id-2', perfil='desconhecido',
                 turno='mesmo', ts='2026-06-01T10:30:00+01:00'),
            dict(evento='resposta', ts='ilegível'), None,
        ]
        self.ledger.write_text(''.join(json.dumps(e) + '\n' for e in eventos) + '{', encoding='utf-8')
        self.assertEqual(self.obter(), dict(total=3, modelos={'efectivo': 1, '': 2},
                         perfis={'pessoal': 1, '': 2}, tarefas={'arquitetura': 1, '': 2},
                         superficies={'cron': 1, '': 2}, origens={'superficie': 1, '': 2},
                         inicio='2026-06-01T10:30:00+01:00', fim='2026-06-01T10:00:00+00:00'))

    def test_cache_reutiliza_leitura_e_invalida_com_alteracoes(self):
        linha = '{"evento":"resposta","tarefa":"a"}\n'
        self.ledger.write_text(linha)
        with patch.object(api, '_linhas_ledger', wraps=api._linhas_ledger) as ler:
            self.assertEqual(self.obter()['tarefas'], {'a': 1})
            self.assertEqual(self.obter()['tarefas'], {'a': 1})
            self.assertEqual(ler.call_count, 1)
            with self.ledger.open('a') as f:
                f.write(linha)
            self.assertEqual(self.obter()['total'], 2)
            substituto = self.ledger.with_suffix('.novo')
            substituto.write_text(linha.replace('"a"', '"b"') * 2)
            substituto.replace(self.ledger)
            self.assertEqual(self.obter()['tarefas'], {'b': 2})
            self.assertEqual(ler.call_count, 3)

    def test_janelas_civis_lisboa_e_cache(self):
        from datetime import datetime
        # A semana atravessa o fim da hora de verão: à meia-noite de 22/10
        # ainda é 21/10 em UTC; à meia-noite de 29/10 já não há diferença.
        datas = ['2026-09-28T22:59:59Z', '2026-09-28T23:00:00Z',
                 '2026-10-21T22:59:59Z', '2026-10-21T23:00:00Z',
                 '2026-10-28T23:59:59Z', '2026-10-29T00:00:00Z', 'inválido']
        self.ledger.write_text(''.join(json.dumps(dict(evento='resposta', ts=ts, respondido_por=str(i))) + '\n'
                                       for i, ts in enumerate(datas)))
        def obter(periodo):
            r = self.client.get('/estatisticas', params={'periodo': periodo})
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()
        with patch.object(api, 'datetime', wraps=datetime) as relogio, \
                patch.object(api, '_linhas_ledger', wraps=api._linhas_ledger) as ler:
            def fixar(ts):
                relogio.now.side_effect = lambda tz: datetime.fromisoformat(ts).astimezone(tz)
            fixar('2026-10-28T23:59:59+00:00')
            semana, mes = obter('semana'), obter('mes')
            for r, inicio, modelos in [(semana, '2026-10-22', ['3', '4']),
                                       (mes, '2026-09-29', ['1', '2', '3', '4'])]:
                self.assertEqual((r['janela_inicio'], r['janela_fim']), (inicio, '2026-10-28'))
                self.assertEqual(r['modelos'], dict.fromkeys(modelos, 1))
                self.assertEqual(r['total'], len(modelos))
            chamadas = ler.call_count
            self.assertEqual(obter('semana'), semana)
            self.assertEqual(obter('mes'), mes)
            self.assertEqual(ler.call_count, chamadas)
            self.assertEqual(obter('sempre'), self.obter())
            self.assertEqual(self.obter()['total'], len(datas))
            fixar('2026-10-29T00:00:00+00:00')
            self.assertEqual(obter('semana')['modelos'], {'4': 1, '5': 1})
        self.assertIn(self.client.get('/estatisticas?periodo=ano').status_code, (400, 422))

    def test_ausente_ou_sem_detalhe(self):
        vazio = dict(total=0, modelos={}, perfis={}, tarefas={}, superficies={}, origens={},
                     inicio=None, fim=None)
        self.assertEqual(self.obter(), vazio)
        self.assertFalse(self.ledger.exists())
        self.ledger.write_text('{"evento":"resumo_dia"}\n')
        self.assertEqual(self.obter(), vazio)


if __name__ == '__main__':
    unittest.main()
