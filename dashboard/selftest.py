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
import shutil
import subprocess
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
    # A versao do endpoint tem de ser a do manifest: este numero ja divergiu
    # (3.3.0 no /estado, 3.6.x no manifest) e e o primeiro sitio onde se ve que
    # codigo esta a correr no contentor.
    manifesto = json.loads((RAIZ / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    check("/estado versao vem do manifest",
          est.get("versao_painel") == manifesto.get("version"),
          f"{est.get('versao_painel')} vs {manifesto.get('version')}")

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

    # --- indicador do cartao: modelo (qual) ou heuristica -------------------
    # O cartao tem de responder por perfil: que modo esta em vigor, de onde vem
    # (proprio/herdado), que modelo, e se o modelo falhou no ultimo turno. Um
    # perfil que herda nao pode aparecer como "desligado", e um bloco invalido
    # nao pode aparecer como "ligado" — foi por isso que o indicador passou a
    # ler `gestor.classificador_efectivo`, e nao o `activo` cru.
    cfg_ind = json.loads(json.dumps(CONFIG))
    cfg_ind["analise"]["classificador"] = {
        "activo": True, "provider": "inception", "modelo": "mercury-3",
        "esforco": "baixo", "max_tokens": 1200,
    }
    # Trabalho LIGADO e a falhar: e o cartao que tem de mostrar o ⚠, porque a
    # falha e do classificador que esta em vigor. O caso "desligado" — em que a
    # mesma falha passa a historia — verifica-se mais abaixo.
    cfg_ind["perfis"]["trabalho"].setdefault("analise", {})["classificador"] = {
        "activo": True, "provider": "inception", "modelo": "mercury-3",
        "esforco": "baixo",
    }
    (d / "config.json").write_text(json.dumps(cfg_ind, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    ledger_ind = [
        {"ts": "2026-11-02T11:00:00+00:00", "evento": "resposta", "perfil": "trabalho",
         "sessao": "t1", "provider": "deepseek", "modelo": "deepseek-flash",
         "nivel": "media", "origem": "analise"},
        {"ts": "2026-11-02T11:00:01+00:00", "evento": "sem_troca", "perfil": "trabalho",
         "nivel": "media",
         "classificador": {"provider": "inception", "modelo": "mercury-3",
                           "perfil": "trabalho", "falhou": "ValueError"},
         "classificador_erro": "resposta sem nivel reconhecel (length 320tok)"},
        {"ts": "2026-11-02T11:01:00+00:00", "evento": "resposta", "perfil": "default",
         "sessao": "d1", "provider": "deepseek", "modelo": "deepseek-flash",
         "nivel": "media", "origem": "analise"},
        {"ts": "2026-11-02T11:02:00+00:00", "evento": "troca", "perfil": "default",
         "nivel": "alta",
         "classificador": {"provider": "inception", "modelo": "mercury-3",
                           "lido": "alta", "ms": 1620, "cache": False}},
        {"ts": "2026-11-02T11:03:00+00:00", "evento": "resposta", "perfil": "default",
         "sessao": "d1", "provider": "deepseek", "modelo": "deepseek-v4-pro",
         "nivel": "alta", "origem": "modelo"},
    ]
    with (d / "uso.jsonl").open("w", encoding="utf-8") as fh:
        for ev in ledger_ind:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    est = cli.get("/estado").json()
    pdef = (est.get("perfis") or {}).get("default") or {}
    ptrb = (est.get("perfis") or {}).get("trabalho") or {}
    cdef = (pdef.get("analise") or {}).get("classificador") or {}
    ctrb = (ptrb.get("analise") or {}).get("classificador") or {}
    check("indicador: perfil que herda aparece como modelo",
          cdef.get("modo") == "modelo" and cdef.get("origem") == "herdado", str(cdef))
    check("indicador: diz qual o modelo e o esforco em vigor",
          cdef.get("provider") == "inception" and cdef.get("modelo") == "mercury-3"
          and cdef.get("esforco") == "baixo", str(cdef))
    check("indicador: conta as origens dos ultimos turnos",
          (pdef.get("analise") or {}).get("origens") == {"analise": 1, "modelo": 1},
          str((pdef.get("analise") or {}).get("origens")))
    check("indicador: traz o rasto do classificador (ms e leitura)",
          ((pdef.get("analise") or {}).get("rasto") or {}).get("lido") == "alta"
          and ((pdef.get("analise") or {}).get("rasto") or {}).get("ms") == 1620,
          str((pdef.get("analise") or {}).get("rasto")))
    check("indicador: perfil com classificador proprio ligado aparece como modelo",
          ctrb.get("modo") == "modelo" and ctrb.get("origem") == "proprio", str(ctrb))
    check("indicador: falha do classificador chega ao cartao",
          "length 320tok" in str((ptrb.get("analise") or {}).get("erro")),
          str((ptrb.get("analise") or {}).get("erro")))
    check("indicador: a ultima leitura falhada fica marcada no rasto",
          ((ptrb.get("analise") or {}).get("rasto") or {}).get("falhou") == "ValueError",
          str((ptrb.get("analise") or {}).get("rasto")))
    check("indicador: leitura boa nao fica marcada como falhada",
          ((pdef.get("analise") or {}).get("rasto") or {}).get("falhou") == "",
          str((pdef.get("analise") or {}).get("rasto")))
    check("indicador: leitura valida nao deixa historico",
          (pdef.get("analise") or {}).get("historico") is None,
          str((pdef.get("analise") or {}).get("historico")))
    check("indicador: limiares do cartao sao os efectivos (herdados)",
          (pdef.get("limiares") or {}).get("alta") == 7, str(pdef.get("limiares")))

    # --- desligar o classificador: a falha antiga passa a historia ---------
    # Uma falha de um classificador que ja nao esta em vigor nao e estado do
    # cartao: mostra-se como historico datado, senao o cartao acusa um erro que
    # ja nao pode acontecer (o classificador esta desligado).
    cfg_off = json.loads(json.dumps(cfg_ind))
    cfg_off["perfis"]["trabalho"]["analise"]["classificador"] = {
        "activo": False, "provider": "inception", "modelo": "mercury-3",
    }
    (d / "config.json").write_text(json.dumps(cfg_off, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    est_off = cli.get("/estado").json()
    ptb_off = (est_off.get("perfis") or {}).get("trabalho") or {}
    ana_off = ptb_off.get("analise") or {}
    ctrb_off = ana_off.get("classificador") or {}
    check("desligado: aparece como heuristica",
          ctrb_off.get("modo") == "heuristica" and ctrb_off.get("origem") == "proprio"
          and ctrb_off.get("motivo") == "desligado", str(ctrb_off))
    check("desligado: a falha antiga nao aparece como erro actual",
          ana_off.get("erro") == "", str(ana_off.get("erro")))
    check("desligado: o rasto deixa de ser estado", ana_off.get("rasto") is None,
          str(ana_off.get("rasto")))
    check("desligado: a leitura passa a historico datado",
          (ana_off.get("historico") or {}).get("falhou") == "ValueError"
          or (ana_off.get("historico") or {}).get("lido") is not None
          or (ana_off.get("historico") or {}).get("ts") is not None,
          str(ana_off.get("historico")))
    # Repor o estado do fixture para as verificacoes seguintes.
    (d / "config.json").write_text(json.dumps(cfg_ind, ensure_ascii=False, indent=2),
                                   encoding="utf-8")


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

    # --- painel: classificador por modelo, editado no proprio editor -------
    # Sem rota nova: a seccao muta o JSON que o editor ja carregou e grava-se
    # pelo mesmo /config. Uma rota nova so seria montada a reiniciar o gateway.
    check("painel: seccao do classificador existe", "function SecaoClassificador(props)" in js)
    check("painel: seccao renderizada no editor", "h(SecaoClassificador, {" in js)
    check("painel: tres modos por perfil (herdar/desligado/modelo)",
          "\"herdar do global\"" in js and "desligado (heurística)" in js
          and "\"decide um modelo\"" in js)
    check("painel: herdar apaga o bloco do perfil",
          "if (modo === \"herdar\") return aplicar(perfil, null);" in js)
    check("painel: escreve no JSON, nao numa rota nova",
          "props.onMudar(JSON.stringify(comBloco(cfg, perfil, bloco), null, 2))" in js)
    check("painel: campos com sugestoes do catalogo", "id: \"vmm-modelos\"" in js and "id: \"vmm-pares\"" in js)
    for cls in (".vmm-classif", ".vmm-classif-linha", ".vmm-classif-nome"):
        check(f"painel: CSS {cls}", cls in css)

    # A logica pura do painel, corrida a serio em node (sem browser).
    node = shutil.which("node")
    if node:
        teste = RAIZ / "dashboard" / "test_logica.js"
        check("painel: test_logica.js existe", teste.is_file(), str(teste))
        if teste.is_file():
            r = subprocess.run([node, str(teste)], capture_output=True, text=True, timeout=60)
            check("painel: logica pura passa em node (comBloco/herdar)",
                  r.returncode == 0, (r.stdout or r.stderr)[-400:])
        c = subprocess.run([node, "--check", str(RAIZ / "dashboard" / entry)],
                           capture_output=True, text=True, timeout=60)
        check("painel: entry servido e JS valido", c.returncode == 0, (c.stderr or "")[-300:])
    else:
        print("SEM node: teste da logica pura do painel nao corrido")

    print(f"\n{OK} verificacoes OK, {len(FALHAS)} falhas")
    for f in FALHAS:
        print("  FALHA:", f)
    print("VEREDITO:", "PASS" if not FALHAS else "FAIL")
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    raise SystemExit(main())
