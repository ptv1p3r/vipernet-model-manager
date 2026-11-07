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


def _chaves_do_codigo() -> set[str]:
    """Todas as chaves de traducao presentes no codigo, lidas por AST.

    O AST, e nao regex, porque o Python junta literais adjacentes numa unica
    `Constant`: uma ajuda partida em tres linhas conta como uma chave so, que e
    exactamente o que o dicionario tem de cobrir. Apanha as duas formas em uso —
    `t("...")` na API, `_i18n.t("...", nome=...)` no CLI e `_t("...")` no gestor — e
    as `help="..."` (que so se traduzem por `_traduzir_ajuda`, mas contam para a
    cobertura).
    """
    import ast

    raiz = Path(__file__).resolve().parent
    chaves: set[str] = set()

    def _e_chamada_t(no: ast.Call) -> bool:
        f = no.func
        if isinstance(f, ast.Name):
            return f.id in ("t", "_t")
        if isinstance(f, ast.Attribute):
            return f.attr == "t"
        return False

    for rel in ("dashboard/api.py", "cli.py", "gestor.py"):
        arvore = ast.parse((raiz / rel).read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.Call) and _e_chamada_t(no) and no.args:
                arg = no.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    chaves.add(arg.value)
            elif isinstance(no, ast.keyword) and no.arg == "help":
                valor = no.value
                if isinstance(valor, ast.Constant) and isinstance(valor.value, str):
                    chaves.add(valor.value)
    return chaves


def test_dicionario_cobre_o_codigo() -> None:
    """Nenhuma chave usada no codigo pode faltar no dicionario.

    Sem esta prova, `en` devolve a chave e a sessao inglesa le portugues — o
    defeito que a camada i18n existe para impedir.
    """
    import i18n

    usadas = _chaves_do_codigo()
    dic = i18n.todas_as_chaves()
    faltam = sorted(k for k in usadas if k not in dic)
    verificar("o dicionario cobre todas as chaves do codigo",
              not faltam,
              f"{len(usadas)} usadas, faltam {len(faltam)}: {faltam[:3]}")
    verificar("ha chaves suficientes para valer a pena",
              len(usadas) > 90, f"usadas={len(usadas)}")


def test_pt_devolve_a_chave_intacta() -> None:
    """Em `pt` a traducao e a propria chave: o historico fica byte a byte."""
    import i18n

    amostras = [
        ("'activo' tem de ser booleano", {}),
        ("perfil {nome!r} nao existe", {"nome": "pessoal"}),
        ("modelo {id!r} nao existe no catalogo de {perfil!r}",
         {"id": "x", "perfil": "default"}),
    ]
    i18n.definir_para_testes("pt")
    for chave, vals in amostras:
        esperado = chave.format(**vals) if vals else chave
        verificar(f"pt mantem a frase {chave[:28]!r}", i18n.t(chave, **vals) == esperado,
                  i18n.t(chave, **vals))


def test_en_traduz_sem_faltas() -> None:
    """Em `en` todas as chaves do codigo dao ingles, e nenhuma falta."""
    import i18n

    usadas = _chaves_do_codigo()
    dic = i18n.todas_as_chaves()
    i18n.definir_para_testes("en")
    i18n.MISSES.clear()
    iguais = []
    for chave in sorted(usadas & dic):
        saida = i18n.t(chave)
        if saida == chave and chave not in _IDENTICAS_DE_PROPOSITO:
            iguais.append(chave)
    verificar("en nao registou faltas", not i18n.MISSES, f"{sorted(i18n.MISSES)[:3]}")
    verificar("en traduz de facto (nao devolve a chave portuguesa)", not iguais,
              f"{len(iguais)} iguais: {iguais[:3]}")


def test_marcadores_sobrevivem_a_traducao() -> None:
    """Uma traducao que perca um marcador nao pode rebentar o comando."""
    import i18n

    chave = "gravacao nao confirmada: pedido {pedido}, lido {lido}"
    i18n.definir_para_testes("en")
    saida = i18n.t(chave, pedido=1, lido=2)
    verificar("en formata os marcadores", "1" in saida and "2" in saida, saida)
    verificar("en usa o texto ingles", "requested" in saida or "read back" in saida, saida)

    i18n.definir_para_testes("pt")
    verificar("pt formata os marcadores",
              i18n.t(chave, pedido=1, lido=2) == "gravacao nao confirmada: pedido 1, lido 2")


def test_ingles_e_o_primeiro_e_o_padrao() -> None:
    """`en` e a primeira lingua e o padrao de fabrica; `pt` e a alternativa."""
    import i18n

    verificar("en e a primeira lingua", i18n.IDIOMAS[0] == "en", str(i18n.IDIOMAS))
    verificar("o padrao e en", i18n.IDIOMA_PADRAO == "en")
    verificar("pt-PT e reconhecido", i18n.normalizar("pt-PT") == "pt")
    verificar("en-GB e reconhecido", i18n.normalizar("en-GB") == "en")
    verificar("codigo desconhecido cai em en", i18n.normalizar("xx") == "en")


# Chaves cujo ingles coincide com o portugues: sao nomes de valores do proprio
# dominio (`leve/media/alta`, `herdar`, `rodizio`, `baixo|medio|alto`), que nao
# se traduzem sob pena de deixarem de corresponder ao que a config aceita.
_IDENTICAS_DE_PROPOSITO = {
    "entrada:<id> | nivel:<leve|media|alta> | provider/modelo",
    "cron, api_server, cli, whatsapp...",
    "modelo que classifica leve/media/alta",
    "leve,media,alta (lista separada por virgulas)",
    "quem afirma: gate, releitura, testes, ha, jira, wiki, git, skill, manual",
    # Molde de embrulho: so identificadores e um `{p3}` que ja vem traduzido de
    # dentro. Verter os separadores mudaria a leitura sem mudar o sentido.
    "catalogo[{p0}] ({p1}:{p2}) {p3}",
    # Saida do CLI: rotulos que sao a mesma palavra nas duas linguas ("global",
    # "config", "ledger", "vmm", "MB", setas). Traduzir seria inventar diferenca.
    "\nglobal: {v0} · {v1}/{v2}",
    "  {v0:.2f} MB -> {v1:.2f} MB ({v2:.1f}%)",
    "config:  {v0}",
    "config: {v0}",
    "global: {v0} / {v1} ({v2})",
    "ledger {v0}",
    "vmm: {v0}",
    # Palavras que sao a mesma coisa nas duas linguas: "global" escreve-se igual,
    # e "provider" e o termo tecnico que o plugin nunca verte (nem nos textos).
    "global",
    "provider",
}


def test_validador_fala_a_lingua_activa() -> None:
    """A ligacao, e nao o dicionario: a recusa real sai na lingua escolhida.

    `test_en_traduz_sem_faltas` prova que ha ingles para as chaves que o codigo
    usa, mas nao que a mensagem do validador passe por la. Sem este teste, as 75
    mensagens podiam estar traduzidas no dicionario e continuar a sair em
    portugues na sessao inglesa — que era exactamente o defeito.
    """
    import i18n

    casos = [
        (
            {"versao": 9},
            "config.versao 9 nao suportada (este plugin le a versao 3)",
            "config.versao 9 not supported (this plugin reads version 3)",
        ),
        (
            {"idioma": "xx"},
            "idioma invalido: 'xx' (aceita: en, pt)",
            "invalid idioma: 'xx' (accepts: en, pt)",
        ),
        (
            {"sombra": []},
            "sombra nao e um objecto",
            "sombra is not an object",
        ),
        (
            {"perfis": {"default": {"catalogo": [], "analise": {"circuit_breaker": {"limite_falhas": 999}}}}},
            "perfis.default.analise.circuit_breaker.limite_falhas fora de 1..50 (veio 999)",
            "perfis.default.analise.circuit_breaker.limite_falhas out of range 1..50 (got 999)",
        ),
    ]
    for cfg, esperado_pt, esperado_en in casos:
        for codigo, esperado in (("pt", esperado_pt), ("en", esperado_en)):
            i18n.definir_para_testes(codigo)
            _, erros = gestor.validar(copy.deepcopy(cfg))
            verificar(
                f"validador em {codigo} diz {esperado[:34]!r}",
                esperado in erros,
                f"erros={erros[:2]}",
            )
    i18n.definir_para_testes(None)


def main() -> int:
    test_normalizar()
    test_efectivo()
    test_validar()
    test_dicionario_cobre_o_codigo()
    test_pt_devolve_a_chave_intacta()
    test_en_traduz_sem_faltas()
    test_marcadores_sobrevivem_a_traducao()
    test_validador_fala_a_lingua_activa()
    test_ingles_e_o_primeiro_e_o_padrao()
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
