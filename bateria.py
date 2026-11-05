#!/usr/bin/env python3
"""Bateria do plugin: descobre, corre e conta — um numero, uma casa.

Antes disto os numeros vinham de um `for` escrito a mao na shell, com um
`grep` que so conhecia um dos tres formatos de saida: os testes `unittest`
("Ran 40 tests") e os do `eval/` ("15 verificacoes, 0 falhas") apareciam como
`0/0` e o total ficava a mentir por omissao. Um total que ignora ficheiros e
pior do que nenhum, porque parece medido.

Formatos reconhecidos, por ordem:
  * `Ran N tests`                     (unittest)
  * `N/M verificacoes ok`             (testes proprios)
  * `N verificacoes, M falhas`        (eval/)
  * `N/M` no fim da saida do node     (logica do painel)

`rc != 0` conta sempre como falha, mesmo que o ficheiro nao imprima contagem:
um teste que rebenta no import nao tem numeros e nao pode passar despercebido.

Uso:  python3 bateria.py [-v]
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
_PADROES = [
    (re.compile(r"Ran (\d+) tests?"), "unittest"),
    (re.compile(r"verificacoes ok:\s*(\d+)", re.IGNORECASE), "contagem"),
    (re.compile(r"(\d+) verificacoes?\s*,\s*(\d+) falhas?", re.IGNORECASE), "eval"),
    # Generico por ultimo: `18/18 (100%) | baseline 17`, `54/54 PASS`,
    # `203/203 verificacoes OK`. O ULTIMO par da saida e o total do ficheiro;
    # pares anteriores sao contagens parciais que fariam o total mentir.
    (re.compile(r"(\d+)\s*/\s*(\d+)"), "proprio"),
]


def _contar(saida: str, rc: int) -> tuple[int, int, str]:
    """Devolve (ok, total, formato)."""
    for padrao, nome in _PADROES:
        achados = padrao.findall(saida)
        if not achados:
            continue
        ultimo = achados[-1]
        if nome == "unittest":
            n = int(ultimo)
            return (n if rc == 0 else 0), n, nome
        if nome == "contagem":
            n = int(ultimo)
            return (n if rc == 0 else 0), n, nome
        if nome == "eval":
            n, m = int(ultimo[0]), int(ultimo[1])
            return n - m, n, nome
        ok, n = int(ultimo[0]), int(ultimo[1])
        if rc != 0 and ok >= n:
            ok = n - 1  # rc manda: nunca reportar verde com rc != 0
        return ok, n, nome
    return 0, 0, "-"


def main(argv: list[str]) -> int:
    verboso = "-v" in argv
    ficheiros = sorted(AQUI.glob("test_*.py")) + sorted(AQUI.glob("eval/test_*.py"))
    ficheiros = [f for f in ficheiros if f.name != "bateria.py"]
    extras: list[tuple[str, list[str]]] = []
    if (AQUI / "selftest.py").exists():
        extras.append(("selftest.py", [sys.executable, "selftest.py"]))
    logica = AQUI / "dashboard" / "test_logica.js"
    if logica.exists():
        extras.append(("dashboard/test_logica.js", ["node", str(logica)]))
    # O selftest do painel bate na API a serio (FastAPI + ficheiro temporario) e
    # le o config.json do disco. Sem esta linha, as verificacoes que provam que
    # o POST grava o que o formulario diz nunca corriam na bateria.
    api_painel = AQUI / "dashboard" / "selftest.py"
    if api_painel.exists():
        extras.append(("dashboard/selftest.py", [sys.executable, str(api_painel)]))

    total_ok = total = 0
    falhas: list[str] = []
    for ficheiro in ficheiros:
        proc = subprocess.run([sys.executable, ficheiro.name if ficheiro.parent == AQUI
                               else str(ficheiro)],
                              cwd=ficheiro.parent if ficheiro.parent != AQUI else AQUI,
                              capture_output=True, text=True)
        ok, n, formato = _contar(proc.stdout + proc.stderr, proc.returncode)
        total_ok += ok
        total += n
        if proc.returncode != 0:
            falhas.append(f"{ficheiro.relative_to(AQUI)} (rc={proc.returncode})")
        nome = str(ficheiro.relative_to(AQUI))
        print(f"  {nome:38s} {ok:5d}/{n:<5d} {formato:9s} rc={proc.returncode}")
        if verboso and proc.returncode != 0:
            print((proc.stdout + proc.stderr)[-1500:])

    for nome, cmd in extras:
        proc = subprocess.run(cmd, cwd=AQUI, capture_output=True, text=True)
        ok, n, formato = _contar(proc.stdout + proc.stderr, proc.returncode)
        total_ok += ok
        total += n
        if proc.returncode != 0:
            falhas.append(f"{nome} (rc={proc.returncode})")
        print(f"  {nome:38s} {ok:5d}/{n:<5d} {formato:9s} rc={proc.returncode}")
        if verboso and proc.returncode != 0:
            print((proc.stdout + proc.stderr)[-1500:])

    print(f"\n{len(ficheiros) + len(extras)} ficheiros   {total_ok}/{total} verificacoes")
    if falhas:
        print("FALHAS: " + ", ".join(falhas))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
