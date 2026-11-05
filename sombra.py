"""Ensaio em sombra: desenho a priori e decisao por nao-inferioridade.

O problema que resolve
----------------------
A medicao passiva nao distingue dois modelos: com uma taxa base de falha de
4,63% (2 249 falhas em 48 582 chamadas de ferramenta medidas), a diferenca entre
dois modelos bons e menor do que o ruido de algumas centenas de turnos. Sem um
tamanho de amostra declarado **antes** de ver os dados, qualquer resultado serve
para justificar qualquer conclusao — e escolher a margem depois de olhar para o
numero e a forma mais facil de mentir sem mentir.

O desenho fica escrito e assinado (sha256 do proprio JSON) antes de correr. O
script de analise le o pre-registo e recusa decidir se os numeros de entrada
nao forem os do pre-registo.

Regra de decisao (nao-inferioridade)
------------------------------------
O modelo barato promove se a taxa de falha nao for pior do que a do caro por
mais do que a margem declarada, com confianca de 95% unilateral:

    promover  <=>  limite_superior(falha_barato - falha_caro) < margem

Um resultado que cruze a margem e `indeterminado`, nao `promover` — e a
diferenca entre nao provar que e pior e provar que nao e.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

Z_ALFA_95_UNILATERAL = 1.6448536269514722
Z_PODER_80 = 0.8416212335729143

LIMIARES = {"alfa": 0.05, "poder": 0.80, "unilateral": True}


def n_por_braco(p0: float, margem: float, deff: float = 1.0,
                alfa: float = 0.05, poder: float = 0.80) -> int:
    """Chamadas por braco para detectar p0 -> p0+margem.

    Formula classica de duas proporcoes com correccao de continuidade de
    Fleiss, inflacionada pelo design effect (clustering por turno).
    """
    if not (0 < p0 < 1):
        raise ValueError("p0 tem de estar em (0,1)")
    if margem <= 0:
        raise ValueError("margem tem de ser positiva")
    p1 = min(p0 + margem, 1 - 1e-9)
    z_a = Z_ALFA_95_UNILATERAL if abs(alfa - 0.05) < 1e-9 else _z(1 - alfa)
    z_b = Z_PODER_80 if abs(poder - 0.80) < 1e-9 else _z(poder)
    pbar = (p0 + p1) / 2
    n = ((z_a + z_b) ** 2 * 2 * pbar * (1 - pbar)) / ((p1 - p0) ** 2)
    return int(math.ceil(n * deff))


def _z(p: float) -> float:
    """Inversa aproximada da normal (Acklam), suficiente para 1e-9 a 0.05."""
    if p <= 0 or p >= 1:
        raise ValueError("p tem de estar em (0,1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def desenhar(
    *,
    caro: str,
    barato: str,
    p0: float,
    margem: float,
    deff: float,
    chamadas_por_dia: float,
    medida: str = "taxa de falha por chamada de ferramenta medida por exit_code",
    nota: str = "",
) -> dict[str, Any]:
    """Constroi o pre-registo; o `sha256` sela os numeros antes dos dados."""
    n = n_por_braco(p0, margem, deff)
    dias = (2 * n) / chamadas_por_dia if chamadas_por_dia > 0 else None
    registo: dict[str, Any] = {
        "ensaio": f"{barato} vs {caro}",
        "braco_a": caro,
        "braco_b": barato,
        "medida_primaria": medida,
        "p0": round(p0, 6),
        "margem_nao_inferioridade": margem,
        "alfa": LIMIARES["alfa"],
        "poder": LIMIARES["poder"],
        "unilateral": LIMIARES["unilateral"],
        "design_effect": deff,
        "n_por_braco": n,
        "n_total": 2 * n,
        "chamadas_por_dia": round(chamadas_por_dia, 1),
        "dias_estimados": round(dias, 2) if dias else None,
        "unidade_de_randomizacao": "turno",
        "regra_de_decisao": (
            "promover se o limite superior a 95% unilateral de "
            "(falha_barato - falha_caro) for inferior a margem; "
            "indeterminado se o intervalo cruzar a margem; "
            "manter se o limite inferior for superior a margem"
        ),
        "nota": nota,
    }
    canonico = json.dumps(registo, sort_keys=True, ensure_ascii=False).encode("utf-8")
    registo["sha256"] = hashlib.sha256(canonico).hexdigest()
    return registo


def verificar(registo: dict[str, Any]) -> bool:
    """O sha256 sela o pre-registo; se os numeros mudarem, deixa de bater."""
    copia = {k: v for k, v in registo.items() if k != "sha256"}
    canonico = json.dumps(copia, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonico).hexdigest() == registo.get("sha256")


def decidir(registo: dict[str, Any], *, falhas_a: int, n_a: int,
            falhas_b: int, n_b: int) -> dict[str, Any]:
    """Aplica a regra de nao-inferioridade aos resultados.

    `a` e o braco caro (referencia), `b` o barato (candidato).
    """
    if not verificar(registo):
        raise ValueError("pre-registo alterado: o sha256 não bate")
    if n_a <= 0 or n_b <= 0:
        raise ValueError("n por braço tem de ser positivo")

    pa = falhas_a / n_a
    pb = falhas_b / n_b
    dif = pb - pa
    # erro padrao da diferenca de proporcoes (Wald; declarado como aproximacao)
    se = math.sqrt(pa * (1 - pa) / n_a + pb * (1 - pb) / n_b)
    ic95_inf = dif - Z_ALFA_95_UNILATERAL * se
    ic95_sup = dif + Z_ALFA_95_UNILATERAL * se
    margem = registo["margem_nao_inferioridade"]

    if ic95_sup < margem:
        decisao = "promover"
    elif ic95_inf > margem:
        decisao = "manter"
    else:
        decisao = "indeterminado"

    amostra_suficiente = n_a >= registo["n_por_braco"] and n_b >= registo["n_por_braco"]
    if not amostra_suficiente:
        decisao = "indeterminado (amostra abaixo do pre-registado)"

    return {
        "decisao": decisao,
        "falha_a": pa,
        "falha_b": pb,
        "diferenca": dif,
        "ic95_unilateral": [ic95_inf, ic95_sup],
        "margem": margem,
        "n_a": n_a,
        "n_b": n_b,
        "amostra_suficiente": amostra_suficiente,
        "regra": registo["regra_de_decisao"],
    }


def braco_do_turno(registo: dict[str, Any], perfil: str, sessao: str,
                   turno: str) -> str:
    """Braco atribuido a um turno, deterministico e selado pelo pre-registo.

    A unidade de randomizacao e o turno (declarado em
    `unidade_de_randomizacao`), nao a chamada: as chamadas de ferramenta de um
    mesmo turno nao sao independentes (mesmo modelo, mesmo contexto), e
    randomizar por chamada misturava os dois bracos dentro do mesmo turno.

    A semente inclui o `sha256` do pre-registo, logo o mesmo turno cai no mesmo
    braco enquanto o desenho nao mudar, e um pre-registo novo (numeros
    differentes, selo differente) re-embaralha. Sem isso, um turno podia mudar
    de braco a meio de uma recolha e as contagens deixavam de fechar.
    """
    if not verificar(registo):
        raise ValueError("pre-registo alterado: o sha256 não bate")
    semente = "|".join([str(registo.get("sha256", "")), perfil or "",
                        sessao or "", turno or ""])
    h = hashlib.md5(semente.encode("utf-8")).hexdigest()
    return "a" if int(h[:8], 16) < 0x80000000 else "b"


def braco_do_modelo(registo: dict[str, Any], modelo: str) -> str | None:
    """Braco a que pertence o modelo que respondeu, ou None se for outro.

    O braco gravado e um facto, nao uma intencao: deriva-se do modelo que
    efectivamente serviu o turno e so vale se esse modelo for um dos dois do
    desenho. Marcar um turno com um braco que nao serviu era pior do que nao
    marcar nada — a analise contava como ensaio um turno que nunca o foi.
    """
    if not verificar(registo):
        raise ValueError("pre-registo alterado: o sha256 não bate")

    def _chave(valor: Any) -> str:
        return str(valor or "").strip().lower()

    alvo = _chave(modelo)
    if not alvo:
        return None
    for braco in ("a", "b"):
        declarado = _chave(registo.get(f"braco_{braco}"))
        if not declarado:
            continue
        if alvo == declarado or alvo.split("/")[-1] == declarado.split("/")[-1]:
            return braco
    return None


def veredicto_do_turno(linhas: list[dict[str, Any]]) -> str:
    """Resultado de um turno a partir das linhas de efeito das suas chamadas.

    `falha` se alguma chamada medida falhou — um turno que rebentou a meio nao
    e um bom resultado mesmo que as outras dez tenham corrido bem. `pass` so
    quando ha pelo menos uma chamada medida e todas correram. `sem_efeito`
    quando nenhuma chamada do turno e medivel (leituras, por exemplo): nao e
    sucesso nem falha, e conta-lo como sucesso inflacionava o braco que faz
    menos trabalho de shell.
    """
    medidas = [l for l in linhas if l.get("efeito") in ("ok", "falha")]
    if not medidas:
        return "sem_efeito"
    if any(l.get("efeito") == "falha" for l in medidas):
        return "falha"
    return "pass"


def recolher(linhas: list[dict[str, Any]],
             registo: dict[str, Any]) -> dict[str, Any]:
    """Agrega o ledger em contagens por braco, prontas para `decidir`.

    O braco de cada turno vem da linha `resposta` (o modelo que serviu); o
    desfecho vem das linhas `efeito` desse turno. As linhas de efeito sem braco
    — anteriores ao ensaio, ou de turnos servidos por um modelo fora do desenho
    — ficam de fora com a contagem declarada: inclui-las num dos bracos seria
    inventar atribuicao.
    """
    if not verificar(registo):
        raise ValueError("pre-registo alterado: o sha256 não bate")
    braco_por_turno: dict[str, str] = {}
    for linha in linhas:
        if linha.get("evento") != "resposta":
            continue
        braco = linha.get("braco")
        turno = linha.get("turno") or linha.get("turn_id")
        if braco in ("a", "b") and turno:
            braco_por_turno[str(turno)] = braco

    por_turno: dict[str, list[dict[str, Any]]] = {}
    sem_braco = 0
    for linha in linhas:
        if linha.get("evento") != "efeito":
            continue
        turno = str(linha.get("turno") or "")
        if turno not in braco_por_turno:
            sem_braco += 1
            continue
        por_turno.setdefault(turno, []).append(linha)

    contagem = {"a": {"n": 0, "falhas": 0, "sem_efeito": 0},
                "b": {"n": 0, "falhas": 0, "sem_efeito": 0}}
    for turno, do_turno in por_turno.items():
        braco = braco_por_turno[turno]
        veredicto = veredicto_do_turno(do_turno)
        if veredicto == "sem_efeito":
            contagem[braco]["sem_efeito"] += 1
            continue
        contagem[braco]["n"] += 1
        if veredicto == "falha":
            contagem[braco]["falhas"] += 1
    return {
        "braco_a": contagem["a"],
        "braco_b": contagem["b"],
        "turnos": len(por_turno),
        "turnos_medidos": contagem["a"]["n"] + contagem["b"]["n"],
        "turnos_marcados": len(braco_por_turno),
        "linhas_sem_braco": sem_braco,
        "n_por_braco_pedido": registo.get("n_por_braco"),
    }


def sondar_vida(registo: dict[str, Any], chamar: Any, *,
                agora: Any = None) -> dict[str, Any]:
    """Mede se cada braco do pre-registo responde; devolve o veredicto.

    `chamar(modelo)` devolve `{"ok": bool, "http": int, "motivo": str}`. A sonda
    corre **fora** do turno de proposito: dentro do middleware pagava-se em
    latencia em todos os turnos, e um 404 descoberto ai queimava o turno — foi
    exactamente o que aconteceu com um slug gratuito que a OpenRouter retirou.

    Um braco cujo slug morreu nao e um braco com mais falhas: e um braco sem
    resultado. Por isso a sonda e o que autoriza (ou nao) o ensaio a arrancar.
    """
    from datetime import datetime

    instante = agora if agora is not None else datetime.now().astimezone()
    saida: dict[str, Any] = {
        "ts": instante.isoformat(timespec="seconds"),
        "ts_epoch": float(instante.timestamp()),
        "sha_registo": str(registo.get("sha256") or ""),
        "ensaio": registo.get("ensaio"),
        "braco_a": {"modelo": registo.get("braco_a")},
        "braco_b": {"modelo": registo.get("braco_b")},
        "todos_vivos": False,
    }
    for braco in ("a", "b"):
        modelo = registo.get(f"braco_{braco}")
        try:
            resposta = chamar(modelo) or {}
        except Exception as exc:  # noqa: BLE001 - a sonda nunca parte
            resposta = {"ok": False, "http": 0,
                        "motivo": f"{type(exc).__name__}: {str(exc)[:140]}"}
        try:
            http = int(resposta.get("http") or 0)
        except (TypeError, ValueError):
            http = 0
        saida[f"braco_{braco}"].update({
            "ok": bool(resposta.get("ok")),
            "http": http,
            "motivo": str(resposta.get("motivo") or "")[:160],
        })
    saida["todos_vivos"] = all(saida[f"braco_{b}"]["ok"] for b in ("a", "b"))
    return saida


def escrever(registo: dict[str, Any], destino: Path) -> dict[str, Any]:
    """Grava o pre-registo e devolve o que ficou em disco (bytes e sha)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(registo, indent=2, ensure_ascii=False) + "\n"
    destino.write_text(texto, encoding="utf-8")
    dados = destino.read_bytes()
    return {
        "caminho": str(destino),
        "bytes": len(dados),
        "sha256_ficheiro": hashlib.sha256(dados).hexdigest(),
        "selo_valido": verificar(json.loads(destino.read_text(encoding="utf-8"))),
    }
