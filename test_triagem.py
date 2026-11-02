#!/usr/bin/env python3
"""Auto-teste da triagem do pedido — janela, modalidade, JSON e latencia.

A decisao por complexidade diz *quanto* raciocinio o turno pede. A triagem diz
o que ele **exige** e que nenhuma pontuacao exprime: se o texto cabe na janela,
se o modelo aceita a modalidade que o pedido traz, se a resposta tem de ser
JSON e se a latencia declarada cabe no teto. E um filtro de impossibilidade:
recusa o que a rota declarou nao conseguir, nunca promove por preferencia.

Cada regra tem o caso positivo **e** o negativo: um filtro que recusa tudo
passa num teste so de recusas, e um que aceita tudo passa num teste so de
aceitacoes.

Uso: python3 test_triagem.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

AQUI = Path(__file__).resolve().parent
_RESULTADOS: list[tuple[str, bool, str]] = []


def check(nome: str, cond: Any, detalhe: str = "") -> None:
    ok = bool(cond)
    _RESULTADOS.append((nome, ok, detalhe))
    print(("ok    " if ok else "FALHA ") + nome + (f"   [{detalhe}]" if detalhe and not ok else ""))


def _mod(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules.setdefault(nome, mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


g = _mod("vmm_gestor_tri", AQUI / "gestor.py")
a = _mod("vmm_analise_tri", AQUI / "analise.py")
plugin = _mod("vmm_plugin_tri", AQUI / "__init__.py")

# Sem cofre, sem rede: a resolucao de credenciais e substituida por uma rota
# falsa. O que se mede aqui e a *selecao*, nao a autenticacao.
_ROTAS_RESOLVIDAS: list[str] = []


def _resolver_falso(perfil: str, provider: str, modelo: str) -> dict[str, Any]:
    _ROTAS_RESOLVIDAS.append(f"{perfil}:{provider}:{modelo}")
    return {"provider": provider, "api_key": "x", "base_url": "", "api_mode": "", "source": "teste"}


plugin.resolver_rota = _resolver_falso  # type: ignore[assignment]


def cfg(catalogo: list[dict], **extra: Any) -> dict[str, Any]:
    base = {
        "activo": True,
        "perfis": {
            "default": {
                "activo": True,
                "estrategia": "prioridade",
                "catalogo": catalogo,
            }
        },
    }
    base["perfis"]["default"].update(extra)
    limpo, erros = g.validar(base)
    if erros:
        raise AssertionError(f"config de teste invalida: {erros}")
    return limpo


# --------------------------------------------------------------------- limites


def testes_limites() -> None:
    r = {
        "id": "grande", "provider": "deepseek", "modelo": "x", "complexidade": ["alta"],
        "contexto_max": 200000, "modalidades": ["texto", "imagem"],
        "json_nativo": True, "latencia_p95_ms": 1200,
    }
    c = cfg([r])
    e = c["perfis"]["default"]["catalogo"][0]
    check("limites: contexto_max normalizado", e.get("contexto_max") == 200000, str(e))
    check("limites: modalidades normalizadas", e.get("modalidades") == ["texto", "imagem"], str(e))
    check("limites: json_nativo preservado", e.get("json_nativo") is True, str(e))
    check("limites: latencia normalizada", e.get("latencia_p95_ms") == 1200, str(e))

    sem_limites = cfg([{"provider": "deepseek", "modelo": "y", "complexidade": ["leve"]}])
    e2 = sem_limites["perfis"]["default"]["catalogo"][0]
    check("limites: rota sem limites nao ganha chaves falsas",
          not any(k in e2 for k in ("contexto_max", "modalidades", "json_nativo", "latencia_p95_ms")),
          str(e2))

    for campo, valor, rotulo in (
        ("contexto_max", "muito", "texto"),
        ("contexto_max", 0, "zero"),
        ("contexto_max", -5, "negativo"),
        ("latencia_p95_ms", "1s", "texto"),
        ("modalidades", ["video"], "modalidade invalida"),
        ("json_nativo", "sim", "nao booleano"),
        ("modalidades", 5, "nao lista"),
    ):
        _, erros = g.validar({"perfis": {"default": {"catalogo": [
            {"provider": "deepseek", "modelo": "z", "complexidade": ["leve"], campo: valor}]}}})
        check(f"limites: recusa {campo}={valor!r} ({rotulo})", bool(erros), str(erros))

    # Um numero em texto e aceite: a config vem de um editor e de JSON escrito a mao.
    c2 = cfg([{"provider": "deepseek", "modelo": "w", "complexidade": ["leve"],
               "contexto_max": "128000"}])
    check("limites: contexto_max em texto vira inteiro",
          c2["perfis"]["default"]["catalogo"][0].get("contexto_max") == 128000)

    # Ida e volta pelo `validar`: o que o editor grava tem de sobreviver.
    c3 = cfg([{"provider": "deepseek", "modelo": "v", "complexidade": ["leve"]}],
             latencia_max_ms=1500)
    check("limites: latencia_max_ms do perfil sobrevive ao validar",
          c3["perfis"]["default"].get("latencia_max_ms") == 1500,
          str(c3["perfis"]["default"]))
    c4 = cfg([{"provider": "deepseek", "modelo": "u", "complexidade": ["leve"]}])
    check("limites: perfil sem latencia nao ganha a chave",
          "latencia_max_ms" not in c4["perfis"]["default"], str(c4["perfis"]["default"]))
    _, erros_lat = g.validar({"perfis": {"default": {
        "latencia_max_ms": "depressa",
        "catalogo": [{"provider": "deepseek", "modelo": "t", "complexidade": ["leve"]}]}}})
    check("limites: latencia_max_ms invalido e erro visivel", bool(erros_lat), str(erros_lat))


# ------------------------------------------------------------------- serve ou nao


def testes_rota_serve() -> None:
    completa = {"id": "c", "contexto_max": 128000, "modalidades": ["texto", "imagem"],
                "json_nativo": True, "latencia_p95_ms": 900}
    pequena = {"id": "p", "contexto_max": 32000, "modalidades": ["texto"],
               "json_nativo": False, "latencia_p95_ms": 3000}
    nua = {"id": "n"}

    check("serve: sem needs nao filtra nada", g.rota_serve(pequena, None) == "")
    check("serve: needs vazio nao filtra", g.rota_serve(pequena, {}) == "")
    check("serve: cabe na janela", g.rota_serve(completa, {"tokens_entrada": 100000}) == "")
    check("serve: nao cabe na janela",
          g.rota_serve(pequena, {"tokens_entrada": 50000}).startswith("contexto"), "")
    check("serve: rota sem janela declarada nao e recusada por janela",
          g.rota_serve(nua, {"tokens_entrada": 900000}) == "")
    check("serve: modalidade declarada cobre o pedido",
          g.rota_serve(completa, {"modalidades": ["imagem"]}) == "")
    check("serve: modalidade em falta recusa",
          g.rota_serve(pequena, {"modalidades": ["imagem"]}).startswith("sem modalidade"), "")
    check("serve: rota sem modalidades declaradas e permissiva",
          g.rota_serve(nua, {"modalidades": ["imagem"]}) == "")
    check("serve: json pedido com suporte nativo", g.rota_serve(completa, {"json": True}) == "")
    check("serve: json pedido em rota que declarou nao ter",
          g.rota_serve(pequena, {"json": True}) == "sem json nativo")
    check("serve: latencia acima do teto recusa",
          g.rota_serve(pequena, {"latencia_max_ms": 1500}).startswith("latencia"), "")
    check("serve: latencia dentro do teto aceita",
          g.rota_serve(completa, {"latencia_max_ms": 1500}) == "")
    check("teto_latencia: inteiro", g.teto_latencia({"latencia_max_ms": 1500}) == 1500)
    check("teto_latencia: texto numerico", g.teto_latencia({"latencia_max_ms": "1500"}) == 1500)
    check("teto_latencia: ausente e sem teto", g.teto_latencia({}) == 0)
    check("teto_latencia: lixo nao rebenta nem inventa teto",
          g.teto_latencia({"latencia_max_ms": "depressa"}) == 0)


# --------------------------------------------------------------- necessidades


def testes_necessidades() -> None:
    curto = a.necessidades([{"role": "user", "content": "ola, tudo bem?"}])
    check("necessidades: texto curto nao e contexto grande",
          curto["tokens_entrada"] < 10 and curto["contexto_grande"] is False, str(curto))

    longo = a.necessidades([{"role": "user", "content": "x" * 400000}])
    check("necessidades: 400k caracteres ~100k tokens",
          longo["tokens_entrada"] == 100000, str(longo))
    check("necessidades: 100k tokens e contexto grande", longo["contexto_grande"] is True, str(longo))
    check("necessidades: o limiar e configuravel",
          a.necessidades([{"role": "user", "content": "x" * 40000}],
                         limiar_contexto=10000)["contexto_grande"] is True)

    imagem = a.necessidades([{"role": "user", "content": [
        {"type": "text", "text": "o que e isto?"},
        {"type": "image_url", "image_url": {"url": "http://x/y.png"}}]}])
    check("necessidades: imagem detetada", imagem["modalidades"] == ["imagem"], str(imagem))

    audio = a.necessidades([{"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"data": "..."}}]}])
    check("necessidades: audio detetado", audio["modalidades"] == ["audio"], str(audio))

    js = a.necessidades([{"role": "user", "content": "devolve json"}],
                        {"response_format": {"type": "json_object"}})
    check("necessidades: response_format json_object deteta exigencia de JSON", js["json"] is True)

    # O negativo que importa: falar de JSON nao e exigir JSON.
    fala = a.necessidades([{"role": "user", "content": "explica-me o que e JSON"}], {})
    check("necessidades: falar de JSON nao e exigir JSON", fala["json"] is False, str(fala))


# ------------------------------------------------------------------ selecao


def testes_selecao() -> None:
    catalogo = [
        {"id": "pequeno", "provider": "deepseek", "modelo": "flash",
         "complexidade": ["alta"], "contexto_max": 32000, "modalidades": ["texto"]},
        {"id": "grande", "provider": "openrouter", "modelo": "janela",
         "complexidade": ["alta"], "contexto_max": 1000000, "modalidades": ["texto", "imagem"]},
    ]
    c = cfg(catalogo)
    p = c["perfis"]["default"]

    entrada, rota, falhadas = plugin.escolher_com_rota(
        p, "alta", perfil="default", sessao="s", needs={"tokens_entrada": 50000})
    check("selecao: salta a rota que nao cabe", entrada is not None and entrada["id"] == "grande",
          str(entrada))
    check("selecao: a razao da recusa fica registada",
          any("pequeno:inviavel:contexto" in f for f in falhadas), str(falhadas))

    entrada2, _, _ = plugin.escolher_com_rota(
        p, "alta", perfil="default", sessao="s", needs={"tokens_entrada": 1000})
    check("selecao: com tudo viavel respeita a ordem declarada",
          entrada2 is not None and entrada2["id"] == "pequeno", str(entrada2))

    # Nenhuma rota serve: devolve None com as razoes, nao uma rota impossivel.
    entrada3, rota3, falhadas3 = plugin.escolher_com_rota(
        p, "alta", perfil="default", sessao="s", needs={"modalidades": ["audio"]})
    check("selecao: sem rota viavel devolve None",
          entrada3 is None and rota3 is None, str(entrada3))
    check("selecao: falha por modalidade fica visivel",
          all("inviavel" in f for f in falhadas3), str(falhadas3))

    # Recurso de janela: o pedido que nao cabe em nenhum nivel procura a maior.
    entrada4, rota4, _ = plugin.escolher_por_janela(
        p, {"tokens_entrada": 400000}, perfil="default", sessao="s")
    check("janela: escolhe a rota de maior janela declarada",
          entrada4 is not None and entrada4["id"] == "grande", str(entrada4))

    entrada5, _, falhadas5 = plugin.escolher_por_janela(
        p, {"tokens_entrada": 4000000}, perfil="default", sessao="s")
    check("janela: nada cabe -> None com razoes",
          entrada5 is None and len(falhadas5) == 2, str(falhadas5))

    # Latencia: teto no perfil, rota rapida serve, rota lenta nao.
    c2 = cfg([
        {"id": "lenta", "provider": "deepseek", "modelo": "a", "complexidade": ["media"],
         "latencia_p95_ms": 4000},
        {"id": "rapida", "provider": "deepseek", "modelo": "b", "complexidade": ["media"],
         "latencia_p95_ms": 700},
    ])
    p2 = c2["perfis"]["default"]
    e6, _, _ = plugin.escolher_com_rota(p2, "media", perfil="default", sessao="s",
                                        needs={"latencia_max_ms": 1500})
    check("selecao: respeita o teto de latencia",
          e6 is not None and e6["id"] == "rapida", str(e6))
    e7, _, _ = plugin.escolher_com_rota(p2, "media", perfil="default", sessao="s",
                                        needs={"latencia_max_ms": 5000})
    check("selecao: com teto largo volta a ordem declarada",
          e7 is not None and e7["id"] == "lenta", str(e7))

    # O recurso de janela so pode disparar em falha de *capacidade*. Um nivel
    # vazio devolve None sem `inviavel`, e o middleware usa isso para nao
    # trocar de rota por fora da decisao de complexidade.
    e8, r8, f8 = plugin.escolher_com_rota(p2, "alta", perfil="default", sessao="s",
                                          needs={"tokens_entrada": 10})
    check("selecao: nivel sem rotas nao inventa rota", e8 is None and r8 is None, str(e8))
    check("selecao: nivel vazio nao marca inviavel (nao dispara a janela)",
          not any(":inviavel:" in f for f in f8), str(f8))


def main() -> int:
    testes_limites()
    testes_rota_serve()
    testes_necessidades()
    testes_selecao()
    falhas = [n for n, ok, _ in _RESULTADOS if not ok]
    print()
    print(f"{len(_RESULTADOS) - len(falhas)}/{len(_RESULTADOS)} PASS" if not falhas
          else f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
