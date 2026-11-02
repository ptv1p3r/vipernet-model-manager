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
                        "ua": self.headers.get("User-Agent"), "caminho": self.path})
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
        self.assertEqual(d["timeout_s"], 30.0)
        self.assertEqual(d["max_tokens"], am._MAX_TOKENS_MAX)  # tecto duro, nao um literal
        self.assertEqual(d["cache_ttl_s"], 0)
        self.assertEqual(d["max_chars"], 200)
        d2, _ = am.normalizar({"timeout_s": 0.1})
        self.assertEqual(d2["timeout_s"], 1.0)

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
        def falha(_p, _m):
            raise RuntimeError("provider sem credencial no perfil")
        r = am.classificar(self.mensagens(), self.definicao(), falha)
        self.assertIsNone(r.get("nivel"))
        self.assertIn("sem credencial", r["erro"])

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
        self.assertIsNone(r)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
