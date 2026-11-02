#!/usr/bin/env python3
"""Extrai uma amostra estratificada de prompts do ledger para rotulagem humana.

A unidade de amostragem e o PROMPT DE UTILIZADOR, nao a decisao. Um turno gera
varios pedidos ao modelo (laco de ferramentas), cada um registado como decisao
no ledger; amostrar decisoes pesaria a amostra pela profundidade do laco de
ferramentas em vez de pela intencao do utilizador. Cada prompt leva o nivel da
PRIMEIRA decisao que o serviu, e o numero de decisoes que gerou.

Le o ledger do plugin (uso.jsonl) e junta a cada decisao o ultimo prompt de
utilizador que o classificador viu, do state.db do perfil (tabela messages,
role='user', timestamp <= ts da decisao).

Privacidade: a saida contem texto real de conversa. Este script recusa-se a
escrever dentro do repositorio do plugin -- a amostra fica fora do repo e nunca
e publicada. O que se publica e este codigo e a rubrica.

Uso:
    python3 extrair_amostra.py --saida /caminho/fora/do/repo/amostra.jsonl
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import random
import sqlite3
import sys
from pathlib import Path

RAIZ = Path("/opt/data")
LEDGER = RAIZ / "plugin-data/vipernet-model-manager/uso.jsonl"
BASES = {
    "default": RAIZ / "state.db",
    "pessoal": RAIZ / "profiles/pessoal/state.db",
    "trabalho": RAIZ / "profiles/trabalho/state.db",
}
REPO = Path(__file__).resolve().parents[1]

SEMENTE = 20261102
MAX_CHARS = 4000
CHAVE_TEXTO = 400  # prefixo usado para identificar o prompt


def _bucket(score):
    if not isinstance(score, int):
        return "sem_score"
    if score == 0:
        return "0"
    if score <= 2:
        return "1-2"
    if score == 3:
        return "3"
    if score <= 5:
        return "4-5"
    return "6+"


# (nome, predicado, alvo). O alvo e o numero de prompts a levar do estrato.
ESTRATOS = [
    ("leve_s0",        lambda p: p["nivel"] == "leve"  and p["bucket"] == "0",        30),
    ("leve_s1_2",      lambda p: p["nivel"] == "leve"  and p["bucket"] == "1-2",      30),
    ("media_s1_2",     lambda p: p["nivel"] == "media" and p["bucket"] == "1-2",      30),
    ("media_s3",       lambda p: p["nivel"] == "media" and p["bucket"] == "3",        30),
    ("media_s4_5",     lambda p: p["nivel"] == "media" and p["bucket"] == "4-5",      30),
    ("media_s6mais",   lambda p: p["nivel"] == "media" and p["bucket"] == "6+",       30),
    ("alta_s1_2",      lambda p: p["nivel"] == "alta"  and p["bucket"] == "1-2",      30),
    ("alta_s3_5",      lambda p: p["nivel"] == "alta"  and p["bucket"] in ("3", "4-5"), 30),
    ("alta_s6mais",    lambda p: p["nivel"] == "alta"  and p["bucket"] == "6+",       30),
    ("por_tarefa",     lambda p: p.get("origem") == "tarefa",                         30),
    ("decidido_modelo", lambda p: p.get("via") == "modelo",                           30),
]


def _epoch(ts: str) -> float:
    return dt.datetime.fromisoformat(ts).timestamp()


def _ler_ledger() -> list[dict]:
    out = []
    with LEDGER.open(encoding="utf-8") as f:
        for linha in f:
            try:
                e = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if "nivel" in e:
                out.append(e)
    return out


def _ligar_texto(eventos: list[dict]) -> None:
    cons = {}
    for perfil, base in BASES.items():
        if base.exists():
            cons[perfil] = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    for e in eventos:
        perfil, sessao = e.get("perfil"), e.get("sessao")
        c = cons.get(perfil)
        if not c or not sessao:
            continue
        linha = c.execute(
            "select content, timestamp from messages "
            "where session_id=? and role='user' and timestamp<=? "
            "order by timestamp desc limit 1",
            (sessao, _epoch(e["ts"])),
        ).fetchone()
        if linha and linha[0] and str(linha[0]).strip():
            texto = str(linha[0])
            if len(texto) > MAX_CHARS:
                texto = texto[:MAX_CHARS]
                e["texto_truncado"] = True
            e["texto"] = texto
            e["texto_ts"] = float(linha[1])
            e["texto_sha1"] = hashlib.sha1(
                texto[:CHAVE_TEXTO].encode("utf-8")).hexdigest()[:12]
    for c in cons.values():
        c.close()


def _agrupar_por_prompt(eventos: list[dict]) -> list[dict]:
    """Colapsa as decisoes no prompt que as originou."""
    grupos: dict[tuple, list[dict]] = collections.defaultdict(list)
    for e in eventos:
        if "texto_sha1" not in e:
            continue
        grupos[(e.get("perfil"), e.get("sessao"), e["texto_sha1"])].append(e)

    prompts = []
    for (perfil, sessao, sha), lista in grupos.items():
        lista.sort(key=lambda x: x["ts"])
        primeiro = lista[0]
        niveis = collections.Counter(x["nivel"] for x in lista)
        cls = next((x["classificador"] for x in lista
                    if isinstance(x.get("classificador"), dict)
                    and x["classificador"].get("lido")), None)
        via = "modelo" if cls else ("tarefa" if primeiro.get("origem") == "tarefa"
                                    else "analise")
        prompts.append({
            "id": sha,
            "perfil": perfil,
            "sessao": sessao,
            "ts_primeiro": primeiro["ts"],
            "decisoes": len(lista),
            "nivel": primeiro["nivel"],
            "niveis_vistos": dict(niveis),
            "instavel": len(niveis) > 1,
            "score": primeiro.get("score"),
            "bucket": _bucket(primeiro.get("score")),
            "sinais": primeiro.get("sinais") or [],
            "origem": primeiro.get("origem"),
            "via": via,
            "tarefa": primeiro.get("tarefa"),
            "classificador": cls,
            "texto": primeiro["texto"],
            "texto_truncado": primeiro.get("texto_truncado", False),
            # campos a preencher pelo rotulador
            "rotulo": None,
            "confianca": None,
            "motivo": None,
            "rotulador": None,
        })
    return prompts


def _amostrar(prompts: list[dict]) -> tuple[list[dict], dict]:
    rnd = random.Random(SEMENTE)
    escolhidos: dict[str, dict] = {}
    relatorio = {}
    for nome, pred, alvo in ESTRATOS:
        cand = sorted([p for p in prompts if pred(p)], key=lambda x: x["ts_primeiro"])
        relatorio[nome] = {"candidatos": len(cand), "alvo": alvo}
        take = cand if len(cand) <= alvo else rnd.sample(cand, alvo)
        for p in take:
            if p["id"] in escolhidos:
                escolhidos[p["id"]]["estratos"].append(nome)
                continue
            item = dict(p)
            item["estratos"] = [nome]
            escolhidos[p["id"]] = item
    itens = list(escolhidos.values())
    itens.sort(key=lambda x: (x["estratos"][0], x["ts_primeiro"]))
    return itens, relatorio


def _pool_de_sessoes() -> list[dict]:
    """Todos os prompts de utilizador distintos, de todas as sessoes.

    O ledger so cobre as sessoes em que o plugin decidiu (144 prompts). O gold
    set precisa de volume e de diversidade de registo, e a reserva esta nas
    sessoes: ~3,6 mil prompts distintos com erros, giria e variacao regional.
    """
    vistos: dict[str, dict] = {}
    for perfil, base in BASES.items():
        if not base.exists():
            continue
        c = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
        for sessao, cont, ts in c.execute(
                "select session_id, content, timestamp from messages "
                "where role='user' and content is not null"):
            t = (cont or "").strip()
            if len(t) < 12:
                continue
            h = hashlib.sha1(t[:CHAVE_TEXTO].encode("utf-8")).hexdigest()[:12]
            if h in vistos:
                continue
            truncado = len(t) > MAX_CHARS
            vistos[h] = {
                "id": h, "perfil": perfil, "sessao": sessao,
                "ts_primeiro": dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(),
                "texto": t[:MAX_CHARS], "texto_truncado": truncado,
                "comprimento": len(t),
                "rotulo": None, "confianca": None, "motivo": None, "rotulador": None,
            }
        c.close()
    return list(vistos.values())


def _estratos_pool(prompts: list[dict], alvo_por_estrato: int) -> tuple[list[dict], dict]:
    def faixa(n):
        return "12-60" if n < 60 else "60-200" if n < 200 else "200-800" if n < 800 else "800+"

    rnd = random.Random(SEMENTE)
    grupos: dict[tuple, list[dict]] = collections.defaultdict(list)
    for p in prompts:
        grupos[(p["perfil"], faixa(p["comprimento"]))].append(p)

    itens, relatorio = [], {}
    for (perfil, f), lista in sorted(grupos.items()):
        nome = f"{perfil}_{f}"
        relatorio[nome] = {"candidatos": len(lista),
                           "levados": min(alvo_por_estrato, len(lista))}
        take = lista if len(lista) <= alvo_por_estrato else rnd.sample(lista, alvo_por_estrato)
        for p in take:
            item = dict(p)
            item["estratos"] = [nome]
            itens.append(item)
    itens.sort(key=lambda x: (x["estratos"][0], x["ts_primeiro"]))
    return itens, relatorio


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", required=True)
    ap.add_argument("--fonte", choices=("ledger", "sessoes"), default="ledger")
    ap.add_argument("--por-estrato", type=int, default=25,
                    help="so com --fonte sessoes")
    ap.add_argument("--permitir-no-repo", action="store_true",
                    help="so para testes; a amostra real nunca vive no repo")
    args = ap.parse_args()

    saida = Path(args.saida).resolve()
    if REPO in saida.parents and not args.permitir_no_repo:
        print(f"recusado: {saida} esta dentro do repositorio do plugin; "
              "a amostra contem conversa real", file=sys.stderr)
        return 2

    if args.fonte == "sessoes":
        pool = _pool_de_sessoes()
        itens, relatorio = _estratos_pool(pool, args.por_estrato)
        corpo = "\n".join(json.dumps(i, ensure_ascii=False) for i in itens) + "\n"
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(corpo, encoding="utf-8")
        manifesto = {
            "gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(),
            "fonte": "sessoes (state.db dos tres perfis)",
            "unidade": "prompt de utilizador distinto, >=12 caracteres",
            "reserva_total": len(pool),
            "amostra": len(itens),
            "por_estrato": dict(sorted(collections.Counter(
                i["estratos"][0] for i in itens).items())),
            "estratos_detalhe": relatorio,
            "semente": SEMENTE,
            "sha256_amostra": hashlib.sha256(corpo.encode("utf-8")).hexdigest(),
        }
        saida.with_suffix(".manifesto.json").write_text(
            json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifesto, ensure_ascii=False, indent=2))
        return 0

    eventos = _ler_ledger()
    _ligar_texto(eventos)
    sem_texto = sum(1 for e in eventos if "texto" not in e)
    prompts = _agrupar_por_prompt(eventos)
    itens, relatorio = _amostrar(prompts)

    corpo = "\n".join(json.dumps(i, ensure_ascii=False) for i in itens) + "\n"
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(corpo, encoding="utf-8")

    manifesto = {
        "gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(),
        "ledger": str(LEDGER),
        "unidade": "prompt de utilizador (primeira decisao que o serviu)",
        "decisoes_no_ledger": len(eventos),
        "decisoes_sem_texto": sem_texto,
        "prompts_distintos": len(prompts),
        "razao_decisoes_por_prompt": round(len(eventos) / max(1, len(prompts)), 2),
        "distribuicao_prompts_por_nivel": dict(collections.Counter(
            p["nivel"] for p in prompts)),
        "distribuicao_prompts_por_bucket": dict(collections.Counter(
            p["bucket"] for p in prompts)),
        "prompts_instaveis": sum(1 for p in prompts if p["instavel"]),
        "amostra": len(itens),
        "por_estrato": dict(sorted(collections.Counter(
            i["estratos"][0] for i in itens).items())),
        "estratos_detalhe": relatorio,
        "semente": SEMENTE,
        "sha256_amostra": hashlib.sha256(corpo.encode("utf-8")).hexdigest(),
    }
    saida.with_suffix(".manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifesto, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
