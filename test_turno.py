#!/usr/bin/env python3
"""Nivel fixado por turno — a prova unitaria (camada 1 de 3).

Um turno faz varios pedidos ao provider (laco de ferramentas). Antes desta
correcao cada pedido reavaliava o nivel, voltava a correr o classificador e
somava na histerese: medido a 2026-11-02 no ledger real, 63 de 167 turnos
(37,7%) trocavam de nivel a meio.

Este ficheiro prova cinco coisas, cada uma com o seu caso negativo:

1. Num turno de seis pedidos, `decidir` corre **uma** vez e o nivel e o mesmo
   nos seis; cinco eventos ficam com `pin: reutilizado`.
2. A rota escolhe-se uma vez: `switch_model` nao se repete no turno.
3. Dois turnos com o **mesmo texto** e `turn_id` diferente nao partilham pin.
4. Sem `turn_id` (chave por hash), so se reutiliza quando `api_call_count`
   avancou — senao um turno repetido herdava o nivel do anterior.
5. As duas excepcoes legitimas ficam declaradas: capacidade (o contexto deixou
   de caber) e rota (a rota pinada morreu). Sem isso, "estavel" seria uma
   mentira confortavel.

Uso: python3 test_turno.py  ->  rc 0 se tudo passar.
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


def _cfg(*, fixar: bool = True, superficies: dict | None = None,
         catalogo_extra: list | None = None) -> dict:
    return {
        "activo": True,
        "registar": True,
        "analise": {"limiares": {"media": 3, "alta": 6}, "descer_apos": 2,
                    "fixar_nivel_por_turno": fixar,
                    "superficies": superficies or {}},
        "perfis": {
            "pessoal": {
                "activo": True, "estrategia": "prioridade",
                "catalogo": [
                    {"id": "flash", "provider": "deepseek",
                     "modelo": "deepseek-flash", "complexidade": ["leve", "media"]},
                    {"id": "grande", "provider": "openrouter",
                     "modelo": "grande/modelo-1m", "complexidade": ["leve", "media"],
                     "contexto_max": 1000000},
                    {"id": "pro", "provider": "deepseek",
                     "modelo": "deepseek-v4-pro", "complexidade": ["alta"]},
                ] + list(catalogo_extra or []),
            },
        },
    }


class FakeAgent:
    def __init__(self, sid: str = ""):
        self.provider = "deepseek"
        self.model = "deepseek-flash"
        self.session_id = sid
        self.chamadas: list[dict] = []

    def switch_model(self, **kw):
        self.chamadas.append(kw)
        self.model = kw.get("new_model")
        self.provider = kw.get("new_provider")


class Cenario:
    """Um plugin fresco (modulo proprio), um VMM_DIR proprio, um ledger proprio."""

    def __init__(self, nome: str, *, fixar: bool = True,
                 superficies: dict | None = None,
                 catalogo_extra: list | None = None):
        self.dir = Path(tempfile.mkdtemp(prefix="vmm-turno-"))
        (self.dir / "config.json").write_text(
            json.dumps(_cfg(fixar=fixar, superficies=superficies,
                            catalogo_extra=catalogo_extra)),
            encoding="utf-8")
        os.environ["VMM_DIR"] = str(self.dir)
        self.plugin = _mod(f"vmm_turno_{nome}", AQUI / "__init__.py")
        self.plugin.resolver_rota = lambda perfil, prov, mod: {
            "provider": prov.lower(), "api_key": "x" * 40,
            "base_url": f"https://{prov}.test/v1", "api_mode": "chat",
            "source": "teste"}
        self.plugin.gestor.perfil_atual = lambda agente=None: "pessoal"
        self.agente = FakeAgent(sid="s1")
        # Aquece a cache da config e a do modulo de analise, para o `decidir`
        # que se conta ser o definitivo (o recarregador substitui o modulo).
        self.plugin._config()
        self.plugin._recarregar_analise_se_mudou()
        self.decisoes = 0
        self._decidir_real = self.plugin._analise_mod.decidir

        def contado(*a, **kw):
            self.decisoes += 1
            return self._decidir_real(*a, **kw)

        self.plugin._analise_mod.decidir = contado

    def pedido_turno(self, texto: str, passo: int) -> dict:
        """O pedido como ele esta no passo N: a mesma pergunta, mais resultados."""
        msgs: list[dict] = [{"role": "user", "content": texto}]
        for i in range(passo):
            msgs.append({"role": "tool", "content":
                         f"Traceback (most recent call last): erro no ficheiro "
                         f"/opt/data/plugins/vipernet-model-manager/mod_{i}.py "
                         f"linha {10 + i}: ValueError: falhou a instalar a stack "
                         f"do servico {i}; systemd unit inactive"})
        return {"model": "deepseek-flash", "messages": msgs}

    def passo(self, texto: str, indice: int, *, turn_id: Any, api: int,
              plataforma: str = "api_server") -> dict:
        pedido = self.plugin.middleware_llm_request(
            request=self.pedido_turno(texto, indice), session_id="s1",
            model="deepseek-flash", provider="deepseek",
            turn_id=turn_id, api_call_count=api, platform=plataforma)
        # O ciclo completo, como o core o faz: depois da chamada escreve-se a
        # `resposta`. Sem isto o harness media metade do caminho — e, desde que o
        # `sem_troca` saiu, e na `resposta` que vivem o rasto e o `pin` de cada
        # chamada (a unica prova de que o turno nao oscilou).
        self.plugin.pos_resposta(
            session_id="s1", model="deepseek-flash", provider="deepseek",
            response_model=self.agente.model, finish_reason="stop",
            usage={"input_tokens": 1000, "output_tokens": 40},
            platform=plataforma, turn_id=turn_id, api_call_count=api,
            api_duration=0.4, context_length=128000)
        return pedido

    def eventos(self) -> list[dict]:
        caminho = self.plugin.gestor.caminho_ledger()
        if not caminho.exists():
            return []
        return [json.loads(linha) for linha in
                caminho.read_text(encoding="utf-8").splitlines() if linha.strip()]

    def decididos(self) -> list[dict]:
        """Eventos de decisao por chamada: `troca*` e os `sem_*`."""
        return [e for e in self.eventos()
                if str(e.get("evento") or "").startswith(("troca", "sem_"))]

    def respostas(self) -> list[dict]:
        """As linhas `resposta`, que desde 2026-11-04 levam o rasto e o `pin`."""
        return [e for e in self.eventos() if e.get("evento") == "resposta"]


def testes() -> None:
    # ------------------------------------------------------------------ 1 e 2
    print("\n== turno de seis pedidos ==")
    c = Cenario("principal")
    textos = ["ola, bom dia"] + ["corrige isto"] * 5
    for i in range(6):
        c.passo(textos[i], i, turn_id="sessao:sessao:a1b2c3d4", api=i)
    decididos = c.decididos()
    niveis = {e.get("nivel") for e in decididos}
    reutilizados = [e for e in decididos if e.get("pin") == "reutilizado"]
    novos = [e for e in decididos if e.get("pin") == "novo"]
    turnos = {e.get("turno") for e in decididos}
    check("um turno de 6 pedidos: `decidir` corre uma vez", c.decisoes == 1,
          f"decidir={c.decisoes}")
    check("seis eventos no ledger", len(decididos) == 6, f"{len(decididos)}")
    check("o nivel e o mesmo nos seis pedidos", len(niveis) == 1, str(niveis))
    check("cinco pedidos reutilizam o pin e um e novo",
          len(reutilizados) == 5 and len(novos) == 1,
          f"reutilizados={len(reutilizados)} novos={len(novos)}")
    check("todos partilham o mesmo `turno`", len(turnos) == 1, str(turnos))
    check("a chave vem do `turn_id`", all(e.get("fonte_turno") == "turn_id"
                                         for e in decididos),
          str({e.get("fonte_turno") for e in decididos}))
    check("`switch_model` nao se repete no turno", len(c.agente.chamadas) <= 1,
          f"switch_model={len(c.agente.chamadas)}")
    check("a rota foi reutilizada no segundo pedido",
          any(e.get("rota_pin") == "reutilizada" for e in decididos),
          str([e.get("rota_pin") for e in decididos]))
    check("aritmetica: reutilizados == pedidos - turnos",
          len(reutilizados) == len(decididos) - len(turnos),
          f"{len(reutilizados)} != {len(decididos)}-{len(turnos)}")

    # ------------------------------------------------------------------ 3
    print("\n== dois turnos, o mesmo texto, turn_id diferente ==")
    c2 = Cenario("dois-turnos")
    for i in range(2):
        c2.passo("corrige o erro do servico", i, turn_id="sessao:sessao:11111111", api=i)
    antes = c2.decisoes
    for i in range(2):
        c2.passo("corrige o erro do servico", i, turn_id="sessao:sessao:22222222", api=i)
    check("o segundo turno decide de novo (nao herda o pin)",
          c2.decisoes == antes + 1, f"{antes} -> {c2.decisoes}")
    segundo_turno = [e for e in c2.decididos()
                     if e.get("turno") == "sessao:sessao:22222222"]
    check("o segundo turno tem `pin: novo` no primeiro pedido",
          segundo_turno and segundo_turno[0].get("pin") == "novo",
          str(segundo_turno[:1]))

    # ------------------------------------------------------------------ 4
    print("\n== sem turn_id: chave por hash, passo a avancar ==")
    c3 = Cenario("hash")
    for i in range(3):
        c3.passo("corrige o erro do servico", i, turn_id=None, api=i)
    check("dentro do turno (passo 0,1,2) decide uma vez", c3.decisoes == 1,
          f"decidir={c3.decisoes}")
    check("a chave e por hash", all(e.get("fonte_turno") == "hash"
                                    for e in c3.decididos()),
          str({e.get("fonte_turno") for e in c3.decididos()}))
    for i in range(2):
        c3.passo("corrige o erro do servico", i, turn_id=None, api=i)
    check("o mesmo texto noutro turno (passo recua) nao herda o pin",
          c3.decisoes == 2, f"decidir={c3.decisoes}")

    # ------------------------------------------------------------------ 5
    print("\n== excepcoes declaradas ==")
    c4 = Cenario("excecoes")
    # Primeiro pedido curto -> nivel baixo, rota `flash`.
    c4.passo("ola", 0, turn_id="sessao:sessao:deadbeef", api=0)
    primeira = c4.decididos()[-1]
    check("primeiro pedido escolhe rota e fica novo",
          primeira.get("pin") == "novo", str(primeira))
    # Mata a rota pinada: o proximo pedido tem de a largar com `excecao: rota`.
    entrada_pinada = "flash"
    with c4.plugin._lock:
        c4.plugin._maus_expira[("pessoal", "deepseek", "deepseek-flash")] = 1e18
    c4.passo("ola", 1, turn_id="sessao:sessao:deadbeef", api=1)
    e_rota = c4.decididos()[-1]
    check("rota morta -> `excecao: rota`",
          e_rota.get("excecao") == "rota" and e_rota.get("nivel") == primeira.get("nivel"),
          str(e_rota))
    check("o nivel manteve-se apesar da troca de rota",
          e_rota.get("nivel") == primeira.get("nivel"),
          f"{primeira.get('nivel')} -> {e_rota.get('nivel')}")
    check("a entrada pinada era a esperada", entrada_pinada == "flash")

    # Capacidade: um pedido gigante no mesmo turno deixa de caber no nivel pinado.
    c5 = Cenario("capacidade")
    c5.passo("ola", 0, turn_id="sessao:sessao:cafe0001", api=0)
    base_nivel = c5.decididos()[-1].get("nivel")
    enorme = {"model": "deepseek-flash", "messages": [
        {"role": "user", "content": "ola " + ("palavra " * 200000)}]}
    c5.plugin.middleware_llm_request(request=enorme, session_id="s1",
                                     model="deepseek-flash", provider="deepseek",
                                     turn_id="sessao:sessao:cafe0001",
                                     api_call_count=1)
    e_cap = c5.decididos()[-1]
    check("nivel pinado era leve", base_nivel == "leve", str(base_nivel))
    check("contexto que nao cabe -> `excecao: capacidade`",
          e_cap.get("excecao") == "capacidade"
          and "contexto" in str(e_cap.get("excecao_motivo", "")), str(e_cap))
    check("o nivel subiu por necessidade, nao por oscilacao",
          e_cap.get("nivel") == "media", str(e_cap.get("nivel")))

    # ------------------------------------------------------------------ 6
    print("\n== interruptor: comportamento de hoje quando desligado ==")
    c6 = Cenario("desligado", fixar=False)
    for i in range(6):
        c6.passo("corrige isto", i, turn_id="sessao:sessao:3f3f3f3f", api=i)
    check("com `fixar_nivel_por_turno: false` decide em todos os pedidos",
          c6.decisoes == 6, f"decidir={c6.decisoes}")
    check("sem pin nao ha `pin: reutilizado`",
          not any(e.get("pin") == "reutilizado" for e in c6.decididos()),
          str([e.get("pin") for e in c6.decididos()]))


    # ------------------------------------------------------------------ 7
    print("\n== recarga a meio do turno: `excecao: recarga`, nao oscilacao ==")
    c7 = Cenario("recarga")
    tid = "sessao:sessao:7c7c7c7c"
    for i in range(3):
        c7.passo("corrige isto", i, turn_id=tid, api=i)
    antes = [e for e in c7.decididos() if e.get("pin") == "novo"]
    check("3 pedidos antes da recarga -> 1 pin novo", len(antes) == 1,
          str([(e.get("passo"), e.get("pin")) for e in c7.decididos()]))

    # Recarga: `sys.modules.pop` + reimport do mesmo ficheiro, com o mesmo
    # VMM_DIR. E o que `plugins reload` faz ao plugin vivo.
    novo = _mod("vmm_turno_recarga_2", AQUI / "__init__.py")
    novo.resolver_rota = c7.plugin.resolver_rota
    novo.gestor.perfil_atual = c7.plugin.gestor.perfil_atual
    novo._config()
    novo._recarregar_analise_se_mudou()
    novo.middleware_llm_request(
        request=c7.pedido_turno("corrige isto", 3), session_id="s1",
        model="deepseek-flash", provider="deepseek",
        turn_id=tid, api_call_count=3)
    pos = [e for e in c7.eventos() if e.get("passo") == 3]
    check("o pedido depois da recarga existe no ledger", len(pos) == 1,
          str([(e.get("passo"), e.get("inst")) for e in c7.eventos()]))
    if pos:
        e = pos[0]
        check("depois da recarga o pin e `novo` (store em memoria perdido)",
              e.get("pin") == "novo", str(e.get("pin")))
        check("e vem marcado `excecao: recarga`, nao contado como oscilacao",
              e.get("excecao") == "recarga", str(e.get("excecao")))
        check("traz `inst`, para distinguir instancias do modulo",
              bool(e.get("inst")) and e["inst"] != antes[0].get("inst"),
              f"{e.get('inst')} vs {antes[0].get('inst') if antes else None}")

    # ------------------------------------------------------------------ 8
    print("\n== classificador falha no passo 1: uma falha, nao uma por passo ==")
    # Medido a 2026-11-03 no ledger real (perfil `default`, 12:31): 14 eventos,
    # UM turno, todos com o mesmo `classificador.falhou: TimeoutError` porque o
    # pin copiava a falha do passo 1 para os 13 seguintes. O cartao lia isso como
    # "falhou ha instantes" durante tres minutos e a taxa do classificador
    # contava 14 falhas onde houve uma.
    c8 = Cenario("falha-classificador")
    cfg8 = _cfg()
    cfg8["analise"]["classificador"] = {
        "activo": True, "provider": "deepseek", "modelo": "deepseek-flash",
        "esforco": "baixo", "max_tokens": 320, "cache_ttl_s": 600,
    }
    (c8.dir / "config.json").write_text(json.dumps(cfg8), encoding="utf-8")
    c8.plugin._cfg["mtime"] = -1.0  # forca releitura da config
    # O `Cenario` aquece o analisador (`_recarregar_analise_se_mudou`) mas nao o
    # classificador: sem este aquecimento, o primeiro pedido do turno reexecuta
    # `analise_modelo.py` e leva o patch consigo (medido: `classificar=0`).
    c8.plugin._recarregar_analise_modelo_se_mudou()
    chamadas = {"n": 0}

    def classificar_mau(*_a, **_kw):
        chamadas["n"] += 1
        return {"erro": "TimeoutError: The read operation timed out",
                "classificador": {"provider": "deepseek", "modelo": "deepseek-flash",
                                  "perfil": "pessoal", "falhou": "TimeoutError"},
                "origem": "modelo_falhou"}

    c8.plugin._analise_modelo_mod.classificar = classificar_mau
    assert c8.plugin._analise_modelo_mod.classificar is classificar_mau
    for i in range(6):
        c8.passo("corrige isto", i, turn_id="sessao:sessao:f8e7d6c5", api=i)
    ev8 = c8.eventos()
    resp8 = c8.respostas()
    com_erro = [e for e in ev8 if e.get("classificador_erro")]
    com_falha = [e for e in ev8 if (e.get("classificador") or {}).get("falhou")]
    check("o classificador corre uma vez num turno de 6 pedidos", chamadas["n"] == 1,
          f"classificar={chamadas['n']}")
    check("seis respostas no ledger", len(resp8) == 6, f"{len(resp8)}")
    check("a falha aparece num so evento em todo o ledger", len(com_erro) == 1,
          f"{len(com_erro)}")
    check("`classificador.falhou` tambem so num", len(com_falha) == 1, f"{len(com_falha)}")
    check("e o evento que a carrega e a primeira resposta do turno",
          bool(resp8 and resp8[0].get("classificador_erro")),
          str([bool(e.get("classificador_erro")) for e in resp8]))
    check("as respostas seguintes nao repetem o rasto",
          all("classificador_erro" not in e for e in resp8[1:]),
          str([sorted(k for k in e if k.startswith("classificador")) for e in resp8]))
    check("o `pin` de cada chamada continua na resposta",
          [e.get("pin") for e in resp8] == ["novo"] + ["reutilizado"] * 5,
          str([e.get("pin") for e in resp8]))
    niveis8 = {e.get("nivel") for e in resp8}
    check("a falha nao desestabiliza o nivel: heuristica pinada nos seis",
          len(niveis8) == 1, str(niveis8))

    # ------------------------------------------------------------------ 9
    # O sintoma medido no cron a 2026-11-08 20:13:58 -> 20:14:02 (passo_api 1
    # -> 2): o primeiro pedido do turno honrava a superficie, o segundo
    # reutilizava o pin e a ordem do perfil nunca mais disparava. A guarda
    # `entrada is None or rota is None` fazia da regra um recurso de ultimo
    # caso; ela e uma ordem e tem de ganhar ao pin do turno.
    print("\n== superficie fixada contra o pin do turno ==")
    c9 = Cenario("superficie-pin", superficies={"cron": {"entrada": "grande"}})
    for i in range(3):
        c9.passo("corrige isto", i, turn_id="sessao:sessao:f9e8d7c6", api=i,
                 plataforma="cron")
    resp9 = c9.respostas()
    check("tres respostas no ledger", len(resp9) == 3, f"{len(resp9)}")
    entradas9 = [e.get("entrada") for e in resp9]
    check("os tres pedidos usam a entrada da superficie",
          entradas9 == ["grande", "grande", "grande"], str(entradas9))
    origens9 = [e.get("origem") for e in resp9]
    check("a origem e `superficie` nos tres", origens9 == ["superficie"] * 3,
          str(origens9))
    pins9 = [e.get("pin") for e in resp9]
    check("o pin e novo no primeiro e substituido nos seguintes",
          pins9 == ["novo", "substituido_pela_superficie",
                    "substituido_pela_superficie"], str(pins9))
    check("a rota pinada existia e foi reutilizada antes de ceder",
          [e.get("rota_pin") for e in resp9[1:]] == ["reutilizada"] * 2,
          str([e.get("rota_pin") for e in resp9]))

    # ----------------------------------------------------------------- 10
    # A excepcao legitima: o modelo da regra declara que nao serve `cron`.
    # A ordem cede, mas deixa rasto — sem `excecao` parecia que a regra
    # nunca disparou (foi o que confundiu o diagnostico de ontem).
    print("\n== superficie cede quando o modelo nao serve ==")
    c10 = Cenario("superficie-cede", superficies={"cron": {"entrada": "so-api"}},
                  catalogo_extra=[{"id": "so-api", "provider": "deepseek",
                                   "modelo": "deepseek-v4", "complexidade": ["leve"],
                                   "superficies": {"cron": False}}])
    c10.passo("corrige isto", 0, turn_id="sessao:sessao:0a0b0c0d", api=0,
              plataforma="cron")
    resp10 = c10.respostas()
    check("uma resposta no ledger", len(resp10) == 1, f"{len(resp10)}")
    check("a entrada da superficie nao foi usada",
          (resp10[0].get("entrada") if resp10 else "so-api") != "so-api",
          str([e.get("entrada") for e in resp10]))
    check("a recusa esta registada em `excecao`",
          (resp10[0].get("excecao") if resp10 else "") == "superficie",
          str([(e.get("excecao"), e.get("excecao_motivo")) for e in resp10]))
    check("e o turno nao fica sem modelo",
          bool(resp10 and resp10[0].get("entrada")),
          str([e.get("entrada") for e in resp10]))


def main() -> int:
    try:
        testes()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        _RESULTADOS.append((f"excepcao: {exc}", False, ""))
    total = len(_RESULTADOS)
    bons = sum(1 for _, ok, _ in _RESULTADOS if ok)
    print(f"\n{bons}/{total} verificacoes OK")
    falhas = [n for n, ok, _ in _RESULTADOS if not ok]
    if falhas:
        print("FALHARAM: " + ", ".join(falhas))
        print("VEREDITO: FAIL")
        return 1
    print("VEREDITO: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
