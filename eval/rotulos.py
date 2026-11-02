#!/usr/bin/env python3
"""Valida rotulos e calcula metricas por classe (macro-F1, confusao, cobertura).

Uso:
    python3 rotulos.py --amostra amostra.jsonl --rotulos rotulos.jsonl
    python3 rotulos.py --amostra a.jsonl --rotulos r.jsonl --previsoes p.jsonl
        compara rotulo humano com a previsao do classificador

Sem --previsoes, so valida a consistencia dos rotulos.

Codigos de saida: 0 ok · 1 rotulos invalidos · 2 uso invalido.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys

DOMINIO = ("leve", "media", "alta")
CONFIANCA = ("alta", "media", "baixa")


def _ler(caminho: str) -> list[dict]:
    out = []
    with open(caminho, encoding="utf-8") as f:
        for n, linha in enumerate(f, 1):
            linha = linha.strip()
            if not linha:
                continue
            try:
                out.append(json.loads(linha))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"rc=2 {caminho}:{n}: JSON invalido ({exc})")
    return out


def validar(amostra: list[dict], rotulos: list[dict]) -> list[str]:
    ids = {a["id"] for a in amostra}
    erros, vistos = [], set()
    for r in rotulos:
        i = r.get("id")
        if not i:
            erros.append("rotulo sem id")
            continue
        if i not in ids:
            erros.append(f"{i}: id nao existe na amostra")
        if i in vistos:
            erros.append(f"{i}: id duplicado")
        vistos.add(i)
        if r.get("rotulo") not in DOMINIO:
            erros.append(f"{i}: rotulo invalido {r.get('rotulo')!r}")
        if r.get("confianca") not in CONFIANCA:
            erros.append(f"{i}: confianca invalida {r.get('confianca')!r}")
        if r.get("confianca") in ("media", "baixa") and not (r.get("motivo") or "").strip():
            erros.append(f"{i}: falta motivo (confianca {r['confianca']})")
        if not r.get("rotulador"):
            erros.append(f"{i}: falta rotulador")
    return erros


def metricas(pares: list[tuple[str, str]]) -> dict:
    classes = DOMINIO
    tp = collections.Counter()
    fp = collections.Counter()
    fn = collections.Counter()
    confusao = collections.Counter()
    for real, prev in pares:
        confusao[(real, prev)] += 1
        if real == prev:
            tp[real] += 1
        else:
            fn[real] += 1
            fp[prev] += 1

    por_classe = {}
    for c in classes:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        por_classe[c] = {"precisao": round(p, 3), "cobertura": round(r, 3),
                         "f1": round(f1, 3), "n": tp[c] + fn[c]}
    macro = sum(v["f1"] for v in por_classe.values()) / len(classes)
    acerto = sum(tp.values()) / len(pares) if pares else 0.0
    return {
        "n": len(pares),
        "acuracia": round(acerto, 4),
        "macro_f1": round(macro, 4),
        "por_classe": por_classe,
        "confusao": {f"{a}->{b}": n for (a, b), n in sorted(confusao.items())},
    }


def concordancia(a: list[dict], b: list[dict]) -> dict:
    """Kappa de Cohen entre dois rotuladores sobre os ids comuns."""
    ma = {x["id"]: x["rotulo"] for x in a if x.get("rotulo")}
    mb = {x["id"]: x["rotulo"] for x in b if x.get("rotulo")}
    comuns = sorted(set(ma) & set(mb))
    if not comuns:
        return {"n": 0}
    obs = sum(1 for i in comuns if ma[i] == mb[i]) / len(comuns)
    ca = collections.Counter(ma[i] for i in comuns)
    cb = collections.Counter(mb[i] for i in comuns)
    esp = sum((ca[c] / len(comuns)) * (cb[c] / len(comuns)) for c in DOMINIO)
    kappa = (obs - esp) / (1 - esp) if esp < 1 else 0.0
    return {"n": len(comuns), "acordo_observado": round(obs, 4),
            "kappa": round(kappa, 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amostra", required=True)
    ap.add_argument("--rotulos", required=True)
    ap.add_argument("--previsoes", help="jsonl com {id, rotulo} do classificador")
    ap.add_argument("--rotulos-b", help="segundo rotulador, para kappa")
    args = ap.parse_args()

    amostra = _ler(args.amostra)
    rotulos = _ler(args.rotulos)

    erros = validar(amostra, rotulos)
    if erros:
        print(f"rc=1 {len(erros)} rotulos invalidos:", file=sys.stderr)
        for e in erros[:40]:
            print("  -", e, file=sys.stderr)
        return 1

    validos = [r for r in rotulos if r.get("rotulo")]
    print(f"rotulos validos: {len(validos)} de {len(amostra)} na amostra")
    if not validos:
        print("nada a medir: nenhum rotulo preenchido")
        return 0

    dist = collections.Counter(r["rotulo"] for r in validos)
    print("distribuicao dos rotulos:", dict(sorted(dist.items())))
    baixa = sum(1 for r in validos if r["confianca"] == "baixa")
    print(f"confianca baixa: {baixa} ({baixa / len(validos):.1%})")

    if args.rotulos_b:
        print("concordancia com o segundo rotulador:",
              json.dumps(concordancia(validos, _ler(args.rotulos_b)), ensure_ascii=False))

    if args.previsoes:
        prev = {p["id"]: p["rotulo"] for p in _ler(args.previsoes)}
        pares = [(r["rotulo"], prev[r["id"]]) for r in validos if r["id"] in prev]
        if not pares:
            print("sem ids comuns entre rotulos e previsoes", file=sys.stderr)
            return 1
        print(json.dumps(metricas(pares), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
