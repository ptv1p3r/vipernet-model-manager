"""Testes do idioma do painel: lista fechada, heranca e escrita por chave.

O que este ficheiro existe para impedir:

1. Um codigo de lingua invalido gravado em silencio. O painel ficava em ingles
   e o utilizador julgava que tinha escolhido portugues — o pior defeito desta
   classe, porque nao ha erro nenhum no ecra.
2. O seletor a escrever a chave e a leitura a deita-la fora. A chave `idioma`
   tem de sobreviver ao `validar()`: e o teste que apanha o esquecimento de a
   acrescentar a `_CHAVES_CONFIG` ou ao `saida`.
3. A ausencia da chave a ser gravada como valor. Ausente tem de continuar
   ausente (= padrao, ingles), senao todo `gravar` do painel escrevia a lingua
   por cima da escolha de outra sessao.

Corre contra um config temporario: nao toca na config viva.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import gestor  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "vmm_api_idioma", RAIZ / "dashboard" / "api.py")
assert _spec is not None and _spec.loader is not None
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

OK = 0
FALHAS: list[str] = []


def verificar(nome: str, condicao: bool, detalhe: str = "") -> None:
    global OK
    if condicao:
        OK += 1
    else:
        FALHAS.append(nome + ((" — " + detalhe) if detalhe else ""))


# --------------------------------------------------------------------- puros


def test_normalizar() -> None:
    base, erros = gestor.normalizar_idioma("en")
    verificar("normalizar: en aceite", base == "en" and not erros, str((base, erros)))
    base, erros = gestor.normalizar_idioma("pt")
    verificar("normalizar: pt aceite", base == "pt" and not erros, str((base, erros)))
    base, erros = gestor.normalizar_idioma("PT")
    verificar("normalizar: maiusculas reduzidas", base == "pt", str((base, erros)))
    base, erros = gestor.normalizar_idioma("pt-PT")
    verificar("normalizar: forma regional pt-PT reduzida", base == "pt", str((base, erros)))
    base, erros = gestor.normalizar_idioma("pt_BR")
    verificar("normalizar: forma regional com underscore reduzida", base == "pt", str((base, erros)))
    base, erros = gestor.normalizar_idioma("en-US")
    verificar("normalizar: en-US reduzido", base == "en", str((base, erros)))
    base, erros = gestor.normalizar_idioma(" pt ")
    verificar("normalizar: espacos ignorados", base == "pt", str((base, erros)))

    # Ausente nao e erro: e o caso normal de quem nunca escolheu.
    for vazio in (None, ""):
        base, erros = gestor.normalizar_idioma(vazio)
        verificar(f"normalizar: {vazio!r} nao e erro", base is None and not erros,
                  str((base, erros)))

    # Fora da lista e erro, nao um silencioso "volta ao padrao".
    for mau in ("fr", "es-ES", "english", "ptx"):
        base, erros = gestor.normalizar_idioma(mau)
        verificar(f"normalizar: {mau!r} recusado", base is None and len(erros) == 1,
                  str((base, erros)))
        verificar(f"normalizar: {mau!r} diz o que aceita",
                  any("en" in e and "pt" in e for e in erros), str(erros))
    base, erros = gestor.normalizar_idioma(123)
    verificar("normalizar: numero recusado", base is None and len(erros) == 1,
              str((base, erros)))


def test_efectivo() -> None:
    verificar("efectivo: sem chave vale o ingles (padrao)",
              gestor.idioma_efectivo({}) == "en")
    verificar("efectivo: pt declarado vale pt",
              gestor.idioma_efectivo({"idioma": "pt"}) == "pt")
    verificar("efectivo: codigo invalido no ficheiro cai no padrao",
              gestor.idioma_efectivo({"idioma": "klingon"}) == "en")
    verificar("efectivo: config ausente nao levanta (None)",
              gestor.idioma_efectivo(None) == "en")
    verificar("efectivo: config de outro tipo nao levanta",
              gestor.idioma_efectivo("nao-e-dict") == "en")


def test_validar() -> None:
    # Declarado: sobrevive ao validar — se a chave nao estiver em
    # `_CHAVES_CONFIG` e no `saida`, isto falha e o seletor do painel nao grava.
    base = {"perfis": {"default": {"catalogo": []}}}
    limpo, erros = gestor.validar({**base, "idioma": "pt"})
    verificar("validar: pt sobrevive", not erros and limpo.get("idioma") == "pt",
              str((limpo.get("idioma"), erros)))
    limpo, erros = gestor.validar({**base, "idioma": "pt-PT"})
    verificar("validar: forma regional e normalizada ao gravar",
              not erros and limpo.get("idioma") == "pt",
              str((limpo.get("idioma"), erros)))

    # Ausente continua ausente: nao e preenchido com o padrao.
    limpo, erros = gestor.validar(dict(base))
    verificar("validar: ausente nao e escrito", "idioma" not in limpo,
              str(list(limpo.keys())))
    verificar("validar: ausente nao e erro", not erros, str(erros))

    limpo, erros = gestor.validar({**base, "idioma": "fr"})
    verificar("validar: invalido e erro", bool(erros), str(erros))
    verificar("validar: invalido nao fica gravado", "idioma" not in limpo,
              str(limpo.get("idioma")))

    limpo, erros = gestor.validar({**base, "idioma": None})
    verificar("validar: null vale ausente", not erros and "idioma" not in limpo,
              str((limpo.get("idioma"), erros)))


def test_estado_inclui_idioma(tmp: Path) -> None:
    original_fc = gestor.ficheiro_config
    original_dir = gestor.directorio
    gestor.ficheiro_config = lambda: tmp  # type: ignore[assignment]
    gestor.directorio = lambda: tmp.parent  # type: ignore[assignment]
    try:
        cliente = _app()
        base = "/api/plugins/vipernet-model-manager"

        r = cliente.get(base + "/estado")
        verificar("estado: 200", r.status_code == 200, str(r.status_code))
        corpo = r.json()
        verificar("estado: traz idioma", "idioma" in corpo, str(list(corpo.keys())))
        verificar("estado: sem chave o painel abre em ingles",
                  corpo.get("idioma") == "en", str(corpo.get("idioma")))

        # Gravar a lingua: 200 e releitura independente do disco.
        r = cliente.post(base + "/idioma", json={"idioma": "pt"})
        verificar("POST idioma: 200", r.status_code == 200,
                  str((r.status_code, r.text[:200])))
        verificar("POST idioma: resposta diz pt", r.json().get("idioma") == "pt",
                  r.text[:200])
        no_disco = json.loads(tmp.read_text(encoding="utf-8"))
        verificar("POST idioma: ficou no ficheiro", no_disco.get("idioma") == "pt",
                  str(no_disco.get("idioma")))
        r = cliente.get(base + "/estado")
        verificar("POST idioma: o estado seguinte ja vem em pt",
                  r.json().get("idioma") == "pt", str(r.json().get("idioma")))

        # Forma regional aceite no endpoint, normalizada.
        r = cliente.post(base + "/idioma", json={"idioma": "PT-PT"})
        verificar("POST idioma: forma regional aceite", r.status_code == 200,
                  str((r.status_code, r.text[:200])))
        verificar("POST idioma: forma regional normalizada no disco",
                  json.loads(tmp.read_text(encoding="utf-8")).get("idioma") == "pt")

        # Invalido: 400 e NADA muda no ficheiro.
        antes = tmp.read_bytes()
        r = cliente.post(base + "/idioma", json={"idioma": "fr"})
        verificar("POST idioma: invalido da 400", r.status_code == 400,
                  str((r.status_code, r.text[:200])))
        verificar("POST idioma: invalido nao escreve", tmp.read_bytes() == antes)
        r = cliente.post(base + "/idioma", json={})
        verificar("POST idioma: sem 'idioma' da 400", r.status_code == 400,
                  str((r.status_code, r.text[:200])))

        # Escrita por chave: o resto do config fica intacto.
        antes_dict = json.loads(antes.decode("utf-8"))
        verificar("POST idioma: nao mexe nas outras chaves",
                  {k: v for k, v in json.loads(tmp.read_text(encoding="utf-8")).items()
                   if k != "idioma"} == {k: v for k, v in antes_dict.items()
                                        if k != "idioma"})
    finally:
        gestor.ficheiro_config = original_fc  # type: ignore[assignment]
        gestor.directorio = original_dir  # type: ignore[assignment]


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/vipernet-model-manager")
    return TestClient(app)


def test_rev_antigo_nao_impede_mudar_a_lingua(tmp: Path) -> None:
    """A lingua e uma preferencia: um `rev` gasto nao obriga a repetir o gesto.

    Quem muda a lingua esta a mudar SO a lingua. Recusar por a config ter
    mudado noutra chave obrigava o utilizador a recarregar e tentar outra vez
    por causa de uma alteracao que nao tem nada a ver com o que ele pediu.
    """
    original_fc = gestor.ficheiro_config
    original_dir = gestor.directorio
    gestor.ficheiro_config = lambda: tmp  # type: ignore[assignment]
    gestor.directorio = lambda: tmp.parent  # type: ignore[assignment]
    try:
        cliente = _app()
        base = "/api/plugins/vipernet-model-manager"
        r = cliente.post(base + "/idioma", json={"idioma": "en", "rev": "0000000000000000"})
        verificar("rev gasto: a lingua muda na mesma", r.status_code == 200,
                  str((r.status_code, r.text[:200])))
        verificar("rev gasto: disco confirma en",
                  json.loads(tmp.read_text(encoding="utf-8")).get("idioma") == "en")
    finally:
        gestor.ficheiro_config = original_fc  # type: ignore[assignment]
        gestor.directorio = original_dir  # type: ignore[assignment]


def main() -> int:
    test_normalizar()
    test_efectivo()
    test_validar()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "config.json"
        # Config minima mas valida, para o teste nao depender da viva.
        limpo, erros = gestor.validar({"perfis": {"default": {"catalogo": []}}})
        assert not erros, erros
        tmp.write_text(json.dumps(limpo, ensure_ascii=False, indent=1), encoding="utf-8")
        test_estado_inclui_idioma(copy.deepcopy(tmp))
        # Repoe a base entre os dois testes de endpoint.
        tmp.write_text(json.dumps(limpo, ensure_ascii=False, indent=1), encoding="utf-8")
        test_rev_antigo_nao_impede_mudar_a_lingua(copy.deepcopy(tmp))

    print("")
    if FALHAS:
        print(f"{len(FALHAS)} de {OK + len(FALHAS)} verificacoes FALHARAM")
        for f in FALHAS:
            print("  FALHA " + f)
        return 1
    print(f"{OK}/{OK} verificacoes OK")
    print("VEREDITO: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
