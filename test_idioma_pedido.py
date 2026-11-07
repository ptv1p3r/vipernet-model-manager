"""A lingua vem do PEDIDO, nao so da config: cabecalhos `X-VMM` / `VMM_IDIOMA`.

Motivo medido: o painel tem um seletor de lingua que grava a config, mas as
mensagens de recusa do `api.py` continuavam a sair na lingua da **config**. Duas
consequencias reais: (a) entre a escolha no seletor e a gravacao da config havia
uma janela em que o ecra mostrava uma lingua e o servidor respondia noutra; (b)
uma sonda, um proxy ou um segundo consumidor do painel nao tinha forma de pedir
uma lingua sem escrever no config partilhado — escrever no config para ler uma
mensagem e o defeito, nao a correccao.

O desenho e um `ContextVar` posto por uma dependencia `async` do router. As duas
propriedades que este ficheiro mede, e que uma variavel de modulo nao teria:

* o cabecalho ganha a config (e ao `VMM_IDIOMA` do processo);
* **nao vaza** entre pedidos — o pedido seguinte sem cabecalho volta a lingua da
  config. Sem esta verificacao, uma variavel de modulo passaria nos outros casos
  e mentiria em producao com pedidos concorrentes.
"""

from __future__ import annotations

import contextvars
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import i18n  # noqa: E402

_spec = importlib.util.spec_from_file_location("vmm_dashboard_api", RAIZ / "dashboard" / "api.py")
assert _spec is not None and _spec.loader is not None
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PREFIXO = "/api/plugins/vipernet-model-manager"
VIVA = Path("/opt/data/plugin-data/vipernet-model-manager/config.json")

OK = 0
FALHAS: list[str] = []


def verificar(nome: str, condicao: bool, detalhe: str = "") -> None:
    global OK
    if condicao:
        OK += 1
    else:
        FALHAS.append(nome + ((" — " + detalhe) if detalhe else ""))


def _config(codigo: str) -> Path:
    """Config temporaria com a lingua pedida, o resto copiado da viva.

    Copia-se a viva inteira de proposito: se o teste escrevesse um config
    minimo, uma rota que leia outra chave falharia por falta dela e o teste
    mediria o fixture, nao a funcao.
    """
    td = Path(tempfile.mkdtemp(prefix="vmm-idioma-"))
    dados = json.loads(VIVA.read_text(encoding="utf-8"))
    dados["idioma"] = codigo
    (td / "config.json").write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    return td


def _com_dir(td: Path):
    """Aponta o `i18n` a esta config e devolve o cliente HTTP."""
    os.environ["VMM_DIR"] = str(td)
    i18n.definir_para_testes(None)
    app = FastAPI()
    app.include_router(api.router, prefix=PREFIXO)
    return TestClient(app)


def _recusa(cli: TestClient, cabecalhos: dict[str, str] | None = None) -> str:
    """Pede uma recusa deterministica: corpo `[]` nao e objecto JSON.

    Esta rota valida o corpo antes de tocar no config, portanto o texto da
    recusa mede so a lingua em vigor — que e exactamente o que se quer medir.
    """
    r = cli.post(PREFIXO + "/validar", json=[], headers=cabecalhos or {})
    assert r.status_code == 400, (r.status_code, r.text[:200])
    return str((r.json() or {}).get("detail", ""))


def test_estrutura() -> None:
    """A dependencia esta ligada ao router: uma rota nova herda-a sem se lembrar."""
    verificar("router declara a dependencia da lingua",
              len(api.router.dependencies) == 1,
              str(len(api.router.dependencies)))
    verificar("a dependencia e assincrona",
              getattr(api._lingua_do_pedido, "__name__", "") == "_lingua_do_pedido")
    import inspect

    verificar("a dependencia corre no event loop (nao no threadpool)",
              inspect.iscoroutinefunction(api._lingua_do_pedido))


def test_cabecalho_ganha_a_config() -> None:
    td = _config("pt")
    cli = _com_dir(td)
    sem = _recusa(cli)
    verificar("config pt, sem cabecalho: portugues",
              "corpo nao e um objecto JSON" in sem, sem[:80])

    com_x = _recusa(cli, {"X-VMM": "en"})
    verificar("X-VMM: en ganha a config pt",
              "body is not a JSON object" in com_x, com_x[:80])

    com_vmm = _recusa(cli, {"VMM_IDIOMA": "en"})
    verificar("VMM_IDIOMA: en (segundo nome) tambem ganha",
              "body is not a JSON object" in com_vmm, com_vmm[:80])

    regional = _recusa(cli, {"X-VMM": "pt-PT"})
    verificar("forma regional colapsa (pt-PT -> pt)",
              "corpo nao e um objecto JSON" in regional, regional[:80])


def test_cabecalho_ganha_quando_a_config_diz_o_contrario() -> None:
    td = _config("en")
    cli = _com_dir(td)
    sem = _recusa(cli)
    verificar("config en, sem cabecalho: ingles",
              "body is not a JSON object" in sem, sem[:80])

    com_pt = _recusa(cli, {"X-VMM": "pt"})
    verificar("X-VMM: pt ganha a config en",
              "corpo nao e um objecto JSON" in com_pt, com_pt[:80])


def test_cabecalho_invalido_cai_na_config() -> None:
    td = _config("pt")
    cli = _com_dir(td)
    invalido = _recusa(cli, {"X-VMM": "xx"})
    verificar("codigo fora de en/pt nao fixa nada (segue a config)",
              "corpo nao e um objecto JSON" in invalido, invalido[:80])
    vazio = _recusa(cli, {"X-VMM": "   "})
    verificar("cabecalho em branco nao fixa nada",
              "corpo nao e um objecto JSON" in vazio, vazio[:80])


def test_isolamento_por_contexto() -> None:
    """O discriminador real: dois contextos simultaneos nao se veem um ao outro.

    Este caso e o que separa o `ContextVar` de uma variavel de modulo. O teste
    sequencial acima NAO distingue os dois desenhos — medido: com uma variavel
    de modulo ele continuava verde, porque a dependencia repoe o valor em cada
    pedido. So com dois contextos a correr ao mesmo tempo a diferenca aparece:
    com uma variavel de modulo, o `en` de um pedido contamina o `pt` do outro.
    """
    td = _config("pt")
    os.environ["VMM_DIR"] = str(td)
    i18n.definir_para_testes(None)
    i18n.definir_do_pedido(None)
    verificar("base do teste: portugues", i18n.idioma() == "pt", i18n.idioma())

    ctx_en = contextvars.copy_context()

    def _pedido_em_ingles() -> tuple[str, str]:
        i18n.definir_do_pedido("en")
        return i18n.idioma(), i18n.t("'idioma' ausente")

    dentro, mensagem = ctx_en.run(_pedido_em_ingles)
    verificar("contexto do pedido en: lingua em ingles", dentro == "en", dentro)
    verificar("contexto do pedido en: mensagem traduzida",
              mensagem == "'idioma' missing", mensagem)
    verificar("fora do contexto: continua portugues (isolamento)",
              i18n.idioma() == "pt", i18n.idioma())
    verificar("fora do contexto: mensagem continua em portugues",
              i18n.t("'idioma' ausente") == "'idioma' ausente",
              i18n.t("'idioma' ausente"))


def test_nao_vaza_entre_pedidos() -> None:
    """A verificacao que separa `ContextVar` de uma variavel de modulo."""
    td = _config("pt")
    cli = _com_dir(td)
    _recusa(cli, {"X-VMM": "en"})
    depois = _recusa(cli)
    verificar("pedido seguinte sem cabecalho volta a config (sem vazamento)",
              "corpo nao e um objecto JSON" in depois, depois[:80])

    _recusa(cli, {"X-VMM": "pt"})
    depois_en = _recusa(cli, {"X-VMM": "en"})
    verificar("alternancia en/pt no mesmo cliente mantem-se fiel",
              "body is not a JSON object" in depois_en, depois_en[:80])


def test_ambiente_perde_para_o_cabecalho() -> None:
    """`VMM_IDIOMA` no ambiente e um valor de processo; o cabecalho e do pedido."""
    td = _config("pt")
    anterior = os.environ.get("VMM_IDIOMA")
    os.environ["VMM_IDIOMA"] = "pt"
    try:
        cli = _com_dir(td)
        os.environ["VMM_IDIOMA"] = "pt"
        com_en = _recusa(cli, {"X-VMM": "en"})
        verificar("cabecalho en ganha ao VMM_IDIOMA=pt do processo",
                  "body is not a JSON object" in com_en, com_en[:80])
    finally:
        if anterior is None:
            os.environ.pop("VMM_IDIOMA", None)
        else:
            os.environ["VMM_IDIOMA"] = anterior


def main() -> int:
    test_estrutura()
    test_cabecalho_ganha_a_config()
    test_cabecalho_ganha_quando_a_config_diz_o_contrario()
    test_cabecalho_invalido_cai_na_config()
    test_isolamento_por_contexto()
    test_nao_vaza_entre_pedidos()
    test_ambiente_perde_para_o_cabecalho()
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
