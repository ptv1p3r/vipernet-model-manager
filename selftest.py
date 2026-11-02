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

    r = vmm("simular", "pessoal", "Implementa isto em /opt/data/x.py e corre os testes")
    check("simular classifica texto de engenharia acima de leve",
          r.returncode == 0 and ("media" in r.stdout or "alta" in r.stdout), r.stdout)

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


def main() -> int:
    testes_gestor()
    testes_plugin()
    testes_realimentacao()
    testes_cli()
    testes_verificar()
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
