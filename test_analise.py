"""Testes do classificador de complexidade (v2).

Dois corpus com nivel esperado — um usado na calibragem, outro de validacao —
mais testes unitarios das regras que a v1 errava. Sem rede, sem estado.

    python3 test_analise.py
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def _carregar():
    spec = importlib.util.spec_from_file_location("analise_teste", RAIZ / "analise.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


a = _carregar()


def m(*textos):
    return [{"role": "user", "content": t} for t in textos]


# Usado na calibragem.
TREINO = [
    ("saudacao", m("olá"), "leve"),
    ("ack", m("ok"), "leve"),
    ("obrigado", m("obrigado!"), "leve"),
    ("pergunta_simples", m("o que é uma VLAN?"), "leve"),
    ("listar_entidades", m("lista as entidades do domínio sensor"), "leve"),
    ("pedido_curto_engenharia", m("implementa o parser de INI com testes"), "alta"),
    ("traceback", m('dá este erro:\nTraceback (most recent call last):\n  File "/opt/data/x.py", line 12\nValueError: bad'), "alta"),
    ("arquitetura", m("quero um parecer de arquitetura para o novo gateway de eventos, com trade-offs"), "alta"),
    ("refactor_multi", m("1. refactoriza o gestor\n2. migra a config\n3. documenta as rotas"), "alta"),
    ("explicacao_media", m("explica como funciona o processo de arranque do gateway e onde ficam os logs"), "media"),
    ("continuacao_apos_pesado", m("implementa o parser INI com testes", "continua"), "alta"),
    ("continuacao_curta", m("analisa a arquitetura do módulo de energia e propõe melhorias", "faz"), "alta"),
    ("historico_longo_trivial", m(*["ok", "obrigado", "sim", "continua"] * 10, "olá"), "leve"),
    ("historico_longo_curto", m(*["ok", "obrigado", "sim", "continua"] * 10, "lista os ficheiros de /opt/data"), "leve"),
    ("debug_com_ficheiro", m("no /opt/data/plugins/x/__init__.py a função não devolve nada"), "media"),
    ("comparacao", m("compara o DeepSeek Flash com o gpt-6.1-sol em custo e latência"), "media"),
    ("pedido_de_codigo", m("escreve uma automação do HA que carrega a bateria nas horas de vazio"), "alta"),
    ("duas_perguntas", m("o que é o NPM? e como se cria um proxy host?"), "leve"),
    ("pedido_de_desenho", m("desenha o diagrama LADD do fluxo de faturação"), "alta"),
    ("pedido_de_auditoria", m("audita a instalação do HA e diz o que está mal"), "alta"),
]

# Nao usado na calibragem — mede generalizacao.
VALIDACAO = [
    ("resume_sessao", m("resume o que fizemos até agora"), "leve"),
    ("traduz", m("traduz este parágrafo para inglês"), "leve"),
    ("consulta_hora", m("há quanto tempo está a correr o gateway?"), "leve"),
    ("log_hora", m("o que diz o log das 10h?"), "leve"),
    ("entidades_unavailable", m("mostra-me as entidades indisponíveis"), "leve"),
    ("instala_plugin", m("instala o plugin novo no perfil pessoal"), "alta"),
    ("corrige_automacao", m("corrige a automação que dispara às 3h da manhã"), "alta"),
    ("parecer_migracao", m("preciso de um parecer sobre a abordagem de migração do HA para o novo LXC"), "alta"),
    ("diagnostico_reboot", m("porque é que o vigia não recuperou depois do reboot?"), "media"),
    ("bateria_noite", m("porque é que a bateria descarrega à noite?"), "media"),
    ("revisao_pr", m("revê o PR 42 e diz o que mudar"), "media"),
    ("custo_tokens", m("compara o custo do flash e do sol para 1M tokens"), "media"),
    ("email_fornecedor", m("escreve um email ao fornecedor a pedir o orçamento"), "media"),
    ("corrige_apos_auditoria", m("audita a instalação do HA", "corrige"), "alta"),
    ("continua_apos_trivial", m("ok", "continua"), "leve"),
    ("pergunta_dominio", m("o que é o keepalived?"), "leve"),
]


class Corpus(unittest.TestCase):
    def _verificar(self, corpus):
        falhas = []
        for nome, mensagens, esperado in corpus:
            d = a.decidir(mensagens)
            if d["nivel"] != esperado:
                falhas.append(f"{nome}: esperado {esperado}, obtido {d['nivel']} "
                              f"(score={d.get('score')}, sinais={d.get('sinais')})")
        self.assertEqual(falhas, [], "\n".join(falhas))

    def test_corpus_treino(self):
        self._verificar(TREINO)

    def test_corpus_validacao(self):
        self._verificar(VALIDACAO)


class Regras(unittest.TestCase):
    def test_contexto_injectado_nao_pontua(self):
        """O prompt do sistema/wiki nao pode inflacionar o nivel do turno."""
        grande = "olá\n[conhecimento local — Vipernet]\n" + ("arquitetura trade-off migração " * 400)
        d = a.decidir(m([grande]))
        self.assertEqual(d["nivel"], "leve")
        self.assertLess(d["score"], 3)

    def test_conversa_longa_nao_inflaciona(self):
        """A v1 somava +2 pelo tamanho da conversa; a v2 nao."""
        curto = a.decidir(m("lista os ficheiros"))
        longo = a.decidir(m(*["ok"] * 200, "lista os ficheiros"))
        self.assertEqual(curto["score"], longo["score"])

    def test_continuacao_herda_nivel(self):
        d = a.decidir(m("implementa o parser com testes", "continua"))
        self.assertIn("continuacao_herda", d["sinais"])
        self.assertEqual(d["nivel"], a.decidir(m("implementa o parser com testes"))["nivel"])

    def test_continuacao_apos_trivial_fica_leve(self):
        self.assertEqual(a.decidir(m("ok", "continua"))["nivel"], "leve")

    def test_consulta_hipotetica_nao_e_producao(self):
        """'como se cria um proxy' e' pergunta, nao pedido de implementacao."""
        d = a.decidir(m("como se cria um proxy host no NPM?"))
        self.assertNotIn("trabalho_producao", " ".join(d["sinais"]))
        self.assertEqual(d["nivel"], "leve")

    def test_substantivo_derivado_nao_e_verbo(self):
        d = a.decidir(m("qual é o estado da instalação e da configuração?"))
        self.assertNotIn("trabalho_producao", " ".join(d["sinais"]))

    def test_stack_trace_pesa_mais_que_sintoma(self):
        com_local = a.decidir(m(
            'Traceback (most recent call last):\n  File "/opt/data/x.py", line 12\nValueError: x'))
        sem_local = a.decidir(m("Traceback (most recent call last):\nValueError: x"))
        sintoma = a.decidir(m("não funciona"))
        self.assertGreater(com_local["score"], sem_local["score"])
        self.assertGreater(sem_local["score"], sintoma["score"])
        # Com ficheiro+linha e' `alta`; um stack nu fica em `media`.
        self.assertEqual(com_local["nivel"], "alta")
        self.assertEqual(sem_local["nivel"], "media")
        self.assertEqual(sintoma["nivel"], "media")

    def test_debug_de_ficheiro_fica_media(self):
        d = a.decidir(m("no /opt/data/x.py a função não devolve nada"))
        self.assertEqual(d["nivel"], "media")

    def test_limiares_configuraveis(self):
        texto = m("implementa o parser com testes")  # 6 pontos
        self.assertEqual(a.decidir(texto)["nivel"], "alta")
        self.assertEqual(a.decidir(texto, limiares={"media": 3, "alta": 9})["nivel"], "media")
        self.assertEqual(a.decidir(texto, limiares={"media": 8, "alta": 12})["nivel"], "leve")

    def test_limiar_incoerente_nao_apaga_alta(self):
        self.assertEqual(a.nivel_de_score(10, {"media": 5, "alta": 2}), "alta")

    def test_pesos_configuraveis(self):
        texto = m("implementa o parser com testes")  # trabalho 5 + dominio 2
        barato = a.decidir(texto, pesos={"trabalho": 0, "dominio": 0}, limiares={"media": 3, "alta": 6})
        self.assertEqual(barato["nivel"], "leve")
        caro = a.decidir(texto, pesos={"trabalho": 20}, limiares={"media": 3, "alta": 6})
        self.assertEqual(caro["nivel"], "alta")

    def test_peso_invalido_e_ignorado(self):
        d = a.decidir(m("implementa o parser com testes"), pesos={"trabalho": "muito"})
        self.assertEqual(d["nivel"], "alta")

    def test_marcador_manda_sobre_analise(self):
        d = a.decidir(m("olá, complexidade: alta"))
        self.assertEqual((d["nivel"], d["origem"]), ("alta", "marcador"))

    def test_marcador_pegajoso(self):
        d = a.decidir(m("complexidade: alta", "lista os ficheiros"))
        self.assertEqual(d["nivel"], "alta")

    def test_marcador_auto_faz_reset(self):
        d = a.decidir(m("complexidade: alta", "complexidade: auto"))
        self.assertEqual(d["nivel"], "")

    def test_trabalho_em_curso_soma(self):
        """Ferramentas de escrita recentes indicam trabalho a decorrer."""
        com_escrita = m("continua o trabalho") + [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "write_file"}}, {"function": {"name": "patch"}}]}]
        sem_escrita = m("continua o trabalho")
        self.assertGreater(a.pontuar(com_escrita)["score"], a.pontuar(sem_escrita)["score"])

    def test_deterministico(self):
        texto = m("audita a instalação e corrige o que está mal")
        self.assertEqual(a.decidir(texto), a.decidir(texto))

    def test_sem_mensagens_nao_rebenta(self):
        self.assertEqual(a.decidir([])["nivel"], "leve")

    def test_content_em_partes(self):
        d = a.decidir([{"role": "user", "content": [
            {"type": "text", "text": "implementa o "}, {"type": "text", "text": "parser com testes"}]}])
        self.assertEqual(d["nivel"], "alta")

    def test_texto_do_utilizador_com_html_nao_rebenta(self):
        d = a.decidir(m("<b>implementa</b> o parser com testes <script>x</script>"))
        self.assertEqual(d["nivel"], "alta")

    # --- regra dos dois sinais (v3) -------------------------------------

    def test_dois_sinais_nao_somam_media(self):
        """Dois sinais fracos não somam um nível pago: `testa o plugin`."""
        d = a.decidir(m("testa o plugin"),
                      limiares={"media_sinais": 2, "alta": 6})
        self.assertEqual(d["nivel"], "leve", d)

    def test_dois_sinais_independentes_dao_media(self):
        d = a.decidir(m("no /opt/data/x.py a função não devolve nada"),
                      limiares={"media_sinais": 2, "alta": 6})
        self.assertEqual(d["nivel"], "media", d)

    def test_dois_sinais_nao_apagam_alta(self):
        d = a.decidir(m("implementa o parser com testes"),
                      limiares={"media_sinais": 2, "alta": 6})
        self.assertEqual(d["nivel"], "alta", d)

    def test_sinais_de_amplitude_nao_contam(self):
        """Volume e forma não são evidência de dificuldade."""
        self.assertEqual(a.sinais_independentes(
            ["texto_longo=1", "texto_muito_longo=1"]), [])

    def test_verificacao_nao_conta_como_analise(self):
        """`testa o plugin` é confirmação, não análise."""
        self.assertEqual(a.sinais_independentes(["analise=testa"]), [])

    def test_sinais_sem_repeticao(self):
        self.assertEqual(len(a.sinais_independentes(
            ["analise=2", "analise=2", "dominio_tecnico=1"])), 2)

    def test_sem_media_sinais_mantem_comportamento_antigo(self):
        """Sem a regra, sinais fracos voltam a somar — mas verificar nunca e analisar.

        A v4 exclui verbos de verificacao no proprio `_ANALISE`, por isso
        "testa o plugin" fica leve mesmo sem `media_sinais`. A prova da
        compatibilidade usa um caso sem verbo de verificacao.
        """
        d = a.decidir(m("analisa o ficheiro /opt/data/x.py"),
                      limiares={"media": 3, "alta": 6})
        self.assertEqual(d["nivel"], "media", d)
        # e verificar continua a nao comprar um nivel pago, com ou sem a regra
        self.assertEqual(a.decidir(m("testa o plugin"))["nivel"], "leve")


class Regras_v4_2(unittest.TestCase):
    """Conceitos que a v4.2 separa — cada um foi um falso positivo ou negativo real."""

    def test_justificar_nao_e_arquitetura(self):
        """"justifica" pede razao: media, nao o modelo mais caro."""
        d = a.decidir(m("NPM ou Traefik para este homelab? justifica"))
        self.assertEqual(d["nivel"], "media", d)
        self.assertIn("justificacao_pedida", d["sinais"])
        self.assertNotIn("arquitetura_decisao", d["sinais"])

    def test_arquitetura_continua_a_ganhar(self):
        d = a.decidir(m("justifica a abordagem de automacao solar"))
        self.assertEqual(d["nivel"], "alta", d)
        self.assertIn("arquitetura_decisao", d["sinais"])

    def test_justificar_com_e_que(self):
        """"porque e que escolheste" tem de casar como "porque escolheste"."""
        d = a.decidir(m("porque e que escolheste o flash para isto?"))
        self.assertIn("justificacao_pedida", d["sinais"], d)

    def test_explicar_conceito_e_media(self):
        d = a.decidir(m("explica como funciona o keepalived"))
        self.assertEqual(d["nivel"], "media", d)
        self.assertIn("explicacao_conceito", d["sinais"])

    def test_ha_nao_e_home_assistant(self):
        """O verbo "ha" nao pode valer como objeto vivo (HA)."""
        d = a.decidir(m("ha um typo na descricao da skill"))
        self.assertNotIn("trabalho_producao=ha", d["sinais"], d)
        self.assertEqual(d["nivel"], "media", d)

    def test_avaria_de_servico_e_alta(self):
        d = a.decidir(m("o container do n8n nao arranca depois do reboot"))
        self.assertIn("avaria_de_servico", d["sinais"], d)
        self.assertEqual(d["nivel"], "alta", d)

    def test_sintoma_simples_nao_e_alta(self):
        """Uma lampada que nao acende nao e um servico em baixo."""
        d = a.decidir(m("a luz da sala nao acende"))
        self.assertNotIn("avaria_de_servico", d["sinais"], d)
        self.assertLess(d["score"], 6, d)

    def test_relatorio_com_verbo_generico(self):
        """"gera/prepara o relatorio" herda o peso do objeto, nao do verbo."""
        for texto in ("gera o relatorio mensal de energia",
                      "prepara o resumo semanal do consumo"):
            d = a.decidir(m(texto))
            self.assertEqual(d["nivel"], "media", (texto, d))
            self.assertNotIn("trabalho_producao=gera", d["sinais"], d)

    def test_mudar_em_pergunta_nao_e_ordem(self):
        """"diz o que mudar" e pergunta; "muda a ordem" e ordem."""
        self.assertNotIn("manutencao_increm",
                         a.decidir(m("revê o PR 42 e diz o que mudar"))["sinais"])
        self.assertIn("manutencao_increm",
                      a.decidir(m("muda a ordem dos cartoes"))["sinais"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
