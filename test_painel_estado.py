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
    verificar("identidade: github e o repositorio do plugin",
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
        verificar("GET estado: plugin.github", ident.get("github") == "https://github.com/ptv1p3r/vipernet-model-manager", str(ident.get("github")))

        # Cliente antigo, corpo cru: grava mas fica marcado sem_rev
        r = cliente.post(base + "/config", json=novo)
        verificar("POST corpo cru (legado): 200", r.status_code == 200, str(r.status_code))
        verificar("POST corpo cru (legado): marcado sem_rev", r.json().get("sem_rev") is True)

        # Config sem perfis continua recusada
        r = cliente.post(base + "/config", json={"config": {"perfis": {}}, "rev": rev2})
        verificar("POST sem perfis: 400", r.status_code == 400, str(r.status_code))

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
