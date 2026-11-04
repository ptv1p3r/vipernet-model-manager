"""Prova do painel em sandbox: CAS e escrita por chave, sem tocar no config vivo.

Monta o router real do plugin (dashboard/api.py) contra um VMM_DIR temporario e
faz os pedidos HTTP como o browser faria. Mede o que interessa:

1. POST /toggle com `rev` velho devolve 409 e nao escreve;
2. POST /toggle sem `rev` escreve so o interruptor — uma chave escrita por
   outro escritor entretanto sobrevive;
3. POST /estrategia com rev velho devolve 409;
4. POST /config (documento inteiro) sem `rev` devolve 400;
5. POST /modelo com rev velho devolve 409.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path("/opt/data/plugins/vipernet-model-manager")
tmp = Path(tempfile.mkdtemp(prefix="vmm-painel-cas-"))
os.environ["VMM_DIR"] = str(tmp)
for k in list(os.environ):
    if k.startswith("VMM_"):
        pass
sys.path.insert(0, str(RAIZ))

import gestor  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(RAIZ / "dashboard"))
import api  # noqa: E402

OK = 0
FALHAS: list[str] = []


def check(nome: str, cond: bool, detalhe: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        FALHAS.append(f"{nome}: {detalhe}")


def cfg_actual() -> dict:
    c, _ = gestor.carregar()
    return c


BASE = {
    "versao": 3, "activo": True, "registar": True,
    "estrategia": {"modo": "prioridade", "por": "sessao"},
    "analise": {"limiares": {"leve": 0.35, "alta": 0.7}},
    "perfis": {
        "a": {"activo": True, "catalogo": [
            {"id": "m1", "provider": "deepseek", "modelo": "deepseek-flash",
             "complexidade": ["leve", "media", "alta"]}]},
        "b": {"activo": True, "catalogo": [
            {"id": "m2", "provider": "deepseek", "modelo": "deepseek-flash",
             "complexidade": ["leve", "media", "alta"]}]},
    },
}
gestor.gravar(BASE)

app = FastAPI()
app.include_router(api.router, prefix="/api/plugins/vipernet-model-manager")
cliente = TestClient(app)
P = "/api/plugins/vipernet-model-manager"

try:
    # 1. rev velho -> 409 e nada escrito
    rev_velho = gestor.rev(cfg_actual())
    r = cliente.post(f"{P}/toggle", json={"ambito": "perfil", "perfil": "a",
                                          "activo": False, "rev": rev_velho})
    check("toggle_ok", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    rev_depois = gestor.rev(cfg_actual())
    r = cliente.post(f"{P}/toggle", json={"ambito": "perfil", "perfil": "b",
                                          "activo": False, "rev": rev_velho})
    check("toggle_rev_velho_409", r.status_code == 409, f"{r.status_code} {r.text[:120]}")
    check("toggle_rev_velho_nao_escreve",
          gestor.rev(cfg_actual()) == rev_depois)

    # 2. sem rev: escreve SO o interruptor, a chave do outro escritor sobrevive
    outro, _ = gestor.carregar()
    outro["perfis"]["a"]["catalogo"][0]["ordem"] = 1      # escritor concorrente
    gestor.gravar(outro)
    r = cliente.post(f"{P}/toggle", json={"ambito": "perfil", "perfil": "b",
                                          "activo": False})
    check("toggle_sem_rev_ok", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    estado = cfg_actual()
    check("toggle_preserva_outra_chave",
          estado["perfis"]["a"]["catalogo"][0].get("ordem") == 1,
          "a chave escrita pelo outro escritor desapareceu")
    check("toggle_aplicou_a_sua", estado["perfis"]["b"]["activo"] is False)

    # 3. estrategia com rev velho -> 409
    rev_velho = gestor.rev(cfg_actual())
    # Escritor concorrente a MUDAR conteudo: gravar o mesmo conteudo nao muda o
    # `rev` (a impressao e do conteudo), e o teste mediria o nada.
    mover, _ = gestor.carregar()
    mover["analise"]["histerese"] = 0.25
    gestor.gravar(mover)
    r = cliente.post(f"{P}/estrategia", json={"ambito": "global", "rev": rev_velho,
                                              "modo": "rodizio"})
    check("estrategia_rev_velho_409", r.status_code == 409, f"{r.status_code} {r.text[:120]}")

    # 4. config inteira sem rev -> 400
    r = cliente.post(f"{P}/config", json={"config": cfg_actual()})
    check("config_sem_rev_400", r.status_code == 400, f"{r.status_code} {r.text[:160]}")

    # 5. config inteira com rev certo -> 200
    estado = cfg_actual()
    estado["registar"] = False
    r = cliente.post(f"{P}/config", json={"config": estado,
                                          "rev": gestor.rev(cfg_actual())})
    check("config_com_rev_200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("config_aplicou", cfg_actual()["registar"] is False)

    # 6. modelo com rev velho -> 409 (o painel nao aceita montar sobre o passado)
    rev_velho = gestor.rev(cfg_actual())
    mover, _ = gestor.carregar()
    mover["analise"]["histerese"] = 0.3
    gestor.gravar(mover)
    r = cliente.post(f"{P}/modelo", json={
        "perfil": "a", "rev": rev_velho,
        "entrada": {"id": "m3", "provider": "deepseek", "modelo": "deepseek-flash",
                    "complexidade": ["leve"]}})
    check("modelo_rev_velho_409", r.status_code == 409, f"{r.status_code} {r.text[:160]}")

    # 7. modelo sem rev -> acrescenta so a rota, preserva o resto
    guardar = cfg_actual()
    guardar["analise"]["janela_mensagens"] = 9
    gestor.gravar(guardar)
    r = cliente.post(f"{P}/modelo", json={
        "perfil": "b",
        "entrada": {"id": "m4", "provider": "deepseek", "modelo": "deepseek-flash",
                    "complexidade": ["leve"]}})
    check("modelo_sem_rev_ok", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    estado = cfg_actual()
    check("modelo_preserva_analise",
          (estado.get("analise") or {}).get("janela_mensagens") == 9,
          "a chave de analise escrita pelo outro escritor desapareceu")
    check("modelo_acrescentou", any(e["id"] == "m4" for e in estado["perfis"]["b"]["catalogo"]))

    print(f"\n{OK} verificacoes, {len(FALHAS)} falhas")
    for f in FALHAS:
        print("FALHA:", f)
    raise SystemExit(1 if FALHAS else 0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
