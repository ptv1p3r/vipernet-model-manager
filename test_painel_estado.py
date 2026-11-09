"""Testes do painel: versao do config (conflito) e verdade do cartao.

Dois defeitos medidos a 2026-11-03 motivam este ficheiro:

1. Uma aba antiga do painel gravou o config inteiro e repôs o passado: o
   conteudo escrito as 11:27:52 era byte a byte igual ao que a pagina tinha
   carregado antes das 11:18:47, apagando a correcao do perfil pessoal. Passa a
   haver `rev` e recusa 409.
2. O cartao mostrava "sem efeito — provider indisponivel" e "leu alta em
   1355 ms" na mesma linha: a leitura era de outro classificador, de 40 min
   antes. Passa a haver `rasto_valido` e `historico` datado.

Corre contra um config temporario: nao toca na config viva.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import gestor  # noqa: E402
import i18n  # noqa: E402

_spec = importlib.util.spec_from_file_location("vmm_dashboard_api", RAIZ / "dashboard" / "api.py")
assert _spec is not None and _spec.loader is not None
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

def _ts(minutos_atras: float) -> str:
    """Timestamp relativo ao agora: as fixtures nao podem envelhecer.

    Estavam presas ao relogio de 2026-11-03 (10:00, 12:00, 11:29): a suite
    ficava verde de manha e vermelha ao inicio da tarde, porque a regra dos
    2 h declarava expirado o motivo que o teste dava como actual. Uma fixture
    com data fixa mede a hora a que corre, nao a funcao.
    """
    return (datetime.now(timezone.utc) - timedelta(minutes=minutos_atras)).isoformat()


OK = 0
FALHAS: list[str] = []


def verificar(nome: str, condicao: bool, detalhe: str = "") -> None:
    global OK
    if condicao:
        OK += 1
    else:
        FALHAS.append(nome + ((" — " + detalhe) if detalhe else ""))


# --------------------------------------------------------------------- puros


def test_rev() -> None:
    a = {"b": 1, "a": {"y": 2, "x": 1}}
    b = {"a": {"x": 1, "y": 2}, "b": 1}
    verificar("rev: ordem das chaves nao muda a versao", api._rev(a) == api._rev(b))
    verificar("rev: valor diferente muda a versao", api._rev(a) != api._rev({"b": 1, "a": {"x": 1, "y": 3}}))
    verificar("rev: tamanho fixo de 16", len(api._rev(a)) == 16, api._rev(a))
    verificar("rev: deterministica", api._rev(a) == api._rev(a))


def test_conflito() -> None:
    verificar("conflito: sem rev do cliente nao ha conflito", api._conflito("", "abc") is False)
    verificar("conflito: rev igual nao ha conflito", api._conflito("abc", "abc") is False)
    verificar("conflito: rev diferente ha conflito", api._conflito("abc", "def") is True)


def test_idade() -> None:
    verificar("idade: ts vazio -> None", api._idade_s("") is None)
    verificar("idade: ts ilegivel -> None", api._idade_s("nem-isto-e-data") is None)
    idade = api._idade_s(_ts(60))
    verificar("idade: ts no passado -> positivo", idade is not None and idade > 0, str(idade))
    futuro = api._idade_s("2099-01-01T00:00:00+00:00")
    verificar("idade: nunca negativa", futuro == 0.0, str(futuro))


def test_decidir_rasto() -> None:
    # A lingua da config viva nao pode decidir o resultado deste teste: fixa-se
    # o portugues, que e o texto que estas verificacoes comparam. A variante
    # inglesa e verificada no fim, para a traducao nao voltar a escapar.
    i18n.definir_para_testes("pt")
    deepseek = {"modo": "modelo", "provider": "deepseek", "modelo": "deepseek-flash", "esforco": "baixo"}
    inception = {"modo": "modelo", "provider": "inception", "modelo": "mercury-3", "esforco": "baixo"}
    leitura = {"provider": "deepseek", "modelo": "deepseek-flash", "lido": "alta", "ms": 1355,
               "ts": _ts(30), "falhou": ""}

    # (a) o caso do incidente: leitura de deepseek com config inception
    #     (motivo recente, para isolar o critério «outro classificador» da idade)
    valido, motivo, hist = api._decidir_rasto(
        inception, leitura, "provider_indisponivel: Unknown provider 'inception'",
        datetime.now(timezone.utc).isoformat())
    verificar("incidente: leitura de outro classificador nao e estado", valido is False)
    verificar("incidente: o motivo mantem-se visivel", motivo.startswith("provider_indisponivel"))
    verificar("incidente: a leitura vai para historico", isinstance(hist, dict) and hist.get("lido") == "alta")
    verificar("incidente: o historico traz a idade", hist is not None and hist.get("idade_s") is not None)
    verificar("incidente: o historico diz porque nao conta",
              hist is not None and hist.get("motivo") == "outro classificador")

    # (b) config e leitura coerentes, sem erro a desmentir
    valido, motivo, hist = api._decidir_rasto(deepseek, leitura, "", "")
    verificar("coerente: leitura valida", valido is True)
    verificar("coerente: sem motivo", motivo == "")
    verificar("coerente: sem historico", hist is None)

    # (c) config corrigida depois do erro: o erro antigo nao fica a assombrar
    valido, motivo, hist = api._decidir_rasto(
        deepseek, leitura, "provider_indisponivel: antigo", _ts(90))
    verificar("corrigido: leitura posterior ao erro conta como estado", valido is True)
    verificar("corrigido: o motivo antigo desaparece", motivo == "")

    # (d) leitura anterior ao erro actual: nao serve de evidencia
    valido, motivo, hist = api._decidir_rasto(
        deepseek, leitura, "provider_indisponivel: actual", _ts(5))
    verificar("anterior ao erro: nao e estado", valido is False)
    verificar("anterior ao erro: motivo visivel", motivo.startswith("provider_indisponivel"))
    verificar("anterior ao erro: historico diz por que",
              hist is not None and hist.get("motivo") == "anterior ao erro actual")

    # (e) sem leitura nenhuma: nada inventado
    valido, motivo, hist = api._decidir_rasto(deepseek, None, "desligado", "")
    verificar("sem leitura: nao valido", valido is False)
    verificar("sem leitura: motivo passa", motivo == "desligado")
    verificar("sem leitura: sem historico", hist is None)

    # (f) heuristica configurada com leitura antiga de modelo
    valido, _motivo, hist = api._decidir_rasto({"modo": "heuristica"}, leitura, "desligado", "")
    verificar("heuristica: leitura de modelo nao e estado", valido is False)
    verificar("heuristica: leitura antiga vai para historico", hist is not None)

    # (g) motivo anterior a ultima escrita do config: nao descreve a config actual
    #     (o caso real: provider declarado as 12:45, motivo das 11:29 no cartao)
    valido, motivo, hist = api._decidir_rasto(
        inception, None, "provider_indisponivel: Unknown provider 'inception'",
        _ts(60), _ts(10))
    verificar("motivo anterior a config: nao e estado", valido is False)
    verificar("motivo anterior a config: sai do detalhe corrente", motivo == "")
    verificar("motivo anterior a config: vai para historico datado",
              isinstance(hist, dict) and (hist.get("motivo_antigo") or {}).get("motivo", "").startswith("provider_indisponivel"))
    verificar("motivo anterior a config: diz por que",
              (hist or {}).get("motivo_antigo", {}).get("porque") == "anterior à config actual")

    # (h) motivo sem config nova mas velho: passou tempo sem um turno que o confirme
    valido, motivo, hist = api._decidir_rasto(
        inception, None, "provider_indisponivel: Unknown provider 'inception'",
        _ts(180), _ts(180))
    verificar("motivo expirado: nao e estado", valido is False)
    verificar("motivo expirado: sai do detalhe corrente", motivo == "")
    verificar("motivo expirado: fica no historico",
              (hist or {}).get("motivo_antigo") is not None)

    # (i) motivo recente e posterior a config: continua a ser estado, como deve
    valido, motivo, hist = api._decidir_rasto(
        inception, None, "provider_indisponivel: Unknown provider 'inception'",
        datetime.now(timezone.utc).isoformat(), _ts(30))
    verificar("motivo recente: continua visivel como estado",
              valido is False and motivo.startswith("provider_indisponivel"))

    # (j) a mesma decisao em ingles: o cartao nao pode mostrar portugues
    i18n.MISSES.clear()
    i18n.definir_para_testes("en")
    _v, _m, hist_en = api._decidir_rasto(
        deepseek, leitura, "provider_indisponivel: actual", _ts(5))
    verificar("en: o historico sai traduzido",
              hist_en is not None and hist_en.get("motivo") == "older than the current error",
              str((hist_en or {}).get("motivo")))
    _v, _m, hist_en = api._decidir_rasto(
        deepseek, leitura, "provider_indisponivel: actual", _ts(5),
        datetime.now(timezone.utc).isoformat())
    porque_en = ((hist_en or {}).get("motivo_antigo") or {}).get("porque")
    verificar("en: o porque sai traduzido",
              porque_en is not None and "older than the current config"
              in str(porque_en), str(porque_en))
    verificar("en: nenhuma chave ficou em falta", not i18n.MISSES, str(sorted(i18n.MISSES)[:3]))
    i18n.definir_para_testes(None)


# ----------------------------------------------------------------------- e2e


def _app(tmp_config: Path) -> TestClient:
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/vipernet-model-manager")
    return TestClient(app)


def test_identidade() -> None:
    """Versao, autor e repositorio no painel — uma casa, nao uma copia a mao.

    A versao tem de vir do ``plugin.yaml`` (a mesma que o loader e o catalogo
    leem). Uma copia escrita no JS divergiu no passado e o cartao mostrou
    3.11.2 com o disco em 3.11.3.
    """
    meta = api._meta_plugin()
    verificar("identidade: traz versao", bool(meta.get("versao")), str(meta))
    verificar("identidade: traz autor", bool(meta.get("autor")), str(meta))
    verificar("identidade: o repositorio do painel e o repositorio PUBLICO (catalogo)",
              meta.get("github") == "https://github.com/ptv1p3r/vipernet-model-manager", str(meta.get("github")))
    verificar("identidade: github do autor separado do repositorio",
              meta.get("autor_github") == "https://github.com/ptv1p3r", str(meta.get("autor_github")))
    verificar("identidade: autor e o nome completo",
              meta.get("autor") == "Pedro Tiago de Jesus Estevanez Roldan", str(meta.get("autor")))

    # A versao do plugin.yaml e a fonte: le-se o ficheiro e compara-se.
    yaml_txt = (RAIZ / "plugin.yaml").read_text(encoding="utf-8")
    versao_yaml = ""
    for linha in yaml_txt.splitlines():
        if linha.startswith("version:"):
            versao_yaml = linha.split(":", 1)[1].strip().strip('"')
            break
    verificar("identidade: versao vem do plugin.yaml", meta.get("versao") == versao_yaml,
              "%s != %s" % (meta.get("versao"), versao_yaml))

    # manifest e plugin.yaml tem de dizer o mesmo numero, senao ha deriva.
    manifest = json.loads((RAIZ / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    verificar("identidade: manifest == plugin.yaml",
              manifest.get("version") == versao_yaml,
              "%s != %s" % (manifest.get("version"), versao_yaml))
    verificar("identidade: sem deriva reportada",
              meta.get("versao_manifest") == meta.get("versao"),
              str(meta.get("versao_manifest")))


def test_endpoint(tmp: Path) -> None:
    original_fc = gestor.ficheiro_config
    original_dir = gestor.directorio
    gestor.ficheiro_config = lambda: tmp  # type: ignore[assignment]
    gestor.directorio = lambda: tmp.parent  # type: ignore[assignment]
    try:
        cliente = _app(tmp)
        base = "/api/plugins/vipernet-model-manager"

        r = cliente.get(base + "/config")
        verificar("GET config: 200", r.status_code == 200, str(r.status_code))
        corpo = r.json()
        verificar("GET config: envelope com config e rev", set(corpo.keys()) >= {"config", "rev"})
        rev = corpo["rev"]
        verificar("GET config: rev e o da config lida", rev == api._rev(corpo["config"]))

        sha_antes = hashlib.sha256(tmp.read_bytes()).hexdigest()

        # Aba antiga: rev que ja nao e o actual
        velho = json.loads(json.dumps(corpo["config"]))
        velho["perfis"]["default"]["activo"] = False
        r = cliente.post(base + "/config", json={"config": velho, "rev": "versao-que-ja-nao-existe"})
        verificar("POST com rev antigo: 409", r.status_code == 409, str(r.status_code))
        verificar("POST com rev antigo: corpo traz a config actual",
                  r.json().get("rev") == rev and "config" in r.json())
        # A razao tem de chegar ao utilizador. O painel corre sobre o `fetchJSON`
        # do dashboard, que le a frase de `detail`/`message`/`error` e, sem
        # nenhuma delas, mostra a copia do estado HTTP ("That change conflicts
        # with the current state on the server.", em ingles). O corpo deste 409
        # trazia-a so em `erro` — chave que ninguem le — e o utilizador ficava
        # sem saber o que aconteceu. Medido a 2026-11-08 08:28:43 num conflito
        # real: a lingua foi gravada pelo seletor e o editor, aberto com a
        # versao anterior, recebeu essa frase generica ao "Gravar".
        corpo409 = r.json()
        legivel = [k for k in ("detail", "message", "error")
                   if isinstance(corpo409.get(k), str) and corpo409[k].strip()]
        verificar("POST com rev antigo: a razao vem numa chave que o painel le",
                  bool(legivel), str(sorted(corpo409)))
        verificar("POST com rev antigo: a razao nao e o codigo nu",
                  bool(legivel) and str(r.status_code) not in corpo409[legivel[0]]
                  and "config" in corpo409[legivel[0]].lower(),
                  corpo409.get(legivel[0] if legivel else "detail", ""))
        sha_depois = hashlib.sha256(tmp.read_bytes()).hexdigest()
        verificar("POST com rev antigo: NAO escreveu", sha_antes == sha_depois)
        verificar("POST com rev antigo: o interruptor nao mudou",
                  json.loads(tmp.read_text())["perfis"]["default"].get("activo") is not False)

        # Aba actual: grava
        novo = json.loads(json.dumps(corpo["config"]))
        novo["analise"]["limiares"]["media"] = 4
        r = cliente.post(base + "/config", json={"config": novo, "rev": rev})
        verificar("POST com rev actual: 200", r.status_code == 200, str(r.status_code))
        verificar("POST com rev actual: nova versao devolvida", r.json().get("rev") != rev)
        verificar("POST com rev actual: escreveu", json.loads(tmp.read_text())["analise"]["limiares"]["media"] == 4)
        verificar("POST com rev actual: sem_rev falso", r.json().get("sem_rev") is False)

        # O rev devolvido serve para a proxima gravacao
        rev2 = r.json()["rev"]
        r = cliente.post(base + "/config", json={"config": novo, "rev": rev2})
        verificar("POST com o rev devolvido: 200", r.status_code == 200, str(r.status_code))

        # O estado traz a identidade do plugin para o cabecalho do painel.
        r = cliente.get(base + "/estado")
        verificar("GET estado: 200", r.status_code == 200, str(r.status_code))
        ident = r.json().get("plugin") or {}
        verificar("GET estado: bloco plugin presente", bool(ident), str(list(ident)))
        verificar("GET estado: plugin.versao", bool(ident.get("versao")), str(ident))
        verificar("GET estado: plugin.autor", ident.get("autor") == "Pedro Tiago de Jesus Estevanez Roldan", str(ident.get("autor")))
        verificar("GET estado: plugin.github traz o repositorio publico", ident.get("github") == "https://github.com/ptv1p3r/vipernet-model-manager", str(ident.get("github")))

        # Cliente antigo, corpo cru: grava mas fica marcado sem_rev
        r = cliente.post(base + "/config", json=novo)
        verificar("POST corpo cru (legado): 200", r.status_code == 200, str(r.status_code))
        verificar("POST corpo cru (legado): marcado sem_rev", r.json().get("sem_rev") is True)

        # Config sem perfis continua recusada
        r = cliente.post(base + "/config", json={"config": {"perfis": {}}, "rev": rev2})
        verificar("POST sem perfis: 400", r.status_code == 400, str(r.status_code))

        # O gesto do dono, medido a 2026-11-08 08:28:43: mudar a lingua no seletor
        # e gravar o formulario logo a seguir. A lingua vive no config, logo
        # grava-la muda o `rev`; se a resposta do /idioma nao o devolvesse, o
        # editor aberto ficava com a versao anterior e o "Gravar" seguinte levava
        # 409 a acusar uma mudanca que o proprio painel tinha feito.
        r = cliente.post(base + "/idioma", json={"idioma": "en"})
        verificar("POST idioma: 200", r.status_code == 200, str(r.status_code))
        corpo_idioma = r.json()
        verificar("POST idioma: devolve o rev novo", bool(corpo_idioma.get("rev")),
                  str(sorted(corpo_idioma)))
        lido = cliente.get(base + "/config").json()
        verificar("POST idioma: o rev devolvido e o do disco",
                  corpo_idioma.get("rev") == lido["rev"],
                  "%s != %s" % (corpo_idioma.get("rev"), lido["rev"]))
        doc = json.loads(json.dumps(lido["config"]))
        doc["idioma"] = "en"
        r = cliente.post(base + "/config", json={"config": doc, "rev": corpo_idioma["rev"]})
        verificar("gravar depois de mudar a lingua: 200", r.status_code == 200,
                  str(r.status_code) + " " + r.text[:160])
        verificar("gravar depois de mudar a lingua: idioma mantido",
                  json.loads(tmp.read_text()).get("idioma") == "en")
        # Deixa o fixture como estava, para os testes seguintes nao herdarem a
        # lingua trocada.
        cliente.post(base + "/idioma", json={"idioma": "pt"})

        # As chaves de adaptacao a modelos arbitrarios chegam ao disco e voltam
        # intactas. Se a validacao do painel as descartasse em silencio, o
        # painel e a config ficavam com dois comportamentos e nenhum aviso —
        # foi o que aconteceu com `via`/`confianca_min` na 3.14.1.
        r = cliente.get(base + "/config")
        rev3 = r.json()["rev"]
        com_modelo = json.loads(json.dumps(r.json()["config"]))
        chaves_adaptacao = {
            "via": "auto",
            "preset": "sistema-um",
            "estado": "medidas",
            "confianca_min": 0.0,
            "temperatura": None,
            "max_tokens_campo": "max_completion_tokens",
            "caminho": "/api/v9/chat",
            "caminho_decisoes": "/api/alpha/decisions",
            "parametros_extra": {"verbosity": "low"},
            "headers_extra": {"HTTP-Referer": "https://vipernet.local"},
        }
        com_modelo["analise"]["classificador"] = dict(
            chaves_adaptacao, activo=True, provider="openrouter",
            modelo="~typesafe/jev-latest")
        r = cliente.post(base + "/config", json={"config": com_modelo, "rev": rev3})
        verificar("POST com chaves de adaptacao: 200", r.status_code == 200,
                  str(r.status_code) + " " + r.text[:200])
        gravado = json.loads(tmp.read_text())["analise"]["classificador"]
        for chave, esperado in chaves_adaptacao.items():
            verificar("config gravada: %s sobreviveu" % chave,
                      chave in gravado and gravado[chave] == esperado,
                      "%r != %r" % (gravado.get(chave), esperado))
        verificar("config gravada: temperatura null preservada",
                  gravado.get("temperatura") is None, repr(gravado.get("temperatura")))
        # E o estado do painel continua a ler esta config sem rebentar.
        r = cliente.get(base + "/estado")
        verificar("GET estado com chaves de adaptacao: 200", r.status_code == 200,
                  str(r.status_code))

        # Um provider que a configuracao ainda nao usa tem de poder receber
        # rota. Medido a 2026-11-08: o painel so deixava escolher entre os
        # providers ja usados (lista fechada no formulario), e um provider que
        # o Hermes serve — com credencial propria — nao podia entrar no
        # catalogo por ali. O backend nunca o recusou; o que faltava era o
        # campo aceitar texto. Aqui prova-se a ponta a ponta: opcoes, validar
        # (sem escrever) e gravar.
        r = cliente.get(base + "/modelo", params={"perfil": "default"})
        verificar("GET modelo novo: 200", r.status_code == 200, str(r.status_code))
        opcoes_modelo = r.json()["opcoes"]
        verificar("GET modelo: opcoes trazem providers_conhecidos",
                  "providers_conhecidos" in opcoes_modelo, str(sorted(opcoes_modelo)))
        verificar("GET modelo: os providers da configuracao estao entre os sugeridos",
                  set(opcoes_modelo.get("providers") or []) <= set(opcoes_modelo.get("providers_conhecidos") or []),
                  str(opcoes_modelo.get("providers")))
        novo_provider = "provider-que-ainda-nao-existe"
        entrada_nova = {"id": "rota-provider-novo", "provider": novo_provider,
                        "modelo": "modelo-1", "complexidade": ["leve"],
                        "acesso": "api", "tarefas": []}
        r = cliente.post(base + "/modelo",
                         json={"perfil": "default", "entrada": entrada_nova, "validar": True})
        verificar("POST modelo validar: provider fora das listas e aceite",
                  r.status_code == 200 and r.json().get("ok") is True,
                  str(r.status_code) + " " + r.text[:200])
        verificar("POST modelo validar: nao escreveu",
                  "rota-provider-novo" not in tmp.read_text())
        rev_m = cliente.get(base + "/config").json()["rev"]
        r = cliente.post(base + "/modelo",
                         json={"perfil": "default", "entrada": entrada_nova, "rev": rev_m})
        verificar("POST modelo gravar: provider fora das listas gravado",
                  r.status_code == 200, str(r.status_code) + " " + r.text[:200])
        catalogo_gravado = json.loads(tmp.read_text())["perfis"]["default"]["catalogo"]
        verificar("POST modelo gravar: o provider escrito ficou no catalogo",
                  any(e.get("id") == "rota-provider-novo" and e.get("provider") == novo_provider
                      for e in catalogo_gravado),
                  str([e.get("provider") for e in catalogo_gravado]))

        # O registo de escritas existe e nao guarda valores do config
        log = tmp.parent / "escritas.log"
        verificar("escritas.log: criado", log.exists())
        if log.exists():
            linhas = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
            verificar("escritas.log: uma linha por tentativa", len(linhas) >= 4, str(len(linhas)))
            verificar("escritas.log: o conflito ficou registado",
                      any(l.get("conflito") for l in linhas))
            verificar("escritas.log: o legado ficou registado como sem_rev",
                      any(l.get("sem_rev") and not l.get("conflito") for l in linhas))
            texto = log.read_text()
            verificar("escritas.log: nao guarda o catalogo", "deepseek-flash" not in texto)
    finally:
        gestor.ficheiro_config = original_fc  # type: ignore[assignment]
        gestor.directorio = original_dir  # type: ignore[assignment]


def test_remover_modelo(tmp: Path) -> None:
    """Remover uma rota do catalogo pelo botao da pagina do modelo.

    Tres desfechos, todos medidos contra a app FastAPI em memoria e sempre numa
    copia: (a) rota livre sai e desaparece do ficheiro; (b) rota chamada por uma
    superficie e recusada com o nome da regra e NAO escreve; (c) rota que ja nao
    existe da 404. A recusa por referencia e o caso que interessa: e o que
    impede o perfil de ficar a apontar para o nada.
    """
    original_fc = gestor.ficheiro_config
    original_dir = gestor.directorio
    gestor.ficheiro_config = lambda: tmp  # type: ignore[assignment]
    gestor.directorio = lambda: tmp.parent  # type: ignore[assignment]
    try:
        cliente = _app(tmp)
        base = "/api/plugins/vipernet-model-manager"

        rev = cliente.get(base + "/config").json()["rev"]
        doc = json.loads(tmp.read_text(encoding="utf-8"))
        # (a) rota livre no perfil `pessoal` — nenhuma superficie lhe chama.
        antes = [e["id"] for e in doc["perfis"]["pessoal"]["catalogo"]]
        alvo = "lightning-free"
        verificar("remover: a fixture tem a rota livre", alvo in antes, str(antes))
        sha_antes = hashlib.sha256(tmp.read_bytes()).hexdigest()

        r = cliente.post(base + "/modelo/remover",
                         json={"perfil": "pessoal", "id": alvo, "rev": rev})
        verificar("remover rota livre: 200", r.status_code == 200, r.text[:200])
        corpo = r.json()
        verificar("remover rota livre: ok", corpo.get("ok") is True, str(corpo))
        verificar("remover rota livre: diz quantas ficam",
                  corpo.get("restantes") == len(antes) - 1, str(corpo.get("restantes")))
        verificar("remover rota livre: rev novo devolvido",
                  bool(corpo.get("rev")) and corpo.get("rev") != rev, str(corpo.get("rev")))
        depois = [e["id"] for e in json.loads(tmp.read_text(encoding="utf-8"))["perfis"]["pessoal"]["catalogo"]]
        verificar("remover rota livre: saiu do ficheiro", alvo not in depois, str(depois))
        verificar("remover rota livre: os outros ficaram",
                  [i for i in antes if i != alvo] == depois, str(depois))
        verificar("remover rota livre: escreveu", sha_antes != hashlib.sha256(tmp.read_bytes()).hexdigest())

        # (b) rota chamada pela superficie `cron` no perfil `default`.
        rev = cliente.get(base + "/config").json()["rev"]
        doc = json.loads(tmp.read_text(encoding="utf-8"))
        sup = (doc["perfis"]["default"].get("analise") or {}).get("superficies") or {}
        chamada = str((sup.get("cron") or {}).get("entrada") or "")
        verificar("remover: a fixture tem superficie com entrada", bool(chamada), str(sup))
        sha_antes = hashlib.sha256(tmp.read_bytes()).hexdigest()
        contagem_antes = len(doc["perfis"]["default"]["catalogo"])

        r = cliente.post(base + "/modelo/remover",
                         json={"perfil": "default", "id": chamada, "rev": rev})
        # 200 com ok=false de proposito: o fetch do painel nao entrega o corpo de
        # uma resposta de erro, e a lista das regras e o que ha a mostrar.
        verificar("remover rota em uso: 200 com ok=false",
                  r.status_code == 200 and r.json().get("ok") is False, r.text[:200])
        corpo = r.json()
        verificar("remover rota em uso: motivo em_uso", corpo.get("motivo") == "em_uso", str(corpo.get("motivo")))
        verificar("remover rota em uso: nomeia a superficie",
                  corpo.get("referencias") == ["analise.superficies.cron"], str(corpo.get("referencias")))
        verificar("remover rota em uso: a razao vem em detail",
                  "analise.superficies.cron" in str(corpo.get("detail") or ""), str(corpo.get("detail")))
        verificar("remover rota em uso: NAO escreveu",
                  sha_antes == hashlib.sha256(tmp.read_bytes()).hexdigest())
        verificar("remover rota em uso: o catalogo nao mudou",
                  len(json.loads(tmp.read_text(encoding="utf-8"))["perfis"]["default"]["catalogo"]) == contagem_antes)

        # (c) rota que ja nao existe.
        r = cliente.post(base + "/modelo/remover",
                         json={"perfil": "pessoal", "id": "nao-existe-esta-rota", "rev": rev})
        verificar("remover rota inexistente: 404", r.status_code == 404, str(r.status_code))

        # (d) pedidos sem os campos obrigatorios.
        r = cliente.post(base + "/modelo/remover", json={"id": "x"})
        verificar("remover sem perfil: 400", r.status_code == 400, str(r.status_code))
        r = cliente.post(base + "/modelo/remover", json={"perfil": "pessoal"})
        verificar("remover sem id: 400", r.status_code == 400, str(r.status_code))

        # (e) rev velho: a aba antiga nao remove o que outra sessao mudou.
        r = cliente.post(base + "/modelo/remover",
                         json={"perfil": "pessoal", "id": "deepseek-flash", "rev": "rev-que-ja-nao-existe"})
        verificar("remover com rev antigo: 409", r.status_code == 409, str(r.status_code))
        verificar("remover com rev antigo: a razao e legivel",
                  bool(str(r.json().get("detail") or "").strip()), str(sorted(r.json())))
    finally:
        gestor.ficheiro_config = original_fc  # type: ignore[assignment]
        gestor.directorio = original_dir  # type: ignore[assignment]


def main() -> int:
    test_rev()
    test_conflito()
    test_idade()
    test_decidir_rasto()
    test_identidade()
    with tempfile.TemporaryDirectory() as td:
        origem = Path("/opt/data/plugin-data/vipernet-model-manager/config.json")
        tmp = Path(td) / "config.json"
        tmp.write_bytes(origem.read_bytes())
        test_endpoint(tmp)
    with tempfile.TemporaryDirectory() as td:
        origem = Path("/opt/data/plugin-data/vipernet-model-manager/config.json")
        tmp = Path(td) / "config.json"
        tmp.write_bytes(origem.read_bytes())
        test_remover_modelo(tmp)
    print("verificacoes ok: %d" % OK)
    if FALHAS:
        print("FALHAS (%d):" % len(FALHAS))
        for f in FALHAS:
            print("  - " + f)
        return 1
    print("VEREDITO: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
