"""Custo por turno e por resultado, a partir do ledger do plugin.

Porque este modulo existe
-------------------------
A coluna `actual_cost_usd` do core esta a zero em todas as 1 551 sessoes do
perfil `default` (e nas 753 de `pessoal`, 657 de `trabalho`): nenhum dos
providers usados devolve custo real, so `estimated_cost_usd`, e essa estimativa
vive agregada por `(sessao, modelo)` em `session_model_usage` — sem coluna de
turno, logo nao atribuivel a um turno nem a um resultado.

O que existe e serve: cada linha de `resposta` no ledger traz o `turn_id` e o
bloco `tokens` da chamada (`input_tokens`, `output_tokens`, `prompt_tokens`,
`total_tokens`), onde `prompt_tokens` **inclui** a leitura de cache e
`input_tokens` e so a parte nao cacheada. Faltava o preco.

O preco derivado, nao copiado
-----------------------------
Nao ha aqui uma tabela escrita a mao. Os tres precos abaixo foram resolvidos
por minimos quadrados contra as 1 277 linhas de `session_model_usage` do
`deepseek-flash` que tem custo estimado (>0) — isto e, contra a mesma conta que
o core ja faz, com `cost_source = official_docs_snapshot`:

    p_in = 0.1500 USD/M   p_cache = 0.0030 USD/M   p_out = 0.6000 USD/M

O ajuste reconstroi 56.5574 USD de 56.5574 USD, 0,00% de erro absoluto em
1 277 linhas. Um ajuste perfeito significa que o custo e exactamente linear
nestas tres colunas: o preco nao e aproximado, e o mesmo que o core aplicou.

Limites, declarados
-------------------
O preco e o do `deepseek-flash`; um modelo `:free` custa 0 por definicao, mas um
modelo pago de outro provider (o `gpt-6.1-sol`, o `v4-pro`) **nao** usa esta
tabela — as suas linhas aparecem como `sem_preco` em vez de inventadas, porque
aplicar-lhe o preco do deepseek daria um numero com aspecto de medido e errado
por ordens de grandeza. `PRECOS_CONHECIDOS` e a lista fechada do que se sabe
medir; tudo o resto e declarado em falta.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

# USD por token (nao por milhao) — ver docstring para a derivacao medida.
PRECOS_CONHECIDOS: dict[str, dict[str, float]] = {
    "deepseek-flash": {
        "input": 0.1500 / 1_000_000,
        "cache_read": 0.0030 / 1_000_000,
        "output": 0.6000 / 1_000_000,
    },
}

# Modelos gratuitos: custo zero por definicao do provider, nao por preco medido.
GRATUITOS = (":free",)

SEM_PRECO = "sem_preco"


def _e_gratuito(modelo: str) -> bool:
    return any(sufixo in modelo for sufixo in GRATUITOS)


def _tokens(bloco: Any) -> dict[str, int] | None:
    """Normaliza o bloco de tokens de uma resposta do ledger.

    `prompt_tokens` inclui o cache; `input_tokens` e so a parte nao cacheada.
    A leitura de cache e a diferenca, nunca menor que zero — um provider que
    reporte `input_tokens > prompt_tokens` (ja visto em respostas truncadas)
    nao pode produzir cache negativo, que daria custo negativo.
    """
    if not isinstance(bloco, dict):
        return None
    try:
        entrada = int(bloco.get("input_tokens") or 0)
        saida = int(bloco.get("output_tokens") or 0)
        prompt = int(bloco.get("prompt_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if entrada < 0 or saida < 0 or prompt < 0:
        return None
    cache = max(0, prompt - entrada)
    return {"input": entrada, "cache_read": cache, "output": saida}


def custo_de(modelo: str, tokens: dict[str, int] | None) -> float | None:
    """Custo em USD de uma chamada.

    Devolve None em dois casos que **nao** sao o mesmo e nao podem ser
    confundidos: o modelo nao tem preco medido (`modelo_tem_preco` diz isso) ou
    a linha nao trouxe o bloco de tokens. Uma linha sem tokens e uma medicao em
    falta — subconta o turno —, nao um modelo caro. Tratar as duas como «sem
    preco» marcava turnos do `deepseek-flash` como se o preco dele fosse
    desconhecido, que e falso.
    """
    if tokens is None:
        return None
    if _e_gratuito(modelo):
        return 0.0
    preco = PRECOS_CONHECIDOS.get(modelo)
    if preco is None:
        return None
    return (
        tokens["input"] * preco["input"]
        + tokens["cache_read"] * preco["cache_read"]
        + tokens["output"] * preco["output"]
    )


def modelo_tem_preco(modelo: str) -> bool:
    """True se o custo deste modelo e calculavel (gratuito conta como sabido)."""
    return _e_gratuito(modelo) or modelo in PRECOS_CONHECIDOS


def _iterar(ledger: Path) -> Iterable[dict]:
    try:
        ficheiro = open(ledger, encoding="utf-8", errors="replace")
    except OSError:
        return
    with ficheiro:
        for linha in ficheiro:
            linha = linha.strip()
            if not linha:
                continue
            try:
                evento = json.loads(linha)
            except Exception:
                continue
            if isinstance(evento, dict):
                yield evento


def por_turno(ledger: Path) -> dict[str, dict]:
    """Agrega custo, tokens e efeito por `turn_id`.

    Devolve, por turno: `custo` (None se algum modelo do turno nao tiver preco),
    `custo_conhecido` (soma do que se sabe), `modelos`, `chamadas`, `efeitos`,
    `falhas` e `todos_com_preco`.
    """
    turnos: dict[str, dict] = {}
    for evento in _iterar(ledger):
        tipo = evento.get("evento")
        if tipo == "resposta":
            tid = evento.get("turn_id")
            modelo = evento.get("respondido_por")
            if not tid or not modelo:
                continue
            t = turnos.setdefault(
                tid,
                {
                    "turno": tid,
                    "sessao": evento.get("sessao"),
                    "perfil": evento.get("perfil"),
                    "modelos": set(),
                    "chamadas": 0,
                    "custo_conhecido": 0.0,
                    "chamadas_sem_tokens": 0,
                    "modelos_sem_preco": set(),
                    "efeitos": [],
                    "tokens": {"input": 0, "cache_read": 0, "output": 0},
                },
            )
            t["modelos"].add(modelo)
            t["chamadas"] += 1
            tok = _tokens(evento.get("tokens"))
            if tok is None:
                # Medição em falta, não modelo caro: subconta conhecida.
                t["chamadas_sem_tokens"] += 1
            else:
                for k in t["tokens"]:
                    t["tokens"][k] += tok[k]
            if not modelo_tem_preco(modelo):
                t["modelos_sem_preco"].add(modelo)
            c = custo_de(modelo, tok)
            if c is not None:
                t["custo_conhecido"] += c
        elif tipo == "efeito":
            tid = evento.get("turno")
            if tid in turnos:
                turnos[tid]["efeitos"].append(evento.get("efeito"))
    for t in turnos.values():
        # O custo total só é nulo quando há um modelo cujo preço não se mede;
        # faltar tokens é declarado à parte, no campo `chamadas_sem_tokens`.
        t["custo"] = t["custo_conhecido"] if not t["modelos_sem_preco"] else None
        t["todos_com_preco"] = not t["modelos_sem_preco"]
        t["modelos_sem_preco"] = sorted(t["modelos_sem_preco"])
        t["modelos"] = sorted(t["modelos"])
        t["falhas"] = sum(1 for e in t["efeitos"] if e == "falha")
        t["tem_efeito"] = bool(t["efeitos"])
    return turnos


def resumo(ledger: Path) -> dict:
    """Resumo para o painel e para o fecho de uma passagem."""
    turnos = por_turno(ledger)
    com_custo = [t for t in turnos.values() if t["custo"] is not None]
    com_efeito = [t for t in turnos.values() if t["tem_efeito"]]
    com_ambos = [t for t in com_efeito if t["custo"] is not None]
    custo_total = sum(t["custo"] for t in com_custo)
    ok = [t for t in com_ambos if t["falhas"] == 0]
    falha = [t for t in com_ambos if t["falhas"] > 0]
    # Custo por turno com resultado conhecido — o denominador que faltava.
    custo_por_turno_ok = sum(t["custo"] for t in ok) / len(ok) if ok else None
    custo_por_turno_falha = sum(t["custo"] for t in falha) / len(falha) if falha else None
    return {
        "turnos": len(turnos),
        "turnos_com_preco": len(com_custo),
        "turnos_com_efeito": len(com_efeito),
        "turnos_com_ambos": len(com_ambos),
        "turnos_ok": len(ok),
        "turnos_com_falha": len(falha),
        "chamadas_sem_tokens": sum(t["chamadas_sem_tokens"] for t in turnos.values()),
        "custo_total_conhecido_usd": round(custo_total, 4),
        "custo_por_turno_ok_usd": (
            round(custo_por_turno_ok, 6) if custo_por_turno_ok is not None else None
        ),
        "custo_por_turno_falha_usd": (
            round(custo_por_turno_falha, 6) if custo_por_turno_falha is not None else None
        ),
        "modelos_sem_preco": sorted(
            {m for t in turnos.values() for m in t["modelos_sem_preco"]}
        ),
    }


def custo_da_linha(evento: dict) -> float | None:
    """Custo de uma unica linha `resposta`, para uso incremental."""
    if evento.get("evento") != "resposta":
        return None
    return custo_de(evento.get("respondido_por", ""), _tokens(evento.get("tokens")))
