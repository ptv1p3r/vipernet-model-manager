"""Regressao da estrategia: listas fechadas, heranca global->perfil, delta.

Offline: nao chama providers nem o gateway. O que se prova aqui e o contrato do
bloco (`modo`/`por`) e o efeito dele na escolha da rota — a diferenca entre
`prioridade` e `rodizio` tem de ser observavel em `escolher`, senao o campo e
decorativo.
"""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('vmm_estrategia_test', ROOT / '__init__.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
g = p.gestor


def rota(rid, nivel='leve'):
    return {'id': rid, 'provider': 'deepseek', 'modelo': 'deepseek-flash',
            'complexidade': [nivel]}


def config(est=None, perfil_est=None, n_rotas=2):
    perfil = {'activo': True, 'catalogo': [rota(f'r{i}') for i in range(n_rotas)]}
    if perfil_est is not None:
        perfil['estrategia'] = perfil_est
    cfg = {'activo': True, 'perfis': {'p': perfil}}
    if est is not None:
        cfg['estrategia'] = est
    return cfg


class Normalizar(unittest.TestCase):
    def test_aceita_o_bloco_novo(self):
        limpo, erros = g.normalizar_estrategia({'modo': 'prioridade', 'por': 'turno'})
        self.assertEqual(erros, [])
        self.assertEqual(limpo, {'modo': 'prioridade', 'por': 'turno'})

    def test_aceita_a_forma_antiga_string(self):
        limpo, erros = g.normalizar_estrategia('prioridade')
        self.assertEqual(erros, [])
        self.assertEqual(limpo, {'modo': 'prioridade'})

    def test_ausencia_devolve_vazio_e_nao_o_default(self):
        # Vazio e "herda"; devolver o default aqui tornaria a heranca numa copia
        # no primeiro `gravar`, que e o que impede mudar o global.
        self.assertEqual(g.normalizar_estrategia(None), ({}, []))
        self.assertEqual(g.normalizar_estrategia(''), ({}, []))

    def test_modo_fora_da_lista_e_erro_com_os_aceites(self):
        _, erros = g.normalizar_estrategia({'modo': 'melhor'}, 'x')
        self.assertTrue(any('x.modo invalido' in e and 'rodizio, prioridade' in e
                            for e in erros), erros)

    def test_por_fora_da_lista_e_erro_com_os_aceites(self):
        _, erros = g.normalizar_estrategia({'por': 'dia'}, 'x')
        self.assertTrue(any('x.por invalido' in e and 'turno, sessao' in e
                            for e in erros), erros)

    def test_chave_desconhecida_e_erro(self):
        _, erros = g.normalizar_estrategia({'modo': 'rodizio', 'extra': 1}, 'x')
        self.assertTrue(any('x.extra desconhecida' in e for e in erros), erros)

    def test_tipo_invalido_e_erro(self):
        _, erros = g.normalizar_estrategia(3, 'x')
        self.assertTrue(erros)

    def test_normaliza_maiusculas_e_espacos(self):
        limpo, erros = g.normalizar_estrategia({'modo': ' RODIZIO ', 'por': 'Turno'})
        self.assertEqual(erros, [])
        self.assertEqual(limpo, {'modo': 'rodizio', 'por': 'turno'})

    def test_opcoes_fechadas_tem_rotulo(self):
        op = g.opcoes_estrategia()
        self.assertEqual([o['valor'] for o in op['modo']], list(g.ESTRATEGIAS))
        self.assertEqual([o['valor'] for o in op['por']], list(g.POR))
        for o in op['modo'] + op['por']:
            self.assertTrue(o['rotulo'])


class Heranca(unittest.TestCase):
    def test_global_vale_para_todos(self):
        limpo, erros = g.validar(config({'modo': 'prioridade', 'por': 'turno'}))
        self.assertEqual(erros, [])
        self.assertEqual(g.estrategia_efectiva(limpo, 'p'),
                         {'modo': 'prioridade', 'por': 'turno', 'origem': 'herdado'})

    def test_perfil_sobrepoe_chave_a_chave(self):
        limpo, erros = g.validar(config({'modo': 'prioridade', 'por': 'turno'},
                                        perfil_est={'por': 'sessao'}))
        self.assertEqual(erros, [])
        ef = g.estrategia_efectiva(limpo, 'p')
        self.assertEqual(ef['modo'], 'prioridade')   # herdado
        self.assertEqual(ef['por'], 'sessao')        # proprio
        self.assertEqual(ef['origem'], 'proprio')

    def test_copia_igual_ao_global_nao_e_gravada(self):
        limpo, erros = g.validar(config({'modo': 'rodizio', 'por': 'sessao'},
                                        perfil_est={'modo': 'rodizio', 'por': 'sessao'}))
        self.assertEqual(erros, [])
        self.assertNotIn('estrategia', limpo['perfis']['p'])
        self.assertEqual(g.estrategia_efectiva(limpo, 'p')['origem'], 'herdado')

    def test_sem_global_nem_perfil_usa_o_padrao(self):
        limpo, erros = g.validar(config())
        self.assertEqual(erros, [])
        self.assertEqual(g.estrategia_efectiva(limpo, 'p'),
                         {'modo': 'rodizio', 'por': 'sessao', 'origem': 'padrao'})

    def test_migra_a_forma_antiga_sem_perder_o_valor(self):
        cfg = {'activo': True, 'perfis': {'p': {
            'activo': True, 'estrategia': 'prioridade',
            'rodizio': {'por': 'turno'}, 'catalogo': [rota('a')]}}}
        limpo, erros = g.validar(copy.deepcopy(cfg))
        self.assertEqual(erros, [])
        ef = g.estrategia_efectiva(limpo, 'p')
        self.assertEqual((ef['modo'], ef['por']), ('prioridade', 'turno'))
        self.assertNotIn('rodizio', limpo['perfis']['p'])

    def test_forma_antiga_invalida_nao_passa_em_silencio(self):
        cfg = {'perfis': {'p': {'activo': True, 'rodizio': {'por': 'hora'},
                                'catalogo': [rota('a')]}}}
        _, erros = g.validar(cfg)
        self.assertTrue(any('perfis.p.estrategia.por' in e for e in erros), erros)


class EfeitoNaNavegacao(unittest.TestCase):
    """A estrategia tem de mudar a rota — senao e decorativa."""

    def test_prioridade_devolve_sempre_a_primeira(self):
        pcfg = g.perfil_config(g.validar(config({'modo': 'prioridade'},
                                              n_rotas=3))[0], 'p')
        vistas = {p.escolher(pcfg, 'leve', perfil='p', sessao=f's{i}')['id']
                  for i in range(30)}
        self.assertEqual(vistas, {'r0'})

    def test_rodizio_alterna_entre_sessoes(self):
        pcfg = g.perfil_config(g.validar(config({'modo': 'rodizio', 'por': 'sessao'},
                                              n_rotas=3))[0], 'p')
        vistas = {p.escolher(pcfg, 'leve', perfil='p', sessao=f's{i}')['id']
                  for i in range(40)}
        self.assertGreater(len(vistas), 1, vistas)
        self.assertTrue(vistas <= {'r0', 'r1', 'r2'}, vistas)

    def test_rodizio_por_turno_muda_dentro_da_sessao(self):
        pcfg = g.perfil_config(g.validar(config({'modo': 'rodizio', 'por': 'turno'},
                                              n_rotas=3))[0], 'p')
        vistas = {p.escolher(pcfg, 'leve', perfil='p', sessao='unica')['id']
                  for _ in range(15)}
        self.assertGreater(len(vistas), 1, vistas)

    def test_prioridade_no_perfil_sobrepoe_rodizio_global(self):
        limpo, _ = g.validar(config({'modo': 'rodizio', 'por': 'sessao'},
                                    perfil_est={'modo': 'prioridade'}, n_rotas=3))
        pcfg = g.perfil_config(limpo, 'p')
        vistas = {p.escolher(pcfg, 'leve', perfil='p', sessao=f's{i}')['id']
                  for i in range(20)}
        self.assertEqual(vistas, {'r0'})


class RoundTrip(unittest.TestCase):
    def test_grava_e_volta_a_ler_igual(self):
        limpo, _ = g.validar(config({'modo': 'prioridade', 'por': 'turno'},
                                    perfil_est={'por': 'sessao'}))
        with tempfile.TemporaryDirectory() as td:
            caminho = Path(td) / 'config.json'
            g.gravar(limpo, caminho)
            lido, erros = g.carregar(caminho)
            self.assertEqual(erros, [])
            self.assertEqual(g.estrategia_efectiva(lido, 'p'),
                             g.estrategia_efectiva(limpo, 'p'))
            bruto = json.loads(caminho.read_text(encoding='utf-8'))
            self.assertEqual(bruto['estrategia'], {'modo': 'prioridade', 'por': 'turno'})
            self.assertEqual(bruto['perfis']['p']['estrategia'], {'por': 'sessao'})

    def test_gravacao_de_valor_invalido_e_recusada(self):
        with tempfile.TemporaryDirectory() as td:
            caminho = Path(td) / 'config.json'
            with self.assertRaises(ValueError) as ctx:
                g.gravar({'estrategia': {'modo': 'inventado'}, 'perfis': {}}, caminho)
            self.assertIn('modo invalido', str(ctx.exception))
            self.assertFalse(caminho.exists(), 'config invalida nao pode ficar no disco')


if __name__ == '__main__':
    unittest.main(verbosity=2)
