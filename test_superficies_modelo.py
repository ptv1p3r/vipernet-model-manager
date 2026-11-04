#!/usr/bin/env python3
"""Superficies por MODELO: a segunda camada, com heranca da do perfil.

O dono pediu duas vezes: "cria a semelhanca da estrategia as superficies que ja
temos por perfil, agora tambem por modelo, com heranca do definido por perfil
ou entao definido modelo a modelo em cada perfil". A camada de perfil ja existia
(`analise.superficies` -> rota); faltava a de modelo.

As duas camadas tem proposito diferente e por isso forma diferente, de proposito:

- perfil: `superficies` -> **quem serve** (entrada / nivel / provider+modelo).
- modelo: `superficies` -> **se pode servir** (booleano). Um modelo nunca aponta
  para outro modelo: isso seria uma terceira copia da decisao.

Este ficheiro prova, cada ponto com o caso negativo:

1. O validador aceita `{superficie: bool}` e a lista de superficies (acucar),
   recusa valor nao-booleano e recusa um nome que nao existe com sugestao —
   `crom` nunca dispararia e o silencio e o pior resultado.
2. `serve_superficie`: ausente = herda (sim); `false` exclui so aquela
   superficie; `"*": false` inverte o sentido e so o que e nomeado passa.
3. A normalizacao nao perde o mapa: gravar e reler devolve-o igual (era o
   risco real — `validar()` descarta chaves que nao conhece).
4. No middleware, o modelo que recusa a superficie da sessao e saltado com
   `inviavel:superficie` e o turno vai para o seguinte que a aceita.
5. Sem mapa (heranca) nada muda: o primeiro da ordem continua a ser servido —
   o caso negativo do 4.
6. A regra do perfil nao se sobrepoe ao que o modelo recusa: se o perfil manda
   `cron` para um modelo que declarou `cron: false`, fica `excecao: superficie`
   e o turno cai na escolha normal.
7. Isolamento entre perfis: escrever no `pessoal` nao altera o `default` nem o
   global (a regra de heranca por ausencia de chave).
8. O CLI `vmm superficie modelo` grava com CAS, alterna `off`/`herdar` e recusa
   um id que nao existe.

Uso: python3 test_superficies_modelo.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

AQUI = Path(__file__).resolve().parent
_RESULTADOS: list[tuple[str, bool, str]] = []


def check(nome: str, cond: Any, detalhe: str = "") -> None:
    ok = bool(cond)
    _RESULTADOS.append((nome, ok, detalhe))
    print(("ok    " if ok else "FALHA ") + nome
          + (f"   [{detalhe}]" if detalhe and not ok else ""))


def _modelo(resp: dict | None) -> str | None:
    """O middleware devolve o envelope `{request, source, reason}`, nao o pedido."""
    if not isinstance(resp, dict):
        return None
    return (resp.get("request") or {}).get("model")


def _mod(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


g = _mod("vmm_g_supmod", AQUI / "gestor.py")


# ---------------------------------------------------------------- 1. validador
def teste_validador() -> None:
    erros: list[str] = []
    mapa = g.normalizar_superficies_modelo({"cron": False, "*": True},
                                           "catalogo[0] (a:b)", erros)
    check("validador: aceita {superficie: bool}",
          mapa == {"cron": False, "*": True} and not erros, str(mapa) + str(erros))

    erros = []
    mapa = g.normalizar_superficies_modelo(["cron", "cli"], "ctx", erros)
    check("validador: lista vale por todas a true",
          mapa == {"cron": True, "cli": True} and not erros, str(mapa))

    erros = []
    mapa = g.normalizar_superficies_modelo({"cron": "nao"}, "ctx", erros)
    check("validador: valor nao-booleano e recusado",
          mapa == {} and any("true ou false" in e for e in erros), str(erros))

    erros = []
    mapa = g.normalizar_superficies_modelo({"crom": False}, "ctx", erros)
    check("validador: nome parecido com 'cron' e recusado com sugestao",
          mapa == {} and any("parecida com 'cron'" in e for e in erros), str(erros))

    erros = []
    mapa = g.normalizar_superficies_modelo({"superficie-maluca": False}, "ctx", erros)
    check("validador: nome fora da taxonomia mas nao parecido e aceite",
          mapa == {"superficie-maluca": False}, str(mapa) + str(erros))

    erros = []
    g.normalizar_superficies_modelo("cron", "ctx", erros)
    check("validador: nao-objecto e recusado",
          any("tem de ser um objecto" in e for e in erros), str(erros))


# ------------------------------------------------------- 2. serve_superficie
def teste_serve() -> None:
    herda = {"id": "a"}
    check("serve: modelo sem mapa herda (permite)",
          g.serve_superficie(herda, "cron") is True)

    so_cron = {"id": "a", "superficies": {"cron": False}}
    check("serve: cron recusado", g.serve_superficie(so_cron, "cron") is False)
    check("serve: superficie nao declarada herda",
          g.serve_superficie(so_cron, "api_server") is True)

    fechado = {"id": "a", "superficies": {"*": False, "cron": True}}
    check("serve: coringa false recusa as nao nomeadas",
          g.serve_superficie(fechado, "cli") is False)
    check("serve: coringa false deixa passar a nomeada",
          g.serve_superficie(fechado, "cron") is True)

    check("serve: superficie vazia nunca filtra",
          g.serve_superficie(so_cron, "") is True)

    mapa_mau = {"id": "a", "superficies": "cron"}
    check("serve: mapa mal escrito nao cala o turno",
          g.serve_superficie(mapa_mau, "cron") is True)


# ---------------------------------------------------- 3. gravacao e releitura
def _cfg(entradas: list[dict] | None = None, *,
         superficies: dict | None = None,
         perfil_superficies: dict | None = None,
         perfis_extra: dict | None = None) -> dict:
    analise: dict[str, Any] = {
        "limiares": {"media": 3, "alta": 6}, "descer_apos": 2,
        "fixar_nivel_por_turno": True,
    }
    if superficies is not None:
        analise["superficies"] = superficies
    perfil: dict[str, Any] = {
        "activo": True, "estrategia": "prioridade",
        "catalogo": entradas if entradas is not None else [
            {"id": "flash", "provider": "deepseek",
             "modelo": "deepseek-flash", "complexidade": ["leve", "media"]},
            {"id": "inkling", "provider": "openrouter",
             "modelo": "thinkingmachines/inkling:free",
             "complexidade": ["leve", "media"]},
            {"id": "grande", "provider": "openrouter",
             "modelo": "outro/grande", "complexidade": ["alta"]},
        ],
    }
    if perfil_superficies is not None:
        perfil["analise"] = {"superficies": perfil_superficies}
    perfis = {"pessoal": perfil}
    if perfis_extra:
        perfis.update(perfis_extra)
    return {"activo": True, "registar": True, "analise": analise, "perfis": perfis}


class FakeAgent:
    def __init__(self, sid: str = "", platform: str = ""):
        self.provider = "deepseek"
        self.model = "deepseek-flash"
        self.session_id = sid
        self.platform = platform
        self.chamadas: list[dict] = []

    def switch_model(self, **kw):
        self.chamadas.append(kw)
        self.model = kw.get("new_model")
        self.provider = kw.get("new_provider")


class Cenario:
    def __init__(self, nome: str, cfg: dict, perfil: str = "pessoal"):
        self.dir = Path(tempfile.mkdtemp(prefix=f"vmm-supm-{nome}-"))
        (self.dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        os.environ["VMM_DIR"] = str(self.dir)
        self.plugin = _mod(f"vmm_supm_{nome}", AQUI / "__init__.py")
        self.plugin.resolver_rota = lambda perfil, prov, mod: {
            "provider": prov.lower(), "api_key": "x" * 40,
            "base_url": f"https://{prov}.test/v1", "api_mode": "chat",
            "source": "teste"}
        self.plugin.gestor.perfil_atual = lambda agente=None: perfil
        self.plugin._config()
        self.plugin._recarregar_analise_se_mudou()
        self.agente = FakeAgent(sid=f"sid-{nome}")

    def pedido(self, texto: str, *, platform: str = "") -> dict | None:
        req = {"model": "deepseek-flash",
               "messages": [{"role": "user", "content": texto}]}
        agent = self.agente
        return self.plugin.middleware_llm_request(
            request=req, session_id=agent.session_id,
            model="deepseek-flash", provider="deepseek",
            turn_id=f"t-{platform}-{texto[:8]}", api_call_count=1,
            platform=platform)

    def eventos(self) -> list[dict]:
        caminho = self.plugin.gestor.caminho_ledger()
        if not caminho.exists():
            return []
        return [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines()
                if l.strip()]


def teste_gravacao() -> None:
    cfg = _cfg(entradas=[
        {"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
         "complexidade": ["leve", "media"], "superficies": {"cron": False}},
        {"id": "inkling", "provider": "openrouter",
         "modelo": "thinkingmachines/inkling:free", "complexidade": ["leve", "media"]},
    ])
    d = Path(tempfile.mkdtemp(prefix="vmm-supm-grava-"))
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    os.environ["VMM_DIR"] = str(d)
    gestor = _mod("vmm_g_grava", AQUI / "gestor.py")
    lido, erros = gestor.carregar()
    check("gravacao: config com superficies de modelo carrega sem erro",
          not erros, str(erros))
    mapa = gestor.superficies_do_modelo(lido["perfis"]["pessoal"], "flash")
    check("gravacao: o mapa sobrevive a normalizacao",
          mapa == {"cron": False}, str(mapa))
    check("gravacao: modelo sem mapa devolve None (heranca)",
          gestor.superficies_do_modelo(lido["perfis"]["pessoal"], "inkling") is None)


# -------------------------------------------------------- 4/5. middleware
def teste_middleware_filtra() -> None:
    c = Cenario("filtra", _cfg(entradas=[
        {"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
         "complexidade": ["leve", "media"], "superficies": {"cron": False}},
        {"id": "inkling", "provider": "openrouter",
         "modelo": "thinkingmachines/inkling:free", "complexidade": ["leve", "media"]},
    ]))
    req = c.pedido("olá", platform="cron")
    check("middleware: cron salta o modelo que recusa cron",
          _modelo(req) == "thinkingmachines/inkling:free", str(_modelo(req)))
    ev = c.eventos()
    ultimo = ev[-1] if ev else {}
    check("middleware: evento diz porque saltou",
          "flash:inviavel:superficie:cron" in str(ultimo.get("preteridas", [])),
          str(ultimo.get("preteridas")))

    # Caso negativo: fora do cron, o primeiro da ordem (flash) serve.
    c2 = Cenario("semfiltro", _cfg(entradas=[
        {"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
         "complexidade": ["leve", "media"], "superficies": {"cron": False}},
        {"id": "inkling", "provider": "openrouter",
         "modelo": "thinkingmachines/inkling:free", "complexidade": ["leve", "media"]},
    ]))
    c2.pedido("olá", platform="cli")
    ev2 = c2.eventos()
    ult2 = ev2[-1] if ev2 else {}
    check("middleware: fora da superficie recusada o primeiro serve",
          c2.agente.model == "deepseek-flash"
          and "inviavel:superficie" not in str(ult2.get("preteridas", [])),
          str(ult2.get("preteridas")))


def teste_middleware_heranca() -> None:
    c = Cenario("heranca", _cfg())
    req = c.pedido("olá", platform="cron")
    # Sem mapa de modelo nao ha troca nenhuma: o modelo da sessao ja e o
    # primeiro da ordem, logo o middleware devolve None. E o caso negativo.
    check("heranca: sem mapa de modelo o perfil decide (nada a trocar)",
          req is None and c.agente.model == "deepseek-flash",
          str(req) + " / " + c.agente.model)


# ------------------------------------------ 6. perfil nao sobrepoe ao modelo
def teste_perfil_cede() -> None:
    c = Cenario("cede", _cfg(
        entradas=[
            {"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
             "complexidade": ["leve", "media"], "superficies": {"cron": False}},
            {"id": "inkling", "provider": "openrouter",
             "modelo": "thinkingmachines/inkling:free",
             "complexidade": ["leve", "media"]},
        ],
        perfil_superficies={"cron": {"entrada": "flash"}}))
    req = c.pedido("olá", platform="cron")
    ev = c.eventos()
    ultimo = ev[-1] if ev else {}
    check("perfil: regra do perfil cede ao modelo que recusa",
          ultimo.get("excecao") == "superficie",
          str(ultimo.get("excecao")) + " / " + str(ultimo.get("excecao_motivo")))
    check("perfil: e o turno vai para quem aceita",
          _modelo(req) == "thinkingmachines/inkling:free", str(_modelo(req)))


# ------------------------------------------------- 7. isolamento entre perfis
def teste_isolamento() -> None:
    outro = {"activo": True, "estrategia": "prioridade",
             "catalogo": [{"id": "flash", "provider": "deepseek",
                           "modelo": "deepseek-flash",
                           "complexidade": ["leve", "media"]}]}
    cfg = _cfg(perfis_extra={"default": outro})
    d = Path(tempfile.mkdtemp(prefix="vmm-supm-iso-"))
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    os.environ["VMM_DIR"] = str(d)
    gestor = _mod("vmm_g_iso", AQUI / "gestor.py")

    def mutar(c):
        c["perfis"]["pessoal"]["catalogo"][0]["superficies"] = {"cron": False}

    gestor.alterar(mutar)
    lido, _ = gestor.carregar()
    check("isolamento: pessoal ficou com o mapa",
          gestor.superficies_do_modelo(lido["perfis"]["pessoal"], "flash")
          == {"cron": False})
    check("isolamento: default nao ganhou mapa",
          gestor.superficies_do_modelo(lido["perfis"]["default"], "flash") is None)
    check("isolamento: global sem mapa de modelo nenhum",
          not any(isinstance(e.get("superficies"), dict)
                  for e in ((lido.get("perfis", {}).get("default") or {})
                            .get("catalogo") or [])))


# ------------------------------------------------------------------- 8. CLI
def teste_cli() -> None:
    cfg = _cfg()
    d = Path(tempfile.mkdtemp(prefix="vmm-supm-cli-"))
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    env = dict(os.environ, VMM_DIR=str(d))

    def vmm(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(AQUI / "cli.py"), *args],
                              capture_output=True, text=True, env=env, timeout=90)

    r = vmm("superficie", "modelo", "pessoal", "flash", "cron", "off")
    check("cli: 'off' grava", r.returncode == 0, r.stderr.strip())
    lido = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("cli: o config no disco tem cron=false no flash",
          lido["perfis"]["pessoal"]["catalogo"][0].get("superficies") == {"cron": False},
          str(lido["perfis"]["pessoal"]["catalogo"][0].get("superficies")))

    r = vmm("superficie", "modelo", "pessoal", "flash", "cron", "herdar")
    lido = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("cli: 'herdar' remove a chave (volta a heranca)",
          "superficies" not in lido["perfis"]["pessoal"]["catalogo"][0],
          str(lido["perfis"]["pessoal"]["catalogo"][0]))

    r = vmm("superficie", "modelo", "pessoal", "nao-existe", "cron", "off")
    check("cli: id inexistente e recusado com rc 3",
          r.returncode == 3 and "nao existe" in r.stderr, r.stderr.strip()[:120])

    r = vmm("superficie", "list", "pessoal")
    check("cli: 'list' corre sem mapa declarado",
          r.returncode == 0, r.stderr.strip())


def main() -> int:
    teste_validador()
    teste_serve()
    teste_gravacao()
    teste_middleware_filtra()
    teste_middleware_heranca()
    teste_perfil_cede()
    teste_isolamento()
    teste_cli()
    falhas = [n for n, ok, _ in _RESULTADOS if not ok]
    print(f"\n{len(_RESULTADOS) - len(falhas)}/{len(_RESULTADOS)} verificacoes ok")
    if falhas:
        print("falhas: " + ", ".join(falhas))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
