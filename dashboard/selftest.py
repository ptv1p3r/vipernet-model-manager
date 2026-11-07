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
    check("manifest: o entry servido tem o verde",
          "✓ Gravar e voltar" in entry_txt and "vmm-btn-ok" in entry_txt)
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
          "setValidacaoOk(null);\n              setErro(T(\"Configuração inválida: \")" in js)
    # Na pagina exclusiva o verde vive no botao principal: validar confirma onde
    # se grava, nao num botao secundario que some ao mudar de pagina.
    check("painel: botao principal confirma a validacao",
          '"✓ Gravar e voltar"' in js)
    check("painel: botao confirmado ganha classe verde",
          'props.validacaoOk ? "vmm-btn-ok" : undefined' in js)
    check("painel: aviso verde dentro da pagina de configuracao",
          js.count('className: "vmm-ok"') >= 2
          and 'role: "status", "aria-live": "polite"' in js)
    check("painel: aviso verde na pagina apos gravar", "className: \"vmm-ok\"" in js)
    check("painel: gravar publica aviso de sucesso",
          "setValidacaoOk(\"Configuração gravada com sucesso" in js)
    # Procurado no corpo da accao, nao no comentario: a indentacao mudou quando
    # a configuracao passou a pagina propria e o comentario deixou de existir.
    def _corpo(assinatura, n=260):
        i = js.find(assinatura)
        return js[i:i + n] if i >= 0 else ""

    check("painel: editar o texto invalida o aviso",
          "setValidacaoOk(null)" in _corpo("onMudarJSON: function (novo) {"))
    check("painel: fechar o editor limpa o aviso",
          "setValidacaoOk(null)" in _corpo("function fecharEditor() {")
          and "setErrosValidacao([])" in _corpo("function fecharEditor() {"))
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

    # --- /estrategia -------------------------------------------------------
    # A estrategia e lista fechada: o endpoint tem de recusar o que a validacao
    # recusa, gravar so o bloco, e deixar os perfis a herdar (sem copia).
    # A fixture repoe-se aqui: os testes anteriores exercitam o caminho de
    # "config ausente" e deixam a pasta sem `config.json`.
    (d / "config.json").write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    est = cli.get("/estado").json()
    check("/estado estrategia global e bloco",
          isinstance(est.get("estrategia_global"), dict), str(est.get("estrategia_global")))
    check("/estado opcoes fechadas",
          [o["valor"] for o in (((est.get("opcoes") or {}).get("estrategia") or {}).get("modo") or [])]
          == ["rodizio", "prioridade"], str(est.get("opcoes")))
    check("/estado rev presente", bool(est.get("rev")))
    check("/estado estrategia por perfil e bloco com origem",
          isinstance((est["perfis"]["trabalho"] or {}).get("estrategia"), dict)
          and "origem" in est["perfis"]["trabalho"]["estrategia"],
          str(est["perfis"]["trabalho"].get("estrategia")))

    antes = (d / "config.json").read_text(encoding="utf-8")
    r = cli.post("/estrategia", json={"ambito": "global", "modo": "inventado"})
    check("/estrategia recusa valor fora da lista", r.status_code == 400, str(r.status_code))
    check("/estrategia erro diz os valores aceites",
          "rodizio, prioridade" in r.text, r.text[:200])
    check("/estrategia nao grava em erro",
          (d / "config.json").read_text(encoding="utf-8") == antes)

    r = cli.post("/estrategia", json={"ambito": "perfil", "perfil": "nao-existe",
                                      "modo": "rodizio"})
    check("/estrategia perfil inexistente da 404", r.status_code == 404, str(r.status_code))

    r = cli.post("/estrategia", json={"ambito": "global", "modo": "prioridade"})
    check("/estrategia global 200", r.status_code == 200, r.text[:200])
    check("/estrategia global devolve o lido",
          ((r.json() or {}).get("estrategia") or {}).get("modo") == "prioridade", r.text[:200])
    em_disco = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("/estrategia global gravada no disco",
          (em_disco.get("estrategia") or {}).get("modo") == "prioridade",
          str(em_disco.get("estrategia")))
    check("/estrategia perfis herdam sem copia",
          all((p.get("estrategia") or {}).get("modo") != "prioridade"
              for p in (em_disco.get("perfis") or {}).values()),
          str({n: p.get("estrategia") for n, p in (em_disco.get("perfis") or {}).items()}))
    est = cli.get("/estado").json()
    check("/estado todos os perfis mostram prioridade herdada",
          all((p.get("estrategia") or {}).get("modo") == "prioridade"
              for p in (est.get("perfis") or {}).values()),
          str({n: p.get("estrategia") for n, p in (est.get("perfis") or {}).items()}))

    # --- isolamento entre perfis: uma escolha explicita nao pode ser engolida --
    # Fluxo real do dono: escolher no perfil o mesmo valor que o global tem.
    r = cli.post("/perfil", json={"perfil": "default", "estrategia": {"modo": "prioridade"}})
    check("/perfil estrategia igual ao global 200", r.status_code == 200, r.text[:200])
    em_disco = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("/perfil estrategia igual ao global fica gravada",
          (em_disco["perfis"]["default"].get("estrategia") or {}).get("modo") == "prioridade",
          str(em_disco["perfis"]["default"].get("estrategia")))
    est = cli.get("/estado").json()
    check("/estado default passa a proprio",
          (est["perfis"]["default"].get("estrategia") or {}).get("origem") == "proprio",
          str(est["perfis"]["default"].get("estrategia")))

    # Mudar o global depois: o default declarado nao se mexe, os outros seguem.
    r = cli.post("/estrategia", json={"ambito": "global", "modo": "rodizio"})
    check("/estrategia global muda com perfil declarado 200", r.status_code == 200, r.text[:200])
    est = cli.get("/estado").json()
    check("/estado perfil declarado resiste a mudanca do global",
          (est["perfis"]["default"].get("estrategia") or {}).get("modo") == "prioridade"
          and (est["perfis"]["default"].get("estrategia") or {}).get("origem") == "proprio",
          str(est["perfis"]["default"].get("estrategia")))
    check("/estado perfil que herda segue o global",
          (est["perfis"]["trabalho"].get("estrategia") or {}).get("modo") == "rodizio"
          and (est["perfis"]["trabalho"].get("estrategia") or {}).get("origem") == "herdado",
          str(est["perfis"]["trabalho"].get("estrategia")))

    # Repor o cenario que os testes seguintes esperam: default a herdar, global prioridade.
    cli.post("/estrategia", json={"ambito": "perfil", "perfil": "default", "herdar": True})
    cli.post("/estrategia", json={"ambito": "global", "modo": "prioridade"})
    est = cli.get("/estado").json()
    check("/estado reposicao do cenario",
          (est["perfis"]["default"].get("estrategia") or {}).get("origem") == "herdado"
          and (est.get("estrategia_global") or {}).get("modo") == "prioridade",
          str(est.get("estrategia_global")))

    r = cli.post("/estrategia", json={"ambito": "perfil", "perfil": "trabalho",
                                      "modo": "rodizio", "por": "turno"})
    check("/estrategia por perfil 200", r.status_code == 200, r.text[:200])
    check("/estrategia por perfil devolve origem proprio",
          ((r.json() or {}).get("estrategia") or {}).get("origem") == "proprio", r.text[:200])
    est = cli.get("/estado").json()
    check("/estado trabalho com escolha propria",
          (est["perfis"]["trabalho"].get("estrategia") or {}).get("por") == "turno",
          str(est["perfis"]["trabalho"].get("estrategia")))
    check("/estado default continua herdado",
          (est["perfis"]["default"].get("estrategia") or {}).get("origem") == "herdado",
          str(est["perfis"]["default"].get("estrategia")))

    r = cli.post("/estrategia", json={"ambito": "perfil", "perfil": "trabalho", "herdar": True})
    check("/estrategia herdar 200", r.status_code == 200, r.text[:200])
    em_disco = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("/estrategia herdar apaga o bloco do perfil",
          "estrategia" not in (em_disco["perfis"]["trabalho"] or {}),
          str((em_disco["perfis"]["trabalho"] or {}).get("estrategia")))

    # --- tarefa persistente: dois sitios, uma heranca ------------------------
    # O bloco vive em `analise`. O painel da configuracao edita o global, o do
    # perfil edita o delta. Antes da 3.27.0 um POST destes era ignorado em
    # silencio; a caixa do painel mentia e nada se queixava — por isso o teste
    # le o disco, nao a resposta.
    corpo = cli.get("/perfil", params={"nome": "trabalho"}).json() or {}
    check("/perfil traz o bloco da tarefa persistente",
          "tarefa_persistente" in (corpo.get("declarado") or {}),
          str(corpo.get("declarado"))[:200])
    check("/perfil sem declaracao devolve nulo (herda)",
          (corpo.get("declarado") or {}).get("tarefa_persistente") is None,
          str((corpo.get("declarado") or {}).get("tarefa_persistente")))
    gp = ((corpo.get("global") or {}).get("tarefa_persistente") or {})
    check("/perfil mostra o global com os padroes",
          gp.get("activo") is True and gp.get("expira_turnos") == 6 and gp.get("expira_min") == 90,
          str(gp))
    check("/perfil efectivo diz que vem do padrao (ainda sem escrita)",
          ((corpo.get("efectivo") or {}).get("tarefa_persistente") or {}).get("origem") == "padrao",
          str((corpo.get("efectivo") or {}).get("tarefa_persistente")))

    r = cli.post("/perfil", json={"perfil": "trabalho",
                                  "tarefa_persistente": {"activo": False, "expira_turnos": 4}})
    check("/perfil tarefa persistente 200", r.status_code == 200, r.text[:300])
    em_disco = json.loads((d / "config.json").read_text(encoding="utf-8"))
    bloco = ((em_disco["perfis"]["trabalho"].get("analise") or {}).get("tarefa_persistente") or {})
    check("/perfil grava so o declarado (sem copiar o global)",
          bloco == {"activo": False, "expira_turnos": 4}, str(bloco))

    corpo = cli.get("/perfil", params={"nome": "trabalho"}).json() or {}
    ef = (corpo.get("efectivo") or {}).get("tarefa_persistente") or {}
    check("/perfil efectivo junta o delta ao global, chave a chave",
          ef.get("activo") is False and ef.get("expira_turnos") == 4 and ef.get("expira_min") == 90,
          str(ef))
    check("/perfil efectivo passa a proprio", ef.get("origem") == "proprio", str(ef))

    # Declarar exactamente os valores do global nao fica escrito: e o que impede
    # um perfil de ficar preso ao valor do dia quando o global mudar.
    r = cli.post("/perfil", json={"perfil": "default",
                                  "tarefa_persistente": {"activo": True, "expira_turnos": 6,
                                                         "expira_min": 90}})
    check("/perfil igual ao global 200", r.status_code == 200, r.text[:300])
    em_disco = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("/perfil igual ao global nao fica gravado",
          "tarefa_persistente" not in (em_disco["perfis"]["default"].get("analise") or {}),
          str(em_disco["perfis"]["default"].get("analise")))

    # Fora dos limites e recusado com o nome da chave: o painel mostra-o junto
    # ao campo em vez de um 400 anonimo.
    r = cli.post("/perfil", json={"perfil": "default",
                                  "tarefa_persistente": {"expira_turnos": 0}})
    check("/perfil fora de limites 400", r.status_code == 400, str(r.status_code))
    check("/perfil o erro nomeia o bloco e a chave",
          "tarefa_persistente" in r.text and "expira_turnos" in r.text, r.text[:300])

    r = cli.post("/perfil", json={"perfil": "default", "tarefa_persistente": "activa"})
    check("/perfil nao-objecto 400", r.status_code == 400, str(r.status_code))

    r = cli.post("/perfil", json={"perfil": "trabalho", "tarefa_persistente": "herdar"})
    check("/perfil herdar tarefa persistente 200", r.status_code == 200, r.text[:300])
    em_disco = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("/perfil herdar apaga o bloco do perfil",
          "tarefa_persistente" not in (em_disco["perfis"]["trabalho"].get("analise") or {}),
          str(em_disco["perfis"]["trabalho"].get("analise")))
    ef = ((cli.get("/perfil", params={"nome": "trabalho"}).json() or {}).get("efectivo") or {}).get("tarefa_persistente") or {}
    check("/perfil volta aos valores do global",
          ef.get("activo") is True and ef.get("expira_turnos") == 6 and ef.get("expira_min") == 90,
          str(ef))

    velho = (cli.get("/estado").json() or {}).get("rev")
    cli.post("/estrategia", json={"ambito": "global", "por": "turno"})
    r = cli.post("/estrategia", json={"ambito": "global", "modo": "rodizio", "rev": velho})
    check("/estrategia com rev antigo da 409", r.status_code == 409, str(r.status_code))
    atual = (cli.get("/estado").json() or {}).get("rev")
    r = cli.post("/estrategia", json={"ambito": "global", "modo": "rodizio", "rev": atual})
    check("/estrategia com rev actual passa", r.status_code == 200, r.text[:200])

    # --- /modelo: uma rota do catalogo, numa pagina propria ----------------
    # O gesto e cirurgico: substitui UMA entrada do perfil e nao toca no resto.
    # O que se prova aqui e o que um teste de strings nao prova — que "Validar"
    # nao escreve, que gravar preserva a posicao (a ordem e a de prioridade),
    # que um id repetido e recusado e que o perfil irmao fica intacto.
    (d / "config.json").write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    antes_sha = __import__("hashlib").sha256((d / "config.json").read_bytes()).hexdigest()

    r = cli.get("/modelo?perfil=trabalho")
    check("/modelo novo 200", r.status_code == 200, str(r.status_code))
    novo = r.json() if r.status_code == 200 else {}
    check("/modelo novo diz que e novo", novo.get("novo") is True, str(novo.get("novo")))
    check("/modelo novo traz entrada em branco",
          (novo.get("entrada") or {}).get("id") == ""
          and (novo.get("entrada") or {}).get("complexidade") == [],
          str(novo.get("entrada"))[:120])
    opc = novo.get("opcoes") or {}
    check("/modelo opcoes fechadas vem do gestor",
          opc.get("niveis") == ["leve", "media", "alta"]
          and opc.get("acessos") == ["api", "subscricao"]
          and opc.get("esforcos") == ["baixo", "medio", "alto"]
          and "texto" in (opc.get("modalidades") or []),
          str(opc)[:200])
    check("/modelo opcoes trazem as tarefas do perfil",
          [t.get("valor") for t in (opc.get("tarefas") or [])] == ["arquitetura"],
          str(opc.get("tarefas"))[:120])

    r = cli.get("/modelo?perfil=trabalho&id=gpt-sol")
    check("/modelo de uma rota 200", r.status_code == 200, str(r.status_code))
    um = r.json() if r.status_code == 200 else {}
    check("/modelo devolve a entrada inteira",
          (um.get("entrada") or {}).get("tarefas") == ["arquitetura"]
          and (um.get("entrada") or {}).get("modelo") == "openai/gpt-6.1-sol",
          str(um.get("entrada"))[:160])
    check("/modelo de uma rota nao e novo", um.get("novo") is False, str(um.get("novo")))

    r = cli.get("/modelo?perfil=trabalho&id=nao-existe")
    check("/modelo id desconhecido da 404", r.status_code == 404, str(r.status_code))
    r = cli.get("/modelo?perfil=nao-existe")
    check("/modelo perfil desconhecido da 404", r.status_code == 404, str(r.status_code))

    # Validar nao escreve: e a promessa do botao.
    entrada_ok = {"id": "gpt-sol", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
                  "complexidade": ["alta"], "acesso": "api", "tarefas": ["arquitetura"],
                  "esforco": "alto", "contexto_max": 200000, "modalidades": ["texto"],
                  "json_nativo": True, "nota": "rota de arquitectura"}
    r = cli.post("/modelo", json={"perfil": "trabalho", "id": "gpt-sol",
                                  "entrada": entrada_ok, "validar": True})
    check("/modelo validar 200", r.status_code == 200, r.text[:200])
    v = r.json() if r.status_code == 200 else {}
    check("/modelo validar ok sem erros", v.get("ok") is True and not v.get("erros"),
          str(v)[:200])
    check("/modelo validar NAO grava",
          __import__("hashlib").sha256((d / "config.json").read_bytes()).hexdigest() == antes_sha,
          "o ficheiro mudou numa validacao")

    r = cli.post("/modelo", json={"perfil": "trabalho", "id": "gpt-sol",
                                  "entrada": {"id": "gpt-sol", "provider": "openrouter",
                                              "modelo": "openai/gpt-6.1-sol",
                                              "complexidade": []},
                                  "validar": True})
    check("/modelo validar recusa sem complexidade", r.status_code == 200, r.text[:200])
    check("/modelo validar diz o erro",
          bool((r.json() or {}).get("erros")), str(r.json())[:200])

    # Gravar a mesma rota: muda o esforco, mantem a posicao no catalogo.
    r = cli.post("/modelo", json={"perfil": "trabalho", "id": "gpt-sol",
                                  "entrada": entrada_ok})
    check("/modelo gravar 200", r.status_code == 200, r.text[:200])
    g = r.json() if r.status_code == 200 else {}
    check("/modelo gravar devolve o lido do disco",
          (g.get("entrada") or {}).get("esforco") == "alto"
          and (g.get("entrada") or {}).get("contexto_max") == 200000,
          str(g.get("entrada"))[:200])
    em_disco = json.loads((d / "config.json").read_text(encoding="utf-8"))
    cat = em_disco["perfis"]["trabalho"]["catalogo"]
    check("/modelo gravar mantem a posicao no catalogo",
          [e.get("id") for e in cat] == ["deepseek-flash", "gpt-sol"],
          str([e.get("id") for e in cat]))
    check("/modelo gravar nao toca no perfil irmao",
          [e.get("id") for e in em_disco["perfis"]["default"]["catalogo"]]
          == ["deepseek-flash", "deepseek-pro"],
          str([e.get("id") for e in em_disco["perfis"]["default"]["catalogo"]]))

    # Criar: entra no fim, com os campos opcionais que se declararam.
    r = cli.post("/modelo", json={"perfil": "trabalho", "id": None,
                                  "entrada": {"id": "flash-baratissimo", "provider": "openrouter",
                                              "modelo": "vendor/flash:free",
                                              "complexidade": ["leve"], "acesso": "api",
                                              "tarefas": [], "modalidades": ["texto", "imagem"]}})
    check("/modelo criar 200", r.status_code == 200, r.text[:200])
    em_disco = json.loads((d / "config.json").read_text(encoding="utf-8"))
    cat = em_disco["perfis"]["trabalho"]["catalogo"]
    check("/modelo criar acrescenta ao fim", (cat[-1] or {}).get("id") == "flash-baratissimo",
          str([e.get("id") for e in cat]))
    check("/modelo criar grava as modalidades",
          (cat[-1] or {}).get("modalidades") == ["texto", "imagem"],
          str(cat[-1])[:160])

    r = cli.post("/modelo", json={"perfil": "trabalho", "id": None,
                                  "entrada": {"id": "flash-baratissimo", "provider": "openrouter",
                                              "modelo": "vendor/outro", "complexidade": ["leve"]}})
    check("/modelo recusa id repetido", r.status_code == 400, str(r.status_code))
    check("/modelo diz qual o id repetido", "flash-baratissimo" in r.text, r.text[:200])

    r = cli.post("/modelo", json={"perfil": "trabalho", "id": "flash-baratissimo",
                                  "entrada": {"id": "flash-baratissimo", "provider": "openrouter",
                                              "modelo": "vendor/flash:free",
                                              "complexidade": ["leve"],
                                              "acesso": "subscricao"}})
    check("/modelo recusa subscricao fora do openai-codex",
          r.status_code == 400 and "subscricao" in r.text, r.text[:200])

    velho = (cli.get("/estado").json() or {}).get("rev")
    cli.post("/modelo", json={"perfil": "trabalho", "id": "flash-baratissimo",
                              "entrada": {"id": "flash-baratissimo", "provider": "openrouter",
                                          "modelo": "vendor/flash:free",
                                          "complexidade": ["leve", "media"]}})
    r = cli.post("/modelo", json={"perfil": "trabalho", "id": "flash-baratissimo",
                                  "entrada": {"id": "flash-baratissimo", "provider": "openrouter",
                                              "modelo": "vendor/flash:free",
                                              "complexidade": ["leve"]}, "rev": velho})
    check("/modelo com rev antigo da 409", r.status_code == 409, str(r.status_code))

    # --- /superficies ------------------------------------------------------
    # A superficie e o unico criterio que o core entrega de forma
    # deterministica; a regra tem de ser gravavel por perfil, ter as tres
    # formas do contrato, e recusar o que nunca dispararia.
    (d / "config.json").write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    est = cli.get("/estado").json()
    p0 = (est.get("perfis") or {}).get("trabalho") or {}
    cat0 = p0.get("catalogo") or [{}]
    entrada = (cat0[0] or {}).get("id")
    check("/superficies: o perfil tem catalogo para testar", bool(entrada),
          str(cat0)[:120])
    check("/estado opcoes de superficie e niveis",
          "cron" in ((est.get("opcoes") or {}).get("superficies") or [])
          and "alta" in ((est.get("opcoes") or {}).get("niveis") or []),
          str(est.get("opcoes"))[:160])

    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "trabalho",
                                       "superficie": "cron", "alvo": "entrada:" + entrada})
    check("/superficies grava por perfil", r.status_code == 200, r.text[:200])
    check("/superficies rele o que gravou",
          ((r.json() or {}).get("regra") or {}).get("entrada") == entrada, r.text[:200])
    e2 = cli.get("/estado").json()
    sup2 = (e2["perfis"]["trabalho"].get("superficies") or {}).get("cron") or {}
    check("/estado mostra a regra no perfil", sup2.get("entrada") == entrada, str(sup2))
    check("/estado diz que a regra e do perfil", sup2.get("origem_regra") == "perfil", str(sup2))
    check("/estado nao estende a regra aos outros perfis",
          not ((e2["perfis"]["default"].get("superficies") or {}).get("cron")),
          str(e2["perfis"]["default"].get("superficies")))

    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "trabalho",
                                       "superficie": "cli", "alvo": "nivel:alta"})
    check("/superficies aceita nivel",
          r.status_code == 200 and ((r.json() or {}).get("regra") or {}).get("nivel") == "alta",
          r.text[:200])

    m0 = cat0[0] or {}
    par = str(m0.get("provider")) + "/" + str(m0.get("modelo"))
    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "trabalho",
                                       "superficie": "whatsapp", "alvo": par})
    check("/superficies aceita provider/modelo",
          r.status_code == 200
          and ((r.json() or {}).get("regra") or {}).get("provider") == m0.get("provider"),
          r.text[:200])

    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "trabalho",
                                       "superficie": "cron", "alvo": "lixo"})
    check("/superficies recusa alvo sem forma", r.status_code == 400, str(r.status_code))
    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "trabalho",
                                       "superficie": "crom", "alvo": "entrada:" + entrada})
    check("/superficies recusa gralha de superficie", r.status_code == 400, r.text[:200])
    check("/superficies diz qual era a superficie certa",
          "cron" in r.text and "parecida" in r.text, r.text[:200])
    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "nao-existe",
                                       "superficie": "cron", "alvo": "entrada:" + entrada})
    check("/superficies perfil inexistente da 404", r.status_code == 404, str(r.status_code))
    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "trabalho",
                                       "superficie": "cron", "alvo": "entrada:nao-existe"})
    check("/superficies recusa entrada fora do catalogo", r.status_code == 400, r.text[:200])

    cli.post("/superficies", json={"ambito": "global", "superficie": "cron",
                                   "alvo": "entrada:" + entrada})
    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "trabalho",
                                       "superficie": "cron", "alvo": "nunca"})
    check("/superficies grava 'nunca'",
          r.status_code == 200 and ((r.json() or {}).get("regra") or {}).get("activo") is False,
          r.text[:200])
    e3 = cli.get("/estado").json()
    check("/estado marca a regra anulada",
          ((e3["perfis"]["trabalho"].get("superficies") or {}).get("cron") or {}).get("activo") is False)
    check("/estado mantem a regra global viva nos outros perfis",
          ((e3["perfis"]["default"].get("superficies") or {}).get("cron") or {}).get("origem_regra") == "global",
          str(e3["perfis"]["default"].get("superficies")))

    r = cli.post("/superficies", json={"ambito": "perfil", "perfil": "trabalho",
                                       "superficie": "cron", "alvo": ""})
    check("/superficies apaga a do perfil", r.status_code == 200, r.text[:200])
    e4 = cli.get("/estado").json()
    check("/estado volta a herdar o global",
          ((e4["perfis"]["trabalho"].get("superficies") or {}).get("cron") or {}).get("origem_regra") == "global",
          str(e4["perfis"]["trabalho"].get("superficies")))

    velho = (cli.get("/estado").json() or {}).get("rev")
    cli.post("/superficies", json={"ambito": "global", "superficie": "cli",
                                   "alvo": "nivel:media"})
    r = cli.post("/superficies", json={"ambito": "global", "superficie": "cli",
                                       "alvo": "nivel:leve", "rev": velho})
    check("/superficies com rev antigo da 409", r.status_code == 409, str(r.status_code))

    # ---- pagina de um perfil: GET/POST proprios, como a pagina de um modelo
    r = cli.get("/perfil", params={"nome": "default"})
    check("/perfil devolve o perfil pedido", r.status_code == 200, str(r.status_code))
    corpo = r.json() if r.status_code == 200 else {}
    check("/perfil separa declarado de efectivo",
          "declarado" in corpo and "efectivo" in corpo and "global" in corpo,
          ", ".join(sorted(corpo)) if corpo else "-")
    check("/perfil traz as listas fechadas", (corpo.get("opcoes") or {}).get("niveis") is not None
          and (corpo.get("opcoes") or {}).get("estrategia") is not None)
    check("/perfil traz a revisao para o 409", bool(corpo.get("rev")))
    r = cli.get("/perfil", params={"nome": "nao-existe"})
    check("/perfil de perfil inexistente da 404", r.status_code == 404, str(r.status_code))

    # Validar nao pode escrever: e o que distingue validar de gravar.
    antes = (Path(os.environ["VMM_DIR"]) / "config.json").read_bytes()
    r = cli.post("/perfil", json={"perfil": "default", "validar": True})
    check("/perfil validar responde ok", r.status_code == 200 and (r.json() or {}).get("validado") is True,
          str(r.status_code))
    depois = (Path(os.environ["VMM_DIR"]) / "config.json").read_bytes()
    check("/perfil validar nao escreve no ficheiro", antes == depois)

    r = cli.post("/perfil", json={"perfil": "default", "validar": True,
                                  "estrategia": {"modo": "rodizioo"}})
    check("/perfil recusa modo fora da lista fechada", r.status_code == 400, str(r.status_code))
    check("/perfil mostra os valores aceites no erro",
          "rodizio" in (r.text or "") and "prioridade" in (r.text or ""))

    # Gravar cirurgico: o catalogo nao pode desaparecer por se gravar o perfil.
    cat_antes = json.loads(depois.decode())["perfis"]["default"]["catalogo"]
    r = cli.post("/perfil", json={"perfil": "default", "validar": False,
                                  "latencia_max_ms": 30000,
                                  "tarefas": {"revisao": {"termos": "codigo, review",
                                                          "prioridade": 5}}})
    check("/perfil grava", r.status_code == 200, (r.text or "")[:200])
    gravado = json.loads((Path(os.environ["VMM_DIR"]) / "config.json").read_text())
    check("/perfil gravar preserva o catalogo",
          gravado["perfis"]["default"]["catalogo"] == cat_antes)
    check("/perfil gravar normaliza os termos",
          gravado["perfis"]["default"]["tarefas"]["revisao"]["termos"] == ["codigo", "review"],
          json.dumps(gravado["perfis"]["default"]["tarefas"], ensure_ascii=False))

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
    # A contagem no formato que a `bateria.py` le (`N/M`), depois das falhas e
    # antes do veredito: e o ultimo par da saida, logo o que conta.
    print(f"{OK}/{OK + len(FALHAS)} verificacoes")
    print("VEREDITO:", "PASS" if not FALHAS else "FAIL")
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    raise SystemExit(main())
