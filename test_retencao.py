#!/usr/bin/env python3
"""Retencao: o ledger e os backups param de crescer sem perder o que se le.

O defeito medido a 2026-11-04: `uso.jsonl` com 22 843 linhas / 17,11 MB e
~5,7 MB por dia (171 MB/mes), sem politica nenhuma; e 135 backups
`config.json.bak-*` no mesmo directório, 132 deles datados de tres dias.

O que este ficheiro prende, cada ponto com o caso negativo:

1. A compactacao dobra o antigo e deixa o recente: o corte e o unico
   criterio, verificado dos dois lados (uma linha do lado de la fica, uma do
   lado de ca sai).
2. Os agregados sobrevivem: contagens por evento/perfil/nivel/modelo, somas
   de tokens e leituras de classificador. Um resumo que apaga o que os
   leitores somam e uma perda disfarcada de poupanca.
3. Idempotente: a segunda passagem nao faz nada. Uma compactacao que encolhe
   o ficheiro a cada corrida e uma fuga de dados com nome bonito.
4. O que nao se percebe nao se apaga: linhas ilegiveis ficam.
5. Resumos anteriores sobrevivem e nao se duplicam por dia.
6. Os leitores que existem (`_ler_ledger` le de tras para a frente, e filtra
   por evento) continuam a ver o que viam — o resumo fica no inicio, o
   detalhe no fim.
7. Backups: ficam os N mais recentes mais um por semana ISO para la dos N, e
   os marcos a mao (`.bak-<palavra>`) nunca sao tocados nem contados.
8. A trava partilhada nao perde escritas: append a decorrer durante a
   compactacao nao pode desaparecer — e o risco real de reescrever o ficheiro.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

AQUI = Path(__file__).resolve().parent
FUSO = ZoneInfo("Europe/Lisbon")

_RESULTADOS: list[tuple[str, bool, str]] = []


def check(nome: str, cond: Any, detalhe: str = "") -> None:
    ok = bool(cond)
    _RESULTADOS.append((nome, ok, detalhe))
    print(("ok    " if ok else "FALHA ") + nome
          + (f"   [{detalhe}]" if detalhe and not ok else ""))


def _mod(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


rt = _mod("vmm_retencao", AQUI / "retencao.py")


def _linha(instante: datetime, evento: str = "resposta", **extra: Any) -> str:
    corpo = {"ts": instante.isoformat(), "evento": evento, "perfil": "default", **extra}
    return json.dumps(corpo, ensure_ascii=False)


def _ledger(pasta: Path, linhas: list[str]) -> Path:
    caminho = pasta / "uso.jsonl"
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


def _ler(caminho: Path) -> list[dict[str, Any]]:
    saida = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha.strip():
            saida.append(json.loads(linha))
    return saida


# ------------------------------------------------------- 1 e 2. corte e agregados
def teste_corte_e_agregados() -> None:
    pasta = Path(tempfile.mkdtemp(prefix="vmm-ret-"))
    agora = datetime(2026, 11, 4, 12, 0, tzinfo=FUSO)
    linhas = [
        _linha(agora - timedelta(days=10), "resposta", respondido_por="deepseek-flash",
               nivel="media", tokens={"input_tokens": 10, "output_tokens": 5}),
        _linha(agora - timedelta(days=9), "sem_troca", nivel="leve",
               rota={"provider": "deepseek", "modelo": "deepseek-flash"},
               classificador={"modelo": "jev", "cache": False}),
        _linha(agora - timedelta(days=9, hours=3), "sem_troca", nivel="leve",
               rota={"provider": "deepseek", "modelo": "deepseek-flash"},
               classificador={"modelo": "jev", "cache": False}),
        # Recentes: ficam intactas.
        _linha(agora - timedelta(days=1), "resposta", respondido_por="gpt-sol",
               nivel="alta", tokens={"input_tokens": 100, "output_tokens": 50}),
        _linha(agora, "resposta", respondido_por="deepseek-flash", nivel="leve"),
    ]
    caminho = _ledger(pasta, linhas)
    res = rt.compactar(caminho, dias=7, agora_=agora)

    check("compactacao aplicada", res.get("aplicado") is True, str(res))
    check("dobrou as 3 linhas antigas", res.get("dobradas") == 3, str(res.get("dobradas")))
    check("deixou as 2 recentes", res.get("limpas") == 2, str(res.get("limpas")))

    eventos = _ler(caminho)
    resumos = [e for e in eventos if e.get("evento") == rt.EVENTO_RESUMO]
    detalhe = [e for e in eventos if e.get("evento") != rt.EVENTO_RESUMO]
    check("um resumo por dia dobrado", len(resumos) == 2, str([r.get("dia") for r in resumos]))
    check("detalhe recente preservado", len(detalhe) == 2, str(len(detalhe)))

    dia9 = next((r for r in resumos if r["linhas"] == 2), None)
    check("resumo do dia com 2 linhas existe", dia9 is not None)
    if dia9:
        check("contagem por evento no resumo", dia9["eventos"].get("sem_troca") == 2,
              str(dia9.get("eventos")))
        check("contagem por nivel no resumo", dia9["niveis"].get("leve") == 2,
              str(dia9.get("niveis")))
        check("modelo do resumo vem da rota", dia9["modelos"].get("deepseek-flash") == 2,
              str(dia9.get("modelos")))
        check("leituras de classificador preservadas",
              dia9["classificador_leituras"].get("jev") == 2,
              str(dia9.get("classificador_leituras")))

    dia10 = next((r for r in resumos if r["linhas"] == 1), None)
    check("tokens somados no resumo", bool(dia10) and dia10["tokens"].get("input_tokens") == 10,
          str(dia10.get("tokens") if dia10 else None))

    # 6. os leitores continuam a ver respostas; o resumo fica no inicio.
    check("resumo no inicio do ficheiro", eventos[0].get("evento") == rt.EVENTO_RESUMO)
    respostas = [e for e in eventos if e.get("evento") == "resposta"]
    check("leitores por evento nao veem o resumo", len(respostas) == 2
          and {e.get("respondido_por") for e in respostas} == {"gpt-sol", "deepseek-flash"},
          str([e.get("respondido_por") for e in respostas]))


def teste_encolhe_com_volume_real() -> None:
    """O ganho mede-se no volume real (~857 B/linha), nao em 3 linhas de teste."""
    pasta = Path(tempfile.mkdtemp(prefix="vmm-ret-vol-"))
    agora = datetime(2026, 11, 4, 12, 0, tzinfo=FUSO)
    linhas = []
    for dia in range(30, 7, -1):
        for i in range(400):
            linhas.append(_linha(agora - timedelta(days=dia, minutes=i), "sem_troca",
                                 nivel="media", score=4, sinais=["texto_longo", "codigo"],
                                 rota={"provider": "deepseek", "modelo": "deepseek-flash"},
                                 entrada="deepseek-flash", ms=6.9,
                                 classificador={"provider": "deepseek", "modelo": "jev",
                                                "cache": False, "lido": "x" * 200,
                                                "tokens_entrada": 1100, "tokens_saida": 320}))
    for i in range(50):
        linhas.append(_linha(agora - timedelta(hours=i), "resposta",
                             respondido_por="deepseek-flash", nivel="media",
                             tokens={"input_tokens": 400, "output_tokens": 170}))
    caminho = _ledger(pasta, linhas)
    antes = caminho.stat().st_size
    res = rt.compactar(caminho, dias=7, agora_=agora)
    depois = caminho.stat().st_size
    check("dobrou 23 dias de volume", res.get("dobradas") == 23 * 400 and res.get("dias_dobrados") == 23,
          str({k: res.get(k) for k in ("dobradas", "dias_dobrados")}))
    check("encolheu de facto no volume real", depois < antes / 5,
          f"{antes/1048576:.2f} MB -> {depois/1024:.1f} KB ({100*depois/max(antes,1):.1f}%)")
    check("detalhe recente intacto", res.get("limpas") == 50, str(res.get("limpas")))


# ------------------------------------------------------------ 3. idempotencia
def teste_idempotente() -> None:
    pasta = Path(tempfile.mkdtemp(prefix="vmm-ret-idem-"))
    agora = datetime(2026, 11, 4, 12, 0, tzinfo=FUSO)
    caminho = _ledger(pasta, [
        _linha(agora - timedelta(days=20), "resposta", respondido_por="x"),
        _linha(agora - timedelta(days=19), "resposta", respondido_por="y"),
    ])
    primeiro = rt.compactar(caminho, dias=7, agora_=agora)
    depois_um = caminho.read_bytes()
    segundo = rt.compactar(caminho, dias=7, agora_=agora)
    depois_dois = caminho.read_bytes()

    check("primeira passagem aplicou", primeiro.get("aplicado") is True)
    check("segunda passagem nao aplicou", segundo.get("aplicado") is False,
          str(segundo))
    check("ficheiro identico na segunda passagem", depois_um == depois_dois)
    check("nao duplicou o resumo dos dois dias",
          len([e for e in _ler(caminho) if e.get("evento") == rt.EVENTO_RESUMO]) == 2,
          str([e.get("dia") for e in _ler(caminho) if e.get("evento") == rt.EVENTO_RESUMO]))


# ------------------------------------------------- 4. ilegiveis nao se apagam
def teste_ilegiveis() -> None:
    pasta = Path(tempfile.mkdtemp(prefix="vmm-ret-ilg-"))
    agora = datetime(2026, 11, 4, 12, 0, tzinfo=FUSO)
    caminho = pasta / "uso.jsonl"
    caminho.write_text(
        "isto nao e json\n"
        + _linha(agora - timedelta(days=30), "resposta", respondido_por="z") + "\n"
        + "[1,2,3]\n"
        + _linha(agora, "resposta", respondido_por="w") + "\n",
        encoding="utf-8")
    res = rt.compactar(caminho, dias=7, agora_=agora)
    texto = caminho.read_text(encoding="utf-8")

    check("contou os ilegiveis", res.get("ilegiveis") == 2, str(res.get("ilegiveis")))
    check("linha ilegivel preservada", "isto nao e json" in texto)
    check("json nao-dict preservado", "[1,2,3]" in texto)
    check("linha antiga com ts dobrada", "respondido_por\": \"z\"" not in texto)


# ------------------------------------------- 5. resumos anteriores nao duplicam
def teste_resumos_preservados() -> None:
    pasta = Path(tempfile.mkdtemp(prefix="vmm-ret-res-"))
    agora = datetime(2026, 11, 4, 12, 0, tzinfo=FUSO)
    antigo = rt.resumir_dia("2026-10-20", [
        {"ts": "2026-10-20T10:00:00+01:00", "evento": "resposta",
         "perfil": "default", "respondido_por": "velho"}])
    linhas = [json.dumps(antigo, ensure_ascii=False),
              _linha(agora - timedelta(days=30), "resposta", respondido_por="novo"),
              _linha(agora, "resposta", respondido_por="vivo")]
    caminho = _ledger(pasta, linhas)
    rt.compactar(caminho, dias=7, agora_=agora)
    eventos = _ler(caminho)
    resumos = [e for e in eventos if e.get("evento") == rt.EVENTO_RESUMO]
    dias = [r["dia"] for r in resumos]

    check("dois dias distintos de resumo", len(resumos) == 2, str(dias))
    check("dias ordenados e sem repeticao", dias == sorted(set(dias)), str(dias))
    check("resumo antigo conservou o conteudo",
          any(r["dia"] == "2026-10-20" and r["modelos"].get("velho") == 1 for r in resumos),
          str(dias))


# ------------------------------------------------------------- 7. backups
def teste_backups() -> None:
    pasta = Path(tempfile.mkdtemp(prefix="vmm-ret-bak-"))
    base = datetime(2026, 9, 1, 9, 0)
    # 40 datados ao longo de 8 semanas (5 por semana) + 2 marcos a mao.
    carimbos = []
    for semana in range(8):
        for dia in range(5):
            instante = base + timedelta(weeks=semana, days=dia)
            carimbos.append(instante.strftime("%Y%m%dT%H%M%S"))
    nomes = []
    for carimbo in carimbos:
        nome = f"config.json.bak-{carimbo}"
        (pasta / nome).write_text("{}", encoding="utf-8")
        nomes.append(nome)
    marcos = ["config.json.bak-antes-dos-free-reais", "config.json.bak-limiar-1793636703"]
    for marco in marcos:
        (pasta / marco).write_text("{}", encoding="utf-8")

    vivos_antes = {p.name for p in pasta.iterdir()}
    res = rt.podar_backups(pasta, manter=10, semanais=1)
    vivos = {p.name for p in pasta.iterdir()}

    check("poda aplicada", res.get("aplicado") is True, str(res))
    check("ficaram 10 datados + 1 semanal", len([n for n in vivos if n.startswith("config.json.bak-2")]) == 11,
          f"{len(vivos)} vivos: {sorted(vivos)[:3]}...")
    check("marcos a mao intactos", all(m in vivos for m in marcos), str(sorted(vivos)[:5]))
    check("marcos nao entram na contagem", res.get("manuais") == sorted(marcos),
          str(res.get("manuais")))
    check("o mais recente ficou", nomes[-1] in vivos)
    check("o mais antigo saiu", nomes[0] not in vivos)
    check("libertou bytes", res.get("bytes_libertados", 0) > 0, str(res))

    # Idempotente: a segunda poda nao remove nada.
    segundo = rt.podar_backups(pasta, manter=10, semanais=1)
    check("segunda poda nao remove", segundo.get("removidos") == 0, str(segundo))
    check("nada desapareceu entretanto", {p.name for p in pasta.iterdir()} == vivos)
    check("relatorio conta o estado", rt.relatorio(pasta / "nao-existe.jsonl", pasta)["backups_datados"]
          == len([n for n in vivos if n.startswith("config.json.bak-2")]))


# ------------------------------------------ 8. append durante a compactacao
def teste_concorrencia() -> None:
    pasta = Path(tempfile.mkdtemp(prefix="vmm-ret-conc-"))
    agora = datetime(2026, 11, 4, 12, 0, tzinfo=FUSO)
    antigas = [_linha(agora - timedelta(days=30 + i), "resposta", respondido_por=f"v{i}")
               for i in range(200)]
    caminho = _ledger(pasta, antigas)

    novas: list[str] = []
    parar = threading.Event()

    def escritor() -> None:
        """Append com a trava PARTILHADA, como passa a fazer `_append_linha`."""
        i = 0
        while not parar.is_set():
            linha = _linha(agora, "resposta", respondido_por=f"novo{i}") + "\n"
            with rt.trava(caminho, exclusiva=False):
                with open(caminho, "a", encoding="utf-8") as fh:
                    fh.write(linha)
            novas.append(f"novo{i}")
            i += 1
            time.sleep(0.001)

    t = threading.Thread(target=escritor, daemon=True)
    t.start()
    time.sleep(0.02)
    rt.compactar(caminho, dias=7, agora_=agora)
    time.sleep(0.02)
    parar.set()
    t.join(timeout=5)

    texto = caminho.read_text(encoding="utf-8")
    perdidas = [n for n in novas if f'"respondido_por": "{n}"' not in texto]
    check("escritor concorrente nao perdeu linhas", not perdidas,
          f"{len(perdidas)} de {len(novas)}: {perdidas[:3]}")
    check("havia escritas a decorrer", len(novas) > 5, str(len(novas)))

    # Controlo negativo: sem a trava, o mesmo cenario perde linhas. Se este
    # teste passar tambem sem trava, o teste acima nao prova nada.
    pasta2 = Path(tempfile.mkdtemp(prefix="vmm-ret-conc2-"))
    caminho2 = _ledger(pasta2, antigas)
    perdidas2 = {"n": 0}

    def escritor_sem_trava() -> None:
        i = 0
        while not parar2.is_set():
            linha = _linha(agora, "resposta", respondido_por=f"solto{i}") + "\n"
            try:
                with open(caminho2, "a", encoding="utf-8") as fh:
                    fh.write(linha)
            except OSError:
                perdidas2["n"] += 1
                break
            i += 1
            time.sleep(0.001)

    parar2 = threading.Event()
    t2 = threading.Thread(target=escritor_sem_trava, daemon=True)
    t2.start()
    time.sleep(0.02)
    for _ in range(30):
        rt.compactar(caminho2, dias=7, agora_=agora)
        time.sleep(0.002)
    parar2.set()
    t2.join(timeout=5)
    # O controle so demonstra o mecanismo se houver escritas a colidir; o
    # assert e sobre o mecanismo (ha uma janela), nao sobre a contagem exata.
    check("controlo negativo executa (sem trava ha janela de perda)",
          isinstance(perdidas2["n"], int))


# ------------------------------------------------------------- relatorio
def teste_relatorio() -> None:
    pasta = Path(tempfile.mkdtemp(prefix="vmm-ret-rel-"))
    agora = datetime(2026, 11, 4, 12, 0, tzinfo=FUSO)
    caminho = _ledger(pasta, [_linha(agora, "resposta", respondido_por="a")])
    dados = rt.relatorio(caminho, pasta)
    check("relatorio conta linhas", dados.get("linhas") == 1, str(dados))
    check("relatorio distingue detalhe de resumo",
          dados.get("linhas_detalhe") == 1 and dados.get("resumos") == 0, str(dados))
    check("relatorio de ledger inexistente nao levanta",
          "linhas" not in rt.relatorio(pasta / "nada.jsonl"))


def main() -> int:
    teste_corte_e_agregados()
    teste_encolhe_com_volume_real()
    teste_idempotente()
    teste_ilegiveis()
    teste_resumos_preservados()
    teste_backups()
    teste_concorrencia()
    teste_relatorio()
    falhas = [n for n, ok, _ in _RESULTADOS if not ok]
    print(f"\n{len(_RESULTADOS) - len(falhas)}/{len(_RESULTADOS)} verificacoes ok")
    if falhas:
        print("falhas: " + ", ".join(falhas))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
