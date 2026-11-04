#!/usr/bin/env python3
"""Chaves fechadas: uma chave desconhecida e ERRO, nunca descarte silencioso.

O defeito medido a 2026-11-04, antes desta guarda: treze posicoes do config
(top, perfil, entrada de catalogo, `analise`, `analise.limiares`,
`analise.classificador`, regra de tarefa) aceitavam qualquer chave e o valor
declarado desaparecia na gravacao com ZERO erros. O utilizador julgava ter
mudado uma definicao que nunca chegou ao disco — o pior resultado possivel,
porque e silencioso e tem aspecto de sucesso. Um typo de uma letra (`cust` por
`custo`) bastava.

O que este ficheiro prende, cada ponto com o caso negativo:

1. A config viva continua LIMPA. Uma guarda que acusa a config em producao e
   um bloqueio, nao uma protecao: o primeiro teste e este, e vale mais do que
   os outros todos.
2. `validar` e idempotente: revalidar o proprio resultado nao inventa erros.
3. Os treze casos do defeito passam a produzir erro, e o erro e O erro certo —
   com o caminho exato (`catalogo[0].cust`), nao um erro qualquer que faria o
   teste passar por acidente.
4. As listas fechadas nao ficam para tras do codigo: `_CHAVES_ANALISE` tem de
   cobrir o que o laco trata e `analise_modelo.CHAVES` tem de ser o `PADRAO`
   dele. Uma chave nova acrescentada ao PADRAO entra sozinha; o teste falha se
   alguem a acrescentar so no laco.
5. A sugestao do typo so aparece quando e util. `cust` -> `custo` sim;
   `superficies` no topo NAO pode sugerir `perfis` (0,706), porque uma sugestao
   errada manda mudar a chave certa.
6. `versao` igual a 3 nao e erro, mesmo em string — falso positivo aqui
   bloqueava a config inteira.
7. Chaves legitimas sobrevivem a normalizacao com o valor intacto: e o oposto
   do defeito, e o que impede a "correcao" de virar um filtro que apaga tudo.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
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


# Sandbox: as sondas de escrita do `gestor` nao podem tocar na producao. Sem
# isto, importar o modulo com `VMM_DIR` ausente le a config viva — e uma sonda
# mal escrita ja a reescreveu duas vezes nesta instalacao.
_SANDBOX = tempfile.mkdtemp(prefix="vmm-chaves-")
os.environ["VMM_DIR"] = _SANDBOX

g = _mod("vmm_g_chaves", AQUI / "gestor.py")
am = _mod("vmm_am_chaves", AQUI / "analise_modelo.py")


def _base() -> dict[str, Any]:
    """Config de trabalho: a viva quando existe, senao a semente."""
    if CONFIG_VIVA.exists():
        try:
            bruto = json.loads(CONFIG_VIVA.read_text(encoding="utf-8-sig"))
            if isinstance(bruto, dict):
                return bruto
        except (OSError, json.JSONDecodeError):
            pass
    return copy.deepcopy(g.SEED)


# ------------------------------------------------- 1. a config viva esta limpa
def teste_viva_limpa() -> None:
    limpo, erros = g.validar(_base())
    check("config viva valida sem erros (guarda nao e bloqueio)", not erros,
          "; ".join(erros[:3]))
    check("config viva normalizada mantem os perfis",
          bool(limpo.get("perfis")), str(sorted(limpo.get("perfis") or {})))


# ------------------------------------------------------- 2. idempotencia
def teste_idempotente() -> None:
    uma, e1 = g.validar(_base())
    duas, e2 = g.validar(uma)
    check("validar e idempotente (1a passagem)", not e1, "; ".join(e1[:3]))
    check("validar e idempotente (2a passagem)", not e2, "; ".join(e2[:3]))
    check("idempotente: o resultado nao muda na segunda passagem",
          json.dumps(uma, sort_keys=True) == json.dumps(duas, sort_keys=True))


# ------------------------------------------- 3. os treze casos do defeito
def _mut(mutar) -> list[str]:
    cfg = copy.deepcopy(_base())
    mutar(cfg)
    _, erros = g.validar(cfg)
    return erros


def teste_recusa() -> None:
    primeira = lambda c: c["perfis"][sorted(c["perfis"])[0]]  # noqa: E731

    casos: list[tuple[str, Any, str]] = [
        ("top: chave no topo",
         lambda c: c.__setitem__("classificador", {"activo": False}),
         "config.classificador"),
        ("top: acento no nome",
         lambda c: c.__setitem__("perfís", {}),
         "config.perfís"),
        ("top: superficies no topo (devia ser em analise)",
         lambda c: c.__setitem__("superficies", {"cron": "x"}),
         "config.superficies"),
        ("perfil: chave de modelo no perfil",
         lambda c: primeira(c).__setitem__("modelo", "deepseek-flash"),
         ".modelo desconhecida"),
        ("perfil: latencia_max sem _ms",
         lambda c: primeira(c).__setitem__("latencia_max", 5000),
         "latencia_max desconhecida"),
        ("perfil: classificador no perfil (devia ser em .analise)",
         lambda c: primeira(c).__setitem__("classificador", {}),
         ".classificador desconhecida"),
        ("entrada: typo no custo",
         lambda c: primeira(c)["catalogo"][0].__setitem__("cust", 0.5),
         "catalogo[0].cust desconhecida"),
        ("entrada: superficie no singular",
         lambda c: primeira(c)["catalogo"][0].__setitem__("superficie", {}),
         "catalogo[0].superficie desconhecida"),
        ("analise: janela_msgs",
         lambda c: c["analise"].__setitem__("janela_msgs", 99),
         "analise.janela_msgs desconhecida"),
        ("classificador: via2",
         lambda c: (c["analise"].setdefault("classificador", {})
                    .__setitem__("via2", "x")),
         "analise.classificador.via2 desconhecida"),
        ("limiares: nivel que nao existe",
         lambda c: c["analise"].setdefault("limiares", {}).__setitem__("leve", 1),
         "analise.limiares.leve desconhecida"),
        ("tarefa: prioridade_",
         lambda c: primeira(c).setdefault("tarefas", {}).update(
             {"nova": {"termos": ["a"], "prioridade": 0, "prioridade_": 2}}),
         "tarefas.nova.prioridade_ desconhecida"),
        ("versao: ficheiro de uma versao futura",
         lambda c: c.__setitem__("versao", 4),
         "config.versao 4 nao suportada"),
    ]
    certos = 0
    for nome, mutar, esperado in casos:
        erros = _mut(mutar)
        alvo = [e for e in erros if esperado in e]
        certos += bool(alvo)
        check(f"recusa {nome}", bool(alvo),
              (erros[0] if erros else "nenhum erro")[:120])
    check("recusa os treze casos com o erro certo", certos == len(casos),
          f"{certos}/{len(casos)}")


# ------------------------------------ 4. as listas nao ficam para tras do codigo
def teste_listas_sincronizadas() -> None:
    tratadas = set(g.ANALISE_PADRAO) | {"pesos", "aprendiz", "classificador"}
    check("_CHAVES_ANALISE cobre tudo o que o laco trata",
          tratadas <= set(g._CHAVES_ANALISE),
          ", ".join(sorted(tratadas - set(g._CHAVES_ANALISE))))
    check("analise_modelo.CHAVES e o PADRAO dele",
          set(am.CHAVES) == set(am.PADRAO),
          ", ".join(sorted(set(am.PADRAO) ^ set(am.CHAVES))))
    # Toda a chave que o normalizador de entrada PRODUZ tem de ser aceite por
    # ele proprio, senao a segunda passagem acusava a config que ele acabou de
    # escrever (o teste de idempotencia apanha isto, mas a mensagem aqui e
    # direta).
    entrada = {"id": "x", "provider": "deepseek", "modelo": "deepseek-flash",
               "complexidade": ["leve"], "custo": 0.1, "ordem": 1,
               "nota": "n", "superficies": {"cron": False}}
    limpa = g._normalizar_entrada(entrada, 0, [])
    check("entrada completa passa a lista fechada",
          limpa is not None and set(entrada) <= set(g._CHAVES_ENTRADA),
          str(sorted(set(entrada) - set(g._CHAVES_ENTRADA))))


# ------------------------------------------------- 5. sugestao so quando e util
def teste_sugestao() -> None:
    erros = _mut(lambda c: primeira_mut(c).__setitem__("latencia_max", 5))
    check("typo com uma letra sugere a chave certa",
          any("queria dizer 'latencia_max_ms'" in e for e in erros),
          "; ".join(erros[:2]))
    erros = _mut(lambda c: c.__setitem__("superficies", {}))
    check("sugestao errada nao aparece (superficies nao e perfis)",
          not any("queria dizer 'perfis'" in e for e in erros),
          "; ".join(erros[:2]))
    check("sem sugestao mostra a lista aceita",
          any("aceita:" in e for e in erros), "; ".join(erros[:2]))


def primeira_mut(c):
    return c["perfis"][sorted(c["perfis"])[0]]


# --------------------------------------------------------- 6. versao tolerante
def teste_versao() -> None:
    for valor in (3, "3", None):
        cfg = copy.deepcopy(_base())
        if valor is None:
            cfg.pop("versao", None)
        else:
            cfg["versao"] = valor
        _, erros = g.validar(cfg)
        check(f"versao {valor!r} aceite", not erros, "; ".join(erros[:2]))


# ------------------------------------------- 7. chaves legitimas sobrevivem
def teste_legitimas_sobrevivem() -> None:
    cfg = copy.deepcopy(_base())
    nome = sorted(cfg["perfis"])[0]
    p = cfg["perfis"][nome]
    p["latencia_max_ms"] = 15000
    p["analise"] = {"janela_mensagens": 4}
    p["catalogo"][0]["custo"] = 0.42
    p["catalogo"][0]["ordem"] = 7
    p["catalogo"][0]["nota"] = "fica"
    cfg["analise"]["varredura_max_mensagens"] = 12
    limpo, erros = g.validar(cfg)
    check("chaves legitimas nao produzem erro", not erros, "; ".join(erros[:3]))
    check("latencia_max_ms do perfil sobrevive",
          limpo["perfis"][nome].get("latencia_max_ms") == 15000)
    check("janela_mensagens do perfil sobrevive",
          (limpo["perfis"][nome].get("analise") or {}).get("janela_mensagens") == 4)
    e = limpo["perfis"][nome]["catalogo"][0]
    check("custo/ordem/nota da entrada sobrevivem",
          (e.get("custo"), e.get("ordem"), e.get("nota")) == (0.42, 7, "fica"),
          f"{e.get('custo')!r}/{e.get('ordem')!r}/{e.get('nota')!r}")
    check("varredura_max_mensagens da analise sobrevive",
          limpo["analise"].get("varredura_max_mensagens") == 12)


def teste_nao_levanta() -> None:
    for bruto in (None, [], "texto", 3, {"analise": "x", "perfis": 7}):
        try:
            g.validar(bruto)
            ok = True
        except Exception as exc:  # noqa: BLE001 — o contrato e nunca levantar
            ok = False
            print(f"        levantou com {bruto!r}: {exc}")
        check(f"validar nao levanta com {bruto!r}", ok)


def main() -> int:
    teste_viva_limpa()
    teste_idempotente()
    teste_recusa()
    teste_listas_sincronizadas()
    teste_sugestao()
    teste_versao()
    teste_legitimas_sobrevivem()
    teste_nao_levanta()
    falhas = [n for n, ok, _ in _RESULTADOS if not ok]
    print(f"\n{len(_RESULTADOS) - len(falhas)}/{len(_RESULTADOS)} verificacoes ok")
    if falhas:
        print("falhas: " + ", ".join(falhas))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
