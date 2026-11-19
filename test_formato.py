"""Testes do formato por locale: o valor formata-se na apresentacao, nao no dado.

O que este ficheiro existe para impedir, por ordem de gravidade:

1. Um valor formatado a mao fora do `formato.py`. Foi assim que o custo saiu com
   seis casas e ponto fixo, a confianca sempre com ponto decimal e os milhares
   de tokens sem separador. A verificacao 3 varre a arvore por AST e falha com
   ficheiro e linha — nao ha como "nao reparar".
2. O painel e o CLI a discordarem. O painel fixou `en-GB` (dia primeiro, 24 h) e
   o Python acompanha; um teste cruza os dois sentidos.
3. O diagnostico a mudar de formato com a lingua. O `neutro()` e igual nas duas:
   uma prova que se escreve de maneira diferente conforme quem a escreveu nao
   serve para comparar linhas do ledger.

Corre sem tocar na config: o locale e passado como argumento e, quando vem da
config, e fixado em memoria por `i18n.definir_para_testes`.
"""

from __future__ import annotations

import ast
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import formato  # noqa: E402
import i18n  # noqa: E402

OK = 0
FALHAS: list[str] = []

NBSP = "\u00a0"


def verificar(nome: str, condicao: bool, detalhe: str = "") -> None:
    global OK
    if condicao:
        OK += 1
    else:
        FALHAS.append(nome + ((" — " + detalhe) if detalhe else ""))


# --------------------------------------------------------------- 1. numero
def test_numero() -> None:
    verificar("pt: quatro digitos NAO agrupam (minimumGroupingDigits=2)",
              formato.numero(1234.5, 1, "pt") == "1234,5",
              repr(formato.numero(1234.5, 1, "pt")))
    verificar("pt: cinco digitos agrupam com ESPACO (U+00A0)",
              formato.numero(12345, 0, "pt") == f"12{NBSP}345",
              repr(formato.numero(12345, 0, "pt")))
    verificar("en milhar usa virgula e ponto decimal",
              formato.numero(1234.5, 1, "en") == "1,234.5",
              repr(formato.numero(1234.5, 1, "en")))
    verificar("pt agrupa milhoes duas vezes",
              formato.numero(1234567, 0, "pt") == f"1{NBSP}234{NBSP}567",
              repr(formato.numero(1234567, 0, "pt")))
    verificar("en agrupa milhoes duas vezes",
              formato.numero(1234567, 0, "en") == "1,234,567",
              repr(formato.numero(1234567, 0, "en")))
    verificar("negativo leva o sinal antes do primeiro digito",
              formato.numero(-12345.5, 2, "pt") == f"-12{NBSP}345,50",
              repr(formato.numero(-12345.5, 2, "pt")))
    verificar("zero e zero, nao vazio", formato.numero(0, 0, "pt") == "0")
    verificar("None e celula vazia, nao zero", formato.numero(None, 2, "pt") == "")
    verificar("sem casas fixas: inteiro nao ganha decimais",
              formato.numero(42, None, "pt") == "42")
    verificar("sem casas fixas: float mostra as que tem",
              formato.numero(12.25, None, "pt") == "12,25",
              repr(formato.numero(12.25, None, "pt")))
    verificar("valor nao numerico devolve o texto, nao rebenta",
              formato.numero("lixo", 0, "pt") == "lixo")
    verificar("NaN devolve vazio", formato.numero(float("nan"), 2, "pt") == "")
    # O ponto e o caracter que nao se ve: fixar o code point evita discussao.
    verificar("separador pt e exactamente U+00A0",
              ord(formato.SEP_MILHARES["pt"]) == 0x00A0)


# ------------------------------------------------------------- 2. neutro
def test_neutro() -> None:
    verificar("neutro usa ponto e nao agrupa", formato.neutro(1234.5, 2) == "1234.50",
              repr(formato.neutro(1234.5, 2)))
    verificar("neutro do custo mantem as seis casas",
              formato.neutro(0.000123, 6) == "0.000123")
    verificar("neutro de inteiro nao ganha .0", formato.neutro(7) == "7")
    antes = formato.neutro(1234.5678, 4)
    i18n.definir_para_testes("pt")
    em_pt = formato.neutro(1234.5678, 4)
    i18n.definir_para_testes("en")
    em_en = formato.neutro(1234.5678, 4)
    i18n.definir_para_testes(None)
    verificar("neutro e igual nas duas linguas (prova comparavel)",
              antes == em_pt == em_en == "1234.5678",
              f"{antes!r} / {em_pt!r} / {em_en!r}")


# ---------------------------------------------------------- 3. percentagem
def test_percentagem() -> None:
    verificar("pt: 83.42 -> 83,4%", formato.percentagem(83.42, 1, "pt") == "83,4%",
              repr(formato.percentagem(83.42, 1, "pt")))
    verificar("en: 83.42 -> 83.4%", formato.percentagem(83.42, 1, "en") == "83.4%")
    verificar("sem casas: arredonda a inteiro", formato.percentagem(100.0, 0, "pt") == "100%")
    verificar("None nao vira 0%", formato.percentagem(None) == "")


# -------------------------------------------------------------- 4. moeda
def test_moeda() -> None:
    verificar("pt: simbolo depois do valor",
              formato.moeda(1234.5, "EUR", 2, "pt") == f"1234,50{NBSP}\u20ac",
              repr(formato.moeda(1234.5, "EUR", 2, "pt")))
    verificar("en: simbolo antes do valor",
              formato.moeda(1234.5, "EUR", 2, "en") == "\u20ac1,234.50",
              repr(formato.moeda(1234.5, "EUR", 2, "en")))
    verificar("pt: negativo com sinal antes do valor",
              formato.moeda(-5, "EUR", 2, "pt") == f"-5,00{NBSP}\u20ac",
              repr(formato.moeda(-5, "EUR", 2, "pt")))
    verificar("pt: custo pequeno com ISO a vista",
              formato.moeda_iso(0.000123, "USD", 6, "pt") == f"0,000123{NBSP}USD",
              repr(formato.moeda_iso(0.000123, "USD", 6, "pt")))
    verificar("en: custo pequeno com ISO antes",
              formato.moeda_iso(0.000123, "USD", 6, "en") == f"USD{NBSP}0.000123",
              repr(formato.moeda_iso(0.000123, "USD", 6, "en")))
    verificar("moeda desconhecida usa o codigo, nao inventa simbolo",
              formato.moeda(10, "CHF", 2, "pt").endswith("CHF"))
    verificar("None nao vira 0,00", formato.moeda(None, "EUR") == "")


# --------------------------------------------------------------- 5. datas
def test_datas() -> None:
    iso = "2026-11-07T14:05:00+00:00"
    verificar("pt: data curta dia/mes/ano",
              formato.data(iso, "curta", "pt") == "07/11/2026",
              repr(formato.data(iso, "curta", "pt")))
    verificar("en: data curta dia/mes/ano (en-GB, como o painel)",
              formato.data(iso, "curta", "en") == "07/11/2026",
              repr(formato.data(iso, "curta", "en")))
    verificar("pt: data longa por extenso",
              formato.data(iso, "longa", "pt") == "7 de novembro de 2026",
              repr(formato.data(iso, "longa", "pt")))
    verificar("en: data longa por extenso",
              formato.data(iso, "longa", "en") == "7 November 2026",
              repr(formato.data(iso, "longa", "en")))
    verificar("hora em 24 h nas duas linguas",
              formato.hora(iso, "pt") == "14:05" and formato.hora(iso, "en") == "14:05",
              f"{formato.hora(iso, 'pt')!r} / {formato.hora(iso, 'en')!r}")
    verificar("data_hora junta as duas",
              formato.data_hora(iso, "pt") == "07/11/2026 14:05",
              repr(formato.data_hora(iso, "pt")))
    verificar("texto invalido devolve vazio, nao rebenta", formato.data("lixo") == "")
    verificar("None devolve vazio", formato.hora(None) == "")
    # O fuso tem de ser Europe/Lisbon e nao UTC: o inverno e WET (UTC+0).
    verificar("data de um instante de verao desloca para o fuso da casa",
              formato.hora("2026-07-01T23:30:00+00:00", "pt") == "00:30",
              repr(formato.hora("2026-07-01T23:30:00+00:00", "pt")))


# --------------------------------------------------- 6. locale e coerencia
def test_locale() -> None:
    verificar("locale explicito manda", formato.locale("pt-PT") == "pt")
    verificar("regiao colapsa para a base", formato.locale("pt_BR") == "pt")
    verificar("codigo desconhecido cai no padrao", formato.locale("xx") == "en")
    i18n.definir_para_testes("pt")
    verificar("sem argumento segue o i18n (pt)", formato.locale() == "pt")
    verificar("e o numero acompanha", formato.numero(1234.5, 1) == "1234,5",
              repr(formato.numero(1234.5, 1)))
    i18n.definir_para_testes("en")
    verificar("sem argumento segue o i18n (en)", formato.locale() == "en")
    verificar("e o numero acompanha em en", formato.numero(1234.5, 1) == "1,234.5")
    i18n.definir_para_testes(None)


# ------------------------------- 7. ninguem formata valores fora daqui
#: Ficheiros que ainda podem conter `strftime` — e por que razao. Nao e uma
#: licenca geral: e a lista fechada, e um ficheiro novo aqui faz o teste falhar.
STRFTIME_PERMITIDO = {
    "gestor.py": "carimbos ISO do ledger, semanas de retencao e nomes de backup",
    "dashboard/api.py": "carimbo ISO na API, nao apresentacao",
    "__init__.py": "carimbo ISO do debug do middleware (gravado, nao mostrado)",
}
#: `eval/` e ferramenta de avaliacao, nao superficie do plugin: os relatorios
#: dela nao sao lidos por um utilizador do plugin.
PASTAS_FORA = ("eval",)


def _fontes():
    for caminho in sorted(RAIZ.rglob("*.py")):
        rel = caminho.relative_to(RAIZ)
        if rel.parts[0] in PASTAS_FORA:
            continue
        yield caminho, str(rel)


#: Isencoes declaradas: (ficheiro, tipo) -> razao por escrito.
#:
#: Uma isencao nunca e implicita: o ficheiro continua a ser varrido e o sitio
#: tem de continuar a existir. Se o sitio desaparecer, a isencao fica orfa e o
#: teste falha — uma isencao morta esconde o proximo sitio que aparecer ali.
EXCECOES: dict[tuple[str, str], str] = {
    ("bateria.py", "spec_numerico"):
        "relatorio do proprio harness de testes (alinhamento de colunas); "
        "nao e superficie do utilizador e nao sai do processo",
}

#: Funcoes do painel autorizadas a falar com o `Intl`: sao a fonte unica do
#: locale no browser. Qualquer outra conversao numero->texto em JS e violacao.
#: `fmtPct` e a gemea JS do `formato.percentagem` (valor em pontos percentuais)
#: — o painel tem de mostrar percentagens pela mesma casa que o CLI.
JS_AUXILIARES: frozenset[str] = frozenset({"fmtNum", "fmtPct", "localeHora", "hora"})

TIPOS_NUMERICOS = frozenset("diouxXeEfFgG%")
_RE_PCT_NUM = re.compile(r"%[-+ #0]*[0-9]*(?:\.[0-9]+)?[diouxXeEfFgG]")
_RE_MOLDE = re.compile(r"\{[A-Za-z_]\w*:([^}]*)\}")
_RE_UNIDADE = re.compile(r"^(?:tok|ms|MB|KB|GB|USD|EUR|min|h|s)\b|^%")
_RE_JS_NUM = re.compile(r"\.toFixed\(|\.toPrecision\(|\.toLocaleString\("
                        r"|Intl\.NumberFormat|Intl\.DateTimeFormat")
_RE_JS_CONCAT = re.compile(r"\+\s*[\"']\s*(?:ms|tok|%|MB|GB|KB)\s*[\"']")
_FUNCOES_RE = frozenset({"compile", "match", "search", "fullmatch", "findall",
                         "finditer", "sub", "subn", "split"})


def _spec_e_numerico(spec: str) -> bool:
    """Classifica um format_spec: formata um numero (e nao texto alinhado)?"""
    if not spec:
        return False
    ultimo = spec[-1]
    if ultimo in TIPOS_NUMERICOS:
        return True
    if ultimo in "sc":
        return False
    # Sem caracter de tipo: agrupamento, precisao implicita ou zero a esquerda
    # continuam a ser formatacao numerica; largura sozinha sobre texto nao e.
    return bool(re.search(r"[.,]", spec)) or spec.lstrip("<>^=+-").startswith("0")


def _literais_regex(arvore: ast.AST) -> set[int]:
    """Ids das constantes que sao padroes de regex (nunca texto apresentado).

    Uma ancora `$` e `Sc` em Unicode tal como o euro; sem esta isencao
    estrutural a regra de moeda dava cinco falsos positivos so neste plugin.
    """
    ids: set[int] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        f = no.func
        nome = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if nome not in _FUNCOES_RE:
            continue
        for arg in no.args:
            for sub in ast.walk(arg):
                ids.add(id(sub))
    return ids


def _violacoes(texto: str, rel: str) -> dict[str, list[str]]:
    """A CLASSE de conversoes numero->texto num ficheiro, por tipo.

    Devolve os sitios encontrados, sem julgar isencoes: quem chama decide. E
    esta funcao que os fixtures sinteticos exercitam, para provar que uma forma
    nova — que nao pertence a nenhuma lista catalogada — tambem e apanhada.
    """
    achados: dict[str, list[str]] = {}

    def regista(tipo: str, sitio: str) -> None:
        achados.setdefault(tipo, []).append(sitio)

    if Path(rel).name.startswith("test_") or rel == "formato.py":
        return achados

    try:
        arvore = ast.parse(texto)
    except SyntaxError:
        return achados

    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(arvore)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and n.body and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
        and isinstance(n.body[0].value.value, str)
    }
    regex = _literais_regex(arvore)

    for no in ast.walk(arvore):
        # --- (1) f-string que formata o valor com um especificador -----------
        if isinstance(no, ast.FormattedValue):
            spec = no.format_spec
            if spec is None:
                pass
            else:
                # Em Python o `format_spec` e sempre um `JoinedStr`, mesmo para
                # `:.2f`: desembrulha-se quando e so texto e classifica-se.
                partes = spec.values if isinstance(spec, ast.JoinedStr) else [spec]
                if all(isinstance(p, ast.Constant) for p in partes):
                    literal = "".join(str(p.value) for p in partes)  # type: ignore[union-attr]
                    if _spec_e_numerico(literal):
                        regista("spec_numerico", f"{rel}:{no.lineno} spec={literal!r}")
                else:
                    # Spec montado em tempo de execucao (`{v:{w}.{p}f}`): nao ha
                    # como ler o formato, logo nao pode estar fora do modulo.
                    regista("spec_numerico", f"{rel}:{no.lineno} spec=dinamico")

        # --- (2) %-format com conversao numerica ----------------------------
        if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Mod) \
                and isinstance(no.left, ast.Constant) and isinstance(no.left.value, str):
            for m in _RE_PCT_NUM.finditer(no.left.value):
                regista("pct_numerico", f"{rel}:{no.lineno} {m.group(0)!r}")

        # --- (3) format(x, spec) --------------------------------------------
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) \
                and no.func.id == "format" and len(no.args) >= 2:
            arg = no.args[1]
            if not (isinstance(arg, ast.Constant) and not _spec_e_numerico(str(arg.value))):
                regista("format_builtin", f"{rel}:{no.lineno}")

        # --- (4) datas e horas formatadas a mao -----------------------------
        if isinstance(no, ast.Attribute) and no.attr == "strftime" and rel not in STRFTIME_PERMITIDO:
            regista("strftime", f"{rel}:{no.lineno}")

        if not (isinstance(no, ast.Constant) and isinstance(no.value, str)):
            continue
        if id(no) in docstrings or id(no) in regex:
            continue
        texto_no = no.value

        # --- (5) simbolo monetario em texto, por categoria Unicode ----------
        for ch in texto_no:
            if unicodedata.category(ch) == "Sc":
                regista("moeda", f"{rel}:{no.lineno} U+{ord(ch):04X} {ch!r}")
                break

        # --- (6) molde de traducao que ainda formata o valor ---------------
        for m in _RE_MOLDE.finditer(texto_no):
            if _spec_e_numerico(m.group(1)):
                regista("molde_numerico", f"{rel}:{no.lineno} {m.group(0)!r}")
                break

        # --- (7) codigo ISO colado a frase ---------------------------------
        for cod in ("USD", "EUR"):
            if cod in texto_no and texto_no.strip() != cod:
                regista("codigo_moeda", f"{rel}:{no.lineno}:{cod}")

    # --- (8) valor cru com unidade colada (`f"{n}tok"`) ---------------------
    # Nenhuma das guardas anteriores via esta classe: nao ha especificador, nao
    # ha `%`, nao ha simbolo de moeda. O numero chega ao ecra tal como o Python
    # o converte, e o separador passa a ser o do interpretador.
    for no in ast.walk(arvore):
        if not isinstance(no, ast.JoinedStr):
            continue
        vals = no.values
        for i, v in enumerate(vals):
            if not isinstance(v, ast.FormattedValue):
                continue
            if i + 1 >= len(vals):
                continue
            seguinte = vals[i + 1]
            if not isinstance(seguinte, ast.Constant):
                continue
            seg = str(seguinte.value).lstrip()
            if not _RE_UNIDADE.match(seg):
                continue
            expr = v.value
            ja_formatado = (
                isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                and isinstance(expr.func.value, ast.Name)
                and (expr.func.value.id == "formato" or expr.func.value.id.startswith("_formato"))
            )
            if not ja_formatado:
                regista("unidade_crua", f"{rel}:{v.lineno} + {seg[:10]!r}")
    return achados


def _violacoes_js(texto: str) -> list[str]:
    """Conversao numero->texto no painel, fora dos auxiliares declarados."""
    linhas = texto.splitlines()
    inicio: list[tuple[int, str]] = []
    for i, linha in enumerate(linhas):
        m = re.match(r"\s*function\s+(\w+)\s*\(", linha)
        if m:
            inicio.append((i, m.group(1)))
    fora: list[str] = []
    for i, linha in enumerate(linhas):
        dono = None
        for j, nome in inicio:
            if j <= i:
                dono = nome
            else:
                break
        if _RE_JS_NUM.search(linha) and dono not in JS_AUXILIARES:
            fora.append(f"index.js:{i + 1} dentro de {dono or '<topo>'}")
        if _RE_JS_CONCAT.search(linha):
            fora.append(f"index.js:{i + 1} unidade concatenada")
    return fora


def test_nada_formata_fora_do_modulo() -> None:
    observado: dict[tuple[str, str], int] = {}
    for caminho, rel in _fontes():
        achados = _violacoes(caminho.read_text(encoding="utf-8"), rel)
        for tipo, sitios in achados.items():
            observado[(rel, tipo)] = len(sitios)
            isento = (rel, tipo) in EXCECOES
            verificar(f"{tipo} fora do formato.py em {rel}",
                      isento, f"{len(sitios)} sitio(s): {', '.join(sitios)}")

    js = RAIZ / "dashboard" / "dist" / "index.js"
    if js.exists():
        fora = _violacoes_js(js.read_text(encoding="utf-8"))
        verificar("painel: Intl/toFixed so nos auxiliares declarados",
                  not fora, ", ".join(fora))

    # Prova de vida das isencoes: uma isencao cujo sitio desapareceu esconde o
    # proximo sitio que ali aparecer, porque deixa de haver falha a espera.
    orfas = [f"{r}:{t} ({EXCECOES[(r, t)]})" for (r, t) in EXCECOES if (r, t) not in observado]
    verificar("nenhuma isencao orfa", not orfas, "; ".join(orfas))


#: Formas que NAO pertencem a nenhuma lista negra anterior. Se a guarda voltar
#: a ser uma lista de formas conhecidas, a primeira que aqui nao estiver passa a
#: verde e este teste vermelho — que e o que se quer.
FIXTURES_APANHADOS: dict[str, str] = {
    "f-string com agrupamento": 'x = f"{v:,.0f}"\n',
    "format() builtin": 'x = format(v, ".2f")\n',
    "spec montado em runtime": 'x = f"{v:{larg}.{prec}f}"\n',
    "tipo inteiro com largura": 'x = f"{v:5d}"\n',
    "zero a esquerda": 'x = f"{v:08}"\n',
    "unidade colada ao valor": 'x = f"{v} tok"\n',
    "moeda fora do euro": 'x = f"{v} \u20b9"\n',
    "pct-format inteiro": 'x = "%d itens" % v\n',
    "molde de traducao": 'x = _t("{v0:,.2f} MB", v0=n)\n',
    "strftime": 'x = d.strftime("%d/%m/%Y")\n',
    "spec por nome em format": 'x = "{v:>12,.3f}".format(v=n)\n',
}

#: Formas que tem de continuar limpas: uma guarda que dispara em alinhamento de
#: texto obriga a isencoes em massa e deixa de ser lida.
FIXTURES_LIMPOS: dict[str, str] = {
    "alinhamento de texto": 'x = f"{s:>38s}"\n',
    "largura sem tipo": 'x = f"{s:20}"\n',
    "moeda pelo formatador": 'x = f"{formato.moeda(v)}"\n',
    "ancora de regex": 'p = re.compile(r"^\\d{4}$")\n',
    "docstring": 'def f():\n    """Custo em USD."""\n    return 1\n',
}


def test_guarda_apanha_forma_nova() -> None:
    for nome, fonte in FIXTURES_APANHADOS.items():
        achados = _violacoes(fonte, "fixture_apresentacao.py")
        verificar(f"guarda apanha: {nome}", bool(achados), "passou em claro")
    for nome, fonte in FIXTURES_LIMPOS.items():
        achados = _violacoes(fonte, "fixture_apresentacao.py")
        verificar(f"guarda respeita: {nome}", not achados, f"falso positivo: {achados}")


def test_guarda_js_apanha_forma_nova() -> None:
    for nome, fonte in {
        "toFixed no corpo do painel": "function cartao() {\n  return x.toFixed(2);\n}\n",
        "concatena unidade": "function cartao() {\n  return y + \" ms\";\n}\n",
    }.items():
        verificar(f"painel apanha: {nome}", bool(_violacoes_js(fonte)), "passou em claro")
    limpo = "function fmtNum(v) {\n  return new Intl.NumberFormat(localeHora()).format(v);\n}\n"
    verificar("painel respeita o auxiliar declarado", not _violacoes_js(limpo))



def main() -> int:
    test_numero()
    test_neutro()
    test_percentagem()
    test_moeda()
    test_datas()
    test_locale()
    test_nada_formata_fora_do_modulo()
    test_guarda_apanha_forma_nova()
    test_guarda_js_apanha_forma_nova()

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
