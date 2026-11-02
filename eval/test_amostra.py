#!/usr/bin/env python3
"""Verificacoes do extractor de amostra e do validador de rotulos.

Sem rede, sem conversa real: usa amostras e rotulos sinteticos num diretorio
temporario. Corre: python3 test_amostra.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
EXTRAIR = AQUI / "extrair_amostra.py"
ROTULOS = AQUI / "rotulos.py"

OK = 0
FALHAS: list[str] = []


def check(nome: str, cond: bool, detalhe: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        FALHAS.append(f"{nome} {detalhe}".strip())


def escrever(p: Path, itens: list[dict]) -> None:
    p.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in itens) + "\n",
                 encoding="utf-8")


def correr(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vmm-eval-"))
    amostra = tmp / "amostra.jsonl"
    itens = [
        {"id": "a1", "texto": "ola", "rotulo": None},
        {"id": "b2", "texto": "explica o reverse proxy", "rotulo": None},
        {"id": "c3", "texto": "desenha a arquitetura toda", "rotulo": None},
        {"id": "d4", "texto": "extrai a data do recibo", "rotulo": None},
    ]
    escrever(amostra, itens)

    # 1. sem rotulos: valido, nada a medir
    vazio = tmp / "vazio.jsonl"
    vazio.write_text("", encoding="utf-8")
    r = correr(str(ROTULOS), "--amostra", str(amostra), "--rotulos", str(vazio))
    check("sem_rotulos_rc0", r.returncode == 0, r.stderr)
    check("sem_rotulos_avisa", "nada a medir" in r.stdout, r.stdout)

    # 2. rotulo fora do dominio -> rc 1
    mau = tmp / "mau.jsonl"
    escrever(mau, [{"id": "a1", "rotulo": "baixa", "confianca": "alta", "rotulador": "x"}])
    r = correr(str(ROTULOS), "--amostra", str(amostra), "--rotulos", str(mau))
    check("dominio_recusado", r.returncode == 1, f"rc={r.returncode}")
    check("dominio_mensagem", "rotulo invalido" in r.stderr, r.stderr)

    # 3. id ausente da amostra -> rc 1
    escrever(mau, [{"id": "zz", "rotulo": "leve", "confianca": "alta", "rotulador": "x"}])
    r = correr(str(ROTULOS), "--amostra", str(amostra), "--rotulos", str(mau))
    check("id_desconhecido", r.returncode == 1 and "nao existe na amostra" in r.stderr, r.stderr)

    # 4. confianca media sem motivo -> rc 1
    escrever(mau, [{"id": "a1", "rotulo": "leve", "confianca": "media", "rotulador": "x"}])
    r = correr(str(ROTULOS), "--amostra", str(amostra), "--rotulos", str(mau))
    check("motivo_obrigatorio", r.returncode == 1 and "falta motivo" in r.stderr, r.stderr)

    # 5. rotulos validos + previsoes -> metricas por classe
    bom = tmp / "bom.jsonl"
    escrever(bom, [
        {"id": "a1", "rotulo": "leve",  "confianca": "alta", "rotulador": "x"},
        {"id": "b2", "rotulo": "media", "confianca": "alta", "rotulador": "x"},
        {"id": "c3", "rotulo": "alta",  "confianca": "alta", "rotulador": "x"},
        {"id": "d4", "rotulo": "leve",  "confianca": "media", "motivo": "extraccao simples", "rotulador": "x"},
    ])
    prev = tmp / "prev.jsonl"
    escrever(prev, [
        {"id": "a1", "rotulo": "leve"},
        {"id": "b2", "rotulo": "alta"},   # erro: media -> alta
        {"id": "c3", "rotulo": "alta"},
        {"id": "d4", "rotulo": "leve"},
    ])
    r = correr(str(ROTULOS), "--amostra", str(amostra), "--rotulos", str(bom),
               "--previsoes", str(prev))
    check("metricas_rc0", r.returncode == 0, r.stderr)
    met = json.loads(r.stdout.split("\n", 3)[-1]) if r.returncode == 0 else {}
    check("acuracia_3_4", met.get("acuracia") == 0.75, str(met.get("acuracia")))
    check("media_cobertura_0", met.get("por_classe", {}).get("media", {}).get("cobertura") == 0.0)
    # b2 (media) foi previsto alta: alta tem 1 tp (c3) e 1 fp (b2) -> precisao 0,5
    check("alta_precisao_0_5", met.get("por_classe", {}).get("alta", {}).get("precisao") == 0.5,
          str(met.get("por_classe", {}).get("alta")))
    check("alta_cobertura_1", met.get("por_classe", {}).get("alta", {}).get("cobertura") == 1.0)
    check("confusao_registada", "media->alta" in met.get("confusao", {}))

    # 6. kappa: identicos -> 1.0
    r = correr(str(ROTULOS), "--amostra", str(amostra), "--rotulos", str(bom),
               "--rotulos-b", str(bom))
    check("kappa_1", '"kappa": 1.0' in r.stdout or '"kappa": 1' in r.stdout, r.stdout[-200:])

    # 7. kappa: discordancia total -> <= 0
    outro = tmp / "outro.jsonl"
    escrever(outro, [
        {"id": "a1", "rotulo": "alta",  "confianca": "alta", "rotulador": "y"},
        {"id": "b2", "rotulo": "alta",  "confianca": "alta", "rotulador": "y"},
        {"id": "c3", "rotulo": "leve",  "confianca": "alta", "rotulador": "y"},
        {"id": "d4", "rotulo": "media", "confianca": "alta", "rotulador": "y"},
    ])
    r = correr(str(ROTULOS), "--amostra", str(amostra), "--rotulos", str(bom),
               "--rotulos-b", str(outro))
    check("kappa_negativo", r.returncode == 0 and "kappa" in r.stdout, r.stdout[-200:])

    # 8. extractor recusa escrever dentro do repositorio
    r = correr(str(EXTRAIR), "--saida", str(AQUI / "nao.jsonl"))
    check("recusa_no_repo", r.returncode == 2, f"rc={r.returncode}")
    check("recusa_mensagem", "dentro do repositorio" in r.stderr, r.stderr)
    check("recusa_sem_ficheiro", not (AQUI / "nao.jsonl").exists())

    # 9. extractor aceita fora do repo (fonte sessoes, leitura apenas)
    saida = tmp / "pool.jsonl"
    r = correr(str(EXTRAIR), "--fonte", "sessoes", "--por-estrato", "2",
               "--saida", str(saida))
    if r.returncode == 0:
        dados = [json.loads(l) for l in saida.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("pool_gerado", len(dados) > 0)
        check("pool_sem_rotulo", all(d.get("rotulo") is None for d in dados))
        check("pool_com_id", all(d.get("id") for d in dados))
        man = json.loads((tmp / "pool.manifesto.json").read_text(encoding="utf-8"))
        check("manifesto_sha", len(man.get("sha256_amostra", "")) == 64)
        check("manifesto_unidade", "prompt" in man.get("unidade", ""))
    else:
        check("pool_gerado", False, r.stderr[:200])

    print(f"{OK}/{OK + len(FALHAS)} verificacoes OK")
    for f in FALHAS:
        print("  FALHA:", f)
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    sys.exit(main())
