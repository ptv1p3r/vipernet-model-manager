#!/usr/bin/env python3
"""Configuracao do ensaio A/B no painel: a prova do que se grava e do que se recusa.

O dono pediu: "a configuracao dos modelos para o teste a/b deve estar na pagina
de configuracao". A informacao existia, mas partida por dois sitios que nenhum
formulario mostrava: o interruptor e a validade da sonda no `config.json`
(bloco `sombra`), e os MODELOS DOS BRACOS no pre-registo selado em
`aprendiz/sombra/pre-registo-*.json`.

Este ficheiro prova, cada ponto com o seu caso negativo:

1. `_sombra_estado` le os dois sitios e devolve os bracos, o selo, os numeros
   do ensaio e a sonda de vida — sem inventar campos que nao existem.
2. Um pre-registo adulterado depois de selado NAO e aceite: `_pre_registo_actual`
   salta-o (o selo e o que impede escolher a margem depois de ver os dados).
3. O nome do pre-registo novo ordena DEPOIS do actual. A leitura usa
   `sorted(...)[-1]`; um nome que ordene antes gravaria um ensaio que ninguem
   corre, e o painel diria "ok".
4. `POST /sombra/bracos` recusa sem `confirmar_reabertura` (409): reabrir
   invalida a amostra recolhida.
5. Recusa dois bracos iguais (400) — um ensaio de um modelo contra si mesmo
   nao mede nada.
6. Aceita com confirmacao, sela o registo, herda p0/deff/chamadas do anterior
   (valores medidos) e a releitura devolve os bracos novos.
7. `POST /sombra` escreve so o bloco, preserva chaves desconhecidas e nao toca
   nos bracos.
8. `_verdadeiro` nao confunde a string "false" com verdadeiro — `bool("false")`
   e `True`, e isso ligaria o ensaio ao desligar.

Uso: python3 test_sombra_painel.py  ->  rc 0 se tudo passar.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo

    _FUSO = ZoneInfo("Europe/Lisbon")
except Exception:  # pragma: no cover - ambiente sem tzdata
    _FUSO = None

from datetime import datetime as _dt

# `_proximo_nome_pre_registo` compoe o nome com a data de hoje: um fixture
# com data fixa so passa no dia em que foi escrito.
HOJE_PRE_REGISTO = _dt.now(_FUSO).strftime("%Y%m%d") if _FUSO else _dt.now().strftime("%Y%m%d")

# Slug do braco b usado nos casos de re-selagem. Uma so casa: o fixture
# declara-o no catalogo copiado com este nome e as assercoes leem-no daqui.
SLUG_BRACO_B = "nvidia/nemotron-3.5-lightning:free"

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


def _preparar(base: Path) -> None:
    """Config viva copiada como base + um pre-registo selado, com o desenho real.

    A config e copiada da instalacao em vez de escrita a mao: o schema tem
    dezenas de chaves e um fixture inventado valida mal (o proprio
    `config.example.json` do repo esta desatualizado — tem
    `analise.contexto_grande_tokens`, que o validador ja recusa). Uma config
    que o `gestor` aceita e o que o teste precisa, nao uma que se pareca.
    """
    sombra = _mod("sombra_teste", AQUI / "sombra.py")
    (base / "aprendiz" / "sombra").mkdir(parents=True, exist_ok=True)
    vivo = Path(os.environ.get("VMM_CONFIG_VIVA")
                or "/opt/data/plugin-data/vipernet-model-manager/config.json")
    cfg = json.loads(vivo.read_text(encoding="utf-8"))
    cfg["sombra"] = {"activo": True, "vida_ttl_h": 24}
    # O slug do braco b usado na re-selagem e declarado no catalogo copiado. O
    # teste mede a resolucao pelo catalogo (e a origem "catalogo"), nao a
    # existencia do modelo na instalacao viva — que muda quando o dono limpa o
    # catalogo e fazia a suite falhar por um motivo que nao e do codigo.
    for _perfil in (cfg.get("perfis") or {}).values():
        _cat = _perfil.get("catalogo") if isinstance(_perfil, dict) else None
        if not _cat:
            continue
        if not any(str(e.get("modelo") or "") == SLUG_BRACO_B for e in _cat):
            _entrada = dict(_cat[0])
            _entrada.update({"id": "teste-braco-b", "modelo": SLUG_BRACO_B,
                             "provider": "openrouter", "ordem": 98})
            _cat.append(_entrada)
        break
    (base / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    registo = sombra.desenhar(
        caro="deepseek-flash",
        barato="nvidia/nemotron-3-ultra-550b-a55b:free",
        p0=0.0463,
        margem=0.01,
        deff=3.08,
        chamadas_por_dia=16194.0,
        nota="teste",
    )
    sombra.escrever(registo, base / "aprendiz" / "sombra"
                   / ("pre-registo-" + HOJE_PRE_REGISTO + "b.json"))
    (base / "aprendiz" / "sombra" / "vida.json").write_text(json.dumps({
        "ts": "2026-11-05T10:29:59+00:00",
        "sha_registo": registo["sha256"],
        "braco_a": {"modelo": "deepseek-flash", "ok": True, "http": 200, "motivo": ""},
        "braco_b": {"modelo": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "ok": True, "http": 200, "motivo": ""},
        "todos_vivos": True,
    }), encoding="utf-8")


def testes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "plugin-data" / "vipernet-model-manager"
        base.mkdir(parents=True)
        _preparar(base)
        os.environ["VMM_DIR"] = str(base)

        api = _mod("api_teste_sombra", AQUI / "dashboard" / "api.py")
        from fastapi import HTTPException

        # -------------------------------------------------- 1. leitura
        estado = api._sombra_estado(api._cfg())
        check("estado: le os dois bracos do pre-registo",
              estado["bracos"]["a"] == "deepseek-flash"
              and estado["bracos"]["b"] == "nvidia/nemotron-3-ultra-550b-a55b:free",
              json.dumps(estado["bracos"]))
        check("estado: selo valido", estado["selo_valido"] is True, str(estado["selo"]))
        check("estado: traz os numeros do ensaio",
              estado["p0"] == 0.0463 and estado["margem"] == 0.01
              and estado["n_por_braco"] == 18536,
              json.dumps({k: estado[k] for k in ("p0", "margem", "n_por_braco")}))
        check("estado: traz a sonda de vida dos dois bracos",
              estado["vida"]["bracos"]["a"].get("http") == 200
              and estado["vida"]["bracos"]["b"].get("ok") is True,
              json.dumps(estado["vida"]))
        check("estado: interruptor vem do config, nao do pre-registo",
              estado["activo"] is True and estado["vida_ttl_h"] == 24)

        # -------------------------------------------------- 2. selo adulterado
        caminho = (base / "aprendiz" / "sombra"
                   / ("pre-registo-" + HOJE_PRE_REGISTO + "b.json"))
        original = caminho.read_text(encoding="utf-8")
        adulterado = json.loads(original)
        adulterado["p0"] = 0.001  # margem escolhida depois de ver os dados
        caminho.write_text(json.dumps(adulterado, indent=2), encoding="utf-8")
        reg, _ = api._pre_registo_actual()
        check("selo: pre-registo adulterado nao e aceite", reg is None,
              str(reg)[:80])
        estado_mau = api._sombra_estado(api._cfg())
        check("selo: o painel distingue 'existe mas nao bate'",
              estado_mau["selo_valido"] is False
              and estado_mau["pre_registo_recente"] != ""
              and estado_mau["pre_registo_recente_selado"] is False,
              json.dumps({k: estado_mau[k] for k in
                          ("selo_valido", "pre_registo_recente", "pre_registo_recente_selado")}))
        caminho.write_text(original, encoding="utf-8")

        # -------------------------------------------------- 3. nome ordena depois
        proximo = api._proximo_nome_pre_registo()
        check("nome: o proximo ficheiro ordena depois do actual",
              proximo > caminho.stem, f"{proximo} vs {caminho.stem}")
        check("nome: e o sufixo seguinte da mesma data",
              proximo == "pre-registo-" + HOJE_PRE_REGISTO + "c", proximo)

        # -------------------------------------------------- 4. recusa sem confirmacao
        try:
            api.sombra_bracos_post(corpo={"braco_a": "a/x", "braco_b": "b/y"})
            recusou = False
            detalhe = "gravou"
        except HTTPException as exc:
            recusou = exc.status_code == 409
            detalhe = f"{exc.status_code} {exc.detail}"
        check("bracos: recusa sem confirmar_reabertura (409)", recusou, detalhe)
        check("bracos: a recusa nao deixou ficheiro novo",
              len(list((base / "aprendiz" / "sombra").glob("pre-registo-*.json"))) == 1)

        # -------------------------------------------------- 5. bracos iguais
        try:
            api.sombra_bracos_post(corpo={
                "braco_a": "igual/x", "braco_b": "igual/x",
                "confirmar_reabertura": True})
            recusou = False
            detalhe = "gravou"
        except HTTPException as exc:
            recusou = exc.status_code == 400
            detalhe = f"{exc.status_code} {exc.detail}"
        check("bracos: recusa os dois bracos iguais (400)", recusou, detalhe)

        # -------------------------------------------------- 6. re-sela
        r = api.sombra_bracos_post(corpo={
            "braco_a": "deepseek-flash",
            "braco_b": SLUG_BRACO_B,
            "confirmar_reabertura": True,
        })
        corpo_r = json.loads(bytes(r.body).decode("utf-8"))
        check("bracos: grava e sela", corpo_r["ok"] is True
              and corpo_r["pre_registo"]["selo_valido"] is True,
              json.dumps(corpo_r.get("pre_registo")))
        check("bracos: releitura independente devolve os bracos novos",
              corpo_r["sombra"]["bracos"]["b"] == SLUG_BRACO_B,
              json.dumps(corpo_r["sombra"]["bracos"]))
        # O provider passou a fazer parte do desenho: sem ele a sonda e o
        # middleware tinham de adivinhar a credencial pelo nome do modelo.
        check("bracos: o provider de cada braco fica selado",
              corpo_r["sombra"]["providers"] == {"a": "deepseek", "b": "openrouter"},
              json.dumps(corpo_r["sombra"].get("providers")))
        check("bracos: os dois providers vem declarados",
              corpo_r["sombra"]["providers_declarados"] is True,
              json.dumps(corpo_r["sombra"].get("providers_declarados")))
        check("bracos: a origem do provider e dita (catalogo, nunca palpite)",
              corpo_r["providers_origem"] == {"a": "catalogo", "b": "catalogo"},
              json.dumps(corpo_r.get("providers_origem")))
        # A lista e fechada E vem do catalogo: deriva-se da config copiada em vez
        # de se fixar um instantaneo. Fixa, quebra sozinha no dia em que o dono
        # acrescenta um provider ao catalogo (aconteceu a 2026-11-08 com o
        # `inception` do mercury-3): a assercao media o ambiente, nao o codigo.
        _cfg_lida = json.loads((base / "config.json").read_text(encoding="utf-8"))
        _esperados = sorted({
            str(_e.get("provider") or "").strip().lower()
            for _p in (_cfg_lida.get("perfis") or {}).values() if isinstance(_p, dict)
            for _e in (_p.get("catalogo") or [])
            if isinstance(_e, dict) and str(_e.get("provider") or "").strip()})
        check("bracos: a lista de providers e fechada e vem do catalogo",
              corpo_r["sombra"]["providers_disponiveis"] == _esperados
              and {"deepseek", "openrouter"} <= set(_esperados),
              json.dumps({"lista": corpo_r["sombra"].get("providers_disponiveis"),
                          "esperado": _esperados}))
        check("bracos: o ficheiro novo e o que a leitura escolhe",
              Path(corpo_r["sombra"]["pre_registo"]).name
              == "pre-registo-" + HOJE_PRE_REGISTO + "c.json",
              corpo_r["sombra"]["pre_registo"])
        check("bracos: herda p0/deff/chamadas medidos do anterior",
              corpo_r["sombra"]["p0"] == 0.0463
              and corpo_r["sombra"]["n_por_braco"] == 18536,
              json.dumps({k: corpo_r["sombra"][k] for k in ("p0", "n_por_braco")}))
        check("bracos: o anterior continua em disco (nada apagado)",
              len(list((base / "aprendiz" / "sombra").glob("pre-registo-*.json"))) == 2)

        # ------------------------------------------------- 6b. provider errado
        # Os dois casos negativos que faltavam: um provider que nao serve o
        # modelo e um modelo que nao esta em catalogo nenhum sao recusados em
        # vez de selados (um braco assim nunca podia ser servido).
        for rotulo, corpo_mau, esperado in (
            ("provider que nao serve o modelo",
             {"braco_a": "deepseek-flash", "braco_a_provider": "openrouter",
              "braco_b": SLUG_BRACO_B,
              "confirmar_reabertura": True}, 400),
            ("modelo fora de todos os catalogos",
             {"braco_a": "deepseek-flash", "braco_b": "fantasma/modelo:free",
              "confirmar_reabertura": True}, 400),
            ("provider inexistente",
             {"braco_a": "deepseek-flash", "braco_a_provider": "anthropic",
              "braco_b": SLUG_BRACO_B,
              "confirmar_reabertura": True}, 400),
        ):
            try:
                api.sombra_bracos_post(corpo=corpo_mau)
                recusou, detalhe = False, "gravou"
            except HTTPException as exc:
                recusou, detalhe = exc.status_code == esperado, f"{exc.status_code} {exc.detail}"
            check(f"bracos: recusa {rotulo}", recusou, detalhe)
        check("bracos: as recusas nao deixaram ficheiro novo",
              len(list((base / "aprendiz" / "sombra").glob("pre-registo-*.json"))) == 2)

        # -------------------------------------------------- 7. interruptor
        r2 = api.sombra_post(corpo={"activo": False})
        corpo2 = json.loads(bytes(r2.body).decode("utf-8"))
        check("interruptor: desliga", corpo2["sombra"]["activo"] is False,
              json.dumps(corpo2["sombra"]["activo"]))
        check("interruptor: nao mexe nos bracos",
              corpo2["sombra"]["bracos"]["b"] == SLUG_BRACO_B,
              json.dumps(corpo2["sombra"]["bracos"]))
        cfg_disco = json.loads((base / "config.json").read_text(encoding="utf-8"))
        # O `alterar` valida a config INTEIRA: uma escrita cirurgica que
        # apagasse o resto passaria despercebida no painel e rebentaria ao
        # gravar. Prova-se que os outros blocos continuam la.
        check("interruptor: nao mexe no resto da config",
              cfg_disco.get("analise", {}).get("limiares")
              and list(cfg_disco.get("perfis", {})) == ["default", "pessoal", "trabalho"],
              json.dumps({"perfis": list(cfg_disco.get("perfis", {}))}))
        check("interruptor: a sonda de vida fica intacta",
              cfg_disco["sombra"].get("vida_ttl_h") == 24,
              json.dumps(cfg_disco.get("sombra")))
        check("interruptor: so o bloco sombra mudou de tamanho",
              set(cfg_disco["sombra"]) == {"activo", "vida_ttl_h"},
              json.dumps(sorted(cfg_disco.get("sombra", {}))))

        # -------------------------------------------------- 8. booleano honesto
        check("_verdadeiro: 'false' nao e verdadeiro", api._verdadeiro("false") is False)
        check("_verdadeiro: 'true' e verdadeiro", api._verdadeiro("true") is True)
        check("_verdadeiro: booleano passa tal e qual",
              api._verdadeiro(True) is True and api._verdadeiro(False) is False)

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
