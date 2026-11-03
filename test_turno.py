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


def _cfg(*, fixar: bool = True) -> dict:
    return {
        "activo": True,
        "registar": True,
        "analise": {"limiares": {"media": 3, "alta": 6}, "descer_apos": 2,
                    "fixar_nivel_por_turno": fixar},
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
                ],
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

    def __init__(self, nome: str, *, fixar: bool = True):
        self.dir = Path(tempfile.mkdtemp(prefix="vmm-turno-"))
        (self.dir / "config.json").write_text(json.dumps(_cfg(fixar=fixar)),
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

    def passo(self, texto: str, indice: int, *, turn_id: Any, api: int) -> dict:
        return self.plugin.middleware_llm_request(
            request=self.pedido_turno(texto, indice), session_id="s1",
            model="deepseek-flash", provider="deepseek",
            turn_id=turn_id, api_call_count=api)

    def eventos(self) -> list[dict]:
        caminho = self.plugin.gestor.caminho_ledger()
        if not caminho.exists():
            return []
        return [json.loads(linha) for linha in
                caminho.read_text(encoding="utf-8").splitlines() if linha.strip()]

    def decididos(self) -> list[dict]:
        return [e for e in self.eventos()
                if str(e.get("evento") or "").startswith(("troca", "sem_"))]


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
