"""Ensaio em sombra ligado ao middleware (3.38.0).

O defeito que este ficheiro fecha: `sombra.braco_do_turno` existia, sorteava por
turno com a semente selada pelo pre-registo e **nao era chamado em lado nenhum**
(as quatro ocorrencias eram todas em `test_sombra.py`). O middleware so derivava
o braco do modelo que tinha servido, e como nada forcava o modelo do braco, o
braco b nunca era servido: um "A/B" em que metade das celulas ficava vazia.

O que fica provado, cada coisa com o seu caso negativo:

1. O braco sorteado **manda no modelo**: braco b serve o barato, braco a serve o
   caro, em ambos os casos contra a rota normal (que escolheria o barato).
2. O braco que aplica fica na linha do turno (`origem: sombra`) e o `braco` da
   linha continua a ser derivado do modelo que respondeu — facto, nao intencao.
3. Um braco **morto** (slug retirado) para o ensaio inteiro: nao se corre um A/B
   com um modelo contra nada, e o turno segue a rota normal.
4. Vida nunca medida, vida expirada e pre-registo adulterado tambem nao aplicam —
   medir sobre um desenho que ninguem assinou nao vale.
5. As prioridades do dono ganham: tarefa associada e marcador explicito.
6. Nivel nao coberto e modelo fora do catalogo recusam com o motivo certo.
7. A sonda de vida distingue 404 de vivo, e nao imprime credenciais.

Uso: python3 test_sombra_middleware.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("vmm_sombra_mw_test", ROOT / "__init__.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

sombra = p._sombra_mod

CARO = "deepseek-flash"
BARATO = "nvidia/nemotron-3-ultra-550b-a55b:free"
PERFIL = "trabalho"
SESSAO = "sombra-mw"


def config(*, activo: bool = True, complexidade_barato=None) -> dict:
    """Catalogo minimo: a rota normal escolhe o **barato** (ordem 1).

    Assim, quando o braco a serve o caro, a troca so pode vir do sorteio — sem
    isso um teste passaria por acidente.
    """
    return {
        "activo": True,
        "sombra": {"activo": activo, "vida_ttl_h": 24},
        "perfis": {PERFIL: {
            "activo": True, "estrategia": "prioridade",
            "catalogo": [
                {"id": "barato", "provider": "openrouter", "modelo": BARATO,
                 "complexidade": complexidade_barato or ["leve", "media", "alta"],
                 "custo": 0.0, "ordem": 1},
                {"id": "caro", "provider": "deepseek", "modelo": CARO,
                 "complexidade": ["leve", "media", "alta"],
                 "custo": 0.3, "ordem": 2},
            ],
        }},
    }


class Agente:
    provider = "deepseek"
    model = CARO
    platform = "api_server"

    def switch_model(self, **kw):
        self.provider, self.model = kw["new_provider"], kw["new_model"]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vmm-sombra-"))
        (self.dir / "aprendiz" / "sombra").mkdir(parents=True)
        self.registo = sombra.desenhar(
            caro=CARO, barato=BARATO, p0=0.0463, margem=0.01, deff=3.08,
            chamadas_por_dia=16194)
        sombra.escrever(self.registo, self.dir / "aprendiz" / "sombra"
                        / "pre-registo-teste.json")
        self.cfg = config()
        self.agent = Agente()
        self.events: list[dict] = []
        p._ensaio_cache.clear()
        p._sombra_vida_cache.clear()
        p._historico.clear()
        p._contadores.clear()
        p._turno.clear()
        p._tarefa_sessao.clear()
        # O dedupe do rasto e por chave de turno e sobrevive entre testes: sem
        # isto, a segunda metade da bateria perde o rasto na linha `resposta` e
        # o teste falha por estado de outro teste, nao por defeito do codigo.
        p._trace_resposta.clear()
        p._ultima_decisao.clear()
        p._avisados.clear()
        self.patches = [
            patch.object(p, "_config",
                         side_effect=lambda: p.gestor.validar(self.cfg)[0]),
            patch.object(p.gestor, "directorio", return_value=self.dir),
            patch.object(p.gestor, "perfil_atual", return_value=PERFIL),
            patch.object(p, "agente_vivo", return_value=self.agent),
            patch.object(p, "_registar",
                         side_effect=lambda e, **kw: self.events.append(e)),
            patch.object(p, "resolver_rota",
                         side_effect=lambda perfil, provider, modelo: {
                             "provider": provider, "api_key": "",
                             "base_url": "", "api_mode": ""}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(p._ensaio_cache.clear)
        self.addCleanup(p._sombra_vida_cache.clear)

    # -- ferramentas ---------------------------------------------------
    def escrever_vida(self, *, ok_a=True, ok_b=True, horas_atras=0.0,
                      motivo_b="") -> None:
        instante = time.time() - horas_atras * 3600
        dados = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(instante)),
            "ts_epoch": instante,
            "sha_registo": self.registo["sha256"],
            "ensaio": self.registo["ensaio"],
            "braco_a": {"modelo": CARO, "ok": ok_a, "http": 200 if ok_a else 404,
                        "motivo": ""},
            "braco_b": {"modelo": BARATO, "ok": ok_b, "http": 200 if ok_b else 404,
                        "motivo": motivo_b},
            "todos_vivos": ok_a and ok_b,
        }
        destino = self.dir / "aprendiz" / "sombra" / "vida.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(dados), encoding="utf-8")

    def turno_do_braco(self, braco: str, sessao: str = SESSAO) -> str:
        for i in range(2000):
            tid = f"t{i}"
            if sombra.braco_do_turno(self.registo, PERFIL, sessao, tid) == braco:
                return tid
        raise AssertionError(f"nenhum turno caiu no braco {braco}")

    def run_turn(self, tid: str, *, texto: str = "Diz apenas bom dia.",
                 sessao: str = SESSAO) -> dict:
        p.middleware_llm_request(
            request={"model": self.agent.model,
                     "messages": [{"role": "user", "content": texto}]},
            session_id=sessao, provider=self.agent.provider, turn_id=tid,
            api_call_count=1, platform="api_server")
        p.pos_resposta(session_id=sessao, model=self.agent.model,
                       provider=self.agent.provider,
                       response_model=self.agent.model, finish_reason="stop",
                       usage={"total_tokens": 10}, platform="api_server",
                       turn_id=tid)
        return self.events[-1]

    def decidir(self, *, nivel="media", needs=None, superficie="api_server",
                associada=False, marcador=False, superficie_fixada=False,
                config=None, registo=None):
        return p._sombra_do_turno(
            p.gestor.perfil_config(config or self.cfg, PERFIL),
            config or self.cfg, PERFIL, SESSAO, self.turno_do_braco("b"),
            nivel=nivel, needs=needs, superficie=superficie,
            superficie_fixada=superficie_fixada,
            associada=associada, marcador=marcador)


class LigacaoAoMiddleware(Base):
    def test_rota_normal_escolhe_o_barato(self):
        """Sem ensaio ligado a rota e o barato: a base das provas seguintes."""
        self.cfg["sombra"]["activo"] = False
        e = self.run_turn(self.turno_do_braco("a"))
        self.assertEqual(self.agent.model, BARATO)
        self.assertNotIn("sombra", e)

    def test_braco_b_serve_o_barato_e_marca_o_turno(self):
        self.escrever_vida()
        tid = self.turno_do_braco("b")
        e = self.run_turn(tid)
        self.assertEqual(self.agent.model, BARATO)
        self.assertEqual(e["origem"], "sombra")
        self.assertEqual(e["sombra"]["braco"], "b")
        self.assertTrue(e["sombra"]["aplicado"])
        self.assertEqual(e["braco"], "b", "o braco da linha vem do modelo que serviu")

    def test_braco_a_serve_o_caro_contra_a_rota_normal(self):
        self.escrever_vida()
        tid = self.turno_do_braco("a")
        e = self.run_turn(tid)
        self.assertEqual(self.agent.model, CARO,
                         "o braco a tem de forcar o caro; a rota normal daria barato")
        self.assertEqual(e["origem"], "sombra")
        self.assertEqual(e["sombra"]["braco"], "a")
        self.assertEqual(e["braco"], "a")

    def test_braco_morto_para_o_ensaio_inteiro(self):
        self.escrever_vida(ok_b=False, motivo_b="This model is unavailable for free")
        tid = self.turno_do_braco("b")
        e = self.run_turn(tid)
        self.assertEqual(self.agent.model, BARATO, "turno segue a rota normal")
        self.assertNotEqual(e["origem"], "sombra")
        self.assertEqual(e["sombra"]["motivo"], "braco_sem_vida:b")
        self.assertFalse(e["sombra"]["aplicado"])
        self.assertIn("unavailable", e["sombra"]["mortos"]["b"])

    def test_vida_nao_medida_nao_aplica(self):
        e = self.run_turn(self.turno_do_braco("b"))
        self.assertEqual(e["sombra"]["motivo"], "vida_nao_medida")
        self.assertNotEqual(e["origem"], "sombra")

    def test_vida_expirada_nao_aplica(self):
        self.escrever_vida(horas_atras=48)
        e = self.run_turn(self.turno_do_braco("b"))
        self.assertEqual(e["sombra"]["motivo"], "vida_expirada")

    def test_pre_registo_adulterado_nao_decide(self):
        caminho = self.dir / "aprendiz" / "sombra" / "pre-registo-teste.json"
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        dados["braco_b"] = "outro/modelo:free"
        caminho.write_text(json.dumps(dados), encoding="utf-8")
        p._ensaio_cache.clear()
        self.escrever_vida()
        e = self.run_turn(self.turno_do_braco("a"))
        self.assertEqual(e["sombra"]["motivo"], "sem_pre_registo")
        self.assertNotEqual(e["origem"], "sombra")
        self.assertEqual(self.agent.model, BARATO)


class SuperficieFixadaNoMiddleware(Base):
    def test_superficie_fixada_nao_e_trocada_pelo_braco(self):
        """O sintoma do cron, no middleware: o braco b nao rouba a superficie.

        A rota normal escolheria o barato (ordem 1) e o braco b tambem e o
        barato; a regra de superficie fixa o caro. Sem a recusa, `origem` saia
        `sombra` e o caro nunca era servido.
        """
        cfg = config()
        cfg["perfis"][PERFIL]["analise"] = {
            "superficies": {"api_server": {"entrada": "caro"}}}
        self.cfg = cfg
        self.escrever_vida()
        e = self.run_turn(self.turno_do_braco("b"))
        self.assertEqual(e["origem"], "superficie")
        self.assertFalse(e["sombra"]["aplicado"])
        self.assertEqual(e["sombra"]["motivo"], "superficie_fixada")
        self.assertEqual(e["sombra"]["braco"], "b")
        self.assertEqual(self.agent.model, CARO)

    def test_sem_regra_de_superficie_o_braco_continua_a_servir(self):
        """O caso negativo: sem regra, o ensaio aplica como antes."""
        self.escrever_vida()
        e = self.run_turn(self.turno_do_braco("b"))
        self.assertEqual(e["origem"], "sombra")
        self.assertTrue(e["sombra"]["aplicado"])
        self.assertEqual(self.agent.model, BARATO)


class PrioridadesDoDono(Base):
    def test_superficie_fixada_ganha_ao_sorteio(self):
        """A regra de superficie e uma ordem: o sorteio nao a troca.

        Medido a 2026-11-08: o perfil `default` fixou o cron no mercury-3 e 42
        de 43 turnos de cron sairam no braco, com `origem: sombra`. A config
        estava certa; faltava esta recusa.
        """
        self.escrever_vida()
        diag = self.decidir(superficie_fixada=True)
        self.assertFalse(diag["aplicado"])
        self.assertEqual(diag["motivo"], "superficie_fixada")
        self.assertNotIn("entrada", diag)

    def test_superficie_fixada_ganha_a_tarefa_e_ao_marcador(self):
        """Ordem das prioridades: a superficie vem antes das outras duas."""
        self.escrever_vida()
        diag = self.decidir(superficie_fixada=True, associada=True, marcador=True)
        self.assertEqual(diag["motivo"], "superficie_fixada")

    def test_tarefa_associada_ganha_ao_sorteio(self):
        self.escrever_vida()
        diag = self.decidir(associada=True)
        self.assertFalse(diag["aplicado"])
        self.assertEqual(diag["motivo"], "tarefa_associada")

    def test_marcador_explicito_ganha_ao_sorteio(self):
        self.escrever_vida()
        diag = self.decidir(marcador=True)
        self.assertFalse(diag["aplicado"])
        self.assertEqual(diag["motivo"], "marcador_explicito")


class Recusas(Base):
    def test_nivel_nao_declarado_serve_e_fica_registado(self):
        """O sorteio e a autorizacao: um nivel fora do declarado nao trava o A/B.

        Alargar o catalogo para destravar o ensaio mudaria tambem o routing
        normal (um modelo gratuito, custo 0, ganharia os turnos); em vez disso o
        turno e servido e o facto fica no diagnostico para a analise separar.
        """
        self.escrever_vida()
        self.cfg = config(complexidade_barato=["media"])
        diag = self.decidir(nivel="alta")
        self.assertTrue(diag["aplicado"])
        self.assertTrue(diag["nivel_nao_declarado"])
        self.assertEqual(diag["braco"], "b")

    def test_modelo_fora_do_catalogo(self):
        self.escrever_vida()
        registo = sombra.desenhar(caro=CARO, barato="x/y:free", p0=0.0463,
                                  margem=0.01, deff=3.08, chamadas_por_dia=16194)
        sombra.escrever(registo, self.dir / "aprendiz" / "sombra"
                        / "pre-registo-teste.json")
        p._ensaio_cache.clear()
        # A sonda tem de valer para o selo NOVO: uma vida do selo anterior não
        # autoriza outro desenho (é o que `vida_de_outro_selo` recusa).
        self.registo = registo
        self.escrever_vida()
        diag = self.decidir()
        self.assertFalse(diag["aplicado"])
        self.assertEqual(diag["motivo"], "modelo_fora_do_catalogo")

    def test_vida_de_outro_selo_nao_autoriza_o_ensaio(self):
        """Re-selar sem nova sonda deixa o ensaio parado, não a correr.

        Os braços mudaram de modelo; a vida medida para o selo anterior diria
        «vivo» sobre o modelo errado. O turno segue a rota normal e o motivo
        diz que falta a sonda — não se inventa um veredicto.
        """
        self.escrever_vida()
        registo = sombra.desenhar(caro=CARO, barato=BARATO, p0=0.0463,
                                  margem=0.02, deff=3.08, chamadas_por_dia=16194)
        self.assertNotEqual(registo.get("sha256"), self.registo.get("sha256"))
        sombra.escrever(registo, self.dir / "aprendiz" / "sombra"
                        / "pre-registo-teste2.json")
        p._ensaio_cache.clear()
        diag = self.decidir()
        self.assertFalse(diag["aplicado"])
        self.assertEqual(diag["motivo"], "vida_de_outro_selo")

    def test_superficie_nao_serve(self):
        self.escrever_vida()
        self.cfg["perfis"][PERFIL]["catalogo"][0]["superficies"] = {"cron": False}
        diag = self.decidir(superficie="cron")
        self.assertFalse(diag["aplicado"])
        self.assertEqual(diag["motivo"], "superficie_nao_serve")

    def test_interruptor_desligado_devolve_none(self):
        self.cfg["sombra"]["activo"] = False
        self.assertIsNone(self.decidir())


class SondaDeVida(unittest.TestCase):
    def setUp(self):
        self.registo = sombra.desenhar(caro=CARO, barato=BARATO, p0=0.0463,
                                       margem=0.01, deff=3.08, chamadas_por_dia=16194)

    def test_marca_404_como_morto(self):
        def chamar(provider, modelo):
            if modelo == BARATO:
                return {"ok": False, "http": 404,
                        "motivo": "This model is unavailable for free."}
            return {"ok": True, "http": 200, "motivo": ""}

        v = sombra.sondar_vida(self.registo, chamar)
        self.assertTrue(v["braco_a"]["ok"])
        self.assertFalse(v["braco_b"]["ok"])
        self.assertEqual(v["braco_b"]["http"], 404)
        self.assertFalse(v["todos_vivos"])
        self.assertIsInstance(v["ts_epoch"], float)

    def test_dois_vivos_autoriza(self):
        v = sombra.sondar_vida(self.registo,
                               lambda p, m: {"ok": True, "http": 200, "motivo": ""})
        self.assertTrue(v["todos_vivos"])
        self.assertEqual(v["sha_registo"], self.registo["sha256"])

    def test_excepcao_nao_parte_a_sonda(self):
        def chamar(provider, modelo):
            raise TimeoutError("sem rede")

        v = sombra.sondar_vida(self.registo, chamar)
        self.assertFalse(v["braco_a"]["ok"])
        self.assertIn("TimeoutError", v["braco_a"]["motivo"])

    # --- provider declarado -------------------------------------------------

    def test_provider_declarado_chega_a_sonda(self):
        """O provider do braco viaja explicito: sem ele a sonda adivinha."""
        registo = sombra.desenhar(caro=CARO, barato=BARATO, p0=0.0463,
                                  margem=0.01, deff=3.08,
                                  chamadas_por_dia=16194,
                                  provider_caro="deepseek",
                                  provider_barato="openrouter")
        vistos = []

        def chamar(provider, modelo):
            vistos.append((provider, modelo))
            return {"ok": True, "http": 200, "motivo": ""}

        v = sombra.sondar_vida(registo, chamar)
        self.assertEqual(v["braco_a"]["provider"], "deepseek")
        self.assertEqual(v["braco_b"]["provider"], "openrouter")
        self.assertIn(("deepseek", CARO), vistos)
        self.assertIn(("openrouter", BARATO), vistos)

    def test_provider_nao_declarado_fica_vazio(self):
        """Vazio nao e um provider: e "nao declarado", e quem le sabe-o."""
        self.assertEqual(sombra.provider_do_braco(self.registo, "a"), "")
        v = sombra.sondar_vida(self.registo,
                               lambda p, m: {"ok": True, "http": 200, "motivo": ""})
        self.assertEqual(v["braco_a"]["provider"], "")

    def test_provider_entra_no_selo(self):
        """Declarar o provider muda o selo — e a sonda deixa de valer a antiga."""
        sem = sombra.desenhar(caro=CARO, barato=BARATO, p0=0.0463, margem=0.01,
                              deff=3.08, chamadas_por_dia=16194)
        com = sombra.desenhar(caro=CARO, barato=BARATO, p0=0.0463, margem=0.01,
                              deff=3.08, chamadas_por_dia=16194,
                              provider_caro="deepseek",
                              provider_barato="openrouter")
        self.assertNotEqual(sem["sha256"], com["sha256"])
        self.assertTrue(sombra.verificar(com))
        self.assertNotIn("braco_a_provider", sem)

    def test_braco_do_modelo_desempata_por_provider(self):
        """Mesmo slug em dois providers: so casa o par exacto."""
        registo = sombra.desenhar(caro="shared/model", barato=BARATO, p0=0.0463,
                                  margem=0.01, deff=3.08, chamadas_por_dia=16194,
                                  provider_caro="openrouter",
                                  provider_barato="deepseek")
        self.assertEqual(
            sombra.braco_do_modelo(registo, "shared/model", "openrouter"), "a")
        # O mesmo nome servido por outro provider nao e o braco a.
        self.assertIsNone(
            sombra.braco_do_modelo(registo, "shared/model", "deepseek"))
        # Sem provider informado mantem-se a comparacao por nome (compat).
        self.assertEqual(sombra.braco_do_modelo(registo, "shared/model"), "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
