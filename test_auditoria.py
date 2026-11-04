#!/usr/bin/env python3
"""Auditoria de autoria: quem escreveu a config, e se o disco veio daí.

O `escritas-quem.log` nasceu de um incidente sem rasto. A premissa "nunca foi
exercitado" deixou de ser verdade a 2026-11-04: o log tem escritas reais do
painel (`api.py:1159:modelo_post`, pid do gateway) e do CLI
(`cli.py:721:cmd_superficie_add`), com a cadeia de hashes intacta.

O que continuava por provar era a outra metade: o log atribuia autoria QUANDO
havia escrita, mas uma edicao do JSON por fora nao deixava linha nenhuma e
lia-se como "ninguem escreveu". `verificar_cadeia` fecha isso comparando o sha
dos bytes vivos com o `depois_sha` da ultima linha.

Este ficheiro prova, tudo em sandbox:

1. Cadeia intacta -> OK, rc 0. Escrita por fora -> FORA, rc 1.
2. Escrita por fora ENTRE duas escritas legitimas deixa uma quebra na cadeia
   com o numero da linha: o registo denuncia o intervalo, nao so o fim.
3. Registo apagado com config presente -> FORA (nao ha como atribuir).
4. Linha corrompida conta como ilegivel e nao derruba a auditoria.
5. O tecto do log corta para as ultimas 400 linhas e nao perde a mais recente.
6. A instalacao viva continua a dar OK — a auditoria em producao nao e um
   alarme falso.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

AQUI = Path(__file__).resolve().parent
CONFIG_VIVA = Path("/opt/data/plugin-data/vipernet-model-manager/config.json")

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


_SANDBOX_ENV = tempfile.mkdtemp(prefix="vmm-aud-env-")
os.environ["VMM_DIR"] = _SANDBOX_ENV
g = _mod("vmm_g_aud", AQUI / "gestor.py")


def _nova_sandbox() -> Path:
    """Sandbox com uma escrita legitima ja feita (config + registo)."""
    d = Path(tempfile.mkdtemp(prefix="vmm-aud-"))
    g.gravar(copy.deepcopy(g.SEED), d / "config.json")
    return d


# --------------------------------------------------------- 1. estado inicial
def teste_cadeia_limpa() -> None:
    d = _nova_sandbox()
    info = g.verificar_cadeia(d / "config.json")
    check("escrita legitima: cadeia intacta", info["cadeia_continua"],
          str(info["quebras"]))
    check("escrita legitima: nao esta fora da cadeia",
          not info["fora_da_cadeia"],
          f"vivo={info['vivo_sha']} ultimo={info['ultimo_depois_sha']}")
    check("escrita legitima: vivo == ultimo depois_sha",
          info["vivo_sha"] == info["ultimo_depois_sha"])
    check("escrita legitima: uma linha no registo", info["linhas"] == 1,
          str(info["linhas"]))


# --------------------------------------------- 2. escrita por FORA do caminho
def teste_escrita_por_fora() -> None:
    d = _nova_sandbox()
    cfg = d / "config.json"
    # Edicao directa: o que um `sed`, um vim ou um script mal comportado fazem.
    bruto = json.loads(cfg.read_text(encoding="utf-8"))
    bruto["activo"] = False
    cfg.write_text(json.dumps(bruto, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    info = g.verificar_cadeia(cfg)
    check("escrita por fora e detectada", info["fora_da_cadeia"],
          f"vivo={info['vivo_sha']} ultimo={info['ultimo_depois_sha']}")
    check("escrita por fora mantem o registo intacto",
          info["linhas"] == 1 and not info["quebras"], str(info["quebras"]))
    check("escrita por fora: o vivo difere do ultimo atribuido",
          info["vivo_sha"] != info["ultimo_depois_sha"])


# --------------------------- 3. escrita por fora ENTRE duas escritas legitimas
def teste_quebra_entre_escritas() -> None:
    d = _nova_sandbox()
    cfg = d / "config.json"
    # (a) fora, (b) legitima: a segunda linha tem de denunciar o buraco.
    bruto = json.loads(cfg.read_text(encoding="utf-8"))
    bruto["activo"] = False
    cfg.write_text(json.dumps(bruto, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    g.gravar(copy.deepcopy(g.SEED), cfg)
    info = g.verificar_cadeia(cfg)
    check("a cadeia denuncia a escrita por fora no meio",
          bool(info["quebras"]), str(info["quebras"]))
    check("a quebra e apontada na linha seguinte", info["quebras"] == [2],
          str(info["quebras"]))
    check("o disco final volta a estar atribuido",
          not info["fora_da_cadeia"],
          f"vivo={info['vivo_sha']} ultimo={info['ultimo_depois_sha']}")


# ------------------------------------------------- 4. registo apagado
def teste_registo_apagado() -> None:
    d = _nova_sandbox()
    (d / "escritas-quem.log").unlink()
    info = g.verificar_cadeia(d / "config.json")
    check("registo ausente com config presente -> fora da cadeia",
          info["fora_da_cadeia"] and not info["existe"])
    d2 = Path(tempfile.mkdtemp(prefix="vmm-aud-"))
    info2 = g.verificar_cadeia(d2 / "config.json")
    check("sem config e sem registo nao e alarme",
          not info2["fora_da_cadeia"] and not info2["existe"])


# ------------------------------------------------- 5. linha corrompida
def teste_linha_corrompida() -> None:
    d = _nova_sandbox()
    with (d / "escritas-quem.log").open("a", encoding="utf-8") as fh:
        fh.write("{isto nao e json\n")
        fh.write("[1,2,3]\n")
    info = g.verificar_cadeia(d / "config.json")
    check("linhas ilegiveis sao contadas", info["ilegiveis"] == 2,
          str(info["ilegiveis"]))
    check("linhas ilegiveis nao derrubam a auditoria",
          info["linhas"] == 1 and not info["fora_da_cadeia"],
          f"linhas={info['linhas']} fora={info['fora_da_cadeia']}")


# ------------------------------------------------- 6. tecto do registo
def teste_tecto() -> None:
    d = _nova_sandbox()
    registo = d / "escritas-quem.log"
    linha = json.dumps({"ts": "2026-11-04T00:00:00+0000", "pid": 1, "ppid": 1,
                        "cmdline": "x" * 120, "pilha": [],
                        "antes_sha": "", "depois_sha": "f" * 16,
                        "antes_b": 0, "depois_b": 0})
    with registo.open("a", encoding="utf-8") as fh:
        for _ in range(1600):
            fh.write(linha + "\n")
    antes_b = registo.stat().st_size
    check("registo passou do tecto antes da poda", antes_b > g._LIMITE_LOG_AUDITORIA,
          f"{antes_b} B")
    # Uma escrita legitima dispara a poda.
    g.gravar(copy.deepcopy(g.SEED), d / "config.json")
    linhas = registo.read_text(encoding="utf-8").splitlines()
    check("registo cortado para o numero de linhas configurado",
          len(linhas) == g._LINHAS_LOG_AUDITORIA, str(len(linhas)))
    vivo = hashlib.sha256((d / "config.json").read_bytes()).hexdigest()[:16]
    ultima = json.loads(linhas[-1])
    check("a poda guarda a linha MAIS RECENTE (a da escrita que a disparou)",
          ultima.get("depois_sha") == vivo,
          f"ultima={ultima.get('depois_sha')} vivo={vivo}")
    check("a poda nao deixa a linha da escrita fora do registo",
          ultima.get("cmdline") != "x" * 120,
          str(ultima.get("cmdline"))[:60])
    info = g.verificar_cadeia(d / "config.json")
    check("depois da poda o disco continua atribuido",
          not info["fora_da_cadeia"],
          f"vivo={info['vivo_sha']} ultimo={info['ultimo_depois_sha']}")


# ------------------------------------------------- 7. producao nao e alarme falso
def teste_producao() -> None:
    if not CONFIG_VIVA.exists():
        print("      (sem config viva — salto o teste de producao)")
        return
    info = g.verificar_cadeia(CONFIG_VIVA)
    check("producao: cadeia continua", info["cadeia_continua"], str(info["quebras"]))
    check("producao: config viva veio do caminho unico",
          not info["fora_da_cadeia"],
          f"vivo={info['vivo_sha']} ultimo={info['ultimo_depois_sha']}")
    check("producao: houve escritas atribuidas", info["linhas"] >= 1,
          str(info["linhas"]))


def main() -> int:
    teste_cadeia_limpa()
    teste_escrita_por_fora()
    teste_quebra_entre_escritas()
    teste_registo_apagado()
    teste_linha_corrompida()
    teste_tecto()
    teste_producao()
    falhas = [n for n, ok, _ in _RESULTADOS if not ok]
    print(f"\n{len(_RESULTADOS) - len(falhas)}/{len(_RESULTADOS)} verificacoes ok")
    if falhas:
        print("falhas: " + ", ".join(falhas))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
