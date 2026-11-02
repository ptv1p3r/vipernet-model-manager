#!/usr/bin/env python3
"""Avaliacao medida do classificador de complexidade — heuristico e por modelo.

Porque existe: escolher um modelo de classificacao por intuicao e o mesmo que
nao escolher. Este banco mede o que interessa ao dono do plugin — acerto em
relacao a um referencial, latencia, tokens, custo, estabilidade e sensibilidade
ao registo do texto — e compara sempre contra a heuristica como *baseline*.

Regras da metodologia (aplicadas, nao sugeridas):

  1. Metricas objectivas — acerto, macro-F1, matriz de confusao, p50/p95 de
     latencia, tokens de entrada/saida e custo por 1000 classificacoes. Cada
     numero sai do mesmo conjunto e da mesma passagem.
  2. Gold set congelado — o sha256 do `gold.jsonl` fica em `.gold.lock` e o
     banco recusa `--afinar`, para que o conjunto de teste nunca vire conjunto
     de treino (contaminacao). Alterar o gold invalida a comparacao anterior.
  3. Dados representativos e extremos — cada caso tem `categoria`: claro,
     ambigua, giria, typo, adversaria, extremo, misto.
  4. Parametros fixos — temperatura 0, top_p 1, mesmo prompt de sistema,
     mesmo `max_tokens`; tudo registado no relatorio.
  5. Reproducao — `--repeticoes N` repete cada caso e mede a concordancia
     (determinismo); o relatorio guarda modelo, endpoint, hora e sha256 das
     entradas.
  6. Enviesamento — `--variantes` mede se o MESMO conteudo tecnico, escrito em
     registos diferentes (cortes, seco, pt-BR, impessoal), recebe o mesmo
     nivel. Divergencia e enviesamento de forma, nao de substancia.
  7. Humano no circuito — `--revisao` escreve um CSV para o dono confirmar os
     rotulos; `eval/rotulos-humanos.json` guarda o que ele confirmou e o
     relatorio separa o acerto no subconjunto confirmado por humano.

Uso:
  python3 eval/medir.py                          # heuristica + modelo da config
  python3 eval/medir.py --repeticoes 3 --variantes
  python3 eval/medir.py --revisao                # gera eval/revisao.csv
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
NIVEIS = ("leve", "media", "alta")

# Os modulos do plugin importam-se entre si pelo nome simples quando carregados
# fora de um pacote (`analise_modelo` faz `import gestor`). Com a raiz no path,
# o banco carrega exactamente o mesmo codigo que corre em producao.
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


# ---------------------------------------------------------------------------
# Carregamento dos modulos do plugin (por ficheiro: nao e um pacote instalado)
# ---------------------------------------------------------------------------

def _mod(nome: str):
    spec = importlib.util.spec_from_file_location(f"vmm_eval_{nome}", RAIZ / f"{nome}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"nao consegui carregar {nome}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"vmm_eval_{nome}"] = m
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# Gold set e congelamento
# ---------------------------------------------------------------------------

def sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def carregar_jsonl(caminho: Path) -> list[dict[str, Any]]:
    casos = []
    for n, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        try:
            casos.append(json.loads(linha))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{caminho}:{n}: JSON invalido — {exc}") from exc
    return casos


def congelar(caminho: Path, *, permitir_criar: bool) -> dict[str, Any]:
    """Lock do gold set. Sem lock, cria-o; com lock diferente, avisa (nao trava)."""
    lock = AQUI / f".{caminho.stem}.lock"
    atual = sha256(caminho)
    if not lock.exists():
        if not permitir_criar:
            return {"sha256": atual, "estado": "sem-lock"}
        lock.write_text(json.dumps({
            "sha256": atual,
            "congelado_em": datetime.now(ZoneInfo("Europe/Lisbon")).isoformat(timespec="seconds"),
            "usado_para_afinar": False,
            "nota": "Conjunto de teste. Nunca usar para afinar limiares ou prompt.",
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"sha256": atual, "estado": "congelado-agora"}
    try:
        dados = json.loads(lock.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"sha256": atual, "estado": "lock-ilegivel"}
    if dados.get("sha256") == atual:
        return {"sha256": atual, "estado": "igual", "congelado_em": dados.get("congelado_em")}
    return {"sha256": atual, "estado": "alterado", "antes": dados.get("sha256"),
            "congelado_em": dados.get("congelado_em")}


# ---------------------------------------------------------------------------
# Rotulos humanos (humano no circuito)
# ---------------------------------------------------------------------------

def carregar_rotulos_humanos() -> dict[str, str]:
    caminho = AQUI / "rotulos-humanos.json"
    if not caminho.exists():
        return {}
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    saida = {}
    for chave, valor in (dados or {}).items():
        if isinstance(valor, dict):
            valor = valor.get("nivel")
        if valor in NIVEIS:
            saida[str(chave)] = str(valor)
    return saida


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------

def matriz(pares: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    m = {r: {p: 0 for p in NIVEIS} for r in NIVEIS}
    for esperado, obtido in pares:
        if esperado in m and obtido in m[esperado]:
            m[esperado][obtido] += 1
    return m


def metricas(pares: list[tuple[str, str]]) -> dict[str, Any]:
    total = len(pares)
    if not total:
        return {"n": 0}
    acertos = sum(1 for e, o in pares if e == o)
    por_classe: dict[str, Any] = {}
    f1s = []
    for c in NIVEIS:
        tp = sum(1 for e, o in pares if e == c and o == c)
        fp = sum(1 for e, o in pares if e != c and o == c)
        fn = sum(1 for e, o in pares if e == c and o != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        f1s.append(f1)
        por_classe[c] = {"precisao": round(prec, 3), "cobertura": round(rec, 3),
                         "f1": round(f1, 3), "n": sum(1 for e, _ in pares if e == c)}
    return {"n": total, "acerto": round(acertos / total, 4),
            "macro_f1": round(sum(f1s) / len(f1s), 4),
            "por_classe": por_classe, "matriz": matriz(pares)}


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    if len(valores) == 1:
        return float(valores[0])
    ordenado = sorted(valores)
    k = (len(ordenado) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordenado) - 1)
    return float(ordenado[lo] + (ordenado[hi] - ordenado[lo]) * (k - lo))


def resumo_amostra(valores: list[float]) -> dict[str, float]:
    if not valores:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    return {"n": len(valores), "p50": round(percentil(valores, 0.5), 1),
            "p95": round(percentil(valores, 0.95), 1),
            "min": round(min(valores), 1), "max": round(max(valores), 1),
            "media": round(statistics.fmean(valores), 1)}


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def runner_heuristica(casos: list[dict[str, Any]], *_a, **_k) -> dict[str, Any]:
    analise = _mod("analise")
    linhas = []
    for caso in casos:
        msgs = [{"role": "user", "content": caso["texto"]}]
        t0 = time.perf_counter()
        try:
            r = analise.decidir(msgs, limiares=None, janela=8, maximo=60)
            nivel = str(r.get("nivel") or "")
            erro = ""
            sinais = r.get("sinais") or []
            score = r.get("score")
        except Exception as exc:  # noqa: BLE001
            nivel, erro, sinais, score = "", f"{type(exc).__name__}: {exc}", [], None
        ms = (time.perf_counter() - t0) * 1000
        linhas.append({"id": caso["id"], "esperado": caso["esperado"], "obtido": nivel,
                       "categoria": caso["categoria"], "ms": ms, "erro": erro,
                       "sinais": sinais, "score": score})
    return {"nome": "heuristica", "linhas": linhas}


def runner_modelo(casos: list[dict[str, Any]], definicao: dict[str, Any], *,
                  repeticoes: int = 1, resolver=None) -> dict[str, Any]:
    modelo = _mod("analise_modelo")
    seams = _mod("seams")
    if resolver is None:
        resolver = seams.resolver_provider
    definicao = dict(definicao)
    definicao["cache_ttl_s"] = 0        # medir o modelo, nao a cache
    definicao["janela_mensagens"] = 1   # cada caso isolado
    try:
        modelo.limpar_cache()
    except Exception:  # noqa: BLE001
        pass
    linhas = []
    for caso in casos:
        msgs = [{"role": "user", "content": caso["texto"]}]
        obtidos, ms_lista, erros, tokens_in, tokens_out = [], [], [], 0, 0
        for _ in range(max(1, repeticoes)):
            t0 = time.perf_counter()
            try:
                r = modelo.classificar(msgs, definicao, resolver, perfil="avaliacao")
            except Exception as exc:  # noqa: BLE001
                r = None
                erros.append(f"{type(exc).__name__}: {exc}")
            ms_lista.append((time.perf_counter() - t0) * 1000)
            if r and r.get("nivel"):
                obtidos.append(str(r["nivel"]))
                clf = r.get("classificador") or {}
                tokens_out += int(clf.get("output_tokens") or 0)
                tokens_in += int(clf.get("input_tokens") or 0)
            else:
                erro = (r or {}).get("erro") or (erros[-1] if erros else "")
                erros.append(str(erro))
                obtidos.append("")
        nivel = max(set(obtidos), key=obtidos.count) if obtidos else ""
        linhas.append({
            "id": caso["id"], "esperado": caso["esperado"], "obtido": nivel,
            "categoria": caso["categoria"], "ms": statistics.fmean(ms_lista),
            "erro": erros[0] if erros else "", "tentativas": obtidos,
            "concordancia": round(obtidos.count(nivel) / len(obtidos), 3) if nivel else 0.0,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
        })
    return {"nome": f"modelo:{definicao.get('provider')}/{definicao.get('modelo')}",
            "definicao": {k: definicao.get(k) for k in
                          ("provider", "modelo", "esforco", "formato_json",
                           "max_tokens", "timeout_s")},
            "linhas": linhas}


# ---------------------------------------------------------------------------
# Variantes de registo (enviesamento de forma)
# ---------------------------------------------------------------------------

def medir_variantes(casos_grupo: list[dict[str, Any]], correr, *_a, **_k) -> dict[str, Any]:
    """Mesmo conteudo tecnico, registos diferentes -> deve dar o mesmo nivel."""
    grupos: dict[str, list[dict[str, Any]]] = {}
    for c in casos_grupo:
        grupos.setdefault(str(c["grupo"]), []).append(c)
    resultado = []
    for grupo, itens in sorted(grupos.items()):
        r = correr(itens)
        niveis = [linha["obtido"] for linha in r["linhas"]]
        resultado.append({"grupo": grupo, "niveis": niveis,
                          "consistente": len(set(n for n in niveis if n)) == 1})
    consistentes = sum(1 for g in resultado if g["consistente"])
    return {"grupos": resultado, "n": len(resultado),
            "taxa_consistencia": round(consistentes / len(resultado), 3) if resultado else 0.0}


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------

def resumir(runner: dict[str, Any], rotulos_humanos: dict[str, str]) -> dict[str, Any]:
    linhas = runner["linhas"]
    validos = [l for l in linhas if l["obtido"] in NIVEIS]
    pares = [(l["esperado"], l["obtido"]) for l in validos]
    fora = [l for l in linhas if l["obtido"] not in NIVEIS]
    resumo: dict[str, Any] = {
        "runner": runner["nome"],
        "n_casos": len(linhas),
        "n_validos": len(validos),
        "n_sem_nivel": len(fora),
        "sem_nivel_ids": [l["id"] for l in fora][:20],
        "metricas": metricas(pares),
        "latencia_ms": resumo_amostra([l["ms"] for l in linhas]),
        "custo_tokens": {
            "entrada": sum(int(l.get("tokens_in") or 0) for l in linhas),
            "saida": sum(int(l.get("tokens_out") or 0) for l in linhas),
        },
    }
    por_cat: dict[str, Any] = {}
    for cat in sorted({l["categoria"] for l in linhas}):
        sub = [(l["esperado"], l["obtido"]) for l in linhas
               if l["categoria"] == cat and l["obtido"] in NIVEIS]
        if sub:
            m = metricas(sub)
            por_cat[cat] = {"n": m["n"], "acerto": m["acerto"]}
    resumo["por_categoria"] = por_cat
    if any("concordancia" in l for l in linhas):
        conc = [l["concordancia"] for l in linhas if "concordancia" in l]
        resumo["determinismo"] = {
            "repeticoes": max((len(l.get("tentativas") or []) for l in linhas), default=1),
            "concordancia_media": round(statistics.fmean(conc) if conc else 0.0, 3),
            "casos_instaveis": [l["id"] for l in linhas
                                if "concordancia" in l and l["concordancia"] < 1.0][:20],
        }
    if rotulos_humanos:
        pares_h = [(rotulos_humanos[l["id"]], l["obtido"]) for l in validos
                   if l["id"] in rotulos_humanos]
        if pares_h:
            resumo["subconjunto_humano"] = metricas(pares_h)
    if "definicao" in runner:
        resumo["definicao"] = runner["definicao"]
    resumo["erros"] = [{"id": l["id"], "erro": l["erro"]} for l in linhas if l.get("erro")][:15]
    return resumo


def escrever_md(rel: dict[str, Any], caminho: Path) -> None:
    L = ["# Avaliacao do classificador de complexidade", "",
         f"Gerado: {rel['quando']}  ·  gold `{rel['gold']['sha256'][:16]}` "
         f"({rel['gold']['estado']}, {rel['gold']['n']} casos)", ""]
    L.append("Parametros fixos: temperatura 0, top_p 1, mesmo prompt, "
             f"max_tokens por definicao. Repeticoes: {rel['repeticoes']}.")
    L.append("")
    L.append("| runner | acerto | macro-F1 | p50 ms | p95 ms | sem nivel |")
    L.append("|---|---|---|---|---|---|")
    for r in rel["runners"]:
        m = r["metricas"]
        if not m.get("n"):
            L.append(f"| {r['runner']} | — | — | — | — | {r['n_sem_nivel']} |")
            continue
        L.append(f"| {r['runner']} | {m['acerto']*100:.1f}% | {m['macro_f1']:.3f} | "
                 f"{r['latencia_ms']['p50']:.0f} | {r['latencia_ms']['p95']:.0f} | "
                 f"{r['n_sem_nivel']} |")
    L.append("")
    for r in rel["runners"]:
        m = r["metricas"]
        if not m.get("n"):
            continue
        L.append(f"## {r['runner']}")
        if r.get("definicao"):
            d = r["definicao"]
            L.append(f"Modelo: `{d.get('provider')}/{d.get('modelo')}` · esforco "
                     f"`{d.get('esforco') or '—'}` · formato_json `{d.get('formato_json')}` "
                     f"· max_tokens `{d.get('max_tokens')}`")
        L.append("")
        L.append("| classe | precisao | cobertura | F1 | n |")
        L.append("|---|---|---|---|---|")
        for c in NIVEIS:
            pc = m["por_classe"][c]
            L.append(f"| {c} | {pc['precisao']:.2f} | {pc['cobertura']:.2f} | "
                     f"{pc['f1']:.2f} | {pc['n']} |")
        L.append("")
        L.append("Acerto por categoria: " + " · ".join(
            f"{k} {v['acerto']*100:.0f}% ({v['n']})" for k, v in r["por_categoria"].items()))
        if r.get("determinismo"):
            d = r["determinismo"]
            L.append(f"Determinismo: concordancia media {d['concordancia_media']:.2f} "
                     f"em {d['repeticoes']} repeticoes; instaveis: "
                     f"{', '.join(d['casos_instaveis']) or 'nenhum'}")
        if r.get("subconjunto_humano"):
            L.append(f"Subconjunto confirmado por humano: acerto "
                     f"{r['subconjunto_humano']['acerto']*100:.1f}% "
                     f"(n={r['subconjunto_humano']['n']})")
        L.append("")
    if rel.get("variantes"):
        for v in rel["variantes"]:
            L.append(f"Consistencia entre registos ({v['runner']}): "
                     f"{v['taxa_consistencia']*100:.0f}% em {v['n']} grupos.")
            for g in v["grupos"]:
                if not g["consistente"]:
                    L.append(f"- grupo `{g['grupo']}` divergiu: {g['niveis']}")
        L.append("")
    caminho.write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Avaliacao do classificador de complexidade")
    ap.add_argument("--gold", default=str(AQUI / "gold.jsonl"))
    ap.add_argument("--variantes", default=str(AQUI / "variantes.jsonl"))
    ap.add_argument("--runners", default="heuristica,modelo")
    ap.add_argument("--provider", default="")
    ap.add_argument("--modelo", default="")
    ap.add_argument("--esforco", default="baixo")
    ap.add_argument("--formato-json", default="auto")
    ap.add_argument("--max-tokens", type=int, default=320)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--repeticoes", type=int, default=1)
    ap.add_argument("--sem-variantes", action="store_true",
                    help="nao medir consistencia entre registos")
    ap.add_argument("--revisao", action="store_true",
                    help="escreve CSV para confirmacao humana dos rotulos")
    ap.add_argument("--afinar", action="store_true", help="recusado: o gold nao serve para afinar")
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    if args.afinar:
        print("RECUSADO: o gold set e de teste. Afinar com ele invalida-o "
              "(contaminacao). Use um conjunto separado de treino.", file=sys.stderr)
        return 2

    caminho_gold = Path(args.gold)
    if not caminho_gold.exists():
        print(f"gold inexistente: {caminho_gold}", file=sys.stderr)
        return 2
    casos = carregar_jsonl(caminho_gold)
    if not casos:
        print("gold vazio", file=sys.stderr)
        return 2
    faltam = [c["id"] for c in casos
              if c.get("esperado") not in NIVEIS or not c.get("texto") is not None
              or c.get("categoria") not in
              {"claro", "ambigua", "giria", "typo", "adversaria", "extremo", "misto"}]
    if faltam:
        print(f"casos invalidos (nivel/categoria/texto): {faltam[:10]}", file=sys.stderr)
        return 2

    lock = congelar(caminho_gold, permitir_criar=True)
    if lock["estado"] == "alterado":
        print(f"AVISO: gold alterado desde o congelamento "
              f"({lock.get('antes', '?')[:16]} -> {lock['sha256'][:16]}); "
              f"resultados anteriores nao sao comparaveis.", file=sys.stderr)

    rotulos_humanos = carregar_rotulos_humanos()
    pedidos = [r.strip() for r in args.runners.split(",") if r.strip()]

    definicao_base = {
        "provider": args.provider, "modelo": args.modelo, "esforco": args.esforco,
        "formato_json": args.formato_json, "max_tokens": args.max_tokens,
        "timeout_s": args.timeout, "janela_mensagens": 1, "max_chars": 4000,
        "cache_ttl_s": 0, "activo": True,
    }
    if "modelo" in pedidos and not (args.provider and args.modelo):
        try:
            gestor = _mod("gestor")
            cfg, _avisos = gestor.carregar()
            efetiva = gestor.classificador_efectivo(cfg, "default")
            if efetiva and efetiva.get("provider"):
                definicao_base.update({
                    "provider": efetiva["provider"], "modelo": efetiva["modelo"],
                    "esforco": efetiva.get("esforco") or args.esforco,
                    "formato_json": efetiva.get("formato_json") or args.formato_json,
                })
                print(f"definicao vinda da config ({efetiva.get('origem')}): "
                      f"{efetiva['provider']}/{efetiva['modelo']}")
        except Exception as exc:  # noqa: BLE001
            print(f"nao li a config ({type(exc).__name__}: {exc}); use --provider/--modelo",
                  file=sys.stderr)
        if not (definicao_base["provider"] and definicao_base["modelo"]):
            print("sem definicao de modelo: passe --provider e --modelo", file=sys.stderr)
            return 2

    modelo_mod = _mod("analise_modelo")
    if "modelo" in pedidos:
        definicao_base, erros_norm = modelo_mod.normalizar(definicao_base)
        if erros_norm:
            print(f"definicao invalida: {erros_norm}", file=sys.stderr)
            return 2
    else:
        definicao_base, erros_norm = modelo_mod.normalizar({**definicao_base, "activo": False})

    t_ini = time.time()
    resultados = []
    if "heuristica" in pedidos:
        print("heuristica...")
        resultados.append(runner_heuristica(casos))
    if "modelo" in pedidos:
        print(f"modelo {definicao_base['provider']}/{definicao_base['modelo']} "
              f"x{args.repeticoes}...")
        resultados.append(runner_modelo(casos, definicao_base, repeticoes=args.repeticoes))

    variantes_res = []
    caminho_variantes = Path(args.variantes)
    if not args.sem_variantes and caminho_variantes.exists():
        casos_v = carregar_jsonl(caminho_variantes)
        if casos_v:
            if "heuristica" in pedidos:
                variantes_res.append({"runner": "heuristica",
                                      **medir_variantes(casos_v, runner_heuristica)})
            if "modelo" in pedidos:
                variantes_res.append({"runner": f"modelo:{definicao_base['provider']}/"
                                                f"{definicao_base['modelo']}",
                                      **medir_variantes(
                                          casos_v,
                                          lambda cs: runner_modelo(cs, definicao_base, repeticoes=1))})

    quando = datetime.now(ZoneInfo("Europe/Lisbon")).isoformat(timespec="seconds")
    relatorio = {
        "quando": quando,
        "duracao_s": round(time.time() - t_ini, 1),
        "gold": {"sha256": sha256(caminho_gold), "estado": lock["estado"], "n": len(casos)},
        "repeticoes": args.repeticoes,
        "parametros_fixos": {"temperature": 0, "top_p": 1,
                             "prompt": "system do classificador (analise_modelo.PROMPT)"},
        "rotulos_humanos": len(rotulos_humanos),
        "runners": [resumir(r, rotulos_humanos) for r in resultados],
        "variantes": variantes_res,
    }
    for r in relatorio["runners"]:
        m = r["metricas"]
        print(f"  {r['runner']:<40} acerto {m.get('acerto', 0)*100:5.1f}%  "
              f"macroF1 {m.get('macro_f1', 0):.3f}  "
              f"p50 {r['latencia_ms']['p50']:6.0f} ms  p95 {r['latencia_ms']['p95']:6.0f} ms  "
              f"sem nivel {r['n_sem_nivel']}")
    for v in variantes_res:
        print(f"  consistencia ({v['runner']}): {v['taxa_consistencia']*100:.0f}%")

    saida_json = Path(args.json) if args.json else AQUI / "reports" / f"rel-{quando[:19].replace(':', '')}.json"
    saida_md = Path(args.md) if args.md else saida_json.with_suffix(".md")
    saida_json.parent.mkdir(parents=True, exist_ok=True)
    saida_json.write_text(json.dumps(relatorio, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    escrever_md(relatorio, saida_md)
    print(f"json: {saida_json}\nmd:   {saida_md}")

    if args.revisao:
        csv = AQUI / "revisao.csv"
        linhas = ["id,esperado,heuristica,modelo,categoria,texto"]
        h = next((r for r in resultados if r["nome"] == "heuristica"), None)
        mo = next((r for r in resultados if r["nome"].startswith("modelo")), None)
        for c in casos:
            rh = next((l["obtido"] for l in h["linhas"] if l["id"] == c["id"]), "") if h else ""
            rm = next((l["obtido"] for l in mo["linhas"] if l["id"] == c["id"]), "") if mo else ""
            texto = c["texto"].replace('"', '""')
            linhas.append(f'{c["id"]},{c["esperado"]},{rh},{rm},{c["categoria"]},"{texto}"')
        csv.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        print(f"revisao: {csv} — confirme e grave em eval/rotulos-humanos.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
