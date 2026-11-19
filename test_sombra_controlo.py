"""Interruptor global do ensaio em sombra: `global_control`, heranca e rasto (3.47.20).

O que este ficheiro prova, cada coisa com o seu caso negativo:

1. `normalizar_sombra` aceita `global_control` com as quatro chaves e recusa o
   resto — chave desconhecida, `activo` que nao e booleano, `ts` que nao e data.
2. `activo` e `global_control.activo` a discordarem e **erro de validacao**: um
   ficheiro ambiguo nao pode decidir trafego pela ordem de leitura.
3. `sombra_ligada` le o bloco novo e cai no `activo` legado quando ele falta.
4. `sombra_efectiva` e o `_sombra_config` do middleware — as duas leituras que
   decidem, uma no CLI e outra no turno — concordam: global desligado desliga o
   perfil que herda, e um `activo` proprio ganha enquanto a heranca nao for dita.
5. `herda: true` obedece ao global mesmo com um `activo` antigo no mesmo bloco.
6. O ledger de controlo grava uma linha por gesto, com antes/depois, e le-se de
   volta; linha corrompida nao esconde as outras.

Uso: python3 test_sombra_controlo.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import gestor  # noqa: E402

spec = importlib.util.spec_from_file_location("vmm_sombra_ctl_test", ROOT / "__init__.py")
assert spec is not None and spec.loader is not None
mw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mw)

FALHAS: list[str] = []


def check(nome: str, ok: bool, detalhe: str = "") -> None:
    print(f"{'ok  ' if ok else 'FALHA'} {nome}" + (f" — {detalhe}" if detalhe and not ok else ""))
    if not ok:
        FALHAS.append(nome)


def cfg(activo: bool = True, controlo: dict | None = None,
        perfis: dict | None = None) -> dict:
    bloco: dict = {"activo": activo, "vida_ttl_h": 24}
    if controlo is not None:
        bloco["global_control"] = controlo
    return {"activo": True, "sombra": bloco, "perfis": perfis if perfis is not None else {}}


CTRL_ON = {"activo": True, "ts": "2026-11-19T01:00:00+00:00", "por": "teste", "motivo": "x"}
CTRL_OFF = {"activo": False, "ts": "2026-11-19T01:00:00+00:00", "por": "teste", "motivo": "x"}


class TestNormalizacao(unittest.TestCase):
    def test_aceita_as_quatro_chaves(self):
        saida, erros = gestor.normalizar_sombra({"global_control": CTRL_ON})
        check("normalizar: global_control completo passa", not erros, json.dumps(erros))
        check("normalizar: valor preservado",
              saida.get("global_control") == CTRL_ON, json.dumps(saida, ensure_ascii=False))

    def test_recusa_chave_desconhecida(self):
        _, erros = gestor.normalizar_sombra(
            {"global_control": {"activo": True, "autor": "eu"}})
        check("normalizar: chave desconhecida dentro de global_control e recusada",
              any("autor" in e for e in erros), json.dumps(erros, ensure_ascii=False))

    def test_recusa_activo_nao_booleano(self):
        _, erros = gestor.normalizar_sombra({"global_control": {"activo": "sim"}})
        check("normalizar: global_control.activo tem de ser booleano",
              any("booleano" in e for e in erros), json.dumps(erros, ensure_ascii=False))

    def test_recusa_ts_invalido(self):
        _, erros = gestor.normalizar_sombra(
            {"global_control": {"activo": True, "ts": "ontem"}})
        check("normalizar: global_control.ts tem de ser ISO 8601",
              any("ISO" in e for e in erros), json.dumps(erros, ensure_ascii=False))

    def test_recusa_discordancia(self):
        _, erros = gestor.normalizar_sombra({"activo": True, "global_control": CTRL_OFF})
        check("normalizar: activo e global_control.activo a discordarem e erro",
              any("discordam" in e for e in erros), json.dumps(erros, ensure_ascii=False))
        _, erros_ok = gestor.normalizar_sombra({"activo": False, "global_control": CTRL_OFF})
        check("normalizar: concordar não e erro", not erros_ok, json.dumps(erros_ok))

    def test_aceita_herda(self):
        saida, erros = gestor.normalizar_sombra({"herda": True})
        check("normalizar: herda=true passa", not erros and saida.get("herda") is True,
              json.dumps([saida, erros], ensure_ascii=False))
        _, erros_mau = gestor.normalizar_sombra({"herda": "sim"})
        check("normalizar: herda tem de ser booleano",
              any("herda" in e for e in erros_mau), json.dumps(erros_mau, ensure_ascii=False))


class TestLeitura(unittest.TestCase):
    def test_sombra_ligada_precedencia(self):
        check("sombra_ligada: global_control desligado ganha ao activo=true",
              gestor.sombra_ligada({"activo": True, "global_control": CTRL_OFF}) is False)
        check("sombra_ligada: global_control ligado ganha ao activo=false",
              gestor.sombra_ligada({"activo": False, "global_control": CTRL_ON}) is True)
        check("sombra_ligada: sem global_control cai no activo",
              gestor.sombra_ligada({"activo": True}) is True)
        check("sombra_ligada: bloco malformado e desligado",
              gestor.sombra_ligada(None) is False)

    def test_perfil_sem_bloco_herda_do_global(self):
        c = cfg(controlo=CTRL_OFF, perfis={"trabalho": {"activo": True}})
        ef = gestor.sombra_efectiva(c, "trabalho")
        check("efectiva: perfil sem bloco segue o global desligado",
              ef["activo"] is False and ef["origem"] == "global", json.dumps(ef))
        check("efectiva: interruptor lido de global_control",
              ef["interruptor"] == "global_control", json.dumps(ef))
        check("middleware concord: perfil sem bloco segue o global",
              mw._sombra_config(c, "trabalho").get("activo") is False)

    def test_perfil_com_activo_proprio_ganha(self):
        c = cfg(controlo=CTRL_OFF, perfis={"pessoal": {"sombra": {"activo": True}}})
        ef = gestor.sombra_efectiva(c, "pessoal")
        check("efectiva: activo proprio do perfil ganha ao global desligado",
              ef["activo"] is True and ef["origem"] == "proprio", json.dumps(ef))
        check("middleware concord: activo proprio ganha",
              mw._sombra_config(c, "pessoal").get("activo") is True)

    def test_herda_obedece_ao_global(self):
        # O caso que a heranca existe para fechar: um `activo` antigo no bloco do
        # perfil nao pode ressuscitar o ensaio por cima do interruptor global.
        c = cfg(controlo=CTRL_OFF,
                perfis={"trabalho": {"sombra": {"activo": True, "herda": True}}})
        ef = gestor.sombra_efectiva(c, "trabalho")
        check("efectiva: herda=true obedece ao global desligado",
              ef["activo"] is False and ef["origem"] == "herdado", json.dumps(ef))
        check("middleware concord: herda=true obedece ao global",
              mw._sombra_config(c, "trabalho").get("activo") is False)

    def test_herda_liga_quando_o_global_liga(self):
        c = cfg(controlo=CTRL_ON, perfis={"trabalho": {"sombra": {"herda": True}}})
        check("efectiva: herda=true liga com o global ligado",
              gestor.sombra_efectiva(c, "trabalho")["activo"] is True)
        check("middleware concord: herda com global ligado",
              mw._sombra_config(c, "trabalho").get("activo") is True)

    def test_config_viva_concorda(self):
        """A config real: o CLI e o middleware tem de responder o mesmo.

        Um desacordo aqui era o defeito original — o dono desligava num sitio e o
        ensaio continuava a servir no turno.
        """
        real, erros = gestor.carregar(Path("/opt/data/plugin-data/vipernet-model-manager/config.json"))
        check("config viva: sem erros de validacao", not erros, json.dumps(erros, ensure_ascii=False))
        for nome in sorted(real.get("perfis") or {}):
            ef = gestor.sombra_efectiva(real, nome)
            mw_activo = bool(mw._sombra_config(real, nome).get("activo"))
            check(f"config viva: {nome} — CLI e middleware concordam",
                  ef["activo"] == mw_activo, f"cli={ef['activo']} mw={mw_activo}")
            # A origem tem de corresponder ao que o bloco do perfil declara — e
            # nao a um perfil concreto: fixar aqui `trabalho` herda o global
            # passava a falhar no dia em que o dono ligasse ou desligasse o
            # ensaio nesse perfil, que e precisamente o gesto que isto serve.
            bloco = ((real.get("perfis") or {}).get(nome) or {}).get("sombra")
            bloco = bloco if isinstance(bloco, dict) else {}
            if bloco.get("herda") is True:
                esperado = "herdado"
            elif isinstance(bloco.get("activo"), bool):
                esperado = "proprio"
            else:
                esperado = "global"
            check(f"config viva: {nome} — origem {ef['origem']} corresponde ao bloco",
                  ef["origem"] == esperado,
                  f"origem={ef['origem']} esperado={esperado} bloco={json.dumps(bloco)}")
            if esperado == "proprio":
                check(f"config viva: {nome} — interruptor proprio vale",
                      ef["activo"] is bool(bloco["activo"]),
                      json.dumps(ef))


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vmm-ctl-")
        self._env = os.environ.get("VMM_DIR")
        os.environ["VMM_DIR"] = self.tmp

    def tearDown(self):
        if self._env is None:
            os.environ.pop("VMM_DIR", None)
        else:
            os.environ["VMM_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_grava_e_le(self):
        entrada = gestor.registar_controlo_sombra(
            acao="off", ambito="global", antes={"activo": True}, depois={"activo": False},
            por="teste", rev_cfg="abc123")
        caminho = gestor.dir_sombra() / gestor.FICHEIRO_CONTROLO_SOMBRA
        check("ledger: ficheiro criado em aprendiz/sombra",
              caminho.exists() and caminho.parent.name == "sombra", str(caminho))
        linhas = caminho.read_text(encoding="utf-8").splitlines()
        check("ledger: uma linha por gesto", len(linhas) == 1, str(len(linhas)))
        lida = json.loads(linhas[0])
        check("ledger: guarda antes e depois",
              lida["antes"] == {"activo": True} and lida["depois"] == {"activo": False},
              json.dumps(lida, ensure_ascii=False))
        check("ledger: guarda autor, ambito e rev",
              lida["por"] == "teste" and lida["ambito"] == "global" and lida["rev"] == "abc123",
              json.dumps(lida, ensure_ascii=False))
        check("ledger: ts com offset explicito",
              "+" in str(lida["ts"]) or str(lida["ts"]).endswith("Z"), str(lida["ts"]))
        check("ledger: le-se de volta", gestor.ler_controlo_sombra(5) == [entrada])

    def test_append_nao_reescreve(self):
        gestor.registar_controlo_sombra(acao="on", ambito="global", antes=None, depois=None)
        gestor.registar_controlo_sombra(acao="off", ambito="perfil:trabalho",
                                        antes={"activo": True}, depois={"activo": False},
                                        perfil="trabalho")
        entradas = gestor.ler_controlo_sombra(10)
        check("ledger: segunda linha nao apaga a primeira", len(entradas) == 2, str(len(entradas)))
        check("ledger: ordem cronologica de leitura",
              entradas[0]["acao"] == "on" and entradas[1]["perfil"] == "trabalho",
              json.dumps(entradas, ensure_ascii=False))

    def test_linha_corrompida_nao_esconde_as_outras(self):
        caminho = gestor.dir_sombra() / gestor.FICHEIRO_CONTROLO_SOMBRA
        gestor.registar_controlo_sombra(acao="on", ambito="global", antes=None, depois=None)
        with open(caminho, "a", encoding="utf-8") as fh:
            fh.write("{isto nao e json\n")
        gestor.registar_controlo_sombra(acao="off", ambito="global", antes=None, depois=None)
        entradas = gestor.ler_controlo_sombra(10)
        check("ledger: linha ilegivel e ignorada, as boas ficam", len(entradas) == 2,
              str(len(entradas)))
        check("ledger: leitura nao rebenta com linha corrompida",
              [e["acao"] for e in entradas] == ["on", "off"], json.dumps(entradas, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"], exit=False)
    if FALHAS:
        print(f"\nFALHAS: {len(FALHAS)}")
        for nome in FALHAS:
            print(" -", nome)
        sys.exit(1)
    print("\ntodas as verificacoes passaram")
