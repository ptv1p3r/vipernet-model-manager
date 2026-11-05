"""Veredicto de uma entrega, legivel por maquina.

O problema que resolve
----------------------
O efeito de uma chamada de ferramenta ja se mede (`resultado.py`: `exit_code`).
Mas o efeito *do turno* — «esta tarefa ficou feita?» — nunca foi legivel: um job
que correu, um portao que deu PASS, uma releitura independente que bateu certo,
um item fechado no Jira. Cada um desses desfechos vive na cabeca de quem fechou
a passagem, e o ledger so tem `efeito` de ferramenta. Medir por resultado exige
que o veredicto da entrega seja um registo, nao uma frase no relatorio.

O registo
---------
Uma linha `{"evento": "veredicto", ...}` no mesmo ledger append-only do plugin,
com a tarefa, o estado, a **fonte** que o afirma e a evidencia. Escreve-se pelo
CLI (`vmm veredicto --tarefa X --estado pass --fonte gate --evidencia "..."`),
que e o caminho que qualquer skill de fecho pode chamar.

Regras que o modulo impoe, e porquê
-----------------------------------
- `estado` e um de tres valores fechados: `pass`, `falha`, `parcial`. Um
  veredicto ambiguo nao serve de rotulo.
- `fonte` e obrigatoria e fechada: um PASS sem dizer quem o afirmou nao
  distingue «o portao correu» de «eu acho que esta bem». E a diferenca entre
  medido e dito.
- `evidencia` nao pode ser vazia: um veredicto sem rasto e uma opiniao.
- O mesmo turno pode ter varios veredictos (um por fonte); le-se o ultimo por
  `(tarefa, fonte)`, porque uma fonte pode corrigir-se.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

ESTADOS = ("pass", "falha", "parcial")
FONTES = (
    "gate",       # portao de pipeline / lint / validacao de plugin
    "releitura",  # releitura independente do alvo (ficheiro, HTTP, API)
    "testes",     # bateria de testes
    "ha",         # releitura de estado da instalacao Home Assistant
    "jira",       # estado do item no Jira
    "wiki",       # PASS do pipeline da wiki
    "git",        # clone independente / sha do remoto
    "skill",      # script de --self-test de uma skill
    "manual",     # afirmado pelo dono ou por quem fecha, sem prova automatica
)

LIMITE_EVIDENCIA = 500


def _iterar(caminho: Path) -> Iterable[dict]:
    try:
        ficheiro = open(caminho, encoding="utf-8", errors="replace")
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


def validar_veredicto(estado: str, fonte: str, evidencia: str) -> list[str]:
    """Devolve a lista de erros; vazia significa que pode ser registado."""
    erros = []
    if estado not in ESTADOS:
        erros.append(f"estado '{estado}' invalido (use {', '.join(ESTADOS)})")
    if fonte not in FONTES:
        erros.append(f"fonte '{fonte}' invalida (use {', '.join(FONTES)})")
    if not isinstance(evidencia, str) or not evidencia.strip():
        erros.append("evidencia vazia: um veredicto sem rasto e uma opiniao")
    elif len(evidencia) > LIMITE_EVIDENCIA:
        erros.append(f"evidencia com {len(evidencia)} chars (limite {LIMITE_EVIDENCIA})")
    return erros


def registar(
    caminho: Path,
    *,
    tarefa: str,
    estado: str,
    fonte: str,
    evidencia: str,
    detalhe: dict[str, Any] | None = None,
    perfil: str | None = None,
    sessao: str | None = None,
    turno: str | None = None,
    agora: str | None = None,
) -> dict[str, Any]:
    """Escreve o veredicto no ledger. Devolve o evento gravado.

    Levanta `ValueError` com a lista de erros se o veredicto nao for valido —
    recusar antes de escrever evita um registo que parece medido e nao e.
    """
    erros = validar_veredicto(estado, fonte, evidencia)
    if not tarefa or not isinstance(tarefa, str):
        erros.append("tarefa vazia")
    if erros:
        raise ValueError("; ".join(erros))
    if agora is None:
        from datetime import datetime, timezone

        agora = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    evento: dict[str, Any] = {
        "ts": agora,
        "evento": "veredicto",
        "tarefa": tarefa.strip()[:120],
        "estado": estado,
        "fonte": fonte,
        "evidencia": evidencia.strip()[:LIMITE_EVIDENCIA],
    }
    for chave, valor in (
        ("perfil", perfil),
        ("sessao", sessao),
        ("turno", turno),
    ):
        if valor:
            evento[chave] = valor
    if detalhe:
        evento["detalhe"] = detalhe

    caminho.parent.mkdir(parents=True, exist_ok=True)
    linha = json.dumps(evento, ensure_ascii=False) + "\n"
    fd = os.open(str(caminho), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, linha.encode("utf-8"))
    finally:
        os.close(fd)
    return evento


def veredictos(caminho: Path, *args, **kwargs) -> list[dict]:
    return [e for e in _iterar(caminho) if e.get("evento") == "veredicto"]


def ultimo_por_tarefa(caminho: Path) -> dict[str, dict]:
    """O veredicto mais recente de cada `(tarefa, fonte)`.

    Uma fonte pode corrigir-se (um gate que falha e depois passa); o que vale e
    o ultimo, e os anteriores ficam no ledger como historia, nao apagados.
    """
    ultimos: dict[tuple[str, str], dict] = {}
    for e in veredictos(caminho):
        chave = (e.get("tarefa", ""), e.get("fonte", ""))
        anterior = ultimos.get(chave)
        if anterior is None or str(e.get("ts", "")) >= str(anterior.get("ts", "")):
            ultimos[chave] = e
    return {f"{t}/{fonte}": e for (t, fonte), e in ultimos.items()}


def estado_da_tarefa(caminho: Path, tarefa: str) -> dict[str, Any]:
    """Agrega todas as fontes de uma tarefa num veredicto unico.

    `pass` so quando **todas** as fontes que falaram dizem `pass`; uma so
    `falha` derruba o agregado; sem nenhuma fonte, `sem_veredicto` — que nao e
    o mesmo que falha, e por isso tem nome proprio.
    """
    por_fonte = {
        k: v for k, v in ultimo_por_tarefa(caminho).items() if k.rsplit("/", 1)[0] == tarefa
    }
    if not por_fonte:
        return {"tarefa": tarefa, "agregado": "sem_veredicto", "fontes": {}}
    estados = {v["estado"] for v in por_fonte.values()}
    if "falha" in estados:
        agregado = "falha"
    elif "parcial" in estados:
        agregado = "parcial"
    else:
        agregado = "pass"
    return {
        "tarefa": tarefa,
        "agregado": agregado,
        "fontes": {k.rsplit("/", 1)[1]: {"estado": v["estado"], "evidencia": v["evidencia"]}
                   for k, v in por_fonte.items()},
    }


def resumo(caminho: Path) -> dict[str, Any]:
    """Contagens para o painel: quantos veredictos, por estado e por fonte."""
    todos = veredictos(caminho)
    por_estado: dict[str, int] = {}
    por_fonte: dict[str, int] = {}
    tarefas: set[str] = set()
    for e in todos:
        por_estado[e.get("estado", "?")] = por_estado.get(e.get("estado", "?"), 0) + 1
        por_fonte[e.get("fonte", "?")] = por_fonte.get(e.get("fonte", "?"), 0) + 1
        if e.get("tarefa"):
            tarefas.add(e["tarefa"])
    return {
        "veredictos": len(todos),
        "tarefas": len(tarefas),
        "por_estado": por_estado,
        "por_fonte": por_fonte,
    }
