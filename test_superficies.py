#!/usr/bin/env python3
"""Superficie -> rota: a prova unitaria do criterio de contexto de sessao.

O dono pediu: "poder indicar por perfil e por modelo que se a superficie for
por exemplo cron entao seja executado por um determinado modelo dentro de um
determinado perfil".

Antes disto o plugin so tinha dois criterios: `tarefas` (palavra-chave no
prompt) e a analise de complexidade (estimativa). Nenhum distingue um turno
interactivo de uma execucao nao assistida. Medido a 2026-11-03 no ledger:
464 eventos de sessoes `cron`, 273 (59%) classificados `alta`, servidos quase
todos por `deepseek/deepseek-flash` — as execucoes que ninguem esta a ver sao
as que menos previsibilidade tem.

Este ficheiro prova, cada ponto com o seu caso negativo:

1. O validador aceita as tres formas (`entrada`, `provider`+`modelo`,
   `nivel`) e recusa o resto, com a mensagem no contexto certo.
2. Uma regra que aponta para uma entrada inexistente e RECUSADA na gravacao
   — nunca resolvia, e o turno caia na analise em silencio.
3. A regra global e herdada; um perfil pode anula-la com `activo: false` sem
   apagar a do global.
4. Prioridade: marcador explicito > superficie > tarefa > analise. Prova-se
   nos dois sentidos — a superficie vence a tarefa, e o marcador vence a
   superficie.
5. No middleware, `platform="cron"` troca a rota para a entrada da regra, e
   o evento fica com `origem: superficie` e `superficie: cron`.
6. Uma superficie sem regra nao muda nada (caso negativo do 5).
7. Capacidade vence a ordem: se o modelo da regra nao aceita o pedido (ex.:
   imagem), a superficie cede e fica marcado `excecao: capacidade` — nao ha
   troca silenciosa para o modelo errado.

Uso: python3 test_superficies.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import json
import os
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


def _mod(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _cfg(*, superficies: dict | None = None,
         perfil_superficies: dict | None = None) -> dict:
    """Catalogo com dois modelos `leve` e um `alta`, e a regra pedida."""
    analise: dict[str, Any] = {
        "limiares": {"media": 3, "alta": 6}, "descer_apos": 2,
        "fixar_nivel_por_turno": True,
    }
    if superficies is not None:
        analise["superficies"] = superficies
    perfil: dict[str, Any] = {
        "activo": True, "estrategia": "prioridade",
        "catalogo": [
            {"id": "flash", "provider": "deepseek",
             "modelo": "deepseek-flash", "complexidade": ["leve", "media"]},
            {"id": "inkling", "provider": "openrouter",
             "modelo": "thinkingmachines/inkling:free",
             "complexidade": ["leve", "media"]},
            {"id": "flash-alta", "provider": "openrouter",
             "modelo": "outro/grande", "complexidade": ["alta"]},
        ],
    }
    if perfil_superficies is not None:
        perfil["analise"] = {"superficies": perfil_superficies}
    return {"activo": True, "registar": True, "analise": analise,
            "perfis": {"pessoal": perfil}}


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
    def __init__(self, nome: str, cfg: dict):
        self.dir = Path(tempfile.mkdtemp(prefix=f"vmm-sup-{nome}-"))
        (self.dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        os.environ["VMM_DIR"] = str(self.dir)
        self.plugin = _mod(f"vmm_sup_{nome}", AQUI / "__init__.py")
        self.plugin.resolver_rota = lambda perfil, prov, mod: {
            "provider": prov.lower(), "api_key": "x" * 40,
            "base_url": f"https://{prov}.test/v1", "api_mode": "chat",
            "source": "teste"}
        self.plugin.gestor.perfil_atual = lambda agente=None: "pessoal"
        self.plugin._config()
        self.plugin._recarregar_analise_se_mudou()
        self.agente = FakeAgent(sid=f"sid-{nome}")

    def pedido(self, texto: str, *, platform: str = "",
               imagens: bool = False) -> dict | None:
        conteudo: Any = texto
        if imagens:
            conteudo = [{"type": "text", "text": texto},
                        {"type": "image_url",
                         "image_url": {"url": "data:image/png;base64,AAAA"}}]
        req = {"model": "deepseek-flash",
               "messages": [{"role": "user", "content": conteudo}]}
        # `agente_vivo` procura `agent` na pilha: a variavel tem de se chamar
        # `agent` no frame chamador, nao `self.agente`.
        agent = self.agente
        r = self.plugin.middleware_llm_request(
            request=req, session_id=agent.session_id,
            model="deepseek-flash", provider="deepseek",
            turn_id=f"t-{platform}-{texto[:8]}", api_call_count=1,
            platform=platform)
        # A `resposta` e o desfecho de cada chamada e, desde que o `sem_troca`
        # saiu, leva o rasto da decisao (superficie, entrada, excecao, nivel).
        self.plugin.pos_resposta(
            session_id=agent.session_id, model=agent.model, provider=agent.provider,
            response_model=agent.model, finish_reason="stop",
            usage={"total_tokens": 10}, platform=platform,
            turn_id=f"t-{platform}-{texto[:8]}", api_call_count=1)
        return r

    def eventos(self) -> list[dict]:
        caminho = self.plugin.gestor.caminho_ledger()
        if not caminho.exists():
            return []
        return [json.loads(l) for l in
                caminho.read_text(encoding="utf-8").splitlines() if l.strip()]

    def decididos(self) -> list[dict]:
        return [e for e in self.eventos()
                if str(e.get("evento") or "").startswith(("troca", "sem_"))
                or e.get("evento") == "resposta"]


def testes() -> None:
    gestor = _mod("vmm_gestor_sup", AQUI / "gestor.py")

    # ------------------------------------------------------------------ 1
    print("\n== 1. validador: as tres formas e o resto ==")
    erros: list[str] = []
    ok = gestor.normalizar_superficies({
        "cron": {"entrada": "flash-alta"},
        "api_server": {"provider": "deepseek", "modelo": "deepseek-flash"},
        "cli": {"nivel": "alta"},
    }, "x", erros)
    check("aceita entrada, provider+modelo e nivel", not erros and set(ok) ==
          {"cron", "api_server", "cli"}, str(erros))
    check("normaliza o nome da superficie para minusculas",
          gestor.normalizar_superficies({" CRON ": {"nivel": "leve"}}, "x", [])
          .get("cron") == {"nivel": "leve"})

    erros = []
    gestor.normalizar_superficies({"cron": {"provider": "deepseek"}}, "x", erros)
    check("recusa provider sem modelo", any("vão juntos" in e or "vao juntos" in e
                                          for e in erros), str(erros))

    erros = []
    gestor.normalizar_superficies({"cron": {}}, "x", erros)
    check("recusa regra vazia", any("sem entrada" in e for e in erros), str(erros))

    erros = []
    gestor.normalizar_superficies({"cron": {"nivel": "enorme"}}, "x", erros)
    check("recusa nivel invalido", any("nivel invalido" in e for e in erros), str(erros))

    erros = []
    gestor.normalizar_superficies({"crom": {"nivel": "alta"}}, "x", erros)
    check("recusa gralha de superficie conhecida",
          any("parecida com 'cron'" in e for e in erros), str(erros))
    check("a gralha nao entra na config",
          gestor.normalizar_superficies({"crom": {"nivel": "alta"}}, "x", []) == {})

    # Superficie genuinamente nova (provider novo, contexto novo) e aceite: a
    # lista e um anti-gralha, nao uma lista fechada.
    erros = []
    novas = gestor.normalizar_superficies({"mcp": {"entrada": "a"}}, "x", erros,
                                          catalogo=[{"id": "a"}])
    check("superficie nova e aceite", novas == {"mcp": {"entrada": "a"}} and not erros,
          str(erros))

    erros = []
    gestor.normalizar_superficies({"cron": "alta"}, "x", erros)
    check("recusa regra que nao e objecto", any("tem de ser um objecto" in e
                                                for e in erros), str(erros))

    # ------------------------------------------------------------------ 2
    print("\n== 2. gravacao: entrada inexistente e recusada ==")
    # `validar` devolve (config_normalizada, erros): invalido = erros nao vazios.
    cfg = gestor.validar(_cfg(superficies={"cron": {"entrada": "nao-existe"}}))
    check("config com entrada inexistente e invalida", bool(cfg[1]), str(cfg[1])[:200])
    check("o erro nomeia a superficie e o id",
          any("cron" in e and "nao-existe" in e for e in cfg[1]), str(cfg[1])[:300])

    cfg2 = gestor.validar(_cfg(superficies={"cron": {"entrada": "flash-alta"}}))
    check("config com entrada real e valida", not cfg2[1], str(cfg2[1])[:300])
    check("a regra sobrevive normalizada",
          (cfg2[0] or {}).get("analise", {}).get("superficies", {}).get("cron")
          == {"entrada": "flash-alta"}, str((cfg2[0] or {}).get("analise")))

    cfg3 = gestor.validar(_cfg(
        superficies={"cron": {"entrada": "flash"}},
        perfil_superficies={"cron": {"entrada": "nao-existe"}}))
    check("o perfil nao pode apontar para fora do SEU catalogo", bool(cfg3[1]),
          str(cfg3[1])[:300])

    # ------------------------------------------------------------------ 3
    print("\n== 3. heranca e anulacao ==")
    c = Cenario("heranca", _cfg(superficies={"cron": {"entrada": "flash-alta"}}))
    pcfg = c.plugin.gestor.perfil_config(c.plugin._config(), "pessoal")
    regra = c.plugin.gestor.regra_de_superficie(pcfg, "cron")
    check("o perfil herda a regra do global", regra is not None
          and regra["entrada"]["id"] == "flash-alta", str(regra))
    check("superficie sem regra devolve None",
          c.plugin.gestor.regra_de_superficie(pcfg, "whatsapp") is None)

    c2 = Cenario("anula", _cfg(superficies={"cron": {"entrada": "flash-alta"}},
                               perfil_superficies={"cron": {"activo": False}}))
    pcfg2 = c2.plugin.gestor.perfil_config(c2.plugin._config(), "pessoal")
    check("`activo: false` no perfil anula a regra herdada",
          c2.plugin.gestor.regra_de_superficie(pcfg2, "cron") is None)
    check("a regra do global continua la",
          (c2.plugin._config().get("analise", {}).get("superficies") or {})
          .get("cron") == {"entrada": "flash-alta"})

    # Uma regra por perfil nao pode apagar as regras globais das outras
    # superficies: a heranca e superficie a superficie, nao bloco a bloco.
    c2b = Cenario("misto", _cfg(superficies={"cli": {"entrada": "flash-alta"}},
                                perfil_superficies={"cron": {"nivel": "alta"}}))
    pcfg2b = c2b.plugin.gestor.perfil_config(c2b.plugin._config(), "pessoal")
    check("o perfil ve a sua regra e a global ao mesmo tempo",
          c2b.plugin.gestor.regra_de_superficie(pcfg2b, "cron") is not None
          and c2b.plugin.gestor.regra_de_superficie(pcfg2b, "cli") is not None,
          str((pcfg2b.get("analise") or {}).get("superficies")))

    # ------------------------------------------------------------------ 4 e 5
    print("\n== 4 e 5. prioridade e troca no middleware ==")
    c3 = Cenario("troca", _cfg(superficies={"cron": {"entrada": "flash-alta"}}))
    r = c3.pedido("ola, bom dia", platform="cron")
    troca = c3.decididos()
    check("superficie cron escolhe a entrada da regra",
          bool(troca) and troca[-1].get("entrada") == "flash-alta",
          str(troca[-1] if troca else c3.decididos()))
    check("o evento declara a superficie",
          bool(troca) and troca[-1].get("superficie") == "cron", str(troca[-1]))
    check("a origem e a superficie, nao a analise",
          bool(troca) and troca[-1].get("origem") == "superficie", str(troca[-1]))

    # Caso negativo: a mesma mensagem sem superficie segue a analise.
    c4 = Cenario("sem-regra", _cfg(superficies={"cron": {"entrada": "flash-alta"}}))
    c4.pedido("ola, bom dia", platform="api_server")
    troca4 = c4.decididos()
    check("superficie sem regra nao muda a rota",
          bool(troca4) and troca4[-1].get("entrada") != "flash-alta",
          str(troca4[-1] if troca4 else c4.decididos()))
    check("superficie sem regra continua registada",
          bool(troca4) and troca4[-1].get("superficie") == "api_server",
          str(troca4[-1]))

    # Marcador explicito vence a superficie.
    c5 = Cenario("marcador", _cfg(superficies={"cron": {"entrada": "inkling"}}))
    c5.pedido("Responde apenas OK. complexidade: alta", platform="cron")
    troca5 = c5.decididos()
    check("marcador explicito vence a superficie",
          bool(troca5) and troca5[-1].get("entrada") == "flash-alta",
          str(troca5[-1] if troca5 else c5.decididos()))
    check("o evento diz que a origem foi o marcador",
          bool(troca5) and troca5[-1].get("origem") == "marcador", str(troca5[-1]))

    # Superficie vence a tarefa.
    cfg_tarefa = _cfg(superficies={"cron": {"entrada": "flash-alta"}})
    cfg_tarefa["perfis"]["pessoal"]["tarefas"] = {
        "cumprimento": {"termos": ["bom dia"], "prioridade": 5}}
    cfg_tarefa["perfis"]["pessoal"]["catalogo"][1]["tarefas"] = ["cumprimento"]
    c6 = Cenario("vence-tarefa", cfg_tarefa)
    c6.pedido("bom dia", platform="cron")
    troca6 = c6.decididos()
    check("superficie vence a tarefa associada",
          bool(troca6) and troca6[-1].get("entrada") == "flash-alta",
          str(troca6[-1] if troca6 else c6.decididos()))
    check("sem superficie a tarefa voltaria a mandar",
          (lambda cc: (cc.pedido("bom dia", platform="api_server"),
                       [e for e in cc.decididos()
                        if e.get("evento") == "troca"][-1].get("origem"))[1])(
              Cenario("tarefa-so", cfg_tarefa)) == "tarefa")

    # ------------------------------------------------------------------ 6
    print("\n== 6. nivel como regra ==")
    c7 = Cenario("nivel", _cfg(superficies={"cli": {"nivel": "alta"}}))
    c7.pedido("ola", platform="cli")
    troca7 = c7.decididos()
    check("regra por nivel leva o turno ao modelo do nivel",
          bool(troca7) and troca7[-1].get("entrada") == "flash-alta",
          str(troca7[-1] if troca7 else c7.decididos()))
    check("a origem e superficie",
          bool(troca7) and troca7[-1].get("origem") == "superficie", str(troca7[-1]))

    # ------------------------------------------------------------------ 7
    print("\n== 7. capacidade vence a ordem (e fica declarado) ==")
    cfg_img = _cfg(superficies={"cron": {"entrada": "inkling"}})
    for e in cfg_img["perfis"]["pessoal"]["catalogo"]:
        if e["id"] == "inkling":
            e["modalidades"] = ["texto"]
    c8 = Cenario("capacidade", cfg_img)
    c8.pedido("descreve esta imagem", platform="cron", imagens=True)
    ev = c8.decididos()
    troca8 = ev
    check("com imagem o modelo textual da regra nao serve",
          bool(troca8) and troca8[-1].get("entrada") != "inkling",
          str(troca8[-1] if troca8 else ev))
    check("a recusa da regra fica marcada com excecao de capacidade",
          any(e.get("excecao") == "capacidade"
              and "superficie" in str(e.get("excecao_motivo")) for e in ev),
          str([e.get("excecao_motivo") for e in ev]))

    # ------------------------------------------------------------------ rc
    falhas = [r for r in _RESULTADOS if not r[1]]
    print(f"\n{len(_RESULTADOS) - len(falhas)}/{len(_RESULTADOS)} verificacoes OK")
    if falhas:
        print("VEREDITO: FAIL")
        for nome, _, det in falhas:
            print(f"  - {nome}: {det}")
        raise SystemExit(1)
    print("VEREDITO: PASS")


if __name__ == "__main__":
    testes()
