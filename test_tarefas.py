"""Regressao de routing por tarefa; offline, sem chamar providers."""
import copy
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('vmm_tasks_test', ROOT / '__init__.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def config():
    return {'activo': True, 'perfis': {'trabalho': {
        'activo': True, 'estrategia': 'prioridade',
        'tarefas': {'arquitetura': {'termos': ['analise de arquitetura', 'architecture review'], 'prioridade': 10}},
        'catalogo': [
            {'id': 'flash', 'provider': 'deepseek', 'modelo': 'deepseek-flash', 'complexidade': ['leve', 'media']},
            {'id': 'pro', 'provider': 'deepseek', 'modelo': 'deepseek-v4-pro', 'complexidade': ['alta']},
            {'id': 'sol', 'provider': 'openrouter', 'modelo': 'openai/gpt-6.1-sol',
             'complexidade': ['alta'], 'tarefas': ['arquitetura']},
        ]}}}


class Agente:
    provider = 'deepseek'
    model = 'deepseek-flash'
    def switch_model(self, **kw):
        self.provider, self.model = kw['new_provider'], kw['new_model']


class Tarefas(unittest.TestCase):
    def setUp(self):
        self.agent = Agente()
        self.events = []
        self.cfg = config()
        p._historico.clear()
        p._contadores.clear()
        p._turno.clear()
        # (3.32.0) A tarefa passou a viver na sessao: sem limpar, um teste
        # herdava a tarefa do anterior e media a coisa errada.
        p._tarefa_sessao.clear()
        self.patches = [
            patch.object(p, '_config', side_effect=lambda: p.gestor.validar(self.cfg)[0]),
            patch.object(p.gestor, 'perfil_atual', return_value='trabalho'),
            patch.object(p, 'agente_vivo', return_value=self.agent),
            patch.object(p, '_registar', side_effect=lambda e, **kw: self.events.append(e)),
            patch.object(p, 'resolver_rota', side_effect=lambda perfil, provider, modelo: {
                'provider': provider, 'api_key': '', 'base_url': '', 'api_mode': ''}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def run_turn(self, text, history=None):
        p.middleware_llm_request(request={'model': self.agent.model, 'messages':
            (history or []) + [{'role': 'user', 'content': text}]},
            session_id='tasks-test', provider=self.agent.provider)
        # O desfecho da chamada: e na `resposta` que a decisao fica registada
        # desde que o `sem_troca` saiu, e ela e o ultimo evento do turno.
        p.pos_resposta(session_id='tasks-test', model=self.agent.model,
                       provider=self.agent.provider, response_model=self.agent.model,
                       finish_reason='stop', usage={'total_tokens': 10},
                       platform='api_server')
        return self.events[-1]

    def test_task_priority_above_complexity(self):
        e = self.run_turn('Faz uma análise de arquitetura.')
        self.assertEqual(self.agent.model, 'openai/gpt-6.1-sol')
        self.assertEqual(e['origem'], 'tarefa')
        self.assertEqual(e['tarefa'], 'arquitetura')


    def test_task_priority_even_with_auto_marker(self):
        e = self.run_turn('Faz uma análise de arquitetura. complexidade: auto')
        self.assertEqual(e['origem'], 'tarefa')
        self.assertEqual(e['entrada'], 'sol')

    def test_task_priority_even_with_low_marker(self):
        e = self.run_turn('Faz uma análise de arquitetura. complexidade: leve')
        self.assertEqual(e['origem'], 'tarefa')
        self.assertEqual(e['entrada'], 'sol')

    def test_cli_simulation_uses_task_association(self):
        import os
        import json
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory(dir='/opt/data/cache/scratch') as d:
            Path(d, 'config.json').write_text(json.dumps(self.cfg))
            result = subprocess.run(['python3', str(ROOT / 'cli.py'), 'simular', 'trabalho',
                'Faz uma análise de arquitetura.'], env={**os.environ, 'VMM_DIR': d},
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('tarefa: arquitetura', result.stdout)
        self.assertIn('sol: openrouter', result.stdout)
        self.assertNotIn('flash: deepseek', result.stdout)

    def test_unavailable_task_falls_back_to_analysis_with_reason(self):
        original = p.resolver_rota
        def resolve(perfil, provider, modelo):
            if provider == 'openrouter':
                raise RuntimeError('indisponivel no teste')
            return original(perfil, provider, modelo)
        with patch.object(p, 'resolver_rota', side_effect=resolve):
            e = self.run_turn('Faz uma análise de arquitetura.')
        # A v2 classifica "análise de arquitetura" como `alta` (era o defeito da v1,
        # que lhe dava score 1). O fallback cai na análise e chega ao modelo de alta.
        self.assertEqual(e['entrada'], 'pro')
        self.assertEqual(e['origem'], 'analise_fallback_tarefa')
        self.assertTrue(e['falhadas_tarefa'])

    def test_task_persists_across_turns(self):
        # (3.32.0) A associacao vive na sessao: o turno seguinte, que nao fala de
        # arquitetura, continua no modelo da tarefa em vez de cair na
        # complexidade. Antes desta versao este teste exigia o contrario — era
        # exactamente o defeito reportado (o modelo mudava a meio da conversa).
        self.run_turn('Faz uma análise de arquitetura.')
        e = self.run_turn('Diz apenas bom dia.', [
            {'role': 'user', 'content': 'Faz uma análise de arquitetura.'},
            {'role': 'assistant', 'content': 'Resultado da análise de arquitetura.'}])
        self.assertEqual(self.agent.model, 'openai/gpt-6.1-sol')
        self.assertEqual(e['origem'], 'tarefa')
        self.assertEqual(e['tarefa_origem'], 'herdada')

    def test_unassociated_task_uses_complexity(self):
        self.cfg['perfis']['trabalho']['tarefas']['traducao'] = {'termos': ['traduz'], 'prioridade': 0}
        e = self.run_turn('Traduz bom dia.')
        self.assertEqual(e['origem'], 'analise')
        self.assertEqual(e['entrada'], 'flash')

    def test_profile_isolation(self):
        self.cfg['perfis']['trabalho']['catalogo'][2]['tarefas'] = []
        self.cfg['perfis']['pessoal'] = copy.deepcopy(config()['perfis']['trabalho'])
        e = self.run_turn('Faz uma análise de arquitetura.')
        # Sem a associacao de tarefa, decide a analise: arquitetura -> alta -> `pro`.
        # O que este teste prova e' o isolamento (perfil trabalho), nao o modelo barato.
        self.assertEqual(e['entrada'], 'pro')
        self.assertEqual(e['perfil'], 'trabalho')

    def test_injected_context_does_not_classify_task(self):
        e = self.run_turn('Bom dia.\n<memory-context>análise de arquitetura</memory-context>')
        self.assertEqual(e['entrada'], 'flash')

    def test_disabled_profile_does_not_switch(self):
        self.cfg['perfis']['trabalho']['activo'] = False
        self.run_turn('Faz uma análise de arquitetura.')
        self.assertEqual(self.agent.model, 'deepseek-flash')

    def test_validation_rejects_unknown_task(self):
        cfg = config()
        cfg['perfis']['trabalho']['catalogo'][2]['tarefas'] = ['inexistente']
        _, errors = p.gestor.validar(cfg)
        self.assertTrue(any('tarefa desconhecida' in e for e in errors))

    def test_validation_rejects_bad_task_config(self):
        cfg = config()
        cfg['perfis']['trabalho']['tarefas']['arquitetura']['termos'] = 'nao lista'
        _, errors = p.gestor.validar(cfg)
        self.assertTrue(errors)

    def test_priority_resolves_overlapping_tasks(self):
        self.cfg['perfis']['trabalho']['tarefas']['outra'] = {'termos': ['arquitetura'], 'prioridade': 1}
        e = self.run_turn('Faz uma análise de arquitetura.')
        self.assertEqual(e['tarefa'], 'arquitetura')

    def test_task_route_carries_acesso(self):
        self.cfg['perfis']['trabalho']['catalogo'][2]['provider'] = 'openai-codex'
        self.cfg['perfis']['trabalho']['catalogo'][2]['acesso'] = 'subscricao'
        e = self.run_turn('Faz uma análise de arquitetura.')
        self.assertEqual(e['entrada'], 'sol')
        self.assertEqual(e['acesso'], 'subscricao')

    def test_validation_rejects_subscricao_with_non_codex(self):
        cfg = config()
        cfg['perfis']['trabalho']['catalogo'][2]['acesso'] = 'subscricao'
        _, errors = p.gestor.validar(cfg)
        self.assertTrue(any('exige provider openai-codex' in m for m in errors))

    def test_validation_rejects_api_with_codex(self):
        cfg = config()
        cfg['perfis']['trabalho']['catalogo'][2]['provider'] = 'openai-codex'
        cfg['perfis']['trabalho']['catalogo'][2]['acesso'] = 'api'
        _, errors = p.gestor.validar(cfg)
        self.assertTrue(any("use acesso 'subscricao'" in m for m in errors))


if __name__ == '__main__':
    unittest.main(verbosity=2)
