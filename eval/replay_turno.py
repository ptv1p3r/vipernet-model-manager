#!/usr/bin/env python3
"""Replay do nivel por turno sobre turnos REAIS (camada 2 de 3).

Le do `state.db` turnos verdadeiros (mensagem de utilizador -> pedidos do laco
de ferramentas), reconstroi a janela que o core enviaria em cada pedido e
passa-a pelo middleware verdadeiro, com a rota substituida (sem rede).

Compara duas execucoes dos MESMOS turnos:

  hoje  `fixar_nivel_por_turno: false` — o comportamento antes da correcao
  pin   `fixar_nivel_por_turno: true`  — o nivel fixado no primeiro pedido

E mede tres numeros:
  1. linha de base do ledger vivo (eventos `resposta` agrupados por `turn_id`);
  2. turnos instaveis no replay em modo `hoje`;
  3. turnos instaveis no replay em modo `pin`, com as excepcoes declaradas
     contadas a parte.

Criterio de aceitacao: (3) sem excepcao == 0, e (3) tem de ser < (2) quando
(2) > 0 — senao o replay nao reproduziu o defeito e nada foi provado.

Uso:
  python3 eval/replay_turno.py [--turnos 150] [--sessoes 6] [--verboso]
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

AQUI = Path(__file__).resolve().parent
PLUGIN = AQUI.parent
DB = Path("/opt/data/state.db")
LEDGER = Path("/opt/data/plugin-data/vipernet-model-manager/uso.jsonl")
LIMITE_TEXTO = 2000


def _mod(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# --------------------------------------------------------------------- dados


def linha_de_base_ledger() -> dict[str, Any]:
    """O defeito medido no ledger vivo: niveis por `turn_id` nos eventos de resposta."""
    if not LEDGER.exists():
        return {"turnos": 0, "instaveis": 0, "fonte": "ledger ausente"}
    por_turno: dict[str, set[str]] = collections.defaultdict(set)
    for linha in LEDGER.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        try:
            ev = json.loads(linha)
        except json.JSONDecodeError:
            continue
        tid = es_turno = None
        tid = ev.get("turn_id") or ev.get("turno")
        nivel = ev.get("nivel")
        if not tid or not nivel:
            continue
        es_turno = ev.get("evento") or ""
        if not str(es_turno).startswith(("resposta", "troca", "sem_")):
            continue
        por_turno[str(tid)].add(str(nivel))
    instaveis = sum(1 for v in por_turno.values() if len(v) > 1)
    return {"turnos": len(por_turno), "instaveis": instaveis,
            "pct": round(100.0 * instaveis / len(por_turno), 1) if por_turno else 0.0,
            "fonte": str(LEDGER)}


def _texto(msg: dict[str, Any]) -> str:
    bruto = msg.get("content")
    if isinstance(bruto, str):
        return bruto[:LIMITE_TEXTO]
    if isinstance(bruto, list):
        partes = []
        for bloco in bruto:
            if isinstance(bloco, dict) and bloco.get("type") == "text":
                partes.append(str(bloco.get("text") or ""))
        return "".join(partes)[:LIMITE_TEXTO]
    return ""


def turnos_do_db(*, max_sessoes: int = 6, max_turnos: int = 150,
                 min_passos: int = 2, max_passos: int = 12,
                 janela_prefixo: int = 8) -> list[dict[str, Any]]:
    """Turnos reais com pelo menos `min_passos` pedidos, com as janelas de cada um."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sessoes = [r[0] for r in con.execute(
        "select session_id from messages where compacted = 0 "
        "group by session_id order by count(*) desc limit ?", (max_sessoes,))]
    turnos: list[dict[str, Any]] = []
    for sessao in sessoes:
        linhas = list(con.execute(
            "select id, role, content, tool_name, finish_reason, token_count "
            "from messages where session_id = ? and compacted = 0 "
            "order by timestamp, id", (sessao,)))
        historico: list[dict[str, Any]] = []
        atual: list[dict[str, Any]] | None = None
        metas: list[dict[str, Any]] = []
        for linha in linhas:
            registo = dict(linha)
            msg = {"role": registo["role"], "content": _texto(registo)}
            if registo["role"] == "user":
                if atual:
                    turnos.append({"sessao": sessao, "prefixo": historico[-janela_prefixo:],
                                   "mensagens": atual, "meta": metas})
                atual = [msg]
                metas = []
            elif atual is not None:
                atual.append(msg)
                metas.append({"finish_reason": registo.get("finish_reason"),
                              "tokens": registo.get("token_count")})
            historico.append(msg)
        if atual:
            turnos.append({"sessao": sessao, "prefixo": historico[-janela_prefixo:],
                           "mensagens": atual, "meta": metas})
    con.close()

    uteis: list[dict[str, Any]] = []
    for turno in turnos:
        passos = [i for i, m in enumerate(turno["mensagens"]) if m["role"] == "assistant"]
        if min_passos <= len(passos) <= max_passos:
            uteis.append({**turno, "passos": passos, "id": f"t{len(uteis):05d}"})
        if len(uteis) >= max_turnos:
            break
    return uteis


# ------------------------------------------------------------------- replay


def _cfg(*, fixar: bool) -> dict[str, Any]:
    return {
        "activo": True,
        "registar": True,
        "analise": {"limiares": {"media": 3, "alta": 6}, "descer_apos": 2,
                    "fixar_nivel_por_turno": fixar},
        "perfis": {
            "default": {
                "activo": True, "estrategia": "prioridade",
                "catalogo": [
                    {"id": "flash", "provider": "deepseek",
                     "modelo": "deepseek-flash", "complexidade": ["leve", "media"]},
                    {"id": "grande", "provider": "openrouter",
                     "modelo": "grande/modelo-1m", "complexidade": ["leve", "media"],
                     "contexto_max": 1000000},
                    {"id": "pro", "provider": "deepseek",
                     "modelo": "deepseek-v4-pro", "complexidade": ["alta"]},
                ],
            },
        },
    }


class Replay:
    """Um plugin fresco por modo, com rota e perfil substituidos (sem rede)."""

    def __init__(self, nome: str, *, fixar: bool):
        self.dir = Path(tempfile.mkdtemp(prefix=f"vmm-replay-{nome}-"))
        caminho = self.dir / "config.json"
        caminho.write_text(json.dumps(_cfg(fixar=fixar)), encoding="utf-8")
        os.environ["VMM_DIR"] = str(self.dir)
        self.plugin = _mod(f"vmm_replay_{nome}", PLUGIN / "__init__.py")
        self.plugin.resolver_rota = lambda perfil, prov, mod: {
            "provider": prov.lower(), "api_key": "x" * 40,
            "base_url": f"https://{prov}.test/v1", "api_mode": "chat",
            "source": "replay"}
        self.plugin.gestor.perfil_atual = lambda agente=None: "default"
        self.plugin._config()
        self.plugin._recarregar_analise_se_mudou()

    def pedido(self, mensagens: list[dict[str, Any]]) -> dict[str, Any]:
        return {"model": "deepseek-flash", "messages": mensagens}

    def passo(self, mensagens, *, sessao: str, turno: str, api: int,
              meta: dict[str, Any] | None = None) -> str:
        self.plugin.middleware_llm_request(
            request=self.pedido(mensagens), session_id=sessao,
            model="deepseek-flash", provider="deepseek",
            turn_id=turno, api_call_count=api)
        # O hook de resposta entra com o `finish_reason` e os tokens REAIS que o
        # state.db guardou: e ele que alimenta o mecanismo A (subir de nivel
        # quando o anterior truncou). Sem isto o replay nao reproduz a producao.
        meta = meta or {}
        tokens = meta.get("tokens")
        self.plugin.pos_resposta(
            session_id=sessao, model="deepseek-flash", provider="deepseek",
            finish_reason=meta.get("finish_reason"), turn_id=turno,
            usage={"output_tokens": tokens} if isinstance(tokens, int) else None)
        with self.plugin._lock:
            ultimo = dict(self.plugin._ultima_decisao.get(sessao) or {})
        return str(ultimo.get("nivel") or "")

    def eventos(self) -> list[dict[str, Any]]:
        caminho = self.plugin.gestor.caminho_ledger()
        if not caminho.exists():
            return []
        saida = []
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                try:
                    saida.append(json.loads(linha))
                except json.JSONDecodeError:
                    pass
        return saida


def correr(modo: str, turnos: list[dict[str, Any]]) -> dict[str, Any]:
    replay = Replay(modo, fixar=(modo == "pin"))
    niveis: dict[str, list[str]] = {}
    for turno in turnos:
        turn_id = f"{turno['sessao']}:{turno['sessao']}:{turno['id'][-8:]}"
        vistos: list[str] = []
        anterior = list(turno["prefixo"])
        for passo, indice in enumerate(turno["passos"]):
            janela = anterior + turno["mensagens"][:indice]
            metas = turno.get("meta") or []
            meta = metas[indice - 1] if 0 <= indice - 1 < len(metas) else None
            nivel = replay.passo(janela, sessao=turno["sessao"],
                                 turno=turn_id, api=passo, meta=meta)
            vistos.append(nivel)
            anterior = janela[-8:]
        niveis[turno["id"]] = vistos
    eventos = replay.eventos()
    decisao = [e for e in eventos
               if str(e.get("evento") or "").startswith(("troca", "sem_"))]
    excepcoes = collections.Counter()
    for e in decisao:
        if e.get("excecao"):
            excepcoes[str(e["excecao"])] += 1
    return {"niveis": niveis, "eventos": decisao,
            "excepcoes": dict(excepcoes),
            "reutilizados": sum(1 for e in decisao if e.get("pin") == "reutilizado"),
            "pedidos": len(decisao), "dir": str(replay.dir)}


def resumir(nome: str, resultado: dict[str, Any]) -> dict[str, Any]:
    """Turnos com mais de um nivel, e se a variacao tinha excepcao declarada."""
    por_turno: dict[str, set[str]] = {}
    excepcao_por_turno: dict[str, set[str]] = {}
    for e in resultado["eventos"]:
        turno = str(e.get("turno") or "")
        por_turno.setdefault(turno, set()).add(str(e.get("nivel") or ""))
        if e.get("excecao"):
            excepcao_por_turno.setdefault(turno, set()).add(str(e["excecao"]))
    instaveis = [t for t, v in por_turno.items() if len(v) > 1]
    declarados = [t for t in instaveis if t in excepcao_por_turno]
    silenciosos = [t for t in instaveis if t not in excepcao_por_turno]
    total = len(por_turno)
    return {"modo": nome, "turnos": total, "instaveis": len(instaveis),
            "declarados": len(declarados), "silenciosos": len(silenciosos),
            "pct": round(100.0 * len(instaveis) / total, 1) if total else 0.0,
            "lista_silenciosos": silenciosos[:10],
            "excepcoes": resultado["excepcoes"],
            "reutilizados": resultado["reutilizados"], "pedidos": resultado["pedidos"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turnos", type=int, default=150)
    ap.add_argument("--sessoes", type=int, default=6)
    ap.add_argument("--passos-max", type=int, default=12)
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()

    base = linha_de_base_ledger()
    print(f"ledger vivo: {base['instaveis']}/{base['turnos']} turnos com mais de um "
          f"nivel ({base.get('pct', 0)}%)")

    turnos = turnos_do_db(max_sessoes=args.sessoes, max_turnos=args.turnos,
                          max_passos=args.passos_max)
    if not turnos:
        print("sem turnos utilizaveis no state.db")
        return 1
    print(f"replay: {len(turnos)} turnos reais, "
          f"{sum(len(t['passos']) for t in turnos)} pedidos reconstruidos")

    hoje = resumir("hoje", correr("hoje", turnos))
    print(f"  hoje: {hoje['instaveis']}/{hoje['turnos']} instaveis ({hoje['pct']}%)")
    pin = resumir("pin", correr("pin", turnos))
    print(f"  pin:  {pin['instaveis']}/{pin['turnos']} instaveis ({pin['pct']}%) "
          f"— {pin['declarados']} declarados, {pin['silenciosos']} silenciosos")
    print(f"  excepcoes no pin: {pin['excepcoes'] or 'nenhuma'}")
    print(f"  pin: {pin['reutilizados']}/{pin['pedidos']} pedidos reutilizaram a decisao "
          f"(esperado {pin['pedidos'] - pin['turnos']})")
    if args.verboso and pin["lista_silenciosos"]:
        print(f"  turnos silenciosos: {pin['lista_silenciosos']}")

    ok_reproduziu = hoje["instaveis"] > 0
    ok_estavel = pin["silenciosos"] == 0
    ok_aritmetica = pin["reutilizados"] == pin["pedidos"] - pin["turnos"]
    print()
    if not ok_reproduziu:
        print("AVISO: o replay em modo `hoje` nao reproduziu oscilacao nenhuma — "
              "os turnos escolhidos nao mudam de nivel, logo nao provam nada.")
    print(f"critério 1 — estavel sem excepcao: {'PASS' if ok_estavel else 'FAIL'} "
          f"({pin['silenciosos']} turnos)")
    print(f"critério 2 — decisao reutilizada nos pedidos seguintes: "
          f"{'PASS' if ok_aritmetica else 'FAIL'} "
          f"({pin['reutilizados']} vs {pin['pedidos'] - pin['turnos']})")
    print(f"critério 3 — o defeito foi reproduzido: "
          f"{'PASS' if ok_reproduziu else 'INCONCLUSIVO'} ({hoje['instaveis']} turnos)")
    veredito = ok_estavel and ok_aritmetica
    print("VEREDITO: " + ("PASS" if veredito else "FAIL"))
    return 0 if veredito else 1


if __name__ == "__main__":
    raise SystemExit(main())
