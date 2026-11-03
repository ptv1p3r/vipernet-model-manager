#!/usr/bin/env python3
"""Testes do ciclo de aprendizagem — sem rede, sem tocar na config viva.

O que se prova aqui e o que pode mentir em silencio: a metrica, o kappa (que ja
devolveu acordo simples disfarcado de kappa quando estava preso a tres classes),
os portoes (que tem de fechar), o treino (que tem de melhorar num caso separavel)
e a leitura do JSON do modelo (que ja leu o corpo da API como se fosse o
conteudo e rotulou tudo `None`).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL.parent))
sys.path.insert(0, str(EVAL))

import aprender as A  # noqa: E402


class TestMetricas(unittest.TestCase):
    def test_macro_f1_perfeito(self):
        pares = [("leve", "leve"), ("media", "media"), ("alta", "alta")]
        m = A.metricas(pares)
        self.assertEqual(m["macro_f1"], 1.0)
        self.assertEqual(m["acerto"], 1.0)

    def test_macro_f1_conta_por_classe(self):
        # leve: prec 1,0 / cob 0,5 -> 0,667; media: 0; alta: prec 0,5 / cob 1,0 -> 0,667
        pares = [("leve", "leve"), ("leve", "media"), ("media", "alta"), ("alta", "alta")]
        m = A.metricas(pares)
        self.assertAlmostEqual(m["macro_f1"], 0.4445, places=4)
        self.assertEqual(m["por_classe"]["media"]["f1"], 0.0)
        self.assertEqual(m["por_classe"]["alta"]["suporte"], 1)

    def test_vazio_nao_divide_por_zero(self):
        self.assertEqual(A.metricas([])["n"], 0)


class TestKappa(unittest.TestCase):
    def test_acordo_perfeito(self):
        a = [{"id": "1", "rotulo": "leve"}, {"id": "2", "rotulo": "alta"}]
        self.assertEqual(A.kappa(a, a)["kappa"], 1.0)

    def test_vocabulario_binario(self):
        """Regressao: preso a NIVEIS, um juiz binario devolvia acordo como kappa."""
        a = [{"id": "1", "rotulo": "sim"}, {"id": "2", "rotulo": "sim"},
             {"id": "3", "rotulo": "nao"}, {"id": "4", "rotulo": "nao"}]
        b = [{"id": "1", "rotulo": "sim"}, {"id": "2", "rotulo": "nao"},
             {"id": "3", "rotulo": "nao"}, {"id": "4", "rotulo": "nao"}]
        k = A.kappa(a, b)
        # acordo 0,75; esperado 0,5 -> kappa 0,5 (com kappa preso a NIVEIS dava 0,75)
        self.assertEqual(k["acordo"], 0.75)
        self.assertEqual(k["kappa"], 0.5)

    def test_so_ids_comuns(self):
        a = [{"id": "1", "rotulo": "leve"}]
        b = [{"id": "2", "rotulo": "leve"}]
        self.assertEqual(A.kappa(a, b)["n"], 0)


class TestFronteiras(unittest.TestCase):
    def test_limiares(self):
        lim = {"media": 3, "alta": 6}
        self.assertEqual(A.decidir_score(0, lim), "leve")
        self.assertEqual(A.decidir_score(2, lim), "leve")
        self.assertEqual(A.decidir_score(3, lim), "media")
        self.assertEqual(A.decidir_score(5, lim), "media")
        self.assertEqual(A.decidir_score(6, lim), "alta")
        self.assertEqual(A.decidir_score(99, lim), "alta")


class TestConteudoJson(unittest.TestCase):
    def test_le_content_da_api(self):
        """Regressao: `resposta["json"]` e o corpo da API, nao o conteudo."""
        corpo = {"json": {"choices": [{"message": {"content": '{"nivel":"alta"}'}}]}}
        self.assertEqual(A.conteudo_json(corpo)["nivel"], "alta")

    def test_desembrulha_cerca(self):
        corpo = {"json": {"choices": [{"message": {"content": '```json\n{"nivel":"leve"}\n```'}}]}}
        self.assertEqual(A.conteudo_json(corpo)["nivel"], "leve")

    def test_lixo_devolve_vazio(self):
        self.assertEqual(A.conteudo_json({"json": {"choices": []}}), {})
        self.assertEqual(A.conteudo_json({}), {})
        corpo = {"json": {"choices": [{"message": {"content": "nao sei"}}]}}
        self.assertEqual(A.conteudo_json(corpo), {})

    def test_corpo_da_api_nao_e_confundido_com_conteudo(self):
        corpo = {"json": {"id": "x", "object": "chat.completion"}}
        self.assertEqual(A.conteudo_json(corpo), {})


class TestTreino(unittest.TestCase):
    def test_melhora_num_conjunto_separavel(self):
        """Pesos errados de proposito: o treino tem de os corrigir."""
        from analise import PESOS

        casos = [
            {"texto": "bom dia", "rotulo": "leve"},
            {"texto": "obrigado", "rotulo": "leve"},
            {"texto": "desenha a arquitetura do sistema e justifica o trade-off", "rotulo": "alta"},
            {"texto": "otimiza o algoritmo de ordenacao", "rotulo": "alta"},
        ]
        limiares = {"media": 3, "alta": 6}
        zeros = {k: 0 for k in PESOS}
        antes = A.avaliar_pesos(zeros, casos, limiares)
        novos, r = A.treinar(zeros, casos, limiares, passes=1)
        self.assertGreaterEqual(r["depois"]["macro_f1"], antes["macro_f1"])
        self.assertTrue(any(novos[k] != 0 for k in novos), "o treino nao mexeu em nenhum peso")

    def test_nao_quebra_com_zero_casos(self):
        from analise import PESOS

        novos, r = A.treinar(dict(PESOS), [], {"media": 3, "alta": 6}, passes=1)
        self.assertEqual(novos, dict(PESOS))
        self.assertEqual(r["depois"]["n"], 0)


class TestSeparacao(unittest.TestCase):
    def test_disjunta_e_cobre_tudo(self):
        casos = [{"id": f"{i:03d}", "texto": "t", "rotulo": "leve"} for i in range(50)]
        treino, validacao = A.separar(casos, 11)
        ids_t = {c["id"] for c in treino}
        ids_v = {c["id"] for c in validacao}
        self.assertEqual(len(treino) + len(validacao), 50)
        self.assertEqual(ids_t & ids_v, set())
        self.assertEqual(len(treino), 35)

    def test_deterministica(self):
        casos = [{"id": f"{i:03d}", "texto": "t", "rotulo": "leve"} for i in range(30)]
        a = [c["id"] for c in A.separar(casos, 5)[0]]
        b = [c["id"] for c in A.separar(casos, 5)[0]]
        self.assertEqual(a, b)

    def test_conjunto_minusculo_nao_ficca_vazio(self):
        casos = [{"id": "1", "texto": "t", "rotulo": "leve"}]
        treino, validacao = A.separar(casos, 1)
        self.assertEqual(len(treino), 1)
        self.assertEqual(len(validacao), 1)


class TestPortoes(unittest.TestCase):
    @staticmethod
    def _m(macro, media_f1, leve_f1=0.5, alta_f1=0.5):
        return {"macro_f1": macro, "acerto": macro, "n": 50,
                "por_classe": {"leve": {"f1": leve_f1}, "media": {"f1": media_f1},
                               "alta": {"f1": alta_f1}}}

    def test_fecha_com_kappa_baixo(self):
        passou, razoes = A.portoes({"n": 90, "kappa": 0.35}, self._m(0.5, 0.4),
                                   self._m(0.9, 0.9), 0.55, 0.02)
        self.assertFalse(passou)
        self.assertTrue(any("kappa" in r for r in razoes))

    def test_fecha_com_calibracao_insuficiente(self):
        passou, razoes = A.portoes({"n": 5, "kappa": 0.9}, self._m(0.5, 0.4),
                                   self._m(0.9, 0.9), 0.55, 0.02)
        self.assertFalse(passou)
        self.assertTrue(any("insuficiente" in r for r in razoes))

    def test_fecha_com_ganho_pequeno(self):
        passou, razoes = A.portoes({"n": 90, "kappa": 0.8}, self._m(0.70, 0.5),
                                   self._m(0.71, 0.6), 0.55, 0.02)
        self.assertFalse(passou)
        self.assertTrue(any("ganho" in r for r in razoes))

    def test_fecha_se_media_desce(self):
        passou, razoes = A.portoes({"n": 90, "kappa": 0.8}, self._m(0.50, 0.60),
                                   self._m(0.60, 0.55), 0.55, 0.02)
        self.assertFalse(passou)
        self.assertTrue(any("media" in r for r in razoes))

    def test_abre_quando_tudo_melhora(self):
        passou, razoes = A.portoes({"n": 90, "kappa": 0.8}, self._m(0.50, 0.40),
                                   self._m(0.62, 0.60), 0.55, 0.02)
        self.assertTrue(passou, razoes)


class TestColheita(unittest.TestCase):
    def test_exclui_gold_e_o_que_ja_foi_usado(self):
        pool = [{"id": "a" * 12, "texto": "bom dia equipa", "comprimento": 14},
                {"id": "b" * 12, "texto": "x" * 100, "comprimento": 100}]
        original_pool, original_cursor = A._pool, A.CURSOR
        try:
            A._pool = lambda: pool  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as d:
                A.CURSOR = Path(d) / "cursor.json"
                A.CURSOR.write_text(json.dumps({"usados": ["b" * 12]}), encoding="utf-8")
                casos, meta = A.colher(10, 1)
                self.assertEqual(meta["frescos"], 1)
                self.assertEqual([c["id"] for c in casos], ["a" * 12])
        finally:
            A._pool, A.CURSOR = original_pool, original_cursor

    def test_devolve_o_limite_pedido_e_nao_o_resto_da_divisao(self):
        """Pedir 40 dava 39: 40 // 3 estratos = 13, e 13 x 3 = 39."""
        pool = [{"id": f"{i:012d}", "texto": "x" * comp, "comprimento": comp}
                for i, comp in enumerate([20, 100, 400] * 20)]
        orig_pool, orig_cursor, orig_gold = A._pool, A.CURSOR, A._textos_gold
        try:
            A._pool = lambda: pool  # type: ignore[assignment]
            A._textos_gold = lambda: set()  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as d:
                A.CURSOR = Path(d) / "cursor.json"
                casos, meta = A.colher(40, 7)
                self.assertEqual(len(casos), 40)
                self.assertEqual(meta["frescos"], 60)
                self.assertEqual(len({c["id"] for c in casos}), 40)
        finally:
            A._pool, A.CURSOR, A._textos_gold = orig_pool, orig_cursor, orig_gold

    def test_sem_novidades_nao_quebra(self):
        original_pool, original_cursor = A._pool, A.CURSOR
        try:
            A._pool = lambda: []  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as d:
                A.CURSOR = Path(d) / "cursor.json"
                casos, meta = A.colher(10, 1)
                self.assertEqual(casos, [])
                self.assertIn("motivo", meta)
        finally:
            A._pool, A.CURSOR = original_pool, original_cursor

    def test_deterministico_na_mesma_semente(self):
        pool = [{"id": f"{i:012d}", "texto": "y" * (20 + i), "comprimento": 20 + i}
                for i in range(40)]
        original_pool, original_cursor = A._pool, A.CURSOR
        try:
            A._pool = lambda: pool  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as d:
                A.CURSOR = Path(d) / "cursor.json"
                c1, _ = A.colher(8, 7)
                c2, _ = A.colher(8, 7)
                self.assertEqual([c["id"] for c in c1], [c["id"] for c in c2])
        finally:
            A._pool, A.CURSOR = original_pool, original_cursor


class TestJuizEstavel(unittest.TestCase):
    """O juiz raciocina antes de escrever o JSON: o orcamento tem de contar com isso.

    Medido a 2026-11-03: com `max_tokens` 1200 o `deepseek-v4-pro` devolveu
    exactamente 1182 tokens sem nivel reconhecivel, e no ciclo seguinte 71 dos
    90 casos do gold morreram em timeout (30 s) e 500 — o portao fechou com
    "19 casos comuns" e o juiz parecia sem autoridade (kappa 0,83 nos poucos
    que passaram).
    """

    def test_orcamento_do_juiz_conta_com_o_raciocinio(self):
        d = A._juiz_definicao("deepseek", "deepseek-v4-pro")
        self.assertGreaterEqual(int(d["max_tokens"]), 3000)
        self.assertGreaterEqual(float(d["timeout_s"]), 120)

    def test_repõe_o_disjuntor_e_recupera_o_caso(self):
        class Falso:
            def __init__(self):
                self.chamadas = 0
                self.reposto = 0

            def classificar(self, mensagens, definicao, resolver, perfil="", prompt=""):
                self.chamadas += 1
                return None if self.chamadas == 1 else {"nivel": "alta"}

            def limpar_cache(self):
                self.reposto += 1

        falso = Falso()
        original_modelo, original_sleep = A._modelo, A.time.sleep
        A._modelo = lambda: falso
        A.time.sleep = lambda _s: None
        try:
            rotulos = A._etiquetar([{"id": "x1", "texto": "depura o cluster"}],
                                   {"provider": "p"}, lambda: None, 1)
            self.assertEqual(rotulos[0]["rotulo"], "alta")
            self.assertEqual(falso.chamadas, 2)
            self.assertEqual(falso.reposto, 1)
        finally:
            A._modelo, A.time.sleep = original_modelo, original_sleep

    def test_desiste_ao_fim_de_duas_tentativas(self):
        class Sempre:
            def __init__(self):
                self.chamadas = 0

            def classificar(self, *a, **k):
                self.chamadas += 1
                return None

        sempre = Sempre()
        original_modelo, original_sleep = A._modelo, A.time.sleep
        A._modelo = lambda: sempre
        A.time.sleep = lambda _s: None
        try:
            rotulos = A._etiquetar([{"id": "x1", "texto": "a"}], {"provider": "p"},
                                   lambda: None, 1)
            self.assertIsNone(rotulos[0]["rotulo"])
            self.assertEqual(sempre.chamadas, 2)
            self.assertIn("suspensa", rotulos[0]["erro"])
        finally:
            A._modelo, A.time.sleep = original_modelo, original_sleep


class TestAbcModelo(unittest.TestCase):
    def test_none_e_falha_e_nao_estoura(self):
        """Regressao: `classificar` devolve None com a rota suspensa e o A/B morria."""

        class Falso:
            PROMPTS = {"v1": "json v1", "v2": "json v2"}

            def classificar(self, mensagens, definicao, resolver, perfil="", prompt=""):
                if mensagens[0]["content"] == "falha":
                    return None
                return {"nivel": "leve" if prompt.endswith("v1") else "alta"}

        original = A._modelo
        A._modelo = lambda: Falso()
        try:
            casos = [{"id": "1", "texto": "a", "rotulo": "leve"},
                     {"id": "2", "texto": "falha", "rotulo": "alta"}]
            r = A.abc_modelo(casos, {}, None, ["v1", "v2"], 1, 2)
            for v in ("v1", "v2"):
                self.assertIn("macro_f1_media", r[v])
                self.assertEqual(r[v]["falhas"], 1)
                self.assertEqual(r[v]["comparaveis"], 1)
        finally:
            A._modelo = original

    def test_variante_desconhecida_nao_parte(self):
        r = A.abc_modelo([{"id": "1", "texto": "a", "rotulo": "leve"}], {}, None,
                         ["v9"], 1, 1)
        self.assertEqual(r["v9"]["erro"], "variante desconhecida")

    def test_ritmo_espaca_chamadas(self):
        esperar = A._ritmador(0.05)
        inicio = time.monotonic()
        for _ in range(4):
            esperar()
        self.assertGreaterEqual(time.monotonic() - inicio, 0.15)

    def test_sem_ritmo_nao_atrasa(self):
        esperar = A._ritmador(0)
        inicio = time.monotonic()
        for _ in range(50):
            esperar()
        self.assertLess(time.monotonic() - inicio, 0.2)

    def test_todas_falham_devolve_metricas_vazias(self):
        class Nunca:
            PROMPTS = {"v1": "json"}

            def classificar(self, *a, **k):
                return None

        original = A._modelo
        A._modelo = lambda: Nunca()
        try:
            r = A.abc_modelo([{"id": "1", "texto": "a", "rotulo": "leve"}], {}, None, ["v1"], 1, 1)
            self.assertEqual(r["v1"]["comparaveis"], 0)
            self.assertEqual(r["v1"]["macro_f1_media"], 0.0)
        finally:
            A._modelo = original


class TestReversao(unittest.TestCase):
    def test_sem_historico_recusa(self):
        original = A.HISTORICO
        try:
            with tempfile.TemporaryDirectory() as d:
                A.HISTORICO = Path(d) / "historico.jsonl"
                r = A.reverter()
                self.assertFalse(r["ok"])
                self.assertIn("erro", r)
        finally:
            A.HISTORICO = original


class TestVariantes(unittest.TestCase):
    def test_variantes_declaradas_e_json(self):
        modelo = A._modelo()
        self.assertEqual(sorted(modelo.PROMPTS), ["v1", "v2", "v3"])
        # O provider exige a palavra "json" no prompt para aceitar response_format.
        for nome, prompt in modelo.PROMPTS.items():
            self.assertIn("json", prompt.lower(), nome)

    def test_variante_invalida_e_recusada(self):
        modelo = A._modelo()
        _, erros = modelo.normalizar({"provider": "p", "modelo": "m", "prompt_variante": "v9"})
        self.assertTrue(any("prompt_variante" in e for e in erros))

    def test_variante_valida_sobrevive(self):
        modelo = A._modelo()
        d, erros = modelo.normalizar({"provider": "p", "modelo": "m", "prompt_variante": "v3"})
        self.assertEqual(erros, [])
        self.assertEqual(d["prompt_variante"], "v3")


class TestPromocaoEReversao(unittest.TestCase):
    """O caminho que muda comportamento — e por isso o que mais tem de ser provado.

    Medido a 2026-11-02: `promover` e `reverter` nao tinham um unico teste com
    escrita real, so um caso de `reverter` sem historico. O ciclo podia "passar
    os portoes" e gravar lixo sem que nada acusasse.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._config_antigo = A.CONFIG
        self._aprendiz_antigo = A.APRENDIZ
        self._cursor_antigo = A.CURSOR
        self._hist_antigo = A.HISTORICO
        self._rel_antigo = A.RELATORIOS
        self._vmm_dir = os.environ.get("VMM_DIR")
        A.CONFIG = self.dir / "config.json"
        A.APRENDIZ = self.dir / "aprendiz"
        A.CURSOR = A.APRENDIZ / "cursor.json"
        A.HISTORICO = A.APRENDIZ / "historico.jsonl"
        A.RELATORIOS = A.APRENDIZ / "relatorios"
        A.APRENDIZ.mkdir(parents=True, exist_ok=True)
        A.RELATORIOS.mkdir(parents=True, exist_ok=True)
        os.environ["VMM_DIR"] = str(self.dir)
        import gestor
        self.gestor = gestor
        self.gestor.gravar(json.loads(json.dumps(self.gestor.SEED)))

    def tearDown(self):
        A.CONFIG = self._config_antigo
        A.APRENDIZ = self._aprendiz_antigo
        A.CURSOR = self._cursor_antigo
        A.HISTORICO = self._hist_antigo
        A.RELATORIOS = self._rel_antigo
        if self._vmm_dir is None:
            os.environ.pop("VMM_DIR", None)
        else:
            os.environ["VMM_DIR"] = self._vmm_dir
        self._tmp.cleanup()

    def _promover(self, pesos, variante=None):
        return A.promover(pesos, {"ts": "2026-11-02T00:00:00+00:00", "passou": True}, variante)

    def _hist(self, registos):
        with A.HISTORICO.open("w", encoding="utf-8") as f:
            for r in registos:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_promover_grava_pesos_e_meta(self):
        antes = json.loads(A.CONFIG.read_text(encoding="utf-8"))
        self.assertNotIn("pesos", antes.get("analise") or {})
        r = self._promover({"trabalho": 4, "dominio": 2})
        self.assertTrue(r["ok"], r)
        depois = json.loads(A.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(depois["analise"]["pesos"], {"trabalho": 4, "dominio": 2})
        self.assertTrue(depois["analise"]["aprendiz"]["passou"])

    def test_promover_grava_variante_no_classificador(self):
        cfg = json.loads(A.CONFIG.read_text(encoding="utf-8"))
        cfg["analise"]["classificador"] = {"activo": False, "provider": "deepseek",
                                           "modelo": "deepseek-flash"}
        self.gestor.gravar(cfg)
        r = self._promover({"trabalho": 4}, variante="v2")
        self.assertTrue(r["ok"], r)
        depois = json.loads(A.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(depois["analise"]["classificador"]["prompt_variante"], "v2")

    def test_promover_recusa_pesos_invalidos_e_nao_grava(self):
        antes = A.CONFIG.read_text(encoding="utf-8")
        r = self._promover({"trabalho": "muito"})
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("erros"))
        self.assertEqual(A.CONFIG.read_text(encoding="utf-8"), antes)

    def test_reverter_sem_promocao_falha(self):
        r = A.reverter()
        self.assertFalse(r["ok"])
        self.assertIn("nunca houve promocao", r["erro"])

    def test_reverter_uma_promocao_volta_ao_origem(self):
        self._promover({"trabalho": 4})
        self._hist([{"estado": "promovido", "pesos": {"trabalho": 4}}])
        r = A.reverter()
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["para_origem"])
        depois = json.loads(A.CONFIG.read_text(encoding="utf-8"))
        self.assertNotIn("pesos", depois["analise"])
        self.assertNotIn("aprendiz", depois["analise"])

    def test_reverter_duas_promocoes_volta_a_penultima(self):
        self._promover({"trabalho": 5})
        self._hist([{"estado": "promovido", "pesos": {"trabalho": 3}},
                    {"estado": "promovido", "pesos": {"trabalho": 5}}])
        r = A.reverter()
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["para_origem"])
        depois = json.loads(A.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(depois["analise"]["pesos"], {"trabalho": 3})

    def test_reverter_valida_antes_de_gravar(self):
        self._promover({"trabalho": 4})
        antes = A.CONFIG.read_text(encoding="utf-8")
        self._hist([{"estado": "promovido", "pesos": {"trabalho": "lixo"}},
                    {"estado": "promovido", "pesos": {"trabalho": 4}}])
        # anterior = {"trabalho": "lixo"} -> a validacao tem de travar a escrita
        r = A.reverter()
        self.assertFalse(r["ok"])
        self.assertEqual(A.CONFIG.read_text(encoding="utf-8"), antes)

    def test_historico_ilegivel_nao_estoura(self):
        A.HISTORICO.write_text("nao e json\n{\"estado\": \"relatorio\"}\n", encoding="utf-8")
        r = A.reverter()
        self.assertFalse(r["ok"])
        self.assertIn("nunca houve promocao", r["erro"])


class TestPortaoPorFronteira(unittest.TestCase):
    """O portao mede os cortes que mudam de modelo, nao o kappa 3 classes."""

    def _pares(self, reais, previstos):
        a = [{"id": str(i), "rotulo": r} for i, r in enumerate(reais)]
        b = [{"id": str(i), "rotulo": p} for i, p in enumerate(previstos)]
        return a, b

    def _metricas(self, f1=0.80):
        return {"macro_f1": f1, "por_classe": {c: {"f1": f1} for c in A.NIVEIS}}

    def test_concordancia_perfeita(self):
        a, b = self._pares(["leve", "media", "alta"] * 8, ["leve", "media", "alta"] * 8)
        f = A.kappa_fronteiras(a, b)
        self.assertEqual(f["alta"]["kappa"], 1.0)
        self.assertEqual(f["baixa"]["kappa"], 1.0)

    def test_erro_dentro_da_banda_baixa_nao_afunda_a_fronteira_alta(self):
        # `media` sempre previsto como `leve`; `alta` sempre certo
        a, b = self._pares(["leve", "media", "alta"] * 10, ["leve", "leve", "alta"] * 10)
        f = A.kappa_fronteiras(a, b)
        self.assertEqual(f["alta"]["kappa"], 1.0)
        self.assertLess(f["baixa"]["kappa"], 0.4)

    def test_fronteira_baixa_ignora_casos_de_alta(self):
        a, b = self._pares(["alta"] * 3 + ["leve", "media"] * 5,
                           ["alta"] * 3 + ["leve", "leve"] * 5)
        f = A.kappa_fronteiras(a, b)
        self.assertEqual(f["baixa"]["n"], 10)
        self.assertEqual(f["alta"]["n"], 13)

    def test_fronteira_nao_operacional_nao_fecha_o_portao(self):
        # `baixa` sem autoridade, mas a config nao distingue media de leve
        f = {"alta": {"n": 30, "kappa": 0.80}, "baixa": {"n": 30, "kappa": 0.20}}
        op = {"alta": True, "baixa": False}
        ok, razoes = A.portoes({"n": 90, "kappa": 0.40}, self._metricas(0.70),
                               self._metricas(0.80), 0.60, 0.02, f, op)
        self.assertTrue(ok, razoes)

    def test_fronteira_operacional_sem_autoridade_fecha(self):
        f = {"alta": {"n": 30, "kappa": 0.70}, "baixa": {"n": 30, "kappa": 0.20}}
        op = {"alta": True, "baixa": True}
        ok, razoes = A.portoes({"n": 90, "kappa": 0.40}, self._metricas(0.70),
                               self._metricas(0.80), 0.60, 0.02, f, op)
        self.assertFalse(ok)
        self.assertEqual(len(razoes), 1)
        self.assertIn("`baixa`", razoes[0])

    def test_alta_sem_autoridade_fecha_sempre(self):
        f = {"alta": {"n": 30, "kappa": 0.55}, "baixa": {"n": 30, "kappa": 0.90}}
        op = {"alta": True, "baixa": False}
        ok, razoes = A.portoes({"n": 90, "kappa": 0.40}, self._metricas(0.70),
                               self._metricas(0.80), 0.60, 0.02, f, op)
        self.assertFalse(ok)
        self.assertIn("`alta`", razoes[0])

    def test_fronteira_sem_casos_suficientes(self):
        a, b = self._pares(["alta"] * 3 + ["leve"] * 2, ["alta"] * 3 + ["leve"] * 2)
        f = A.kappa_fronteiras(a, b)
        ok, razoes = A.portoes({"n": 5, "kappa": 0.9}, self._metricas(0.70),
                               self._metricas(0.80), 0.60, 0.02, f, {"alta": True, "baixa": True})
        self.assertFalse(ok)
        self.assertTrue(any("sem casos suficientes" in r for r in razoes))

    def test_sem_fronteiras_mantem_o_portao_antigo(self):
        ok, razoes = A.portoes({"n": 90, "kappa": 0.49}, self._metricas(0.80),
                               self._metricas(0.80), 0.60, 0.02, None, None)
        self.assertFalse(ok)
        self.assertTrue(any("discorda dos rotulos" in r for r in razoes))


class TestFronteirasOperacionais(unittest.TestCase):
    """So contam os cortes que mudam de modelo na config viva."""

    def test_leve_e_media_com_o_mesmo_modelo_nao_e_fronteira(self):
        cfg = {"perfis": {}, "catalogo": [
            {"nome": "a", "modelo": "m1", "complexidade": ["leve", "media"]},
            {"nome": "b", "modelo": "m2", "complexidade": ["alta"]},
        ]}
        op = A.fronteiras_operacionais(cfg)
        self.assertTrue(op["alta"])
        self.assertFalse(op["baixa"])

    def test_modelos_diferentes_em_media_e_leve_e_fronteira(self):
        cfg = {"perfis": {}, "catalogo": [
            {"nome": "a", "modelo": "m1", "complexidade": ["leve"]},
            {"nome": "b", "modelo": "m2", "complexidade": ["media"]},
            {"nome": "c", "modelo": "m3", "complexidade": ["alta"]},
        ]}
        op = A.fronteiras_operacionais(cfg)
        self.assertTrue(op["alta"])
        self.assertTrue(op["baixa"])

    def test_um_perfil_que_distingue_chega(self):
        cfg = {"perfis": {
            "default": {"catalogo": [
                {"nome": "a", "modelo": "m1", "complexidade": ["leve", "media"]},
                {"nome": "b", "modelo": "m2", "complexidade": ["alta"]}]},
            "pessoal": {"catalogo": [
                {"nome": "a", "modelo": "m1", "complexidade": ["leve"]},
                {"nome": "b", "modelo": "m2", "complexidade": ["media"]},
                {"nome": "c", "modelo": "m3", "complexidade": ["alta"]}]},
        }, "catalogo": [
            {"nome": "a", "modelo": "m1", "complexidade": ["leve", "media"]},
            {"nome": "b", "modelo": "m2", "complexidade": ["alta"]},
        ]}
        op = A.fronteiras_operacionais(cfg)
        self.assertTrue(op["alta"], "o caso mais exigente e que manda")
        self.assertTrue(op["baixa"])


class TestFronteirasEstaveis(unittest.TestCase):
    """Um juiz que oscila nao ganha autoridade por uma corrida boa."""

    def test_sem_historico_usa_a_corrida_actual(self):
        f = {"alta": {"n": 30, "kappa": 0.80}, "baixa": {"n": 30, "kappa": 0.70}}
        saida, comp = A.fronteiras_estaveis(f, [])
        self.assertEqual(saida["alta"]["kappa"], 0.80)
        self.assertFalse(comp["comparado"])

    def test_pior_valor_entre_as_duas_corridas(self):
        f = {"alta": {"n": 30, "kappa": 0.80}, "baixa": {"n": 30, "kappa": 0.70}}
        hist = [{"estado": "relatorio", "fronteiras": {"alta": {"n": 30, "kappa": 0.54},
                                                       "baixa": {"n": 30, "kappa": 0.90}}}]
        saida, comp = A.fronteiras_estaveis(f, hist)
        self.assertEqual(saida["alta"]["kappa"], 0.54)
        self.assertEqual(saida["baixa"]["kappa"], 0.70)
        self.assertTrue(comp["comparado"])
        self.assertEqual(saida["alta"]["kappa_corrida_anterior"], 0.54)

    def test_ignora_registos_sem_fronteiras(self):
        f = {"alta": {"n": 30, "kappa": 0.80}}
        hist = [{"estado": "promovido"}, {"estado": "relatorio", "fronteiras": {"alta": {"n": 30, "kappa": 0.62}}}]
        saida, comp = A.fronteiras_estaveis(f, hist)
        self.assertEqual(saida["alta"]["kappa"], 0.62)
        self.assertTrue(comp["comparado"])


class TestLeitorGold(unittest.TestCase):
    """O leitor do gold ja ignorou um ficheiro novo em silencio.

    O tuplo `("gold.jsonl", "gold2.jsonl")` estava escrito a mao em dois
    sitios: um `gold3.jsonl` com rotulos humanos nao entrava na calibracao
    nem na barreira de contaminacao — o portao recusava por "0 casos comuns"
    e o caso rotulado a mao podia voltar a ser colhido no ciclo seguinte.
    """

    def _eval_falso(self, d: str) -> Path:
        raiz = Path(d)
        (raiz / "gold.jsonl").write_text(
            json.dumps({"id": "g001", "texto": "bom dia", "esperado": "leve"}) + "\n",
            encoding="utf-8")
        (raiz / "gold2.jsonl").write_text(
            json.dumps({"id": "n001", "texto": "o disco encheu", "rotulo": "media"}) + "\n",
            encoding="utf-8")
        (raiz / "gold3.jsonl").write_text(
            json.dumps({"id": "p001", "texto": "renova o cert do nginx", "rotulo": "media"}) + "\n"
            + json.dumps({"id": "p002", "texto": "sem rotulo ainda", "rotulo": ""}) + "\n"
            + json.dumps({"id": "p003", "texto": "rotulo invalido", "rotulo": "baixa"}) + "\n",
            encoding="utf-8")
        return raiz

    def test_le_todos_os_gold_nao_so_os_dois_primeiros(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = self._eval_falso(d)
            original = A.EVAL
            try:
                A.EVAL = raiz
                nomes = [p.name for p in A._ficheiros_gold()]
                self.assertEqual(nomes, ["gold.jsonl", "gold2.jsonl", "gold3.jsonl"])
                casos = A._casos_gold()
                self.assertEqual(len(casos), 3)  # p002 vazio e p003 fora do dominio caem
                ids = [c["id"] for c in casos]
                self.assertIn("gold3.jsonl:p001", ids)
            finally:
                A.EVAL = original

    def test_aceita_esperado_e_rotulo(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = self._eval_falso(d)
            original = A.EVAL
            try:
                A.EVAL = raiz
                por_id = {c["id"]: c["rotulo"] for c in A._casos_gold()}
                self.assertEqual(por_id["gold.jsonl:g001"], "leve")
                self.assertEqual(por_id["gold2.jsonl:n001"], "media")
                self.assertEqual(por_id["gold3.jsonl:p001"], "media")
                self.assertNotIn("gold3.jsonl:p002", por_id)
                self.assertNotIn("gold3.jsonl:p003", por_id)
            finally:
                A.EVAL = original

    def test_barreira_de_contaminacao_cobre_a_fila_humana(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = self._eval_falso(d)
            original = A.EVAL
            try:
                A.EVAL = raiz
                shas = A._textos_gold()
                self.assertIn(A._sha("renova o cert do nginx"), shas)
                self.assertEqual(len(shas), 3)
            finally:
                A.EVAL = original

    def test_le_o_gold_humano_fora_do_repositorio(self):
        """A fila rotulada sobre conversa real vive fora do repo publicado.

        Nota de nomenclatura: o `_eval_falso` ja tem um `gold3.jsonl`, e a
        dedupe e por nome — o do repo ganha. A fila humana usa `gold4.jsonl`.
        """
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as h:
            raiz = self._eval_falso(d)
            humano = Path(h)
            (humano / "gold4.jsonl").write_text(
                json.dumps({"id": "q001", "texto": "aquilo num ta a dar nada",
                            "rotulo": "media"}) + "\n", encoding="utf-8")
            original_eval, original_humano = A.EVAL, A.GOLD_HUMANO
            try:
                A.EVAL, A.GOLD_HUMANO = raiz, humano
                ids = [c["id"] for c in A._casos_gold()]
                self.assertIn("gold4.jsonl:q001", ids)
                self.assertEqual(len(ids), 4)
                # o repo publicado nao ganha nada por causa disto
                self.assertFalse((raiz / "gold4.jsonl").exists())
            finally:
                A.EVAL, A.GOLD_HUMANO = original_eval, original_humano

    def test_nome_repetido_conta_uma_vez(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as h:
            raiz = self._eval_falso(d)
            humano = Path(h)
            (humano / "gold2.jsonl").write_text(
                json.dumps({"id": "n001", "texto": "duplicado", "rotulo": "alta"}) + "\n",
                encoding="utf-8")
            original_eval, original_humano = A.EVAL, A.GOLD_HUMANO
            try:
                A.EVAL, A.GOLD_HUMANO = raiz, humano
                nomes = [p.name for p in A._ficheiros_gold()]
                self.assertEqual(nomes.count("gold2.jsonl"), 1)
                self.assertEqual(len(A._casos_gold()), 3)
            finally:
                A.EVAL, A.GOLD_HUMANO = original_eval, original_humano


if __name__ == "__main__":
    unittest.main(verbosity=1)
