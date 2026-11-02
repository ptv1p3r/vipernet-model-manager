#!/usr/bin/env python3
"""Auto-teste do vipernet-model-manager — offline, sem rede, sem cofre.

Prova tres coisas que o plugin afirma e que falham em silencio:

1. **A config por perfil manda**: o mesmo nivel de complexidade leva a rotas
   diferentes em perfis diferentes, e um perfil ausente ou desligado nao e tocado.
2. **A complexidade declarada por modelo e respeitada**: uma entrada so de `alta`
   nunca e escolhida para `leve`.
3. **O CLI escreve o que o plugin le**: `vmm rota add/rm`, `on/off --perfil` e
   `perfil add/rm` produzem config que passa a validacao e muda a decisao.

Cada verificacao bloqueante tem o seu caso negativo (um config invalido tem de
ser recusado; uma rota sem credencial tem de ser saltada; um perfil desligado
tem de ficar intacto). Um teste que so passa nao prova nada.

Uso: python3 selftest.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

AQUI = Path(__file__).resolve().parent
_RESULTADOS: list[tuple[str, bool, str]] = []


def check(nome: str, cond: Any, detalhe: str = "") -> None:  # noqa: F821
    ok = bool(cond)
    _RESULTADOS.append((nome, ok, detalhe))
    print(("ok    " if ok else "FALHA ") + nome + (f"   [{detalhe}]" if detalhe and not ok else ""))


def _mod(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


g = _mod("vmm_gestor_t", AQUI / "gestor.py")
a = _mod("vmm_analise_t", AQUI / "analise.py")

_contador_mtime = [1000.0]


def _dir_com(cfg: dict | str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="vmm-t-"))
    texto = cfg if isinstance(cfg, str) else json.dumps(cfg, ensure_ascii=False)
    (d / "config.json").write_text(texto, encoding="utf-8")
    _contador_mtime[0] += 1.0
    os.utime(d / "config.json", (_contador_mtime[0], _contador_mtime[0]))
    return d


def _cfg_base() -> dict:
    return {
        "activo": True,
        "registar": True,
        "analise": {"limiares": {"media": 3, "alta": 7}, "descer_apos": 2},
        "perfis": {
            "pessoal": {
                "activo": True, "estrategia": "rodizio", "rodizio": {"por": "turno"},
                "catalogo": [
                    {"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
                     "complexidade": ["leve", "media"]},
                    {"id": "free-leve", "provider": "openrouter",
                     "modelo": "nvidia/nemotron-3-super-120b-a12b:free", "complexidade": "leve"},
                    {"id": "free-media", "provider": "openrouter",
                     "modelo": "thinkingmachines/inkling:free", "complexidade": "media"},
                ],
            },
            "trabalho": {
                "activo": True, "estrategia": "prioridade", "rodizio": {"por": "sessao"},
                "catalogo": [
                    {"id": "sol", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
                     "complexidade": "alta"},
                    {"id": "pro", "provider": "deepseek", "modelo": "deepseek-v4-pro",
                     "complexidade": "alta"},
                ],
            },
        },
    }


# =========================================================================== 1. gestor


def testes_gestor() -> None:
    print("\n== gestor: validacao e semantica ==")
    d = _dir_com(_cfg_base())
    cfg, erros = g.carregar(d / "config.json")
    check("config valido nao da erros", not erros, str(erros))
    check("dois perfis normalizados", set(cfg["perfis"]) == {"pessoal", "trabalho"})
    check("complexidade em lista mantem-se",
          cfg["perfis"]["pessoal"]["catalogo"][0]["complexidade"] == ["leve", "media"])
    check("complexidade em string vira lista",
          cfg["perfis"]["pessoal"]["catalogo"][1]["complexidade"] == ["leve"])

    # sinonimo `niveis` (catalogo v2) continua aceite
    alt = _cfg_base()
    alt["perfis"]["pessoal"]["catalogo"][0].pop("complexidade")
    alt["perfis"]["pessoal"]["catalogo"][0]["niveis"] = ["leve"]
    cfg2, erros2 = g.validar(alt)
    check("campo 'niveis' aceite como sinonimo", not erros2 and
          cfg2["perfis"]["pessoal"]["catalogo"][0]["complexidade"] == ["leve"], str(erros2))

    # negativos
    mau = _cfg_base()
    del mau["perfis"]["pessoal"]["catalogo"][0]["complexidade"]
    _, e = g.validar(mau)
    check("entrada sem complexidade recusada", any("sem complexidade" in m for m in e), str(e))

    mau = _cfg_base()
    mau["perfis"]["pessoal"]["catalogo"][0]["complexidade"] = ["altissima"]
    _, e = g.validar(mau)
    check("complexidade invalida recusada", any("invalida" in m for m in e), str(e))

    mau = _cfg_base()
    mau["perfis"]["pessoal"]["catalogo"][1]["id"] = "flash"
    _, e = g.validar(mau)
    check("id duplicado no perfil recusado", any("duplicado" in m for m in e), str(e))

    mau = _cfg_base()
    mau["perfis"]["pessoal"]["catalogo"][0]["provider"] = ""
    _, e = g.validar(mau)
    check("provider vazio recusado", any("sem provider" in m for m in e), str(e))

    mau = _cfg_base()
    mau["perfis"]["trabalho"]["estrategia"] = "aleatorio"
    _, e = g.validar(mau)
    check("estrategia invalida recusada", any("estrategia" in m for m in e), str(e))

    mau = _cfg_base()
    mau["perfis"]["trabalho"]["rodizio"] = {"por": "hora"}
    _, e = g.validar(mau)
    check("rodizio.por invalido recusado", any("rodizio" in m for m in e), str(e))

    _, e = g.validar({"perfis": "nada"})
    check("'perfis' que nao e objecto recusado", any("perfis" in m for m in e), str(e))

    # gravar recusa config invalida e nao toca no ficheiro
    d = _dir_com(_cfg_base())
    antes = (d / "config.json").read_text(encoding="utf-8")
    try:
        g.gravar({"perfis": {"x": {"catalogo": [{"provider": "p"}]}}}, d / "config.json")
        recusou = False
    except ValueError:
        recusou = True
    check("gravar recusa config invalida", recusou)
    check("ficheiro intacto apos recusa",
          (d / "config.json").read_text(encoding="utf-8") == antes)

    # gravar valido deixa backup
    cfg3 = _cfg_base()
    cfg3["perfis"]["pessoal"]["activo"] = False
    g.gravar(cfg3, d / "config.json")
    backups = list(d.glob("config.json.bak-*"))
    check("backup criado antes de gravar", len(backups) == 1, str(backups))
    relido, e = g.carregar(d / "config.json")
    check("perfil desligado persiste", relido["perfis"]["pessoal"]["activo"] is False and not e)

    # config ilegivel nao deixa o plugin sem catalogo: cai na semente
    d = _dir_com("{ isto nao e json")
    cfg4, e4 = g.carregar(d / "config.json")
    check("config ilegivel reporta erro", bool(e4))
    check("config ilegivel cai na semente (3 perfis)", len(cfg4["perfis"]) == 3)

    # wildcard
    est = _cfg_base()
    est["perfis"]["*"] = {"activo": True, "estrategia": "prioridade",
                          "catalogo": [{"id": "x", "provider": "deepseek",
                                        "modelo": "deepseek-flash", "complexidade": "leve"}]}
    cfge, _ = g.validar(est)
    pc = g.perfil_config(cfge, "novo")
    check("wildcard serve perfis nao listados", pc is not None and pc["origem_config"] == "*")
    check("perfil exacto tem prioridade sobre wildcard",
          g.perfil_config(cfge, "trabalho")["origem_config"] == "trabalho")
    check("sem wildcard, perfil desconhecido e None",
          g.perfil_config(g.validar(_cfg_base())[0], "novo") is None)

    # nomes de perfil a partir do home
    raiz = Path(tempfile.mkdtemp(prefix="vmm-root-"))
    (raiz / "profiles" / "trabalho").mkdir(parents=True)
    falso = types.SimpleNamespace(get_default_hermes_root=lambda: raiz)
    real = sys.modules.get("hermes_constants")
    sys.modules["hermes_constants"] = falso  # type: ignore[assignment]
    try:
        check("home de perfil -> nome do perfil",
              g.nome_do_perfil(raiz / "profiles" / "trabalho") == "trabalho")
        check("home de lancamento -> default", g.nome_do_perfil(raiz) == "default")
        check("home estranho -> default", g.nome_do_perfil("/tmp/outro") == "default")
    finally:
        if real is None:
            sys.modules.pop("hermes_constants", None)
        else:
            sys.modules["hermes_constants"] = real

    # semente a partir do catalogo v2
    raiz = Path(tempfile.mkdtemp(prefix="vmm-seed-"))
    antigo = raiz / "plugin-data" / "complexidade-modelo"
    antigo.mkdir(parents=True)
    (antigo / "config.json").write_text(json.dumps({
        "catalogo": [{"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
                      "niveis": ["leve", "media"]},
                     {"id": "pro", "provider": "deepseek", "modelo": "deepseek-v4-pro",
                      "niveis": ["alta"]}],
        "estrategia": "prioridade"}), encoding="utf-8")
    falso = types.SimpleNamespace(get_default_hermes_root=lambda: raiz)
    real = sys.modules.get("hermes_constants")
    sys.modules["hermes_constants"] = falso  # type: ignore[assignment]
    try:
        semeado = g._semear_de_config_antigo()
    finally:
        if real is None:
            sys.modules.pop("hermes_constants", None)
        else:
            sys.modules["hermes_constants"] = real
    check("semente aproveita o catalogo v2 no perfil default",
          [r["id"] for r in semeado["perfis"]["default"]["catalogo"]] == ["flash", "pro"])
    check("semente v2 traduz niveis->complexidade",
          semeado["perfis"]["default"]["catalogo"][1]["complexidade"] == ["alta"])
    check("semente mantem pessoal e trabalho",
          {"pessoal", "trabalho"} <= set(semeado["perfis"]))

    # rodizio e histerese
    catalogo = g.catalogo_utilizavel(g.validar(_cfg_base())[0]["perfis"]["pessoal"])
    lista = g.candidatos(catalogo, "leve")
    check("candidatos respeitam a complexidade declarada",
          [c["id"] for c in lista] == ["flash", "free-leve"], str([c["id"] for c in lista]))
    check("nivel sem candidatos devolve lista vazia", g.candidatos(catalogo, "alta") == [])
    check("entrada desligada nao e utilizavel",
          [c["id"] for c in g.catalogo_utilizavel({"catalogo": [
              {"id": "a", "complexidade": ["leve"], "desligado": True}]})] == [])

    cont: dict[str, int] = {}
    seq = [g.indice_rodizio("k", 3, por="turno", perfil="p", sessao="s",
                            nivel="leve", contadores=cont) for _ in range(4)]
    check("rodizio por turno alterna 0,1,2,0", seq == [0, 1, 2, 0], str(seq))
    est = [g.indice_rodizio("k", 3, por="sessao", perfil="p", sessao="s",
                            nivel="leve", contadores={}) for _ in range(3)]
    check("rodizio por sessao e estavel", len(set(est)) == 1, str(est))
    dif = {g.indice_rodizio("k", 3, por="sessao", perfil="p", sessao=f"s{i}",
                            nivel="leve", contadores={}) for i in range(12)}
    check("rodizio por sessao distribui por sessoes diferentes", len(dif) > 1, str(dif))

    # acesso: inferencia retrocompativel e validacao de incompatibilidades
    inf = g.validar(_cfg_base())[0]
    pc = inf["perfis"]["pessoal"]["catalogo"]
    check("acesso inferido: deepseek -> api", pc[0]["acesso"] == "api")
    check("acesso inferido: openrouter -> api", pc[1]["acesso"] == "api")

    sub = _cfg_base()
    sub["perfis"]["pessoal"]["catalogo"].append(
        {"id": "sol-sub", "provider": "openai-codex", "modelo": "gpt-6.1-sol",
         "complexidade": ["media"]})
    gv2, e2 = g.validar(sub)
    check("acesso inferido: openai-codex -> subscricao",
          gv2["perfis"]["pessoal"]["catalogo"][-1]["acesso"] == "subscricao", str(e2))

    exp = _cfg_base()
    exp["perfis"]["pessoal"]["catalogo"][0]["acesso"] = "subscricao"
    _, e = g.validar(exp)
    check("subscricao com provider nao codex recusada",
          any("exige provider openai-codex" in m for m in e), str(e))

    exp = _cfg_base()
    exp["perfis"]["pessoal"]["catalogo"].append(
        {"id": "codex-api", "provider": "openai-codex", "modelo": "gpt-6.1-sol",
         "complexidade": ["media"], "acesso": "api"})
    _, e = g.validar(exp)
    check("api com provider openai-codex recusada",
          any("use acesso 'subscricao'" in m for m in e), str(e))

    exp = _cfg_base()
    exp["perfis"]["pessoal"]["catalogo"][0]["acesso"] = "satelite"
    _, e = g.validar(exp)
    check("acesso invalido recusado", any("acesso invalido" in m for m in e), str(e))

    exp = _cfg_base()
    exp["perfis"]["pessoal"]["catalogo"][0]["acesso"] = "api"
    gv3, _ = g.validar(exp)
    check("acesso explicito nao reescreve provider/modelo",
          gv3["perfis"]["pessoal"]["catalogo"][0]["provider"] == "deepseek"
          and gv3["perfis"]["pessoal"]["catalogo"][0]["modelo"] == "deepseek-flash")

    # casa_do_perfil: o inverso de nome_do_perfil
    raiz = Path(tempfile.mkdtemp(prefix="vmm-casa-"))
    falso = types.SimpleNamespace(get_default_hermes_root=lambda: raiz)
    real = sys.modules.get("hermes_constants")
    sys.modules["hermes_constants"] = falso  # type: ignore[assignment]
    try:
        check("casa_do_perfil default -> raiz", g.casa_do_perfil("default") == raiz)
        check("casa_do_perfil trabalho -> profiles/trabalho",
              g.casa_do_perfil("trabalho") == raiz / "profiles" / "trabalho")
        check("casa_do_perfil e o inverso de nome_do_perfil",
              g.nome_do_perfil(g.casa_do_perfil("pessoal")) == "pessoal")
    finally:
        if real is None:
            sys.modules.pop("hermes_constants", None)
        else:
            sys.modules["hermes_constants"] = real


# =========================================================================== 2. plugin


def testes_plugin() -> None:
    print("\n== plugin: middleware e decisao por perfil ==")
    plugin = _mod("vmm_plugin_t", AQUI / "__init__.py")
    dir_atual = _dir_com(_cfg_base())
    os.environ["VMM_DIR"] = str(dir_atual)

    class FakeAgent:
        def __init__(self, provider="deepseek", model="deepseek-flash", sid=""):
            self.provider = provider
            self.model = model
            self.session_id = sid
            self.chamadas: list[dict] = []

        def switch_model(self, **kw):
            self.chamadas.append(kw)
            self.model = kw.get("new_model")
            self.provider = kw.get("new_provider")

    def chamar(agent=None, request=None, session_id="", provider="deepseek",
               model="deepseek-flash"):
        def _w(agent=None, request=None, session_id="", provider="", model=""):
            return plugin.middleware_llm_request(
                request=request, session_id=session_id, model=model, provider=provider)
        return _w(agent=agent, request=request, session_id=session_id,
                  provider=provider, model=model)

    def pedido(texto: str, modelo="deepseek-flash") -> dict:
        return {"model": modelo, "messages": [{"role": "user", "content": texto}]}

    rotas_reais = plugin.resolver_rota
    perfis_reais = plugin.gestor.perfil_atual
    try:
        plugin.resolver_rota = lambda perfil, prov, mod: {
            "provider": prov.lower(), "api_key": "x" * 40,
            "base_url": f"https://{prov}.test/v1", "api_mode": "chat",
            "source": f"env:{perfil}"}

        # 1. perfil desconhecido -> intacto
        plugin.gestor.perfil_atual = lambda agente=None: "estranho"
        ag = FakeAgent(sid="s1")
        r = chamar(agent=ag, request=pedido("Implementa isto. complexidade: alta"), session_id="s1")
        check("perfil ausente da config: pedido intacto", r is None and not ag.chamadas)
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        check("perfil ausente fica registado",
              any(l.get("evento") == "perfil_nao_gerido" and l.get("perfil") == "estranho"
                  for l in linhas), str(linhas[-3:]))

        # 2. perfil desligado -> intacto
        cfg = _cfg_base()
        cfg["perfis"]["pessoal"]["activo"] = False
        os.environ["VMM_DIR"] = str(_dir_com(cfg))
        plugin.gestor.perfil_atual = lambda agente=None: "pessoal"
        ag = FakeAgent(sid="s2")
        r = chamar(agent=ag, request=pedido("complexidade: alta"), session_id="s2")
        check("perfil desligado: pedido intacto", r is None and not ag.chamadas)
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        check("perfil desligado fica registado",
              any(l.get("evento") == "perfil_desligado" for l in linhas))

        # 3. global desligado -> intacto
        cfg = _cfg_base()
        cfg["activo"] = False
        os.environ["VMM_DIR"] = str(_dir_com(cfg))
        ag = FakeAgent(sid="s3")
        r = chamar(agent=ag, request=pedido("complexidade: alta"), session_id="s3")
        check("interruptor global: pedido intacto", r is None and not ag.chamadas)
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        check("interruptor global fica registado",
              any(l.get("evento") == "desligado" for l in linhas))

        # 4. perfil gerido: nivel alta nao tem rota em pessoal -> desce para media (C)
        os.environ["VMM_DIR"] = str(_dir_com(_cfg_base()))
        ag = FakeAgent(sid="s4")
        r = chamar(agent=ag, request=pedido("complexidade: alta"), session_id="s4")
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        degradou = [l for l in linhas if l.get("degradado_de") == "alta"
                    and l.get("perfil") == "pessoal"]
        check("sem rota no nivel: degrada para o nivel abaixo em vez de desistir",
              bool(degradou), str(linhas[-3:]))
        check("degradacao regista o nivel efectivo",
              bool(degradou) and degradou[-1].get("nivel_efetivo") in {"media", "leve"},
              str(degradou[-1] if degradou else None))
        check("cada decisao regista o motor (versao do manifesto)",
              bool(linhas) and all(l.get("motor") == plugin._VERSAO_MOTOR for l in linhas
                                   if l.get("evento") not in {"resposta"})
              and bool(plugin._VERSAO_MOTOR), str(linhas[-1]))

        # 4b. triagem viva: contexto acima do limiar deixa rasto mesmo com nivel alta
        os.environ["VMM_DIR"] = str(_dir_com(_cfg_base()))
        plugin.gestor.perfil_atual = lambda agente=None: "trabalho"
        ag = FakeAgent(sid="s5")
        chamar(agent=ag, request=pedido("x" * 120000 + " complexidade: alta"), session_id="s5")
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        tri = [l for l in linhas if l.get("triagem") and l.get("perfil") == "trabalho"]
        check("contexto acima do limiar fica registado na triagem", bool(tri), str(linhas[-2:]))
        check("triagem nao altera um nivel que ja era alto",
              bool(tri) and tri[-1].get("nivel") == "alta", str(tri[-1] if tri else None))
        plugin.gestor.perfil_atual = lambda agente=None: "pessoal"  # repoe para o teste 5

        # 5. pessoal / leve -> uma das duas rotas de leve, com o perfil certo
        #    Agente com provider/modelo fora do catalogo: a troca tem de acontecer.
        ag = FakeAgent(sid="s5", provider="openai", model="gpt-x")
        r = chamar(agent=ag, request=pedido("complexidade: leve", modelo="gpt-x"),
                   session_id="s5", provider="openai", model="gpt-x")
        check("pessoal leve troca de modelo", len(ag.chamadas) == 1,
              str(ag.chamadas))
        escolhido = ag.chamadas[0] if ag.chamadas else {}
        check("pessoal leve so escolhe entradas de leve",
              escolhido.get("new_model") in {"deepseek-flash",
                                             "nvidia/nemotron-3-super-120b-a12b:free"},
              str(escolhido))
        check("troca leva provider, chave, base_url e api_mode",
              set(escolhido) >= {"new_model", "new_provider", "api_key", "base_url", "api_mode"},
              str(sorted(escolhido)))
        check("troca usa a credencial do perfil",
              plugin.resolver_rota and escolhido.get("api_key") == "x" * 40)

        # 6. trabalho / alta -> pro ou sol (catalogo diferente do pessoal)
        plugin.gestor.perfil_atual = lambda agente=None: "trabalho"
        ag = FakeAgent(sid="s6", provider="deepseek", model="deepseek-flash")
        r = chamar(agent=ag, request=pedido("complexidade: alta"), session_id="s6")
        check("trabalho alta troca de modelo", len(ag.chamadas) == 1, str(ag.chamadas))
        check("trabalho alta escolhe pro ou sol",
              ag.model in {"deepseek-v4-pro", "openai/gpt-6.1-sol"}, ag.model)

        # 7. segundo turno em trabalho (rodizio por sessao nao muda a rota)
        ag2 = FakeAgent(sid="s6b", provider="deepseek", model="deepseek-flash")
        chamar(agent=ag2, request=pedido("complexidade: alta"), session_id="s6b")
        check("rodizio por sessao: sessoes diferentes podem divergir",
              ag2.model in {"deepseek-v4-pro", "openai/gpt-6.1-sol"}, ag2.model)

        # 8. rota sem credencial e saltada para a proxima
        def resolver_falha(perfil, prov, mod):
            if prov == "openrouter":
                raise RuntimeError("sem credencial")
            return {"provider": prov.lower(), "api_key": "y" * 40,
                    "base_url": "https://deepseek.test/v1", "api_mode": "chat", "source": "env"}
        plugin.resolver_rota = resolver_falha
        plugin.gestor.perfil_atual = lambda agente=None: "trabalho"
        ag = FakeAgent(sid="s8", provider="x", model="y")
        # sessao nova, nivel alta: rodizio por sessao pode calhar em pro ou sol;
        # forca-se a primeira entrada (sol) a falhar e pro tem de servir
        plugin._contadores.clear()
        r = chamar(agent=ag, request=pedido("complexidade: alta"), session_id="s8")
        check("rota sem credencial e saltada", len(ag.chamadas) == 1 and ag.model == "deepseek-v4-pro",
              f"{ag.model} {ag.chamadas}")
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        check("salto por credencial fica registado",
              any(l.get("evento") == "sem_rota" and l.get("perfil") == "trabalho" for l in linhas))

        # 9. sem marcador, a analise decide; texto trivial fica em leve
        plugin.resolver_rota = rotas_reais and (lambda perfil, prov, mod: {
            "provider": prov.lower(), "api_key": "z" * 40,
            "base_url": "https://t/v1", "api_mode": "chat", "source": "env"})
        plugin.gestor.perfil_atual = lambda agente=None: "pessoal"
        ag = FakeAgent(sid="s9")
        chamar(agent=ag, request=pedido("ola, bom dia"), session_id="s9")
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        decisao = [l for l in linhas if l.get("sessao") == "s9" and l.get("evento") in
                   ("troca", "sem_troca")]
        check("texto trivial e classificado leve",
              bool(decisao) and decisao[-1].get("nivel") == "leve",
              str(decisao[-1] if decisao else None))

        # 10. histerese: alta e depois leves seguidos na mesma sessao.
        #     `descer_apos: 2` => a descida so se aplica na SEGUNDA avaliacao abaixo.
        plugin.gestor.perfil_atual = lambda agente=None: "trabalho"
        ag = FakeAgent(sid="s10", provider="deepseek", model="deepseek-v4-pro")
        chamar(agent=ag, request=pedido("complexidade: alta"), session_id="s10")
        chamar(agent=ag, request=pedido("complexidade: leve"), session_id="s10")
        with plugin._lock:
            estado = plugin._historico.get("trabalho\x1fs10") or {}
        check("histerese segura o nivel alto na primeira descida",
              estado.get("nivel") == "alta", str(estado))
        chamar(agent=ag, request=pedido("complexidade: leve"), session_id="s10")
        with plugin._lock:
            estado = plugin._historico.get("trabalho\x1fs10") or {}
        check("histerese desce apos N descidas", estado.get("nivel") == "leve", str(estado))

        # 11. reset explicito
        ag = FakeAgent(sid="s11")
        chamar(agent=ag, request=pedido("complexidade: auto"), session_id="s11")
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        check("marcador auto regista reset",
              any(l.get("evento") == "reset" and l.get("sessao") == "s11" for l in linhas))

        # 12. pedido que nao e dict nao rebenta
        check("pedido invalido devolve None", chamar(agent=FakeAgent(), request=None) is None)

        # 13. pedido sem mensagens do utilizador nao rebenta
        ag = FakeAgent(sid="s13")
        r = chamar(agent=ag, request={"model": "m", "messages": [
            {"role": "system", "content": "so sistema"}]}, session_id="s13")
        check("sem mensagens de utilizador nao troca", r is None and not ag.chamadas)

        # 14. resposta do provider fica registada com o perfil
        os.environ["VMM_DIR"] = str(_dir_com(_cfg_base()))
        plugin.gestor.perfil_atual = lambda agente=None: "pessoal"
        ag = FakeAgent(sid="s14")
        chamar(agent=ag, request=pedido("complexidade: leve"), session_id="s14")
        plugin.pos_resposta(session_id="s14", model="deepseek-flash",
                            response_model="deepseek-flash-2026", finish_reason="stop",
                            usage={"total_tokens": 7})
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        resp = [l for l in linhas if l.get("evento") == "resposta" and l.get("sessao") == "s14"]
        check("resposta registada com perfil e modelo do provider",
              bool(resp) and resp[-1].get("perfil") == "pessoal"
              and resp[-1].get("respondido_por") == "deepseek-flash-2026",
              str(resp[-1] if resp else None))

        # 15. ledger nunca leva o texto da conversa
        texto = "SEGREDO-DA-CONVERSA-1234"
        ag = FakeAgent(sid="s15")
        chamar(agent=ag, request=pedido(f"complexidade: leve {texto}"), session_id="s15")
        check("ledger nao contem texto de conversa",
              texto not in g.caminho_ledger().read_text())

        # 16. resposta regista provider/modelo efectivos, superficie, acesso e correlacao
        os.environ["VMM_DIR"] = str(_dir_com(_cfg_base()))
        plugin.gestor.perfil_atual = lambda agente=None: "pessoal"
        ag = FakeAgent(sid="s16")
        chamar(agent=ag, request=pedido("complexidade: leve"), session_id="s16")
        plugin.pos_resposta(session_id="s16", model="deepseek-flash", provider="deepseek",
                            platform="cli", response_model="deepseek-flash-2026",
                            finish_reason="stop", api_request_id="req-42", turn_id="turn-7",
                            task_id="task-9", usage={"total_tokens": 3})
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        resp = [l for l in linhas if l.get("evento") == "resposta" and l.get("sessao") == "s16"]
        check("resposta regista provider efectivo",
              bool(resp) and resp[-1].get("provider") == "deepseek",
              str(resp[-1] if resp else None))
        check("resposta regista superficie",
              bool(resp) and resp[-1].get("superficie") == "cli")
        check("resposta regista acesso",
              bool(resp) and resp[-1].get("acesso") in ("api", "subscricao"))
        check("resposta regista correlacao request/turn/task_id",
              bool(resp) and resp[-1].get("request_id") == "req-42"
              and resp[-1].get("turn_id") == "turn-7" and resp[-1].get("task_id") == "task-9",
              str(resp[-1] if resp else None))
        check("resposta distingue escolhido de efectivo",
              bool(resp) and isinstance(resp[-1].get("escolhido"), dict)
              and resp[-1]["escolhido"].get("provider") in ("deepseek", "openrouter"),
              str(resp[-1] if resp else None))

        # 17. superficie desconhecida quando o hook nao a entrega
        plugin.pos_resposta(session_id="s16", model="deepseek-flash", provider="deepseek",
                            response_model="x", finish_reason="stop")
        linhas = [json.loads(l) for l in g.caminho_ledger().read_text().splitlines()]
        resp = [l for l in linhas if l.get("evento") == "resposta" and l.get("sessao") == "s16"]
        check("resposta sem platform usa 'desconhecido'",
              bool(resp) and resp[-1].get("superficie") == "desconhecido",
              str(resp[-1] if resp else None))
    finally:
        plugin.resolver_rota = rotas_reais
        plugin.gestor.perfil_atual = perfis_reais


# =========================================================================== 2b. realimentacao


def testes_realimentacao() -> None:
    """A (realimentacao), C (degradacao) e D (zona morta) — o lote v3."""
    print("\n== v3: realimentacao, degradacao e zona morta ==")
    plugin = _mod("vmm_plugin_v3", AQUI / "__init__.py")
    os.environ["VMM_DIR"] = str(_dir_com(_cfg_base()))

    # A: turno anterior truncado no mesmo nivel sobe um nivel
    with plugin._lock:
        plugin._resultado.clear()
        plugin._resultado["sA"] = {"nivel": "media", "finish_reason": "length",
                                   "output_tokens": 8000}
    nivel, motivo = plugin.ajustar_por_resultado("sA", "media")
    check("A: truncagem no turno anterior sobe um nivel",
          nivel == "alta" and "truncado" in motivo, f"{nivel} {motivo}")

    # A: sem truncagem nao mexe
    with plugin._lock:
        plugin._resultado["sA2"] = {"nivel": "media", "finish_reason": "stop",
                                    "output_tokens": 200}
    nivel, motivo = plugin.ajustar_por_resultado("sA2", "media")
    check("A: resposta completa no turno anterior nao sobe",
          nivel == "media" and not motivo, f"{nivel} {motivo}")

    # A: no topo nao passa de alta
    with plugin._lock:
        plugin._resultado["sA3"] = {"nivel": "alta", "finish_reason": "length",
                                    "output_tokens": 9000}
    nivel, motivo = plugin.ajustar_por_resultado("sA3", "alta")
    check("A: alta truncada fica em alta (nao inventa nivel)",
          nivel == "alta" and motivo == "ja_no_topo", f"{nivel} {motivo}")

    # A: nivel diferente do anterior nao conta
    nivel, motivo = plugin.ajustar_por_resultado("sA", "leve")
    check("A: so compara com o mesmo nivel",
          nivel == "leve" and not motivo, f"{nivel} {motivo}")

    # D: zona morta herda o nivel anterior
    pcfg = g.validar(_cfg_base())[0]["perfis"]["pessoal"]
    with plugin._lock:
        plugin._historico.clear()
        plugin._historico["pessoal\x1fsD"] = {"nivel": "alta", "abaixo": 0}
    decisao = {"score": 3, "sinais": ["trabalho_producao=2"]}
    nivel, motivo = plugin.herdar_se_indeciso(
        "pessoal", "sD", "media", decisao, {"media": 3, "alta": 6})
    check("D: zona morta herda o nivel anterior",
          nivel == "alta" and "zona_morta" in motivo, f"{nivel} {motivo}")

    # D: dois sinais independentes decidem media (nao herda)
    decisao = {"score": 3, "sinais": ["trabalho_producao=2", "dominio_tecnico=1"]}
    nivel, motivo = plugin.herdar_se_indeciso(
        "pessoal", "sD", "media", decisao, {"media": 3, "alta": 6})
    check("D: dois sinais independentes fixam media",
          nivel == "media" and not motivo, f"{nivel} {motivo}")

    # D: fora da zona morta nao herda
    decisao = {"score": 5, "sinais": ["trabalho_producao=3", "codigo=1"]}
    nivel, motivo = plugin.herdar_se_indeciso(
        "pessoal", "sD", "media", decisao, {"media": 3, "alta": 6})
    check("D: score acima da zona morta nao herda",
          nivel == "media" and not motivo, f"{nivel} {motivo}")

    # C: degradacao desce ao nivel que tem rota
    os.environ["VMM_DIR"] = str(_dir_com(_cfg_base()))
    pcfg = g.validar(_cfg_base())[0]["perfis"]["pessoal"]
    plugin.resolver_rota = lambda perfil, prov, mod: {
        "provider": prov.lower(), "api_key": "z" * 40,
        "base_url": "https://t.test/v1", "api_mode": "chat", "source": "env"}
    nivel_ef, entrada, rota, _ = plugin.degradar_nivel(
        pcfg, "alta", perfil="pessoal", sessao="sC")
    check("C: alta sem rota desce a media com rota utilizavel",
          nivel_ef == "media" and entrada is not None and rota is not None,
          f"{nivel_ef} {entrada}")

    # C: nao sobe nivel
    nivel_ef, entrada, rota, _ = plugin.degradar_nivel(
        pcfg, "leve", perfil="pessoal", sessao="sC")
    check("C: degradacao nunca sobe nem inventa nivel",
          nivel_ef is None and entrada is None, f"{nivel_ef} {entrada}")

    with plugin._lock:
        plugin._resultado.clear()
        plugin._historico.clear()


# =========================================================================== 3. CLI


def testes_cli() -> None:
    print("\n== CLI: escreve o que o plugin le ==")
    d = _dir_com(_cfg_base())
    env = {**os.environ, "VMM_DIR": str(d)}

    def vmm(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(AQUI / "cli.py"), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    r = vmm("status")
    check("status rc 0", r.returncode == 0, r.stderr)
    check("status mostra os dois perfis", "pessoal" in r.stdout and "trabalho" in r.stdout)

    r = vmm("off", "--perfil", "pessoal")
    check("off --perfil rc 0", r.returncode == 0, r.stderr)
    cfg, _ = g.carregar(d / "config.json")
    check("off --perfil desliga so esse perfil",
          cfg["perfis"]["pessoal"]["activo"] is False and cfg["perfis"]["trabalho"]["activo"] is True)

    r = vmm("on", "--perfil", "pessoal")
    cfg, _ = g.carregar(d / "config.json")
    check("on --perfil volta a ligar", cfg["perfis"]["pessoal"]["activo"] is True)

    r = vmm("off")
    cfg, _ = g.carregar(d / "config.json")
    check("off sem perfil desliga o global", cfg["activo"] is False)
    vmm("on")
    cfg, _ = g.carregar(d / "config.json")
    check("on sem perfil liga o global", cfg["activo"] is True)

    r = vmm("perfil", "add", "novo", "--copiar-de", "pessoal")
    cfg, _ = g.carregar(d / "config.json")
    check("perfil add --copiar-de cria perfil com rotas",
          r.returncode == 0 and len(cfg["perfis"]["novo"]["catalogo"]) == 3, r.stderr)

    r = vmm("perfil", "add", "novo")
    check("perfil add duplicado rc 2", r.returncode == 2, f"rc={r.returncode}")

    r = vmm("rota", "add", "novo", "openrouter", "openai/gpt-6.1-sol",
            "--complexidade", "alta", "--id", "sol")
    cfg, _ = g.carregar(d / "config.json")
    ids = [x["id"] for x in cfg["perfis"]["novo"]["catalogo"]]
    check("rota add grava com a complexidade pedida",
          r.returncode == 0 and "sol" in ids
          and cfg["perfis"]["novo"]["catalogo"][ids.index("sol")]["complexidade"] == ["alta"],
          r.stderr)

    r = vmm("rota", "add", "novo", "x", "y", "--complexidade", "media,alta")
    cfg, _ = g.carregar(d / "config.json")
    entrada = [x for x in cfg["perfis"]["novo"]["catalogo"] if x["id"] == "x:y"]
    check("rota add aceita varios niveis",
          r.returncode == 0 and entrada and entrada[0]["complexidade"] == ["media", "alta"],
          r.stderr)

    r = vmm("rota", "add", "novo", "x", "y", "--complexidade", "enorme")
    check("rota add com nivel invalido rc 2", r.returncode == 2, f"rc={r.returncode}")

    r = vmm("rota", "add", "inexistente", "x", "y", "--complexidade", "leve")
    check("rota add em perfil inexistente rc 3", r.returncode == 3, f"rc={r.returncode}")

    r = vmm("rota", "rm", "novo", "sol")
    cfg, _ = g.carregar(d / "config.json")
    check("rota rm remove",
          r.returncode == 0 and "sol" not in [x["id"] for x in cfg["perfis"]["novo"]["catalogo"]],
          r.stderr)

    r = vmm("rota", "rm", "novo", "nao-existe")
    check("rota rm inexistente rc 3", r.returncode == 3, f"rc={r.returncode}")

    r = vmm("simular", "pessoal", "complexidade: alta")
    check("simular mostra nivel e ausencia de rota",
          r.returncode == 1 and "alta" in r.stdout and "nenhuma" in r.stdout, r.stdout + r.stderr)

    # O ponto e a classificacao, nao a rota: `pessoal` pode nao cobrir `alta`,
    # e nesse caso o simular sai com rc 1 e diz "nenhuma". rc 0/1 sao ambos ok.
    r = vmm("simular", "pessoal", "Implementa isto em /opt/data/x.py e corre os testes")
    check("simular classifica texto de engenharia acima de leve",
          r.returncode in (0, 1) and ("nivel: media" in r.stdout or "nivel: alta" in r.stdout),
          r.stdout + r.stderr)

    r = vmm("perfil", "rm", "novo")
    cfg, _ = g.carregar(d / "config.json")
    check("perfil rm remove o perfil",
          r.returncode == 0 and "novo" not in cfg["perfis"], r.stderr)

    r = vmm("rota", "list", "trabalho")
    check("rota list mostra as complexidades",
          r.returncode == 0 and "alta" in r.stdout and "deepseek-v4-pro" in r.stdout, r.stdout)

    r = vmm("caminho")
    check("caminho imprime o config e o ledger",
          r.returncode == 0 and "config.json" in r.stdout and "uso.jsonl" in r.stdout)

    r = vmm("log", "-n", "2")
    check("log le o ledger (vazio ou nao) sem rebentar", r.returncode in (0, 3), r.stderr)

    # acesso via CLI: inferencia e validacao
    r = vmm("rota", "add", "trabalho", "openai-codex", "gpt-6.1-sol",
            "--complexidade", "media", "--id", "sol-sub")
    cfg, _ = g.carregar(d / "config.json")
    e = [x for x in cfg["perfis"]["trabalho"]["catalogo"] if x["id"] == "sol-sub"]
    check("rota add openai-codex infere subscricao",
          r.returncode == 0 and e and e[0]["acesso"] == "subscricao", r.stderr)

    r = vmm("rota", "add", "trabalho", "openrouter", "openai/gpt-6.1-sol",
            "--complexidade", "media", "--id", "sol-api", "--acesso", "api")
    cfg, _ = g.carregar(d / "config.json")
    e = [x for x in cfg["perfis"]["trabalho"]["catalogo"] if x["id"] == "sol-api"]
    check("rota add --acesso api explicito",
          r.returncode == 0 and e and e[0]["acesso"] == "api", r.stderr)

    r = vmm("rota", "add", "trabalho", "openrouter", "openai/gpt-6.1-sol",
            "--complexidade", "media", "--id", "mau-sub", "--acesso", "subscricao")
    check("rota add subscricao com provider nao codex recusada (rc 1)",
          r.returncode == 1, f"rc={r.returncode} {r.stderr}")

    r = vmm("rota", "add", "trabalho", "openai-codex", "gpt-6.1-sol",
            "--complexidade", "media", "--id", "mau-api", "--acesso", "api")
    check("rota add api com openai-codex recusada (rc 1)",
          r.returncode == 1, f"rc={r.returncode} {r.stderr}")

    r = vmm("rota", "list", "trabalho")
    check("rota list mostra a coluna acesso e a subscricao",
          r.returncode == 0 and "acesso" in r.stdout and "subscricao" in r.stdout, r.stdout)

    r = vmm("log", "--evento", "resposta", "--perfil", "nao-existe")
    check("log filtrando evento/perfil nao rebenta", r.returncode in (0, 3), r.stderr)


def testes_verificar() -> None:
    print("\n== verificar: resolve no contexto do perfil alvo ==")
    chamadas: dict[str, Any] = {}

    class _FakeConstants:
        def get_default_hermes_root(self):
            return chamadas["raiz"]

        def set_hermes_home_override(self, path):
            chamadas["home_set"].append(str(path))
            return ("home", len(chamadas["home_set"]))

        def reset_hermes_home_override(self, token):
            chamadas["home_reset"].append(token)

    class _FakeScope:
        def build_profile_secret_scope(self, casa):
            chamadas["scope_build"].append(str(casa))
            return {"FAKE_KEY": "x" * 40}

        def set_secret_scope(self, secrets, *, profile_home=None):
            chamadas["scope_set"].append(profile_home)
            return ("scope", len(chamadas["scope_set"]))

        def reset_secret_scope(self, token):
            chamadas["scope_reset"].append(token)

    class _FakeRuntimeProvider:
        def resolve_runtime_provider(self, requested, target_model):
            chamadas["resolve"].append((requested, target_model))
            return {"provider": requested, "api_key": "k" * 40,
                    "base_url": "https://t/v1", "api_mode": "chat", "source": "fake"}

    raiz = Path(tempfile.mkdtemp(prefix="vmm-verif-"))
    chamadas.update({"raiz": raiz, "home_set": [], "home_reset": [],
                     "scope_build": [], "scope_set": [], "scope_reset": [],
                     "resolve": []})
    fc, fs, fr = _FakeConstants(), _FakeScope(), _FakeRuntimeProvider()
    agp = types.ModuleType("agent")
    agp.secret_scope = fs  # type: ignore[attr-defined]
    hc = types.ModuleType("hermes_cli")
    hc.runtime_provider = fr  # type: ignore[attr-defined]
    antigos = {k: sys.modules.get(k) for k in
               ("hermes_constants", "agent", "agent.secret_scope",
                "hermes_cli", "hermes_cli.runtime_provider")}
    sys.modules["hermes_constants"] = fc
    sys.modules["agent"] = agp
    sys.modules["agent.secret_scope"] = fs
    sys.modules["hermes_cli"] = hc
    sys.modules["hermes_cli.runtime_provider"] = fr

    d = _dir_com(_cfg_base())
    os.environ["VMM_DIR"] = str(d)
    n_rotas = len(g.carregar(d / "config.json")[0]["perfis"]["trabalho"]["catalogo"])
    cli = _mod("vmm_cli_verif", AQUI / "cli.py")
    try:
        rc = cli.cmd_verificar(types.SimpleNamespace(perfil="trabalho"))
        check("verificar rc 0 com credencial fake", rc == 0, f"rc={rc}")
        check("verificar resolveu no home do perfil",
              any(h.endswith(str(Path("profiles") / "trabalho")) for h in chamadas["home_set"]),
              str(chamadas["home_set"]))
        check("verificar montou o escopo do perfil",
              any(p == str(raiz / "profiles" / "trabalho") for p in chamadas["scope_set"]),
              str(chamadas["scope_set"]))
        check("verificar resolveu as rotas do perfil",
              len(chamadas["resolve"]) == n_rotas,
              f"{len(chamadas['resolve'])} vs {n_rotas}")
        check("verificar restaura o contexto (home e escopo)",
              bool(chamadas["home_reset"]) and bool(chamadas["scope_reset"]))
    finally:
        os.environ.pop("VMM_DIR", None)
        for k, v in antigos.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def testes_classificador() -> None:
    """Classificador por modelo: quando manda, quando nao manda, e quando falha.

    Servidor HTTP local a serio — nao ha mock do transporte. O que se prova:
    o nivel passa a ser o do modelo, o ledger diz que veio do modelo, a tarefa
    associada continua a ganhar (nao se paga a chamada), e um provider em baixo
    devolve o turno a heuristica sem o travar.
    """
    print("\n== classificador por modelo: global, perfil, fail-open ==")
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    plugin = _mod("vmm_plugin_c", AQUI / "__init__.py")

    estado: dict[str, Any] = {"conteudo": "alta", "codigo": 200, "pedidos": []}

    class Servidor(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            tamanho = int(self.headers.get("Content-Length") or 0)
            estado["pedidos"].append(json.loads(self.rfile.read(tamanho) or b"{}"))
            dados = {"choices": [{"message": {"content": estado["conteudo"]}}],
                     "usage": {"prompt_tokens": 90, "completion_tokens": 1}}
            bruto = json.dumps(dados).encode()
            try:
                self.send_response(int(estado["codigo"]))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(bruto)))
                self.end_headers()
                self.wfile.write(bruto)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
    porta = servidor.server_address[1]
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    class FakeAgent:
        def __init__(self, provider="deepseek", model="deepseek-flash", sid=""):
            self.provider, self.model, self.session_id = provider, model, sid
            self.chamadas: list[dict] = []

        def switch_model(self, **kw):
            self.chamadas.append(kw)
            self.model = kw.get("new_model")
            self.provider = kw.get("new_provider")

    def pedido(texto: str, modelo="deepseek-flash") -> dict:
        return {"model": modelo, "messages": [{"role": "user", "content": texto}]}

    def chamar(agent, texto, sessao, provider="deepseek", model="deepseek-flash"):
        return plugin.middleware_llm_request(
            request=pedido(texto, model), session_id=sessao, model=model, provider=provider)

    def linhas() -> list[dict]:
        caminho = g.caminho_ledger()
        if not caminho.exists():
            return []
        return [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines() if l.strip()]

    def ultima(sessao: str) -> dict:
        achadas = [l for l in linhas() if l.get("sessao") == sessao]
        return achadas[-1] if achadas else {}

    def cfg_com(classificador: Any, perfil_classificador: Any = "__ausente__") -> dict:
        cfg = _cfg_base()
        # pessoal ganha uma rota de alta, para o nivel do modelo ter para onde ir
        cfg["perfis"]["pessoal"]["catalogo"].append(
            {"id": "sol", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
             "complexidade": "alta", "tarefas": ["arquitetura"]})
        cfg["perfis"]["pessoal"]["tarefas"] = {
            "arquitetura": {"termos": ["analise de arquitetura", "arquitetura"],
                            "prioridade": 5}}
        cfg["analise"]["classificador"] = classificador
        if perfil_classificador != "__ausente__":
            cfg["perfis"]["pessoal"]["analise"] = {"classificador": perfil_classificador}
        return cfg

    LIGADO = {"activo": True, "provider": "local", "modelo": "classificador-teste",
              "timeout_s": 3.0, "cache_ttl_s": 600}

    real_rota = plugin.resolver_rota
    real_perfil = plugin.gestor.perfil_atual
    try:
        def rota(perfil, prov, mod):
            if prov == "local":
                return {"provider": "local", "api_key": "k" * 30,
                        "base_url": f"http://127.0.0.1:{porta}/v1",
                        "api_mode": "chat", "source": "env:local"}
            return {"provider": prov.lower(), "api_key": "x" * 40,
                    "base_url": f"https://{prov}.test/v1", "api_mode": "chat",
                    "source": f"env:{perfil}"}
        plugin.resolver_rota = rota
        plugin.gestor.perfil_atual = lambda agente=None: "pessoal"

        # 1. heuristica diria `leve`; o modelo diz `alta` -> o modelo manda
        os.environ["VMM_DIR"] = str(_dir_com(cfg_com(LIGADO)))
        estado["pedidos"].clear()
        ag = FakeAgent(sid="c1")
        chamar(ag, "olá, tudo bem?", "c1")
        l1 = ultima("c1")
        check("modelo decide contra a heuristica (leve -> alta)",
              l1.get("nivel") == "alta" and l1.get("origem") == "modelo", str(l1))
        check("nivel do modelo escolhe a rota de alta",
              len(ag.chamadas) == 1 and ag.chamadas[0].get("new_model") == "openai/gpt-6.1-sol",
              str(ag.chamadas))
        check("ledger regista o classificador usado",
              (l1.get("classificador") or {}).get("modelo") == "classificador-teste"
              and (l1.get("classificador") or {}).get("tokens_entrada") == 90, str(l1.get("classificador")))
        check("chamada de classificacao leva o modelo declarado",
              estado["pedidos"] and estado["pedidos"][0].get("model") == "classificador-teste",
              str(estado["pedidos"][:1]))

        # 2. mesma mensagem de novo -> cache, sem segunda chamada ao provider
        n_antes = len(estado["pedidos"])
        chamar(FakeAgent(sid="c2"), "olá, tudo bem?", "c2")
        check("cache evita pagar duas vezes a mesma mensagem",
              len(estado["pedidos"]) == n_antes, f"{len(estado['pedidos'])} vs {n_antes}")

        # 3. heuristica diria `alta`; o modelo diz `leve` -> desce (e a heuristica
        #    nao recupera por histerese nem por zona morta). Agente fora do
        #    catalogo: a troca para uma rota de leve tem de ser visivel.
        estado["conteudo"] = "leve"
        ag = FakeAgent(sid="c3", provider="openai", model="gpt-x")
        chamar(ag, "implementa o refactor do modulo de billing com testes e rollback",
               "c3", provider="openai", model="gpt-x")
        l3 = ultima("c3")
        check("modelo tambem faz descer o nivel (alta -> leve)",
              l3.get("nivel") == "leve" and l3.get("origem") == "modelo", str(l3))
        check("rota escolhida e de leve",
              ag.chamadas and ag.chamadas[0].get("new_model") in
              {"deepseek-flash", "nvidia/nemotron-3-super-120b-a12b:free"},
              str(ag.chamadas))

        # 4. provider do classificador em baixo -> heuristica decide, turno segue
        estado["conteudo"] = "alta"
        estado["codigo"] = 500
        cfg = cfg_com(LIGADO)
        cfg["analise"]["classificador"]["cache_ttl_s"] = 0
        os.environ["VMM_DIR"] = str(_dir_com(cfg))
        ag = FakeAgent(sid="c4")
        chamar(ag, "mensagem que nunca foi classificada antes", "c4")
        l4 = ultima("c4")
        check("falha do classificador devolve o turno a heuristica",
              l4.get("nivel") == "leve" and l4.get("origem") != "modelo", str(l4))
        check("falha do classificador fica registada com motivo",
              "HTTP 500" in str(l4.get("classificador_erro")), str(l4.get("classificador_erro")))

        # 5. tarefa associada ganha: nao se paga chamada de classificacao
        estado["codigo"] = 200
        cfg = cfg_com(LIGADO)
        for e in cfg["perfis"]["pessoal"]["catalogo"]:
            if e["id"] == "sol":
                e["tarefas"] = ["arquitetura"]
        os.environ["VMM_DIR"] = str(_dir_com(cfg))
        estado["pedidos"].clear()
        ag = FakeAgent(sid="c5")
        chamar(ag, "a análise de arquitetura do AAD", "c5")
        l5 = ultima("c5")
        check("tarefa associada nao chama o classificador",
              estado["pedidos"] == [], f"{len(estado['pedidos'])} pedidos")
        check("tarefa associada mantem a prioridade",
              l5.get("origem") == "tarefa" and ag.chamadas
              and ag.chamadas[0].get("new_model") == "openai/gpt-6.1-sol", str(l5))

        # 6. perfil desliga o que a config global liga -> heuristica, zero chamadas
        os.environ["VMM_DIR"] = str(_dir_com(cfg_com(LIGADO, {"activo": False})))
        estado["pedidos"].clear()
        ag = FakeAgent(sid="c6")
        chamar(ag, "olá, tudo bem?", "c6")
        l6 = ultima("c6")
        check("perfil desliga o classificador global",
              estado["pedidos"] == [] and l6.get("origem") != "modelo",
              f"{len(estado['pedidos'])} pedidos / {l6}")
        check("desligado no perfil nao regista falha nenhuma",
              not l6.get("classificador_erro"), str(l6.get("classificador_erro")))

        # 7. classificador ausente (config antiga) -> comportamento de antes
        cfg = cfg_com(LIGADO)
        cfg["analise"].pop("classificador", None)
        os.environ["VMM_DIR"] = str(_dir_com(cfg))
        estado["pedidos"].clear()
        ag = FakeAgent(sid="c7")
        chamar(ag, "olá, tudo bem?", "c7")
        l7 = ultima("c7")
        check("sem classificador a heuristica decide como antes",
              estado["pedidos"] == [] and l7.get("nivel") == "leve"
              and l7.get("origem") == "analise", str(l7))
    finally:
        plugin.resolver_rota = real_rota
        plugin.gestor.perfil_atual = real_perfil
        os.environ.pop("VMM_DIR", None)
        servidor.shutdown()
        servidor.server_close()


def testes_sem_copia_do_global() -> None:
    """Um perfil nao grava copias do global, nem nas chaves simples.

    Medido a 2026-11-02: os tres perfis guardavam `descer_apos`,
    `janela_mensagens`, `varredura_max_mensagens` e `limiares` iguais ao global.
    Nao e uma declaracao: e uma copia que fixa o perfil ao valor do dia.
    `validar` descarta o que e igual ao global, e um perfil sem nada declarado
    fica sem a chave `analise`.
    """
    print("\n== sem copia do global nas chaves simples ==")
    cfg = _cfg_base()
    cfg["analise"]["descer_apos"] = 2
    cfg["analise"]["limiares"] = {"media": 3, "alta": 6}
    cfg["perfis"]["trabalho"]["analise"] = {
        "descer_apos": 2,
        "limiares": {"media": 3, "alta": 7},
        "janela_mensagens": 8,
    }
    saida, erros = g.validar(cfg)
    check("sem copia do global: validar aceita", erros == [], erros)
    analise = saida["perfis"]["trabalho"].get("analise") or {}
    check("sem copia do global: descer_apos igual nao e guardado", "descer_apos" not in analise, analise)
    check("sem copia do global: janela_mensagens igual nao e guardada", "janela_mensagens" not in analise, analise)
    check("sem copia do global: limiares fica so com o que difere",
        analise.get("limiares") == {"alta": 7}, analise.get("limiares"))

    vivo = {"versao": 3, "activo": True, "registar": True,
            "analise": {"descer_apos": 3, "limiares": {"media": 3, "alta": 6},
                        "classificador": {"activo": True, "provider": "deepseek", "modelo": "deepseek-flash"}},
            "perfis": {"trabalho": {"estrategia": "rodizio", "catalogo": [], "tarefas": {}}}}
    saida2, erros2 = g.validar(vivo)
    check("sem copia do global: perfil sem analise nao ganha a chave",
        "analise" not in saida2["perfis"]["trabalho"], saida2["perfis"]["trabalho"].keys())
    check("sem copia do global: o global chega ao perfil na leitura",
        g.perfil_config(saida2, "trabalho")["analise"].get("descer_apos") == 3, erros2)


def testes_heranca_classificador() -> None:
    """Heranca global -> perfil: o que se grava e so o que o perfil declara.

    O que aqui se prova e a correcao de um defeito real: o validador semeava a
    `analise` de cada perfil com uma copia do global e gravava-a, logo um perfil
    "que nao declarava nada" ficava fixo ao valor do dia — e mudar o global
    deixava de chegar-lhe. As provas usam `perfil_config` (a leitura que o
    middleware faz), nao o dicionario cru.
    """
    print("\n== heranca do classificador: global, perfil, e a mudanca do global ==")
    am = g._analise_modelo()  # noqa: SLF001
    LIGADO = {"activo": True, "provider": "deepseek", "modelo": "deepseek-flash"}

    def cfg(global_c, pessoal_c=None):
        base = _cfg_base()
        base["analise"]["classificador"] = global_c
        if pessoal_c is not None:
            base["perfis"]["pessoal"].setdefault("analise", {})["classificador"] = pessoal_c
        return base

    def efectiva(config, perfil):
        return am.efectiva((g.perfil_config(config, perfil) or {}).get("analise") or {})

    DIR = _dir_com(cfg(LIGADO))
    os.environ["VMM_DIR"] = str(DIR)
    try:
        limpo, erros = g.validar(cfg(LIGADO))
        check("validar aceita classificador global", not erros, str(erros))
        check("perfil que nao declara herda o global",
              (efectiva(limpo, "trabalho") or {}).get("modelo") == "deepseek-flash",
              str(efectiva(limpo, "trabalho")))
        check("validar nao grava copia do global no perfil",
              not ((limpo["perfis"]["trabalho"].get("analise") or {}).get("classificador")),
              str(limpo["perfis"]["trabalho"].get("analise")))

        # a prova do defeito: mudar o global chega a um perfil ja gravado
        novo = cfg({"activo": True, "provider": "openrouter",
                    "modelo": "thinkingmachines/inkling:free"})
        novo["perfis"]["trabalho"] = limpo["perfis"]["trabalho"]
        limpo2, _ = g.validar(novo)
        check("mudar o global chega ao perfil que ja estava gravado",
              (efectiva(limpo2, "trabalho") or {}).get("provider") == "openrouter",
              str(efectiva(limpo2, "trabalho")))

        limpo3, _ = g.validar(cfg(LIGADO, {"activo": False}))
        check("perfil que declara desligado vence o global ligado",
              efectiva(limpo3, "pessoal") is None
              and (efectiva(limpo3, "trabalho") or {}).get("modelo") == "deepseek-flash",
              str(efectiva(limpo3, "pessoal")))
        check("perfil que declara modelo proprio vence o global",
              (efectiva(g.validar(cfg(LIGADO, {"activo": True, "provider": "openrouter",
                                               "modelo": "x/y"}))[0], "pessoal") or {}
               ).get("provider") == "openrouter")
        check("perfil que declara igual ao global nao fica com copia",
              not ((g.validar(cfg(LIGADO, dict(LIGADO)))[0]["perfis"]["pessoal"]
                    .get("analise") or {}).get("classificador")))

        # limiares: o perfil declara so o que quer mudar
        parcial = _cfg_base()
        parcial["perfis"]["pessoal"].setdefault("analise", {})["limiares"] = {"alta": 7}
        limpo4, erros4 = g.validar(parcial)
        lim = ((g.perfil_config(limpo4, "pessoal") or {}).get("analise") or {}).get("limiares") or {}
        check("limiar parcial do perfil herda o resto do global",
              lim.get("alta") == 7 and lim.get("media") == 3 and not erros4,
              f"{lim} {erros4}")
    finally:
        os.environ.pop("VMM_DIR", None)


def testes_indicador_analise() -> None:
    """O indicador do cartao: modelo (qual) ou heuristica, e porque nao decide.

    A pergunta do painel e "este perfil esta a usar um modelo ou a heuristica?".
    A resposta tem de vir de uma casa so, senao o cartao e o CLI divergem — e o
    defeito que aqui se trava e o mais provavel: mostrar "desligado" a um perfil
    que herda um global ligado, ou "ligado" a um bloco que falha a validacao.
    """
    am = g._analise_modelo()  # noqa: SLF001 - o modulo que decide a forma

    print("\n== forma da resposta do classificador (json) ==")
    check("classificador: formato_json existe no padrao", g._analise_modelo().PADRAO.get("formato_json") == "auto",
          str(g._analise_modelo().PADRAO.get("formato_json")))
    check("classificador: prompt pede json (o DeepSeek exige a palavra)",
          "json" in g._analise_modelo().PROMPT.lower() and "json" in g._analise_modelo().PROMPT_LOTE.lower(), "sem a palavra json")
    d_f, _ = g._analise_modelo().normalizar({"activo": True, "provider": "p", "modelo": "m",
                            "formato_json": "OFF"})
    check("classificador: formato_json normalizado", d_f["formato_json"] == "off",
          str(d_f.get("formato_json")))
    corpo_f = g._analise_modelo()._corpo("p", "m", "texto", d_f, formato_json=None)
    check("classificador: formato off nao pede response_format",
          "response_format" not in corpo_f, str(sorted(corpo_f)))
    corpo_auto = g._analise_modelo()._corpo("p", "m", "texto", g._analise_modelo().PADRAO)
    check("classificador: formato auto pede response_format",
          corpo_auto.get("response_format") == {"type": "json_object"}, str(sorted(corpo_auto)))

    print("\n== indicador do cartao: modelo ou heuristica, e o motivo ==")
    LIGADO = {"activo": True, "provider": "inception", "modelo": "mercury-3",
              "esforco": "baixo"}
    CHAVES = {"modo", "activo", "provider", "modelo", "esforco", "formato_json",
              "origem", "motivo"}
    def cfg(global_c=None, pessoal_c=None, com_global=True):
        base = _cfg_base()
        if com_global:
            base["analise"]["classificador"] = global_c or LIGADO
        else:
            base["analise"].pop("classificador", None)
        if pessoal_c is not None:
            base["perfis"]["pessoal"].setdefault("analise", {})["classificador"] = pessoal_c
        return base

    def ind(config, perfil):
        return g.classificador_efectivo(config, perfil)

    # 1. heranca: o perfil nao declara, o global decide
    c = ind(cfg(), "trabalho")
    check("indicador: perfil que herda aparece como modelo", c["modo"] == "modelo", str(c))
    check("indicador: heranca diz de onde vem e qual o modelo",
          c["origem"] == "herdado" and c["provider"] == "inception"
          and c["modelo"] == "mercury-3" and c["esforco"] == "baixo", str(c))
    check("indicador: modelo utilizavel nao tem motivo", c["motivo"] == "", str(c))
    check("indicador: devolve sempre as mesmas chaves", set(c) == CHAVES, str(sorted(c)))

    # 2. o perfil desliga o que o global liga
    c = ind(cfg(pessoal_c={"activo": False, "provider": "inception",
                           "modelo": "mercury-3"}), "pessoal")
    check("indicador: perfil que desliga mostra heuristica",
          c["modo"] == "heuristica" and c["origem"] == "proprio"
          and c["motivo"] == "desligado", str(c))
    check("indicador: desligado nao expoe modelo em vigor",
          c["modelo"] == "" and c["provider"] == "", str(c))

    # 3. modelo proprio do perfil vence o global
    c = ind(cfg(pessoal_c={"activo": True, "provider": "deepseek",
                           "modelo": "deepseek-flash", "esforco": "medio"}), "pessoal")
    check("indicador: modelo proprio diz proprio e traz o esforco",
          c["modo"] == "modelo" and c["origem"] == "proprio"
          and c["modelo"] == "deepseek-flash" and c["esforco"] == "medio", str(c))

    # 4. bloco invalido: o cartao nao pode dizer "ligado"
    c = ind(cfg(global_c={"activo": True}), "trabalho")
    check("indicador: activo sem provider/modelo cai em heuristica com motivo",
          c["modo"] == "heuristica" and "exige provider" in c["motivo"], str(c))

    # 5. global desligado mas com rota declarada
    c = ind(cfg(global_c={"activo": False, "provider": "inception",
                          "modelo": "mercury-3"}), "trabalho")
    check("indicador: global desligado explica que esta desligado",
          c["modo"] == "heuristica" and c["origem"] == "herdado"
          and c["motivo"] == "desligado", str(c))

    # 6. config antiga, sem bloco nenhum
    c = ind(cfg(com_global=False), "trabalho")
    check("indicador: sem classificador nenhum e heuristica limpa",
          c["modo"] == "heuristica" and c["origem"] == "heuristica"
          and c["motivo"] == "", str(c))


def testes_esforco() -> None:
    """Esforco baixo/medio/alto: vocabulario, forma no fio e reposicao.

    O esforco nao tem uma forma unica no fio e o `api_kwargs` de cada turno e
    construido antes da middleware. O que se prova: (a) o vocabulario e um so e
    e validado no catalogo e no classificador; (b) a forma sai do perfil do
    provider de destino, nao de campos inventados; (c) o payload deste turno
    sai corrigido, sem a forma do provider anterior; (d) o `reasoning_config`
    do agente volta ao valor de origem quando a rota seguinte nao declara
    esforco; (e) o classificador tambem leva o esforco no corpo.
    """
    print("\n== esforco de raciocinio: baixo, medio, alto ==")
    plugin = _mod("vmm_plugin_e", AQUI / "__init__.py")

    check("esforco: vocabulario do dono", g.normalizar_esforco("alto") == ("alto", ""))
    check("esforco: sinonimo ingles da escada", g.normalizar_esforco("HIGH") == ("alto", ""))
    check("esforco: acento aceite", g.normalizar_esforco("médio") == ("medio", ""))
    check("esforco: vazio e 'nao declarado' e nao e erro", g.normalizar_esforco("") == ("", ""))
    valor_e, erro_e = g.normalizar_esforco("urgente")
    check("esforco: valor invalido recusado com o vocabulario",
          valor_e == "" and "baixo" in erro_e, erro_e)
    check("esforco: mapeamento para a escada do Hermes",
          [g.esforco_wire(v) for v in ("baixo", "medio", "alto")] == ["low", "medium", "high"])

    base = _cfg_base()
    base["perfis"]["pessoal"]["catalogo"][0]["esforco"] = "alto"
    limpo, erros = g.validar(base)
    catalogo = (limpo["perfis"]["pessoal"]["catalogo"] if not erros else []) or []
    check("esforco: aceite e normalizado no catalogo",
          bool(catalogo) and catalogo[0].get("esforco") == "alto" and not erros, str(erros))
    check("esforco: rota sem esforco nao ganha a chave",
          len(catalogo) > 1 and "esforco" not in catalogo[1])
    mau = _cfg_base()
    mau["perfis"]["pessoal"]["catalogo"][0]["esforco"] = "extremo"
    _, erros_mau = g.validar(mau)
    check("esforco: valor invalido recusado no catalogo",
          any("esforco invalido" in e for e in erros_mau), str(erros_mau))

    extra, topo = g.extras_esforco("deepseek", "deepseek-flash", "alto")
    check("esforco: deepseek leva thinking + reasoning_effort",
          extra.get("thinking") == {"type": "enabled"} and topo.get("reasoning_effort") == "high",
          f"{extra} {topo}")
    check("esforco: modelo sem raciocinio nao ganha campos",
          g.extras_esforco("deepseek", "deepseek-v3", "alto") == ({}, {}))
    extra_or, _ = g.extras_esforco("openrouter", "openai/gpt-6.1-sol", "baixo")
    check("esforco: openrouter leva reasoning em extra_body",
          (extra_or.get("reasoning") or {}).get("effort") == "low", str(extra_or))
    check("esforco: provider desconhecido nao inventa forma",
          g.extras_esforco("nao-existe", "x", "alto") == ({}, {}))

    entrada = {"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
               "esforco": "alto"}
    rota = {"provider": "deepseek"}
    pedido = {"model": "deepseek-flash", "temperature": 0.2, "reasoning_effort": "low",
              "extra_body": {"reasoning": {"effort": "low"},
                             "provider": {"order": ["x"]}}}
    novo, aplicado = plugin._pedido_com_esforco(pedido, entrada, rota, "s-esf-1")
    check("esforco: payload deste turno ganha a forma do destino",
          aplicado == "alto"
          and (novo.get("extra_body") or {}).get("thinking") == {"type": "enabled"}
          and novo.get("reasoning_effort") == "high", str(novo))
    check("esforco: forma do provider anterior sai do payload",
          "reasoning" not in (novo.get("extra_body") or {}))
    check("esforco: restantes campos preservados",
          (novo.get("extra_body") or {}).get("provider") == {"order": ["x"]}
          and novo.get("temperature") == 0.2)
    intacto, nada = plugin._pedido_com_esforco(
        {"model": "m"}, {"provider": "deepseek", "modelo": "deepseek-v3",
                         "esforco": "alto"}, rota, "s-esf-1")
    check("esforco: sem forma conhecida o pedido fica intacto",
          nada == "" and intacto == {"model": "m"})

    class Agente:
        def __init__(self) -> None:
            self.reasoning_config = {"enabled": True, "effort": "medium"}

    agente = Agente()
    primeiro = plugin._aplicar_esforco(agente, entrada, "s-esf-2")
    check("esforco: agente recebe o esforco da rota",
          primeiro == "alto"
          and agente.reasoning_config == {"enabled": True, "effort": "high"},
          str(agente.reasoning_config))
    segundo = plugin._aplicar_esforco(
        agente, {"provider": "deepseek", "modelo": "deepseek-flash"}, "s-esf-2")
    check("esforco: rota seguinte sem esforco repoe o valor de origem",
          segundo == "" and agente.reasoning_config == {"enabled": True, "effort": "medium"},
          str(agente.reasoning_config))

    # O classificador monta o corpo a mao (HTTP directo, sem SDK): o esforco
    # tem de entrar plano, senao `extra_body` ia literal para o fio.
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    capturado: list[dict] = []

    class Servidor(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            tamanho = int(self.headers.get("Content-Length") or 0)
            capturado.append(json.loads(self.rfile.read(tamanho) or b"{}"))
            bruto = json.dumps({"choices": [{"message": {"content": "media"}}]}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(bruto)))
                self.end_headers()
                self.wfile.write(bruto)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{servidor.server_address[1]}"
        resultado = g._analise_modelo().classificar(
            [{"role": "user", "content": "mexe no plugin e diz o que falta"}],
            dict(g._analise_modelo().PADRAO, provider="deepseek",
                 modelo="deepseek-flash", esforco="alto"),
            lambda p, m: {"base_url": url, "api_key": "teste"},
            perfil="esf")
        corpo = capturado[-1] if capturado else {}
        check("esforco: classificador leva o esforco plano no corpo",
              corpo.get("thinking") == {"type": "enabled"}
              and corpo.get("reasoning_effort") == "high"
              and "extra_body" not in corpo, str(corpo))
        check("esforco: classificador com esforco sobe o orcamento de saida",
              corpo.get("max_tokens") == 1200, str(corpo.get("max_tokens")))
        check("esforco: classificador classifica com o corpo alterado",
              (resultado or {}).get("nivel") == "media", str(resultado))
        sem = dict(g._analise_modelo().PADRAO, provider="deepseek",
                   modelo="deepseek-flash")
        g._analise_modelo().classificar(
            [{"role": "user", "content": "outra pergunta sem esforco nenhum"}],
            sem, lambda p, m: {"base_url": url, "api_key": "teste"}, perfil="esf2")
        corpo_sem = capturado[-1] if capturado else {}
        check("esforco: sem esforco o orcamento e o declarado e nao ha campos",
              corpo_sem.get("max_tokens") == 320
              and "thinking" not in corpo_sem
              and "reasoning_effort" not in corpo_sem, str(corpo_sem))
    finally:
        servidor.shutdown()


def testes_recarga_de_modulo() -> None:
    """O hot reload e a peca que evita reiniciar o gateway — prova-se a serio.

    Cria-se um modulo temporario, carrega-se, muda-se o ficheiro e o `mtime`, e
    verifica-se que a recarga devolve a versao nova; e que sem mudanca devolve
    o mesmo objecto (nao reexecuta em cada turno).
    """
    import importlib.util as ilu
    import tempfile
    import time as _t

    vmm = _mod("vmm_plugin_reload", AQUI / "__init__.py")

    pasta = tempfile.mkdtemp(prefix="vmm-reload-")
    alvo = os.path.join(pasta, "mod_teste.py")
    with open(alvo, "w", encoding="utf-8") as fh:
        fh.write("VALOR = 1\n")
    spec = ilu.spec_from_file_location("vmm_mod_teste", alvo)
    mod = ilu.module_from_spec(spec)
    sys.modules["vmm_mod_teste"] = mod
    spec.loader.exec_module(mod)
    cache = [0]
    from pathlib import Path as _Path
    ficheiro = _Path(alvo)

    primeiro = vmm._recarregar_modulo(mod, ficheiro, cache, "teste")
    check("recarga: primeiro carregamento devolve modulo vivo",
          getattr(primeiro, "VALOR", None) == 1)

    mesmo = vmm._recarregar_modulo(primeiro, ficheiro, cache, "teste")
    check("recarga: sem alteracao nao reexecuta (mesmo objecto)", mesmo is primeiro)

    _t.sleep(0.01)
    with open(alvo, "w", encoding="utf-8") as fh:
        fh.write("VALOR = 2\n")
    os.utime(alvo, None)
    segundo = vmm._recarregar_modulo(primeiro, ficheiro, cache, "teste")
    check("recarga: ficheiro alterado devolve a versao nova",
          getattr(segundo, "VALOR", None) == 2)
    check("recarga: o objecto antigo nao foi mutado",
          getattr(primeiro, "VALOR", None) == 1)

    # Fail-open: um ficheiro que rebenta nao pode partir o turno.
    with open(alvo, "w", encoding="utf-8") as fh:
        fh.write("raise RuntimeError('boom')\n")
    os.utime(alvo, None)
    terceiro = vmm._recarregar_modulo(segundo, ficheiro, cache, "teste")
    check("recarga: modulo que rebenta mantem a versao em memoria", terceiro is segundo)

    os.remove(alvo)
    quarto = vmm._recarregar_modulo(terceiro, ficheiro, cache, "teste")
    check("recarga: ficheiro ausente devolve o modulo em memoria", quarto is terceiro)
    try:
        os.rmdir(pasta)
    except OSError:
        pass


def main() -> int:
    testes_gestor()
    testes_plugin()
    testes_realimentacao()
    testes_classificador()
    testes_sem_copia_do_global()
    testes_heranca_classificador()
    testes_indicador_analise()
    testes_esforco()
    testes_cli()
    testes_verificar()
    testes_recarga_de_modulo()
    falhas = [n for n, ok, _ in _RESULTADOS if not ok]
    print(f"\n{len(_RESULTADOS) - len(falhas)}/{len(_RESULTADOS)} verificacoes OK")
    if falhas:
        print("FALHARAM: " + "; ".join(falhas))
        print("VEREDITO: FAIL")
        return 1
    print("VEREDITO: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
