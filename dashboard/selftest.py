"""Selftest do painel do Vipernet Model Manager (backend).

Corre sem tocar na configuracao viva: aponta ``VMM_DIR`` a uma pasta temporaria com um
config sintetico e um ledger sintetico, importa ``api.py`` por caminho (como o host faz)
e exercita as rotas com o ``TestClient`` do FastAPI.

    /opt/hermes/.venv/bin/python dashboard/selftest.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

FALHAS: list[str] = []
OK = 0


def check(nome: str, condicao: bool, detalhe: str = "") -> None:
    global OK
    if condicao:
        OK += 1
    else:
        FALHAS.append(f"{nome}{(' — ' + detalhe) if detalhe else ''}")


CONFIG = {
    "versao": 3,
    "activo": True,
    "registar": True,
    "analise": {"janela_mensagens": 8, "varredura_max_mensagens": 60,
                "limiares": {"media": 3, "alta": 7}, "descer_apos": 2},
    "perfis": {
        "default": {
            "activo": True,
            "estrategia": "rodizio",
            "rodizio": {"por": "sessao"},
            "tarefas": {},
            "catalogo": [
                {"id": "deepseek-flash", "provider": "deepseek", "modelo": "deepseek-flash",
                 "complexidade": ["leve", "media"], "tarefas": []},
                {"id": "deepseek-pro", "provider": "deepseek", "modelo": "deepseek-v4-pro",
                 "complexidade": ["alta"], "tarefas": []},
            ],
        },
        "trabalho": {
            "activo": True,
            "estrategia": "rodizio",
            "rodizio": {"por": "sessao"},
            "tarefas": {"arquitetura": {"termos": ["análise de arquitetura"], "prioridade": 10}},
            "catalogo": [
                {"id": "deepseek-flash", "provider": "deepseek", "modelo": "deepseek-flash",
                 "complexidade": ["leve"], "tarefas": []},
                {"id": "gpt-sol", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
                 "complexidade": ["alta"], "acesso": "api", "tarefas": ["arquitetura"]},
            ],
        },
    },
}

LINHAS_LEDGER = [
    {"ts": "2026-11-02T10:00:00+00:00", "evento": "decisao", "perfil": "trabalho", "nivel": "alta"},
    {"ts": "2026-11-02T10:00:01+00:00", "evento": "resposta", "perfil": "trabalho",
     "sessao": "s1", "superficie": "api_server", "provider": "openrouter",
     "modelo": "openai/gpt-6.1-sol", "respondido_por": "openai/gpt-6.1-sol",
     "nivel": "alta", "origem": "tarefa", "acesso": "api"},
    {"ts": "2026-11-02T10:05:00+00:00", "evento": "resposta", "perfil": "default",
     "sessao": "s2", "superficie": "gateway", "provider": "deepseek",
     "modelo": "deepseek-flash", "respondido_por": "deepseek-flash",
     "nivel": "leve", "origem": "analise", "acesso": "api"},
    {"ts": "2026-11-02T10:09:00+00:00", "evento": "sem_troca", "perfil": "default",
     "nivel": "leve", "rota": {"provider": "deepseek", "modelo": "deepseek-flash"}},
]


def preparar_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="vmm-painel-"))
    (d / "config.json").write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    with (d / "uso.jsonl").open("w", encoding="utf-8") as fh:
        for ev in LINHAS_LEDGER:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return d


def importar_api():
    caminho = Path(__file__).resolve().parent / "api.py"
    spec = importlib.util.spec_from_file_location("vmm_dash_api", caminho)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    d = preparar_dir()
    os.environ["VMM_DIR"] = str(d)
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover
        print(f"SEM TestClient: {exc}")
        return 2

    mod = importar_api()
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/vipernet-model-manager")
    pref = "/api/plugins/vipernet-model-manager"

    class _Prefado:
        """O host monta em /api/plugins/<nome>/ — o teste usa os mesmos caminhos."""

        def __init__(self, cliente):
            self._c = cliente

        def get(self, caminho, **kw):
            return self._c.get(pref + caminho, **kw)

        def post(self, caminho, **kw):
            return self._c.post(pref + caminho, **kw)

    cli = _Prefado(TestClient(app))
    # O host monta em /api/plugins/<nome>/; aqui testa-se a raiz do router.
    check("router existe", hasattr(mod, "router"), "api.py sem router")

    # --- /estado -----------------------------------------------------------
    r = cli.get("/estado")
    check("/estado 200", r.status_code == 200, str(r.status_code))
    est = r.json() if r.status_code == 200 else {}
    check("/estado activo", est.get("activo") is True)
    check("/estado perfis", set((est.get("perfis") or {}).keys()) == {"default", "trabalho"},
          str(list((est.get("perfis") or {}).keys())))
    tr = (est.get("perfis") or {}).get("trabalho", {})
    check("/estado tarefas do trabalho", "arquitetura" in (tr.get("tarefas") or {}))
    check("/estado catalogo trabalho", len(tr.get("catalogo") or []) == 2)
    sol = [m for m in (tr.get("catalogo") or []) if m.get("id") == "gpt-sol"]
    check("/estado tarefa associada ao sol", bool(sol) and sol[0].get("tarefas") == ["arquitetura"])
    check("/estado complexidade em lista", bool(sol) and sol[0].get("complexidade") == ["alta"])
    check("/estado ultimo do trabalho", (tr.get("ultimo") or {}).get("modelo") == "openai/gpt-6.1-sol",
          str(tr.get("ultimo")))
    check("/estado ledger", (est.get("ledger") or {}).get("existe") is True)
    check("/estado versao", bool(est.get("versao_painel")))

    # --- /log --------------------------------------------------------------
    r = cli.get("/log?limite=10")
    check("/log 200", r.status_code == 200, str(r.status_code))
    corpo = r.json() if r.status_code == 200 else {}
    check("/log so respostas", all(x.get("modelo") for x in corpo.get("linhas") or []),
          str(corpo.get("linhas")))
    check("/log ordem nova-primeiro",
          [x["ts"] for x in corpo.get("linhas") or []] == sorted(
              [x["ts"] for x in corpo.get("linhas") or []], reverse=True),
          str([x.get("ts") for x in corpo.get("linhas") or []]))
    check("/log total", len(corpo.get("linhas") or []) == 2, str(len(corpo.get("linhas") or [])))

    r = cli.get("/log?limite=10&perfil=trabalho")
    corpo = r.json() if r.status_code == 200 else {}
    check("/log filtra perfil", len(corpo.get("linhas") or []) == 1 and
          (corpo.get("linhas") or [{}])[0].get("perfil") == "trabalho", str(corpo.get("linhas")))
    check("/log limite 0 recusado", cli.get("/log?limite=0").status_code == 422)
    check("/log limite acima do tecto recusado", cli.get("/log?limite=99999").status_code == 422)

    # --- /perfis -----------------------------------------------------------
    r = cli.get("/perfis")
    check("/perfis", r.status_code == 200 and r.json().get("perfis") == ["default", "trabalho"],
          str(r.json()))

    # --- /toggle -----------------------------------------------------------
    r = cli.post("/toggle", json={"ambito": "perfil", "perfil": "trabalho", "activo": False})
    check("/toggle perfil 200", r.status_code == 200, f"{r.status_code} {r.text}")
    lido = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("/toggle perfil gravou", lido["perfis"]["trabalho"]["activo"] is False)
    check("/toggle perfil devolve lido", (r.json() or {}).get("activo") is False)
    check("/toggle perfil nao toca o global", lido["activo"] is True)

    r = cli.post("/toggle", json={"ambito": "global", "activo": False})
    check("/toggle global 200", r.status_code == 200, f"{r.status_code} {r.text}")
    lido = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("/toggle global gravou", lido["activo"] is False)
    check("/toggle global nao mexe nos perfis", lido["perfis"]["trabalho"]["activo"] is False)

    r = cli.post("/toggle", json={"ambito": "perfil", "perfil": "trabalho", "activo": True})
    check("/toggle religa perfil", r.status_code == 200 and r.json().get("activo") is True, r.text)

    # --- recusas -----------------------------------------------------------
    r = cli.post("/toggle", json={"ambito": "perfil", "perfil": "nao-existe", "activo": True})
    check("/toggle perfil desconhecido 404", r.status_code == 404, str(r.status_code))
    r = cli.post("/toggle", json={"ambito": "global", "activo": "sim"})
    check("/toggle activo nao booleano 400", r.status_code == 400, str(r.status_code))
    r = cli.post("/toggle", json={"ambito": "outro", "activo": True})
    check("/toggle ambito invalido 400", r.status_code == 400, str(r.status_code))
    antes = (d / "config.json").read_text(encoding="utf-8")
    check("/toggle invalido nao gravou", "nao-existe" not in antes)

    # --- ledger ausente ----------------------------------------------------
    (d / "uso.jsonl").unlink()
    r = cli.get("/log")
    check("/log sem ledger 200", r.status_code == 200 and r.json().get("linhas") == [], r.text)
    est = cli.get("/estado").json()
    check("/estado sem ledger", est.get("ledger", {}).get("existe") is False)

    # --- config ausente cai na semente ------------------------------------
    (d / "config.json").unlink()
    r = cli.get("/estado")
    check("/estado sem config 200", r.status_code == 200, str(r.status_code))
    check("/estado sem config tem perfis", bool((r.json() or {}).get("perfis")))

    # --- painel: confirmacao verde da validacao (frontend estatico) ---------
    # O sucesso tem de ser visivel: banner verde, botao a verde e texto com o
    # que foi aceite. Sem estado paralelo — deriva do resultado de /validar.
    js = (RAIZ / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
    css = (RAIZ / "dashboard" / "dist" / "style.css").read_text(encoding="utf-8")

    # O que o host serve ao browser e o `entry` do manifest, nao um nome fixo.
    # Foi esta lacuna que deixou passar uma versao sem o verde: o manifest
    # apontava para dist/index-3.3.0.js e as edicoes iam para dist/index.js.
    man = json.loads((RAIZ / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    entry = man.get("entry") or ""
    folha = man.get("css") or ""
    check("manifest: entry existe no pacote", (RAIZ / "dashboard" / entry).is_file(), entry)
    check("manifest: css existe no pacote", (RAIZ / "dashboard" / folha).is_file(), folha)
    check("manifest: entry e o ficheiro editado (dist/index.js)", entry == "dist/index.js", entry)
    check("manifest: css e o ficheiro editado (dist/style.css)", folha == "dist/style.css", folha)
    entry_txt = (RAIZ / "dashboard" / entry).read_text(encoding="utf-8") if entry else ""
    check("manifest: o entry servido tem o verde", "✓ Validado" in entry_txt and "vmm-btn-ok" in entry_txt)
    check("manifest: sem duplicados versionados no dist",
          not list((RAIZ / "dashboard" / "dist").glob("index-*.js")) and
          not list((RAIZ / "dashboard" / "dist").glob("style-*.css")))
    check("painel: estado validacaoOk", "const [validacaoOk, setValidacaoOk] = useState(null)" in js)
    check("painel: validar limpa aviso antes de pedir",
          "function validarConfig() {\n      setErrosValidacao([]);\n      setValidacaoOk(null);" in js)
    check("painel: validar com sucesso publica aviso",
          "setValidacaoOk(mensagemValido(cfg))" in js)
    check("painel: mensagem com contagens medidas",
          "Validado com sucesso — " in js and "rota(s)" in js and "0 erros" in js)
    check("painel: validar com erros nao deixa aviso verde",
          "setValidacaoOk(null);\n              setErro(\"Configuração inválida" in js)
    check("painel: botao Validar muda para confirmado", "\"✓ Validado\" : \"Validar\"" in js)
    check("painel: botao Validar ganha classe verde", "className: validacaoOk ? \"vmm-btn-ok\" : undefined" in js)
    check("painel: aviso verde dentro do editor", "className: \"vmm-editor-ok\"" in js)
    check("painel: aviso verde na pagina apos gravar", "className: \"vmm-ok\"" in js)
    check("painel: gravar publica aviso de sucesso",
          "setValidacaoOk(\"Configuração gravada com sucesso" in js)
    check("painel: editar o texto invalida o aviso",
          "// o texto mudou: o \"validado\" anterior deixa de ser verdade" in js)
    check("painel: fechar o editor limpa o aviso",
          "setErrosValidacao([]);\n      setValidacaoOk(null);\n    }\n\n    if (!estado && !erro)" in js)
    for cls in (".vmm-ok", ".vmm-editor-ok", ".vmm-btn-ok"):
        check(f"painel: CSS {cls}", cls in css)
    check("painel: verde com variante para tema escuro", "[data-theme=\"dark\"] .vmm-ok" in css)

    print(f"\n{OK} verificacoes OK, {len(FALHAS)} falhas")
    for f in FALHAS:
        print("  FALHA:", f)
    print("VEREDITO:", "PASS" if not FALHAS else "FAIL")
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    raise SystemExit(main())
