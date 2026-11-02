#!/usr/bin/env python3
"""Testes do banco de avaliacao (`eval/medir.py`).

O que se prova aqui e a *disciplina* do banco, nao o acerto do classificador:
que as metricas estao certas, que os conjuntos de teste estao congelados, que
afinar com o gold e recusado e que o CSV de revisao humana tem uma linha por
caso. Sem isto, o relatorio podia estar bonito e errado.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
AQUI = RAIZ / "eval"
FALHAS: list[str] = []
N = 0


def ok(cond: bool, nome: str) -> None:
    global N
    N += 1
    if not cond:
        FALHAS.append(nome)
        print(f"FALHA {nome}")
    else:
        print(f"ok    {nome}")


def _medir():
    spec = importlib.util.spec_from_file_location("vmm_eval_medir_t", AQUI / "medir.py")
    m = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


def main() -> int:
    m = _medir()

    # --- metricas -----------------------------------------------------------
    pares = [("leve", "leve"), ("leve", "media"), ("media", "media"), ("alta", "media")]
    r = m.metricas(pares)
    ok(r["n"] == 4, "metricas: conta os pares")
    ok(abs(r["acerto"] - 0.5) < 1e-9, "metricas: acerto 2/4")
    ok(r["matriz"]["leve"]["media"] == 1, "metricas: matriz regista o erro")
    ok(r["matriz"]["alta"]["media"] == 1, "metricas: alta prevista como media")
    # macro-F1 tem de ser a media dos F1 das tres classes, nao o F1 global.
    f1s = [r["por_classe"][c]["f1"] for c in m.NIVEIS]
    ok(abs(r["macro_f1"] - sum(f1s) / 3) < 1.5e-3, "metricas: macro-F1 e a media por classe")
    ok(r["por_classe"]["alta"]["cobertura"] == 0.0, "metricas: classe errada tem cobertura 0")

    # --- percentis ----------------------------------------------------------
    ok(m.percentil([10, 20, 30, 40, 50], 0.5) == 30, "percentil p50 de lista impar")
    ok(m.percentil([10, 20], 0.5) == 15.0, "percentil interpola entre dois valores")
    ok(m.percentil([], 0.95) == 0.0, "percentil de lista vazia e zero")
    ok(m.percentil([7], 0.95) == 7.0, "percentil de um valor e esse valor")

    # --- gold congelado -----------------------------------------------------
    gold = AQUI / "gold.jsonl"
    ok(gold.exists(), "gold.jsonl existe")
    casos = m.carregar_jsonl(gold)
    ok(len(casos) >= 50, f"gold.jsonl tem massa (n={len(casos)})")
    ids = [c["id"] for c in casos]
    ok(len(ids) == len(set(ids)), "ids do gold sao unicos")
    cats = {c["categoria"] for c in casos}
    ok({"claro", "ambigua", "giria", "typo", "adversaria", "extremo"} <= cats,
       "gold cobre as seis categorias obrigatorias")
    ok(all(c["esperado"] in m.NIVEIS for c in casos), "todo o caso tem nivel valido")
    ok(all(isinstance(c["texto"], str) for c in casos), "todo o caso tem texto string")

    lock = m.congelar(gold, permitir_criar=False)
    ok(lock["estado"] in {"igual", "sem-lock"}, f"gold tem lock utilizavel ({lock['estado']})")
    if (AQUI / ".gold.lock").exists():
        dados = json.loads((AQUI / ".gold.lock").read_text(encoding="utf-8"))
        ok(dados.get("usado_para_afinar") is False, "o lock declara que o gold nao serviu para afinar")

    gold2 = AQUI / "gold2.jsonl"
    if gold2.exists():
        c2 = m.carregar_jsonl(gold2)
        ok(len(c2) >= 20, f"gold2 (teste cego) tem massa (n={len(c2)})")
        ok(all(c["id"] not in ids for c in c2), "gold2 nao reutiliza ids do gold")
        textos_gold = {c["texto"].strip().lower() for c in casos}
        repetidos = [c["id"] for c in c2 if c["texto"].strip().lower() in textos_gold]
        ok(not repetidos, f"gold2 nao repete textos do gold ({repetidos[:3]})")

    # --- recusa de afinar com o conjunto de teste ---------------------------
    for extra in (["--afinar"], ["--afinar", "--runners", "heuristica"]):
        p = subprocess.run([sys.executable, str(AQUI / "medir.py"), *extra],
                           capture_output=True, text=True, timeout=120)
        ok(p.returncode == 2 and "RECUSADO" in (p.stdout + p.stderr),
           f"recusa afinar com o gold ({' '.join(extra)})")

    # --- variantes: mesmo conteudo, registos diferentes ---------------------
    var = AQUI / "variantes.jsonl"
    ok(var.exists(), "variantes.jsonl existe")
    if var.exists():
        cv = m.carregar_jsonl(var)
        grupos: dict[str, int] = {}
        for c in cv:
            grupos[c["grupo"]] = grupos.get(c["grupo"], 0) + 1
        ok(all(v >= 2 for v in grupos.values()), "cada grupo de variantes tem >= 2 registos")
        # Consistencia calculada sobre niveis simulados: iguais -> 1.0, diferentes -> parcial.
        sim = m.medir_variantes(
            [{"id": "a", "grupo": "g", "texto": "x", "esperado": "alta", "categoria": "claro"},
             {"id": "b", "grupo": "g", "texto": "y", "esperado": "alta", "categoria": "claro"}],
            lambda cs: {"nome": "sim", "linhas": [
                {"id": c["id"], "obtido": "alta", "esperado": "alta", "categoria": "claro",
                 "ms": 1.0, "erro": ""} for c in cs]})
        ok(sim["taxa_consistencia"] == 1.0, "variantes: mesmo nivel da consistencia 1.0")
        sim2 = m.medir_variantes(
            [{"id": "a", "grupo": "g", "texto": "x", "esperado": "alta", "categoria": "claro"},
             {"id": "b", "grupo": "g", "texto": "y", "esperado": "alta", "categoria": "claro"}],
            lambda cs: {"nome": "sim", "linhas": [
                {"id": "a", "obtido": "alta", "esperado": "alta", "categoria": "claro",
                 "ms": 1.0, "erro": ""},
                {"id": "b", "obtido": "leve", "esperado": "alta", "categoria": "claro",
                 "ms": 1.0, "erro": ""}]})
        ok(sim2["taxa_consistencia"] == 0.0, "variantes: niveis diferentes da consistencia 0.0")

    # --- soma de um runner sem nivel ---------------------------------------
    runner = {"nome": "x", "linhas": [
        {"id": "a", "esperado": "leve", "obtido": "leve", "categoria": "claro", "ms": 2.0, "erro": ""},
        {"id": "b", "esperado": "alta", "obtido": "", "categoria": "claro", "ms": 3.0, "erro": "ValueError"},
    ]}
    s = m.resumir(runner, {})
    ok(s["n_sem_nivel"] == 1 and s["sem_nivel_ids"] == ["b"], "resumo: conta os casos sem nivel")
    ok(s["metricas"]["n"] == 1, "resumo: metricas so sobre os casos com nivel")
    ok(s["erros"] and s["erros"][0]["id"] == "b", "resumo: lista os erros por id")

    # --- humano no circuito -------------------------------------------------
    s2 = m.resumir(runner, {"a": "media"})
    ok("subconjunto_humano" in s2, "resumo: separa o subconjunto confirmado por humano")
    ok(s2["subconjunto_humano"]["n"] == 1, "resumo: subconjunto humano so conta os rotulados")

    print(f"\n{N - len(FALHAS)}/{N} verificacoes OK")
    if FALHAS:
        print("FALHARAM: " + "; ".join(FALHAS))
        print("VEREDITO: FAIL")
        return 1
    print("VEREDITO: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
