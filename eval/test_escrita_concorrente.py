"""Prova da escrita concorrente: per-key sobre o disco, com CAS.

Corre em sandbox (nunca toca no config vivo): dois escritores, cada um muda
uma chave diferente, em processos simultaneos. Sem a correcao, o que grava o
documento inteiro perde a chave do outro; com `alterar`, nenhuma se perde.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import gestor as g  # noqa: E402

OK = 0
FALHAS: list[str] = []


def check(nome: str, cond: bool, detalhe: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        FALHAS.append(f"{nome} {detalhe}".strip())


def _base(tmp: Path) -> Path:
    cfg = {
        "versao": 3, "activo": True, "registar": True,
        "analise": {"limiares": {"leve": 0.35, "alta": 0.7}},
        "perfis": {
            "a": {"activo": True, "catalogo": [
                {"id": "m1", "provider": "deepseek", "modelo": "deepseek-flash",
                 "complexidade": ["leve", "media", "alta"]}]},
            "b": {"activo": True, "catalogo": [
                {"id": "m2", "provider": "deepseek", "modelo": "deepseek-flash",
                 "complexidade": ["leve", "media", "alta"]}]},
        },
    }
    for p in cfg["perfis"].values():
        p["activo"] = False
    caminho = tmp / "config.json"
    g.gravar(cfg, caminho)
    return caminho


# ---------------------------------------------------------------- processos
def _por_chave(caminho: str, perfil: str, valor: bool, barreira) -> None:
    def mutar(cfg):
        cfg["perfis"][perfil]["activo"] = valor
        return cfg
    barreira.wait()
    g.alterar(mutar, Path(caminho))


def _documento_inteiro(caminho: str, perfil: str, valor: bool, barreira) -> None:
    cfg, _ = g.carregar(Path(caminho))
    cfg["perfis"][perfil]["activo"] = valor
    barreira.wait()
    try:
        g.gravar(cfg, Path(caminho))
    except g.Conflito:
        pass


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vmm-cas-"))
    try:
        # 1. rev(): mesma config, ordem de insercao diferente, mesma impressao.
        a = {"x": 1, "y": {"b": 2, "a": [1, 2]}}
        b = {"y": {"a": [1, 2], "b": 2}, "x": 1}
        check("rev_canonica", g.rev(a) == g.rev(b), f"{g.rev(a)} != {g.rev(b)}")
        check("rev_muda_com_conteudo", g.rev(a) != g.rev({**a, "x": 2}))

        # 2. gravar com rev antigo levanta Conflito e NAO escreve.
        caminho = _base(tmp)
        rev0 = g.rev(g.carregar(caminho)[0])
        cfg, _ = g.carregar(caminho)
        cfg["perfis"]["a"]["activo"] = True
        g.gravar(cfg, caminho)                       # outro escritor ganhou
        rev1 = g.rev(g.carregar(caminho)[0])
        check("rev_mudou", rev0 != rev1)
        cfg_velho, _ = g.carregar(caminho)
        cfg_velho["perfis"]["b"]["activo"] = False
        recusou = False
        try:
            g.gravar(cfg_velho, caminho, rev_esperado=rev0)
        except g.Conflito as exc:
            recusou = True
            check("conflito_traz_rev_actual", exc.rev_actual == rev1, exc.rev_actual)
        check("gravar_rev_antigo_recusa", recusou)
        check("nada_escrito_na_recusa",
              g.rev(g.carregar(caminho)[0]) == rev1)

        # 3. gravar com o rev certo passa.
        g.gravar(cfg_velho, caminho, rev_esperado=rev1)
        check("gravar_rev_certo_passa", g.carregar(caminho)[0]["perfis"]["b"]["activo"] is False)

        # 4. alterar aplica o delta sobre o estado fresco, nao sobre a base velha.
        caminho = _base(tmp)
        base, _ = g.carregar(caminho)                 # escritor le esta versao
        outro, _ = g.carregar(caminho)
        outro["perfis"]["a"]["activo"] = True
        g.gravar(outro, caminho)                      # e este escreve primeiro

        def mutar_b(cfg):
            cfg["perfis"]["b"]["activo"] = True
            return cfg
        g.alterar(mutar_b, caminho)                   # delta de b, sobre o fresco
        final, _ = g.carregar(caminho)
        check("delta_preserva_outro", final["perfis"]["a"]["activo"] is True,
              "a escrita de `a` foi perdida pelo delta de `b`")
        check("delta_aplicado", final["perfis"]["b"]["activo"] is True)
        check("base_local_intacta", base["perfis"]["b"]["activo"] is False,
              "o delta mutou o objecto que o chamador tinha em maos")

        # 5. Conflito de intencao: alterar com rev antigo nao aplica nada.
        antes = g.rev(g.carregar(caminho)[0])
        base_velha = antes
        g.alterar(lambda c: c, caminho)               # toca o mtime, mesma versao
        barrou = False
        try:
            g.alterar(mutar_b, caminho, rev_esperado="deadbeefdeadbeef")
        except g.Conflito:
            barrou = True
        check("alterar_rev_antigo_recusa", barrou)
        check("alterar_recusa_nao_escreve", g.rev(g.carregar(caminho)[0]) == base_velha)

        # 6. Corrida real: 40 rondas, dois processos, chaves diferentes.
        perdas = 0
        rondas = 40
        for i in range(rondas):
            caminho = _base(tmp)
            ctx = mp.get_context("fork")
            barreira = ctx.Barrier(2)
            pa = ctx.Process(target=_por_chave, args=(str(caminho), "a", True, barreira))
            pb = ctx.Process(target=_por_chave, args=(str(caminho), "b", True, barreira))
            pa.start(); pb.start(); pa.join(10); pb.join(10)
            estado, _ = g.carregar(caminho)
            if not (estado["perfis"]["a"]["activo"] is True
                    and estado["perfis"]["b"]["activo"] is True):
                perdas += 1
        check("corrida_por_chave_sem_perdas", perdas == 0, f"{perdas}/{rondas} perderam")

        # 7. A mesma corrida com escrita do documento inteiro: perde-se alguma.
        perdas_doc = 0
        for i in range(40):
            caminho = _base(tmp)
            ctx = mp.get_context("fork")
            barreira = ctx.Barrier(2)
            pa = ctx.Process(target=_documento_inteiro, args=(str(caminho), "a", True, barreira))
            pb = ctx.Process(target=_documento_inteiro, args=(str(caminho), "b", True, barreira))
            pa.start(); pb.start(); pa.join(10); pb.join(10)
            estado, _ = g.carregar(caminho)
            # O de `b` le antes de `a` escrever; sem rev, aplicar `b` apagaria o `a`.
            if not (estado["perfis"]["a"]["activo"] is True
                    and estado["perfis"]["b"]["activo"] is True):
                perdas_doc += 1
        check("documento_inteiro_sem_rev_perde", perdas_doc > 0,
              f"esperava perdas e mediu {perdas_doc}/40")
        print(f"  informativo: documento inteiro sem rev perdeu {perdas_doc}/40 "
              f"(o CAS do CLI/painel converte estas em Conflito)")

        # 8. Sem ficheiro de trava orfao e sem .tmp pendente.
        check("sem_tmp_pendente", not list(tmp.glob("*.tmp")))

        print(f"\n{OK} verificacoes, {len(FALHAS)} falhas")
        for f in FALHAS:
            print("FALHA:", f)
        return 1 if FALHAS else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
