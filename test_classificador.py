"""Testes do classificador por modelo — servidor HTTP local, sem mocks.

O que se prova aqui:

1. `normalizar` aplica limites duros e recusa `activo` sem rota.
2. `efectiva` devolve `None` sempre que a heuristica deve mandar (desligado,
   sem provider/modelo, bloco invalido).
3. A heranca global -> perfil e a substituicao por inteiro (um perfil desliga o
   que a config global liga) sao feitas pelo `gestor.validar`.
4. `classificar` faz um POST real a um servidor local, le a resposta, valida o
   corpo enviado (modelo, max_tokens, prompt) e devolve o nivel.
5. Cache: a mesma ultima mensagem nao paga duas chamadas.
6. Fail-open: 500, resposta sem nivel e timeout devolvem `erro` e nenhum nivel.
7. Disjuntor: tres falhas suspendem o alvo.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

AQUI = Path(__file__).resolve().parent


def _mod(nome: str):
    spec = importlib.util.spec_from_file_location(f"vmm_t_{nome}", AQUI / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


am = _mod("analise_modelo")
gestor = _mod("gestor")

PEDIDOS: list[dict] = []
RESPOSTA = {"conteudo": "alta", "codigo": 200, "atraso": 0.0, "json": None}
# Fila para provar o retry: cada POST consome uma entrada; vazia = RESPOSTA.
FILA: list[dict] = []


class Servidor(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = json.loads(self.rfile.read(tamanho) or b"{}")
        PEDIDOS.append({"corpo": corpo, "auth": self.headers.get("Authorization"),
                        "ua": self.headers.get("User-Agent"), "caminho": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()}})
        atual = FILA.pop(0) if FILA else RESPOSTA
        if atual["atraso"]:
            time.sleep(float(atual["atraso"]))
        codigo = int(atual["codigo"])
        if atual["json"] is not None:
            dados = atual["json"]
        else:
            dados = {"choices": [{"message": {"role": "assistant",
                                              "content": atual["conteudo"]}}],
                     "usage": {"prompt_tokens": 120, "completion_tokens": 2}}
        bruto = json.dumps(dados).encode()
        try:
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(bruto)))
            self.end_headers()
            self.wfile.write(bruto)
        except (BrokenPipeError, ConnectionResetError):
            # O cliente desistiu (teste do timeout): nao e falha do teste.
            return

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - assinatura da base
        return


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
        cls.porta = cls.servidor.server_address[1]
        cls.thread = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def setUp(self) -> None:
        PEDIDOS.clear()
        FILA.clear()
        RESPOSTA.update({"conteudo": "alta", "codigo": 200, "atraso": 0.0, "json": None})
        am.limpar_cache()
        # A descoberta de transporte vive em memoria por processo: sem a limpar,
        # um teste herdava o achado do anterior e deixava de provar a sonda.
        am._via_detectada.clear()

    def definicao(self, **extra):
        d = dict(am.PADRAO)
        d.update({"activo": True, "provider": "openrouter",
                  "modelo": "thinkingmachines/inkling:free", "timeout_s": 5.0})
        d.update(extra)
        return d

    def rota(self):
        return {"provider": "openrouter", "api_key": "chave-de-teste",
                "base_url": f"http://127.0.0.1:{self.porta}/api/v1",
                "api_mode": "openai", "source": "env:TESTE"}

    def mensagens(self, texto: str = "desenha a arquitetura do AAD"):
        return [{"role": "user", "content": texto}]


class TestNormalizar(Base):
    def test_desligado_por_omissao(self):
        d, erros = am.normalizar({})
        self.assertFalse(d["activo"])
        self.assertEqual(erros, [])
        self.assertIsNone(am.efectiva({"classificador": d}))

    def test_activo_sem_rota_e_erro(self):
        d, erros = am.normalizar({"activo": True})
        self.assertEqual(len(erros), 1)
        self.assertIn("exige provider e modelo", erros[0])

    def test_limites_duros(self):
        d, _ = am.normalizar({"activo": True, "provider": "p", "modelo": "m",
                              "timeout_s": 999, "max_tokens": 100000,
                              "cache_ttl_s": -5, "max_chars": 5})
        self.assertEqual(d["timeout_s"], am._TIMEOUT_MAX)  # tecto duro, nao um literal
        self.assertEqual(d["max_tokens"], am._MAX_TOKENS_MAX)  # tecto duro, nao um literal
        self.assertEqual(d["cache_ttl_s"], 0)
        self.assertEqual(d["max_chars"], 200)
        d2, _ = am.normalizar({"timeout_s": 0.1})
        self.assertEqual(d2["timeout_s"], 1.0)

    def test_tecto_cabe_o_raciocinio(self):
        """30 s / 1024 tokens truncavam um juiz que pensa antes de escrever."""
        self.assertGreaterEqual(am._TIMEOUT_MAX, 120.0)
        self.assertGreaterEqual(am._MAX_TOKENS_MAX, 3000)
        self.assertGreater(am._MAX_TOKENS_COM_ESFORCO, 1200)
        d, erros = am.normalizar({"activo": True, "provider": "p", "modelo": "m",
                                  "timeout_s": 120, "max_tokens": 3000})
        self.assertEqual(erros, [])
        self.assertEqual(d["timeout_s"], 120.0)
        self.assertEqual(d["max_tokens"], 3000)

    def test_lixo_nao_levanta(self):
        for bruto in ("texto", [], 42, None):
            d, erros = am.normalizar(bruto)
            self.assertIn("activo", d)
        _, erros = am.normalizar("texto")
        self.assertTrue(erros)


class TestEfectiva(Base):
    def test_activo_com_rota(self):
        d = self.definicao()
        self.assertEqual(am.efectiva({"classificador": d})["modelo"],
                         "thinkingmachines/inkling:free")

    def test_sem_provider_nao_e_efectiva(self):
        self.assertIsNone(am.efectiva({"classificador": self.definicao(provider="")}))

    def test_analise_ausente(self):
        self.assertIsNone(am.efectiva(None))
        self.assertIsNone(am.efectiva({}))


class TestHeranca(Base):
    def _config(self, global_c, perfil_c=None):
        perfil: dict[str, Any] = {
            "catalogo": [{"id": "a", "provider": "deepseek",
                          "modelo": "deepseek-flash", "complexidade": ["leve"]}]}
        if perfil_c is not None:
            perfil["analise"] = {"classificador": perfil_c}
        return {"perfis": {"default": perfil}, "analise": {"classificador": global_c}}

    def test_global_herda_para_o_perfil(self):
        cfg, erros = gestor.validar(self._config(self.definicao()))
        self.assertEqual(erros, [])
        pcfg = gestor.perfil_config(cfg, "default")
        self.assertEqual(am.efectiva(pcfg["analise"])["provider"], "openrouter")

    def test_perfil_desliga_o_global(self):
        cfg, erros = gestor.validar(
            self._config(self.definicao(), {"activo": False}))
        self.assertEqual(erros, [])
        pcfg = gestor.perfil_config(cfg, "default")
        # desligado no perfil, activo no global: o perfil manda
        self.assertIsNone(am.efectiva(pcfg["analise"]))
        self.assertTrue(cfg["analise"]["classificador"]["activo"])

    def test_perfil_com_modelo_proprio(self):
        cfg, erros = gestor.validar(self._config(
            self.definicao(),
            {"activo": True, "provider": "deepseek", "modelo": "deepseek-v4-pro"}))
        self.assertEqual(erros, [])
        pcfg = gestor.perfil_config(cfg, "default")
        d = am.efectiva(pcfg["analise"])
        self.assertEqual((d["provider"], d["modelo"]), ("deepseek", "deepseek-v4-pro"))

    def test_perfil_sem_modelo_herda_global_desligado(self):
        cfg, erros = gestor.validar(self._config({"activo": False}))
        self.assertEqual(erros, [])
        self.assertIsNone(am.efectiva(gestor.perfil_config(cfg, "default")["analise"]))

    def test_erro_do_perfil_aparece(self):
        _, erros = gestor.validar(self._config({}, {"activo": True}))
        self.assertTrue(any("perfis.default.analise.classificador" in e for e in erros))


class TestClassificar(Base):
    def test_le_a_resposta_e_valida_o_corpo(self):
        RESPOSTA["conteudo"] = "alta"
        r = am.classificar(self.mensagens(), self.definicao(),
                           lambda p, m: self.rota(), perfil="trabalho")
        self.assertEqual(r["nivel"], "alta")
        self.assertEqual(r["origem"], "modelo")
        self.assertEqual(len(PEDIDOS), 1)
        pedido = PEDIDOS[0]
        self.assertEqual(pedido["corpo"]["model"], "thinkingmachines/inkling:free")
        self.assertEqual(pedido["corpo"]["max_tokens"], am.PADRAO["max_tokens"])
        self.assertFalse(pedido["corpo"]["stream"])
        self.assertEqual(pedido["corpo"]["temperature"], 0)
        self.assertEqual(pedido["caminho"], "/api/v1/chat/completions")
        self.assertEqual(pedido["auth"], "Bearer chave-de-teste")
        self.assertIn("vipernet", (pedido["ua"] or "").lower())
        sistema = pedido["corpo"]["messages"][0]["content"]
        self.assertIn("leve", sistema)
        self.assertIn("desenha a arquitetura", pedido["corpo"]["messages"][1]["content"])
        self.assertEqual(r["classificador"]["perfil"], "trabalho")
        self.assertEqual(r["classificador"]["tokens_entrada"], 120)

    def test_base_url_sem_v1(self):
        RESPOSTA["conteudo"] = "media"
        rota = self.rota()
        rota["base_url"] = f"http://127.0.0.1:{self.porta}/api"
        r = am.classificar(self.mensagens(), self.definicao(), lambda p, m: rota)
        self.assertEqual(r["nivel"], "media")
        self.assertEqual(PEDIDOS[0]["caminho"], "/api/v1/chat/completions")

    def test_acentos_e_pontuacao(self):
        for texto, esperado in (("MÉDIA.", "media"), ("Leve", "leve"),
                                ("a complexidade é alta", "alta")):
            am.limpar_cache()
            RESPOSTA["conteudo"] = texto
            r = am.classificar(self.mensagens(texto + " x"), self.definicao(),
                               lambda p, m: self.rota())
            self.assertEqual(r["nivel"], esperado, texto)

    def test_json_estruturado(self):
        RESPOSTA["conteudo"] = '{"nivel": "media", "porque": "x"}'
        r = am.classificar(self.mensagens(), self.definicao(), lambda p, m: self.rota())
        self.assertEqual(r["nivel"], "media")

    def test_cache_nao_paga_duas_vezes(self):
        am.classificar(self.mensagens("pergunta igual"), self.definicao(),
                       lambda p, m: self.rota())
        r2 = am.classificar(self.mensagens("pergunta igual"), self.definicao(),
                            lambda p, m: self.rota())
        self.assertEqual(len(PEDIDOS), 1)
        self.assertTrue(r2.get("cache"))

    def test_cache_desligado_volta_a_pagar(self):
        d = self.definicao(cache_ttl_s=0)
        am.classificar(self.mensagens("pergunta igual"), d, lambda p, m: self.rota())
        am.classificar(self.mensagens("pergunta igual"), d, lambda p, m: self.rota())
        self.assertEqual(len(PEDIDOS), 2)

    def test_sem_ultima_mensagem_nao_chama(self):
        r = am.classificar([{"role": "assistant", "content": "olá"}],
                           self.definicao(), lambda p, m: self.rota())
        self.assertIsNone(r)
        self.assertEqual(PEDIDOS, [])

    def test_desligado_nao_chama(self):
        self.assertIsNone(am.classificar(self.mensagens(), None, lambda p, m: self.rota()))
        self.assertEqual(PEDIDOS, [])


class TestFailOpen(Base):
    def test_http_500(self):
        RESPOSTA["codigo"] = 500
        r = am.classificar(self.mensagens(), self.definicao(), lambda p, m: self.rota())
        self.assertIsNone(r.get("nivel"))
        self.assertIn("HTTP 500", r["erro"])

    def test_resposta_sem_nivel(self):
        RESPOSTA["conteudo"] = "não sei responder a isso"
        r = am.classificar(self.mensagens(), self.definicao(), lambda p, m: self.rota())
        self.assertIsNone(r.get("nivel"))
        self.assertIn("sem nivel", r["erro"])

    def test_timeout(self):
        RESPOSTA["atraso"] = 2.0
        inicio = time.monotonic()
        r = am.classificar(self.mensagens(), self.definicao(timeout_s=1.0),
                           lambda p, m: self.rota())
        gasto = time.monotonic() - inicio
        self.assertIsNone(r.get("nivel"))
        self.assertLess(gasto, 1.9, "o timeout tem de cortar antes da resposta")
        self.assertIn("erro", r)

    def test_credencial_que_falha(self):
        """Credencial ausente e config, nao avaria: `sem_efeito`, nao `erro`."""
        def falha(_p, _m):
            raise RuntimeError("provider sem credencial no perfil")
        r = am.classificar(self.mensagens(), self.definicao(), falha)
        self.assertIsNone(r.get("nivel"))
        self.assertEqual(r["sem_efeito"], "credencial_ausente")
        self.assertIn("credencial", r["motivo"])
        self.assertNotIn("erro", r)

    def test_disjuntor_suspende_apos_tres(self):
        def falha(_p, _m):
            raise RuntimeError("em baixo")
        d = self.definicao(cache_ttl_s=0)
        for i in range(3):
            am.classificar(self.mensagens(f"pedido {i}"), d, falha)
        # quarta tentativa: suspenso, nao chega a chamar o resolver
        chamadas = []
        r = am.classificar(self.mensagens("pedido final"), d,
                           lambda p, m: chamadas.append(1) or {"api_key": "x"})
        self.assertIsNone(r.get("nivel"))
        self.assertEqual(r["sem_efeito"], "classificador_suspenso")
        self.assertEqual(chamadas, [])
        self.assertEqual(len(am.estado()["alvos_suspensos"]), 1)


class TestTexto(Base):
    def test_ultima_mensagem_manda(self):
        msgs = [{"role": "user", "content": "primeira"},
                {"role": "assistant", "content": "resposta"},
                {"role": "user", "content": "segunda"}]
        corpo, ultima = am.texto(msgs)
        self.assertEqual(ultima, "segunda")
        self.assertIn("[pedido] segunda", corpo)
        self.assertIn("primeira", corpo)

    def test_janela_limita(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        corpo, _ = am.texto(msgs, janela=2)
        self.assertIn("m19", corpo)
        self.assertNotIn("m16", corpo)

    def test_max_chars(self):
        corpo, ultima = am.texto([{"role": "user", "content": "a" * 5000}], max_chars=300)
        self.assertLessEqual(len(corpo), 300)
        self.assertLessEqual(len(ultima), 300)

    def test_conteudo_em_blocos(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "olá bloco"}]}]
        _, ultima = am.texto(msgs)
        self.assertEqual(ultima, "olá bloco")


class TestEstadoDecisoes(Base):
    """O `state` do transporte de decisoes.

    A razao de ser foi medida a 2026-11-03 nos 250 casos do gold: o transcrito
    cru (sete anteriores de outros assuntos) deu kappa de alta 0,3604 e, em
    producao, 127 de 129 leituras abstiveram com p50 0,18. Com o bloco de
    medidas o mesmo pedido deu 0,5479, e com o publicado (medidas + duas
    anteriores rotuladas) 0,6349. Estes testes fixam a FORMA, nao o numero.
    """

    def test_pedido_isolado_e_medido(self):
        corpo, _ = am.texto([{"role": "user", "content": "rever o pipeline"}])
        s = am._estado_decisoes(corpo)
        self.assertIn("[MEDIDAS DO PEDIDO]", s)
        self.assertIn("[PEDIDO A CLASSIFICAR]", s)
        self.assertIn("rever o pipeline", s)
        self.assertIn("caracteres:", s)

    def test_anterior_fica_rotulado_como_contexto(self):
        msgs = [{"role": "user", "content": "primeira coisa"},
                {"role": "assistant", "content": "certo"},
                {"role": "user", "content": "segunda coisa"}]
        corpo, _ = am.texto(msgs)
        s = am._estado_decisoes(corpo)
        self.assertIn("[CONTEXTO ANTERIOR, NAO E O PEDIDO A CLASSIFICAR]", s)
        self.assertIn("primeira coisa", s)
        # o que se classifica vem depois, e so ele
        self.assertGreater(s.index("[PEDIDO A CLASSIFICAR]"),
                           s.index("[CONTEXTO ANTERIOR"))

    def test_no_maximo_duas_anteriores(self):
        msgs = [{"role": "user", "content": f"pedido {i}"} for i in range(7)]
        msgs.append({"role": "user", "content": "o alvo"})
        corpo, _ = am.texto(msgs, janela=8, max_chars=4000)
        s = am._estado_decisoes(corpo)
        bloco = s.split("[CONTEXTO ANTERIOR")[1].split("[/CONTEXTO ANTERIOR]")[0]
        self.assertEqual(bloco.count("[anterior]"), 2, bloco)
        self.assertNotIn("pedido 0", bloco)

    def test_anterior_truncada(self):
        msgs = [{"role": "user", "content": "x" * 900},
                {"role": "user", "content": "alvo"}]
        corpo, _ = am.texto(msgs, janela=8, max_chars=4000)
        s = am._estado_decisoes(corpo)
        bloco = s.split("[CONTEXTO ANTERIOR")[1].split("[/CONTEXTO ANTERIOR]")[0]
        linha = [ln for ln in bloco.splitlines() if ln.startswith("[anterior]")][0]
        self.assertLessEqual(len(linha), 302, len(linha))

    def test_modo_simples_devolve_o_corpo(self):
        corpo, _ = am.texto([{"role": "user", "content": "olá"}])
        self.assertEqual(am._estado_decisoes(corpo, "simples"), corpo)

    def test_modo_desconhecido_cai_em_medidas(self):
        corpo, _ = am.texto([{"role": "user", "content": "olá"}])
        self.assertIn("[MEDIDAS DO PEDIDO]", am._estado_decisoes(corpo, "inventado"))

    def test_sem_marcador_ainda_mede(self):
        s = am._estado_decisoes("texto solto sem marcador")
        self.assertIn("[MEDIDAS DO PEDIDO]", s)
        self.assertIn("[PEDIDO A CLASSIFICAR]", s)
        self.assertIn("texto solto sem marcador", s)

    def test_medidas_contam_artefactos(self):
        s = am._medidas_pedido("```py\nx=1\n```\nTraceback (most recent call last):")
        self.assertIn("1 bloco(s) de codigo", s)
        self.assertIn("stack trace", s)

    def test_medidas_sem_artefactos(self):
        self.assertIn("artefactos colados: nenhum", am._medidas_pedido("olá, tudo bem?"))

    def test_estado_e_mais_pequeno_que_o_transcrito(self):
        msgs = [{"role": "user", "content": "assunto antigo " * 40} for _ in range(7)]
        msgs.append({"role": "user", "content": "o alvo"})
        corpo, _ = am.texto(msgs, janela=8, max_chars=4000)
        s = am._estado_decisoes(corpo)
        self.assertLess(len(s), len(corpo))


class TestRubricaDecisao(Base):
    """A rubrica do `choice`: a curta custava 0,12 de precisao em alta."""

    def test_tres_niveis(self):
        self.assertEqual(set(am.CRITERIOS_DECISAO), {"leve", "media", "alta"})

    def test_contra_ancoras_nomeadas(self):
        i = am.INSTRUCOES_DECISAO
        for n in ("CONTRA-ANCORAS", "avaria", "jargao", "ambito aberto",
                  "comprimento", "artefacto colado"):
            self.assertIn(n, i, n)

    def test_regra_de_bolso(self):
        self.assertIn("descobrir o que fazer", am.INSTRUCOES_DECISAO)

    def test_estado_na_definicao_efectiva(self):
        d = am.normalizar({"activo": True, "provider": "openrouter",
                           "modelo": "~typesafe/jev-latest", "via": "decisoes",
                           "estado": "medidas"})[0]
        self.assertEqual(d["estado"], "medidas")

    def test_estado_invalido_recusado_com_erro(self):
        d, erros = am.normalizar({"activo": True, "provider": "openrouter",
                                  "modelo": "m", "estado": "resumido"})
        self.assertEqual(d["estado"], "medidas")
        self.assertTrue(any("estado deve ser" in e for e in erros), erros)

    def test_estado_por_omissao(self):
        self.assertEqual(am.normalizar({})[0]["estado"], "medidas")


class TestExtracao(Base):
    """O leitor da resposta — o sitio onde o modelo de raciocinio nos apanhou.

    Medido com o `deepseek-flash`: com `max_tokens: 12`, os 12 tokens de saida
    eram todos `reasoning_tokens` e o `content` vinha vazio. O orcamento subiu
    para 320, mas a leitura do raciocinio fica como rede: um modelo de raciocinio
    cortado devolve o ultimo nivel que escreveu, em vez de custar um turno.
    """

    def test_orcamento_por_omissao_cobre_raciocinio(self):
        d, erros = am.normalizar({"activo": True, "provider": "p", "modelo": "m"})
        self.assertEqual(d["max_tokens"], am.PADRAO["max_tokens"])
        self.assertGreaterEqual(am.PADRAO["max_tokens"], 256)  # raciocinio precisa de espaco
        self.assertFalse(erros)
        # e o tecto permite subir mais, para modelos que pensam muito
        alto, _ = am.normalizar({"max_tokens": 5000})
        self.assertEqual(alto["max_tokens"], am._MAX_TOKENS_MAX)

    def test_texto_simples(self):
        nivel, lido = am._extrair_nivel(
            {"choices": [{"message": {"content": "alta"}}]})
        self.assertEqual((nivel, lido), ("alta", "alta"))

    def test_acento(self):
        nivel, _ = am._extrair_nivel(
            {"choices": [{"message": {"content": "MÉDIA"}}]})
        self.assertEqual(nivel, "media")

    def test_json(self):
        nivel, _ = am._extrair_nivel(
            {"choices": [{"message": {"content": '{"nivel": "leve"}'}}]})
        self.assertEqual(nivel, "leve")

    def test_raciocinio_cortado_sem_conteudo(self):
        payload = {"choices": [{"message": {
            "content": "",
            "reasoning_content": "We need answer only one word: media likely. Need classify",
        }}]}
        nivel, lido = am._extrair_nivel(payload)
        self.assertEqual(nivel, "media")
        self.assertTrue(lido.startswith("raciocinio:"))

    def test_ultimo_nivel_do_raciocinio_manda(self):
        payload = {"choices": [{"message": {
            "content": None,
            "reasoning_content": "talvez alta? nao: e manutencao leve",
        }}]}
        nivel, _ = am._extrair_nivel(payload)
        self.assertEqual(nivel, "leve")

    def test_raciocinio_sem_nivel_levanta(self):
        with self.assertRaises(ValueError):
            am._extrair_nivel({"choices": [{"message": {
                "content": "", "reasoning_content": "pensar sem decidir"}}]})

    def test_resposta_vazia_levanta(self):
        with self.assertRaises(ValueError):
            am._extrair_nivel({"choices": [{"message": {"content": ""}}]})


def _sem_nivel(motivo: str = "length", tokens: int = 320) -> dict:
    """Resposta do modelo que gastou o orcamento a raciocinar e nao disse nada."""
    return {"json": {"choices": [{"finish_reason": motivo,
                                  "message": {"role": "assistant", "content": ""}}],
                     "usage": {"prompt_tokens": 120, "completion_tokens": tokens}},
            "codigo": 200, "atraso": 0.0, "conteudo": ""}


class TestVariantes(Base):
    """O modelo nem sempre responde em portugues nem em texto limpo."""

    def test_variantes_de_vocabulario(self):
        for texto, esperado in (("Medium", "media"), ("high", "alta"), ("LOW", "leve"),
                                ("baixa", "leve"), ("moderado", "media"),
                                ("the complexity is high here", "alta")):
            payload = {"choices": [{"message": {"content": texto}, "finish_reason": "stop"}]}
            nivel, _ = am._extrair_nivel(payload)
            self.assertEqual(nivel, esperado, texto)

    def test_json_com_acento(self):
        payload = {"choices": [{"message": {"content": '{"nivel": "média"}'}}]}
        nivel, _ = am._extrair_nivel(payload)
        self.assertEqual(nivel, "media")


class TestRetry(Base):
    """Orcamento gasto a raciocinar: a segunda tentativa tem de recuperar."""

    def test_recupera_com_mais_orcamento_e_sem_esforco(self):
        FILA.extend([_sem_nivel(), {"conteudo": "media", "codigo": 200,
                                    "atraso": 0.0, "json": None}])
        d = self.definicao(max_tokens=320, esforco="baixo")
        r = am.classificar(self.mensagens(), d, lambda p, m: self.rota())
        self.assertEqual(r["nivel"], "media")
        self.assertEqual(r["classificador"]["tentativas"], 2)
        self.assertEqual(len(PEDIDOS), 2)
        primeiro, segundo = PEDIDOS[0]["corpo"], PEDIDOS[1]["corpo"]
        self.assertGreater(segundo["max_tokens"], primeiro["max_tokens"])
        # A segunda tentativa nao leva o esforco: e ele que faz raciocinar.
        for campo in ("reasoning_effort", "thinking", "reasoning"):
            self.assertNotIn(campo, segundo)

    def test_duas_falhas_devolvem_erro(self):
        FILA.extend([_sem_nivel(), _sem_nivel(tokens=1200)])
        r = am.classificar(self.mensagens(), self.definicao(max_tokens=320),
                           lambda p, m: self.rota())
        self.assertIsNone(r.get("nivel"))
        self.assertIn("ValueError", r["erro"])
        self.assertEqual(len(PEDIDOS), 2)


class TestCacheNegativo(Base):
    """Um turno faz varios `llm_request`: a falha nao se paga duas vezes."""

    def test_falha_nao_repete_a_chamada(self):
        RESPOSTA.update(_sem_nivel())
        d = self.definicao(cache_ttl_s=300)
        r1 = am.classificar(self.mensagens(), d, lambda p, m: self.rota())
        self.assertIn("erro", r1)
        chamadas = len(PEDIDOS)          # 1 tentativa + 1 retry
        self.assertEqual(chamadas, 2)
        r2 = am.classificar(self.mensagens(), d, lambda p, m: self.rota())
        self.assertEqual(len(PEDIDOS), chamadas, "a falha voltou a pagar chamada")
        self.assertTrue(r2.get("cache_erro"))

    def test_ttl_zero_desliga_o_cache_negativo(self):
        RESPOSTA.update(_sem_nivel())
        d = self.definicao(cache_ttl_s=0)
        am.classificar(self.mensagens(), d, lambda p, m: self.rota())
        depois = len(PEDIDOS)
        am.classificar(self.mensagens(), d, lambda p, m: self.rota())
        self.assertGreater(len(PEDIDOS), depois)

    def test_sucesso_limpa_o_erro_guardado(self):
        RESPOSTA.update(_sem_nivel())
        d = self.definicao(cache_ttl_s=300)
        am.classificar(self.mensagens(), d, lambda p, m: self.rota())
        am.limpar_cache()
        RESPOSTA.update({"conteudo": "leve", "codigo": 200, "atraso": 0.0, "json": None})
        r = am.classificar(self.mensagens(), d, lambda p, m: self.rota())
        self.assertEqual(r["nivel"], "leve")
        self.assertFalse(r.get("cache_erro"))


class TestFormatoJson(Base):
    """O prompt pede json e o corpo leva `response_format`, com degradacao."""

    def test_o_prompt_contem_a_palavra_json(self):
        # O DeepSeek recusa `response_format: json_object` com 400 quando o
        # prompt nao contem a palavra "json" (medido a 2026-11-02). Esta
        # verificacao guarda essa condicao, que nao e obvia no texto do prompt.
        self.assertIn("json", am.PROMPT.lower())
        self.assertIn("json", am.PROMPT_LOTE.lower())

    def test_corpo_pede_response_format_por_omissao(self):
        corpo = am._corpo("openrouter", "m", "texto", self.definicao())
        self.assertEqual(corpo["response_format"], {"type": "json_object"})

    def test_formato_off_nao_pede(self):
        corpo = am._corpo("openrouter", "m", "texto", self.definicao(formato_json="off"))
        self.assertNotIn("response_format", corpo)

    def test_normalizar_formato(self):
        self.assertEqual(am.normalizar({})[0]["formato_json"], "auto")
        self.assertEqual(am.normalizar({"formato_json": "OFF"})[0]["formato_json"], "off")
        self.assertEqual(am.normalizar({"formato_json": "true"})[0]["formato_json"], "on")
        d, erros = am.normalizar({"formato_json": "talvez"})
        self.assertEqual(d["formato_json"], "auto")
        self.assertTrue(any("formato_json" in e for e in erros))

    def test_provider_recusa_o_campo_e_repete_sem_ele(self):
        # 400 com `response_format` (o que o DeepSeek faz sem a palavra json no
        # prompt): o segundo pedido tem de sair sem o campo e o nivel tem de vir.
        FILA.append({"conteudo": "formato nao suportado", "codigo": 400, "atraso": 0.0,
                     "json": {"error": {"message": "response_format nao suportado"}}})
        RESPOSTA.update({"conteudo": "media", "codigo": 200})
        r = am.classificar(self.mensagens(), self.definicao(), lambda p, m: self.rota())
        self.assertEqual(r["nivel"], "media")
        self.assertIn("response_format", PEDIDOS[0]["corpo"])
        self.assertNotIn("response_format", PEDIDOS[1]["corpo"])
        self.assertEqual(r["classificador"]["formato_pedido"], "texto")

    def test_401_nao_insiste(self):
        RESPOSTA.update({"conteudo": "x", "codigo": 401, "json": {"error": "sem chave"}})
        r = am.classificar(self.mensagens(), self.definicao(), lambda p, m: self.rota())
        self.assertIn("HTTP 401", r["erro"])
        self.assertEqual(len(PEDIDOS), 1, "401 nao se resolve a retirar um campo")

    def test_forma_distingue_json_de_texto(self):
        json_limpo = {"choices": [{"message": {"content": '{"nivel": "media"}'}}]}
        texto = {"choices": [{"message": {"content": "acho que e media"}}]}
        vazio = {"choices": [{"message": {"content": ""}}]}
        self.assertEqual(am.forma(json_limpo), "json")
        self.assertEqual(am.forma(texto), "texto")
        self.assertEqual(am.forma(vazio), "raciocinio")


class TestLote(Base):
    """Classificacao em lote: um pedido, N niveis, alinhados ao que se pediu."""

    def test_lote_devolve_um_nivel_por_pedido(self):
        RESPOSTA.update({"codigo": 200, "json": {"choices": [{"message": {"content": json.dumps(
            {"niveis": [{"i": 1, "nivel": "leve"}, {"i": 2, "nivel": "media"},
                        {"i": 3, "nivel": "alta"}]})}}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 90}}})
        r = am.classificar_lote(["ola", "explica o nginx", "desenha a arquitetura"],
                                self.definicao(), lambda p, m: self.rota())
        self.assertEqual(r["niveis"], ["leve", "media", "alta"])
        self.assertEqual(r["erro"], "")
        self.assertEqual(len(PEDIDOS), 1, "o lote tem de ser um so pedido")
        corpo = PEDIDOS[0]["corpo"]
        self.assertIn("1. ola", corpo["messages"][1]["content"])
        self.assertIn("3. desenha a arquitetura", corpo["messages"][1]["content"])
        self.assertIn("json", corpo["messages"][0]["content"].lower())

    def test_lote_alinha_o_que_faltar_com_none(self):
        RESPOSTA.update({"json": {"choices": [{"message": {"content": json.dumps(
            {"niveis": [{"i": 1, "nivel": "leve"}, {"i": 3, "nivel": "alta"}]})}}]}})
        r = am.classificar_lote(["a", "b", "c"], self.definicao(), lambda p, m: self.rota())
        self.assertEqual(r["niveis"], ["leve", None, "alta"])

    def test_ler_lote_aceita_as_tres_formas(self):
        objetos = {"choices": [{"message": {"content": json.dumps(
            {"niveis": [{"i": 1, "nivel": "leve"}, {"nivel": "alta"}]})}}]}
        self.assertEqual(am._ler_lote(objetos, 2), ["leve", "alta"])
        lista = {"choices": [{"message": {"content": json.dumps(
            {"niveis": ["leve", "media"]})}}]}
        self.assertEqual(am._ler_lote(lista, 2), ["leve", "media"])
        mapa = {"choices": [{"message": {"content": json.dumps(
            {"1": "leve", "2": "média"})}}]}
        self.assertEqual(am._ler_lote(mapa, 2), ["leve", "media"])
        self.assertEqual(am._ler_lote({"choices": []}, 2), [None, None])
        self.assertEqual(am._ler_lote({"choices": [{"message": {"content": "sem json"}}]}, 2),
                         [None, None])

    def test_lote_sem_classificador(self):
        r = am.classificar_lote(["a"], None, lambda p, m: self.rota())
        self.assertEqual(r["niveis"], [None])
        self.assertEqual(PEDIDOS, [])

    # -- config errada nao e avaria ----------------------------------------
    def test_provider_inexistente_e_sem_efeito_e_nao_falhou(self):
        """`AuthError` do core -> `sem_efeito`, suspensao longa, zero `falhou`.

        Medido a 2026-11-03: o perfil `pessoal` herdava um classificador global
        com o provider `inception`, que so existe em `default` e `trabalho`.
        Cada pedido escrevia um `AuthError` no cartao (62 eventos em 82 s) e o
        utilizador via um ⚠ de avaria onde havia config errada.
        """
        def resolver_mau(provider, modelo):
            raise RuntimeError("AuthError: Unknown provider 'inception'. Check 'hermes model'")
        am.limpar_cache()
        d = dict(self.definicao(provider="inception", modelo="mercury-3"))
        r = am.classificar([{"role": "user", "content": "ola"}], d, resolver_mau, perfil="pessoal")
        self.assertIsNotNone(r)
        self.assertEqual(r["sem_efeito"], "provider_indisponivel")
        self.assertNotIn("erro", r)
        self.assertFalse((r.get("classificador") or {}).get("falhou"))
        susp = am.estado()["alvos_suspensos"]
        alvo = "pessoal\x1finception\x1fmercury-3"
        self.assertIn(alvo, susp)
        self.assertGreater(susp[alvo], 3500.0)   # ~1 h, nao os 300 s de avaria
        self.assertEqual(am.estado()["falhas"], {})

    def test_credencial_ausente_tambem_e_sem_efeito(self):
        def resolver_mau(provider, modelo):
            raise RuntimeError("no credential for provider 'x'")
        am.limpar_cache()
        r = am.classificar([{"role": "user", "content": "ola"}],
                           dict(self.definicao(provider="inception", modelo="mercury-3")),
                           resolver_mau, perfil="trabalho")
        self.assertEqual(r["sem_efeito"], "credencial_ausente")
        self.assertNotIn("erro", r)

    def test_avaria_transitoria_continua_a_ser_falha(self):
        """Um 5xx nao e config: continua a contar para a suspensao de 300 s."""
        def resolver_mau(provider, modelo):
            raise RuntimeError("connection reset by peer")
        am.limpar_cache()
        r = am.classificar([{"role": "user", "content": "ola"}],
                           dict(self.definicao(provider="inception", modelo="mercury-3")),
                           resolver_mau, perfil="pessoal")
        self.assertTrue(r.get("erro"))
        self.assertNotIn("sem_efeito", r)
        self.assertIn("pessoal\x1finception\x1fmercury-3", am.estado()["falhas"])

    def test_timeout_suspende_a_primeira(self):
        """Um timeout suspende ao primeiro, nao ao terceiro.

        O timeout e a falha mais cara: custa o `timeout_s` inteiro em latencia do
        turno. Medido a 2026-11-03 com o timeout em 8 s, o perfil `pessoal` levou
        quatro falhas em 45 s porque o disjuntor so abria a terceira — tres
        esperas para ouvir o que a primeira ja tinha dito. A heuristica responde
        em 0 ms, logo uma espera basta.
        """
        RESPOSTA["atraso"] = 2.5
        self.addCleanup(RESPOSTA.__setitem__, "atraso", 0.0)
        alvo = "pessoal\x1fdeepseek\x1fdeepseek-flash"
        r = am.classificar(self.mensagens(),
                           self.definicao(provider="deepseek", modelo="deepseek-flash",
                                          timeout_s=1.0),
                           lambda p, m: self.rota(), perfil="pessoal")
        self.assertIsNone(r.get("nivel"))
        self.assertIn("erro", r)
        suspensos = am.estado()["alvos_suspensos"]
        self.assertIn(alvo, suspensos, f"esperava {alvo} suspenso: {suspensos}")
        self.assertGreater(suspensos[alvo], 200.0, suspensos)
        # E a chamada seguinte nao paga nada: nem rede, nem espera.
        inicio = time.monotonic()
        r2 = am.classificar([{"role": "user", "content": "outro pedido"}],
                            self.definicao(provider="deepseek", modelo="deepseek-flash",
                                           timeout_s=1.0),
                            lambda p, m: self.rota(), perfil="pessoal")
        self.assertLess(time.monotonic() - inicio, 0.2, "o suspenso tem de ser imediato")
        self.assertIsNone(r2.get("nivel"))
        self.assertEqual(r2["sem_efeito"], "classificador_suspenso")

    def test_timeout_padrao_tem_margem_sobre_o_medido(self):
        """O default tem de cobrir o p95 medido sem inflacionar a espera do turno.

        Medido a 2026-11-03 no deepseek-flash: p50 1,5-2,9 s, 5,3 s sob contencao.
        Abaixo de 10 s volta a falhar em pico; acima de 15 s e espera paga pelo
        utilizador por uma decisao opcional (o classificador corre antes do
        pedido do turno).
        """
        self.assertGreaterEqual(am.PADRAO["timeout_s"], 10.0, am.PADRAO["timeout_s"])
        self.assertLessEqual(am.PADRAO["timeout_s"], 15.0, am.PADRAO["timeout_s"])


class TestTransporteDecisoes(Base):
    """`via: decisoes` — modelo System One, escolha tipada com confianca.

    O que se prova: o endpoint e derivado do `base_url` (o OpenRouter serve as
    decisoes sem o `/v1`), o corpo nao tem nada do chat, a escolha e a confianca
    sao lidas, o piso de confianca abstem em vez de adivinhar, e um id de modelo
    inexistente e tratado como config e nao como avaria.
    """

    def resposta(self, choice: str = "alta", conf: Any = 0.93) -> dict:
        return {"answers": {"nivel": {"type": "choice", "choice": choice,
                                      "confidence": conf,
                                      "probabilities": {"leve": 0.02, "media": 0.05,
                                                        "alta": 0.93}}},
                "usage": {"input_tokens": 400, "output_tokens": 38, "cost": 1.7346e-05},
                "model": "typesafe/jev-1.14-20261014", "provider": "TypeSafe"}

    def test_url_deriva_do_base_url(self):
        """A base do chat leva a rota de decisoes; a base ja certa nao duplica."""
        self.assertEqual(am._url_decisoes("https://openrouter.ai/api/v1"),
                         "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(am._url_decisoes("https://openrouter.ai/api/v1/"),
                         "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(am._url_decisoes("https://openrouter.ai/api"),
                         "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(am._url_decisoes("https://openrouter.ai/api/alpha/decisions"),
                         "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(am._url_decisoes("https://gateway.local/v1/chat/completions"),
                         "https://gateway.local/alpha/decisions")
        with self.assertRaises(ValueError):
            am._url_decisoes("")

    def test_via_normalizada(self):
        for bruto, esperado in (("decisoes", "decisoes"), ("decisions", "decisoes"),
                                ("Decisões", "decisoes"), ("chat", "chat"),
                                ("auto", "auto"), ("", "auto")):
            d, erros = am.normalizar({"via": bruto})
            self.assertEqual(d["via"], esperado, bruto)
            self.assertEqual(erros, [], bruto)
        # Sem `via` declarada, o transporte e `auto`: apontar o classificador a
        # um modelo que o plugin nunca viu nao deve exigir saber o tipo dele.
        self.assertEqual(am.normalizar({})[0]["via"], "auto")
        d, erros = am.normalizar({"via": "telepatia"})
        self.assertEqual(d["via"], "chat")
        self.assertTrue(any("via" in e for e in erros), erros)

    def test_confianca_min_limites(self):
        self.assertEqual(am.normalizar({})[0]["confianca_min"], 0.0)
        self.assertEqual(am.normalizar({"confianca_min": 0.75})[0]["confianca_min"], 0.75)
        self.assertEqual(am.normalizar({"confianca_min": 5})[0]["confianca_min"], 1.0)
        self.assertEqual(am.normalizar({"confianca_min": -1})[0]["confianca_min"], 0.0)

    def test_corpo_e_endpoint(self):
        RESPOSTA.update({"json": self.resposta(), "codigo": 200, "atraso": 0.0})
        r = am.classificar(self.mensagens("troca o certificado do proxy"), self.definicao(
            modelo="~typesafe/jev-latest", via="decisoes"), lambda p, m: self.rota())
        self.assertEqual(r["nivel"], "alta")
        self.assertEqual(r["origem"], "modelo")
        enviado = PEDIDOS[0]
        self.assertEqual(enviado["caminho"], "/api/alpha/decisions")
        self.assertEqual(enviado["corpo"]["model"], "~typesafe/jev-latest")
        self.assertIn("troca o certificado", enviado["corpo"]["state"])
        perguntas = enviado["corpo"]["questions"]
        self.assertEqual(perguntas["nivel"]["type"], "choice")
        # O criteria e um mapa opcao -> descricao. Uma lista e recusada pelo
        # endpoint (medido), logo o teste guarda a forma.
        self.assertIsInstance(perguntas["nivel"]["criteria"], dict)
        self.assertEqual(sorted(perguntas["nivel"]["criteria"]), ["alta", "leve", "media"])
        # Nada do contrato do chat pode ir no corpo.
        for proibido in ("messages", "max_tokens", "response_format", "reasoning_effort",
                         "temperature"):
            self.assertNotIn(proibido, enviado["corpo"])

    def test_le_confianca_e_custo(self):
        RESPOSTA.update({"json": self.resposta("media", 0.71), "codigo": 200})
        r = am.classificar(self.mensagens(), self.definicao(modelo="~typesafe/jev-latest",
                                                            via="decisoes"),
                           lambda p, m: self.rota())
        c = r["classificador"]
        self.assertEqual(r["nivel"], "media")
        self.assertEqual(c["via"], "decisoes")
        self.assertEqual(c["confianca"], 0.71)
        self.assertEqual(c["formato_pedido"], "decisoes")
        self.assertEqual(c["modelo_resolvido"], "typesafe/jev-1.14-20261014")
        self.assertAlmostEqual(c["custo_usd"], 1.7346e-05)
        self.assertEqual(c["tentativas"], 1)
        self.assertEqual(sorted(c["probabilidades"]), ["alta", "leve", "media"])

    def test_escolha_em_texto_directo(self):
        """Um proxy pode devolver `{"nivel": "alta"}` em vez do objecto tipado."""
        nivel, lido, conf, probs = am._extrair_decisao({"answers": {"nivel": "alta"}})
        self.assertEqual((nivel, lido), ("alta", "alta"))
        self.assertIsNone(conf)
        self.assertEqual(probs, {})
        for lixo in ({}, {"answers": {}}, {"answers": {"nivel": {"type": "choice"}}},
                     {"answers": {"nivel": {"choice": "enorme"}}}):
            with self.assertRaises(ValueError):
                am._extrair_decisao(lixo)

    def test_piso_de_confianca_abstem(self):
        """Abaixo do piso o turno nao e adivinhado: `sem_efeito` + heuristica."""
        RESPOSTA.update({"json": self.resposta("media", 0.49), "codigo": 200})
        r = am.classificar(self.mensagens(), self.definicao(
            modelo="~typesafe/jev-latest", via="decisoes", confianca_min=0.7),
            lambda p, m: self.rota())
        self.assertIsNone(r.get("nivel"))
        self.assertEqual(r["sem_efeito"], "confianca_baixa")
        self.assertEqual(r["origem"], "modelo_abstencao")
        self.assertIn("0.49", r["motivo"])
        self.assertEqual(r["classificador"]["abstencao"], "confianca_baixa")
        # Abstenção nao e falha: nada de disjuntor nem de cache negativa.
        self.assertNotIn("falhou", r)
        self.assertEqual(am._falhas.get(""), None)

    def test_piso_zero_aceita_tudo(self):
        RESPOSTA.update({"json": self.resposta("leve", 0.34), "codigo": 200})
        r = am.classificar(self.mensagens(), self.definicao(modelo="~typesafe/jev-latest",
                                                           via="decisoes"),
                           lambda p, m: self.rota())
        self.assertEqual(r["nivel"], "leve")

    def test_modelo_invalido_e_config_nao_avaria(self):
        """400 "not a valid model ID" suspende 1 h e nao conta como falha."""
        RESPOSTA.update({"codigo": 400, "json": {"error": {
            "message": "typesafe/jev-latest is not a valid model ID", "code": 400}}})
        r = am.classificar(self.mensagens("a"), self.definicao(
            modelo="typesafe/jev-latest", via="decisoes"), lambda p, m: self.rota())
        self.assertIsNone(r.get("nivel"))
        self.assertEqual(r["sem_efeito"], "modelo_indisponivel")
        self.assertIn("not a valid model ID", r["motivo"])
        # Suspensao de uma hora: a segunda chamada nao chega a bater no servidor,
        # mas tem de dizer que esta suspensa — o silencio fazia o cartao mostrar
        # "heuristica" sem causa e a correcao da config parecer sem efeito.
        antes = len(PEDIDOS)
        r2 = am.classificar(self.mensagens("b"), self.definicao(
            modelo="typesafe/jev-latest", via="decisoes"), lambda p, m: self.rota())
        self.assertIsNone(r2.get("nivel"))
        self.assertEqual(r2["sem_efeito"], "classificador_suspenso")
        self.assertIn("suspenso", r2["motivo"])
        self.assertTrue(r2["classificador"]["suspenso"])
        self.assertGreater(r2["classificador"]["restante_s"], 3000)
        self.assertEqual(len(PEDIDOS), antes, "suspenso nao volta a chamar")

    def test_corrigir_a_config_levanta_a_suspensao(self):
        """Suspensao por config errada nao sobrevive a correcao da config."""
        RESPOSTA.update({"codigo": 400, "json": {"error": {
            "message": "~typesafe/jev-latest is a decisions model and cannot be used "
                       "with the chat/completions endpoint.", "code": 400}}})
        r = am.classificar(self.mensagens("a"), self.definicao(
            modelo="~typesafe/jev-latest", via="chat"), lambda p, m: self.rota())
        self.assertEqual(r["sem_efeito"], "modelo_indisponivel")
        # A mesma definicao volta a bater no cartao: suspensa.
        antes = len(PEDIDOS)
        r2 = am.classificar(self.mensagens("b"), self.definicao(
            modelo="~typesafe/jev-latest", via="chat"), lambda p, m: self.rota())
        self.assertEqual(r2["sem_efeito"], "classificador_suspenso")
        self.assertEqual(len(PEDIDOS), antes)
        # Corrigir `via` tem de voltar a tentar ja, sem esperar a hora.
        RESPOSTA.update({"codigo": 200, "json": self.resposta("alta", 0.9)})
        r3 = am.classificar(self.mensagens("c"), self.definicao(
            modelo="~typesafe/jev-latest", via="decisoes"), lambda p, m: self.rota())
        self.assertEqual(r3["nivel"], "alta", "a config corrigida nao pode ficar suspensa")
        self.assertEqual(len(PEDIDOS), antes + 1)

    def test_transporte_errado_diz_qual_e_o_erro(self):
        """O id certo no endpoint do chat devolve 'is a decisions model'."""
        RESPOSTA.update({"codigo": 400, "json": {"error": {
            "message": "~typesafe/jev-latest is a decisions model and cannot be used "
                       "with the chat/completions endpoint. Use /api/alpha/decisions.",
            "code": 400}}})
        r = am.classificar(self.mensagens("a"), self.definicao(
            modelo="~typesafe/jev-latest", via="chat"), lambda p, m: self.rota())
        self.assertEqual(r["sem_efeito"], "modelo_indisponivel")

    def test_lote_decisoes_um_pedido_por_caso(self):
        """Sem cache: um lote em cache media o cache, nao o modelo."""
        RESPOSTA.update({"json": self.resposta("alta", 0.9), "codigo": 200})
        r = am.classificar_lote(["a", "b", "c"], self.definicao(
            modelo="~typesafe/jev-latest", via="decisoes"), lambda p, m: self.rota())
        self.assertEqual(r["niveis"], ["alta", "alta", "alta"])
        self.assertEqual(r["via"], "decisoes")
        self.assertEqual(len(PEDIDOS), 3)
        self.assertEqual(r["pedidos"], 3)
        self.assertAlmostEqual(r["custo_usd"], round(1.7346e-05 * 3, 8))

    def test_dashboard_le_a_confianca_do_evento(self):
        """A confianca tem de chegar ao cartao, nao ficar no ledger."""
        origem = (AQUI / "dashboard" / "api.py").read_text(encoding="utf-8")
        trecho = origem[origem.index('reg["classificador"] = {'):]
        trecho = trecho[:trecho.index("}") + 1]
        for campo in ("\"via\"", "\"confianca\"", "\"custo_usd\""):
            self.assertIn(campo, trecho)


class TestInstanciaPartilhada(unittest.TestCase):
    """O `gestor` valida com o `analise_modelo` que o plugin tem em memoria.

    Medido a 2026-11-03: o plugin recarrega `analise_modelo.py` a quente, mas o
    `gestor` guardava a instancia do arranque na sua propria cache. A chave nova
    `via` era apagada pelo `normalizar` antigo ao carregar a config e o
    transporte de decisoes nunca chegou a ser usado — sem erro nenhum, porque
    uma chave desconhecida nao e um erro, e simplesmente descartada.
    """

    def test_instancia_do_pacote_ao_vivo_vence_a_do_arranque(self):
        import sys as _sys
        import types as _types

        marcador = _types.ModuleType("fakepkg.analise_modelo")
        marcador.__file__ = "/tmp/fakepkg/analise_modelo.py"
        antigo_pkg = gestor.__package__
        antigo_am, antigo_mt = gestor._AM, gestor._AM_MTIME
        try:
            gestor.__package__ = "fakepkg"
            _sys.modules["fakepkg.analise_modelo"] = marcador
            gestor._AM, gestor._AM_MTIME = am, 0  # instancia do arranque
            self.assertIs(gestor._analise_modelo(), marcador)
        finally:
            _sys.modules.pop("fakepkg.analise_modelo", None)
            gestor.__package__ = antigo_pkg
            gestor._AM, gestor._AM_MTIME = antigo_am, antigo_mt

    def test_validar_preserva_via_e_confianca_min(self):
        import copy

        bruto = copy.deepcopy(gestor.SEED)
        bruto["perfis"].setdefault("default", {})
        bruto["perfis"]["default"]["analise"] = {"classificador": {
            "activo": True, "provider": "openrouter",
            "modelo": "~typesafe/jev-latest",
            "via": "decisoes", "confianca_min": 0.7}}
        limpo, erros = gestor.validar(bruto)
        self.assertEqual([e for e in erros if "catalogo" not in e], [])
        d = limpo["perfis"]["default"]["analise"]["classificador"]
        self.assertEqual(d["via"], "decisoes")
        self.assertAlmostEqual(d["confianca_min"], 0.7)

    def test_validar_recusa_via_desconhecido(self):
        import copy

        bruto = copy.deepcopy(gestor.SEED)
        bruto["perfis"].setdefault("default", {})
        bruto["perfis"]["default"]["analise"] = {"classificador": {
            "activo": True, "provider": "openrouter",
            "modelo": "~typesafe/jev-latest", "via": "telepatia"}}
        _limpo, erros = gestor.validar(bruto)
        self.assertTrue(any("via" in e.lower() or "telepatia" in e.lower() for e in erros),
                        f"esperava erro de via, veio {erros}")


class TestAdaptacaoAModelos(Base):
    """O plugin aceita qualquer modelo: preset, campos omitidos, caminhos e `auto`.

    O que se prova aqui e o que o utilizador pediu: apontar o classificador a um
    modelo que este plugin nunca viu — um LLM comum, um modelo de raciocinio ou
    um modelo System One — sem escrever configuracao especifica do provider.
    """

    def test_preset_inferido_do_id(self):
        self.assertEqual(am.preset_para("~typesafe/jev-latest"), "sistema-um")
        self.assertEqual(am.preset_para("openrouter/~typesafe/jev-latest"), "sistema-um")
        self.assertEqual(am.preset_para("o3-mini"), "raciocinio")
        self.assertEqual(am.preset_para("gpt-5.2"), "raciocinio")
        self.assertEqual(am.preset_para("deepseek/deepseek-flash"), "padrao")
        self.assertEqual(am.preset_para(""), "padrao")

    def test_preset_entra_por_baixo(self):
        d, erros = am.normalizar({"modelo": "~typesafe/jev-latest"})
        self.assertEqual(erros, [])
        self.assertEqual(d["preset"], "sistema-um")
        self.assertEqual(d["via"], "decisoes")
        d, _ = am.normalizar({"modelo": "o3"})
        self.assertEqual(d["preset"], "raciocinio")
        self.assertIsNone(d["temperatura"])
        self.assertEqual(d["max_tokens_campo"], "max_completion_tokens")

    def test_o_declarado_ganha_ao_preset(self):
        d, erros = am.normalizar({"modelo": "o3", "preset": "padrao", "temperatura": 0.3})
        self.assertEqual(erros, [])
        self.assertEqual(d["preset"], "padrao")
        self.assertEqual(d["temperatura"], 0.3)
        self.assertEqual(d["max_tokens_campo"], "max_tokens")
        # Preset inferido + valor declarado: o declarado ganha.
        d, _ = am.normalizar({"modelo": "o3", "temperatura": 0.4})
        self.assertEqual(d["temperatura"], 0.4)

    def test_preset_nenhum_desliga_a_inferencia(self):
        d, _ = am.normalizar({"modelo": "~typesafe/jev-latest", "preset": "nenhum"})
        self.assertEqual(d["preset"], "")
        self.assertEqual(d["via"], "auto")

    def test_temperatura_omitida(self):
        for valor in ("omitir", "OMITIR", None, "-", ""):
            d, erros = am.normalizar({"temperatura": valor})
            self.assertIsNone(d["temperatura"], valor)
            self.assertEqual(erros, [], valor)
        self.assertEqual(am.normalizar({"temperatura": 0.7})[0]["temperatura"], 0.7)
        self.assertEqual(am.normalizar({"temperatura": 9})[0]["temperatura"], 2.0)

    def test_recusa_valores_invalidos(self):
        for chave, valor in (("max_tokens_campo", "tokens"), ("caminho", "sem-barra"),
                             ("caminho_decisoes", "sem-barra"),
                             ("parametros_extra", "nao-e-objecto"),
                             ("headers_extra", [1, 2]), ("preset", "inexistente")):
            _, erros = am.normalizar({chave: valor})
            self.assertTrue(any(chave in e for e in erros), f"{chave}={valor!r}: {erros}")

    def test_corpo_sem_temperatura_e_com_max_completion_tokens(self):
        d = self.definicao(temperatura=None, max_tokens_campo="max_completion_tokens")
        corpo = am._corpo("openrouter", "gpt-5", "pedido", d)
        self.assertNotIn("temperature", corpo)
        self.assertNotIn("top_p", corpo)
        self.assertIn("max_completion_tokens", corpo)
        self.assertNotIn("max_tokens", corpo)
        # E com temperatura declarada, os campos la estao.
        corpo = am._corpo("openrouter", "llm-comum", "pedido", self.definicao())
        self.assertEqual(corpo["temperature"], 0.0)
        self.assertEqual(corpo["top_p"], 1)

    def test_parametros_extra_chegam_ao_corpo(self):
        d = self.definicao(parametros_extra={"verbosity": "low", "model": "outro",
                                             "messages": [{"role": "user", "content": "x"}]})
        corpo = am._corpo("openrouter", "x/y", "pedido", d)
        self.assertEqual(corpo["verbosity"], "low")
        # `model` e `messages` nao se trocam por configuracao: seria perder o
        # rasto do que foi mesmo pedido.
        self.assertEqual(corpo["model"], "x/y")
        self.assertEqual(corpo["messages"][0]["role"], "system")

    def test_headers_extra_chegam_ao_pedido(self):
        d = self.definicao(headers_extra={"HTTP-Referer": "https://vipernet.local"})
        am.classificar(self.mensagens("pedido com cabecalho proprio"), d, lambda p, m: self.rota())
        self.assertEqual(PEDIDOS[-1]["headers"].get("http-referer"), "https://vipernet.local")
        self.assertEqual(PEDIDOS[-1]["auth"], "Bearer chave-de-teste")

    def test_caminho_override(self):
        d = self.definicao(caminho="/api/v9/classificar")
        am.classificar(self.mensagens("pedido com caminho proprio"), d, lambda p, m: self.rota())
        self.assertTrue(PEDIDOS[-1]["caminho"].endswith("/api/v9/classificar"),
                        PEDIDOS[-1]["caminho"])

    def test_url_derivada_sem_caminho(self):
        # Sem override, a derivacao de sempre mantem-se.
        self.assertEqual(am._url("https://x/api/v1"), "https://x/api/v1/chat/completions")
        self.assertEqual(am._url("https://x/api/v1", {"caminho": "/v2/chat"}),
                         "https://x/v2/chat")
        self.assertEqual(am._url_decisoes("https://x/api/v1", {"caminho_decisoes": "/d"}),
                         "https://x/d")

    def test_auto_muda_para_decisoes_e_memoriza(self):
        d = self.definicao(via="auto", confianca_min=0.0)
        FILA.extend([
            # 1. o chat recusa porque o modelo e de decisoes
            {"conteudo": "", "codigo": 400, "atraso": 0.0,
             "json": {"error": {"message": "x is a decisions model and cannot be used with "
                                           "the chat/completions endpoint. Use the "
                                           "/api/alpha/decisions endpoint instead."}}},
            # 2. o mesmo pedido no endpoint de decisoes
            {"conteudo": "", "codigo": 200, "atraso": 0.0,
             "json": {"answers": {"nivel": {"choice": "alta", "confidence": 1.0,
                                            "probabilities": {"alta": 1.0}}},
                      "usage": {"cost": 1e-05}, "model": "x/novo"}},
        ])
        r = am.classificar(self.mensagens("pedido que so um modelo de decisoes entende"),
                           d, lambda p, m: self.rota(), perfil="auto-teste")
        self.assertEqual(r["nivel"], "alta")
        self.assertEqual(r["classificador"]["via"], "decisoes")
        caminhos = [p["caminho"] for p in PEDIDOS]
        self.assertTrue(caminhos[0].endswith("/chat/completions"), caminhos)
        self.assertTrue(caminhos[1].endswith("/alpha/decisions"), caminhos)
        # A descoberta ficou memorizada: a chamada seguinte vai directo ao
        # endpoint certo, sem pagar a sonda outra vez.
        FILA.append({"conteudo": "", "codigo": 200, "atraso": 0.0,
                     "json": {"answers": {"nivel": {"choice": "alta", "confidence": 1.0,
                                                    "probabilities": {"alta": 1.0}}},
                              "usage": {"cost": 1e-05}, "model": "x/novo"}})
        r2 = am.classificar(self.mensagens("outro pedido depois da descoberta"),
                            d, lambda p, m: self.rota(), perfil="auto-teste")
        self.assertEqual(r2["nivel"], "alta")
        self.assertEqual(len(PEDIDOS), 3)
        self.assertTrue(PEDIDOS[-1]["caminho"].endswith("/alpha/decisions"),
                        PEDIDOS[-1]["caminho"])

    def test_auto_com_modelo_de_chat_nao_muda(self):
        d = self.definicao(via="auto")
        r = am.classificar(self.mensagens("pedido normal num llm de chat"),
                           d, lambda p, m: self.rota(), perfil="auto-chat")
        self.assertEqual(r["nivel"], "alta")
        self.assertEqual(r["classificador"]["via"], "chat")
        self.assertEqual(len(PEDIDOS), 1)
        self.assertTrue(PEDIDOS[0]["caminho"].endswith("/chat/completions"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
