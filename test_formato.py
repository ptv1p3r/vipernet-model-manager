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
import sys
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
    verificar("pt milhar usa ESPACO (U+00A0) e virgula decimal",
              formato.numero(1234.5, 1, "pt") == f"1{NBSP}234,5",
              repr(formato.numero(1234.5, 1, "pt")))
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
              formato.numero(-1234.5, 2, "pt") == f"-1{NBSP}234,50",
              repr(formato.numero(-1234.5, 2, "pt")))
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
              formato.moeda(1234.5, "EUR", 2, "pt") == f"1{NBSP}234,50{NBSP}\u20ac",
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
    verificar("e o numero acompanha", formato.numero(1234.5, 1) == f"1{NBSP}234,5",
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


def test_nada_formata_fora_do_modulo() -> None:
    moeda_literal: list[str] = []
    float_a_mao: list[str] = []
    strftime_fora: list[str] = []
    for caminho, rel in _fontes():
        if rel == "formato.py":
            continue
        # Este ficheiro e quem declara os valores esperados: os simbolos de moeda
        # aparecem aqui como comparacao, nao como formatacao. Os restantes
        # `test_*.py` fixam texto e nao apresentam nada a ninguem.
        if Path(rel).name.startswith("test_"):
            continue
        texto = caminho.read_text(encoding="utf-8")
        try:
            arvore = ast.parse(texto)
        except SyntaxError:
            continue
        for no in ast.walk(arvore):
            # Simbolo de moeda dentro de texto: a moeda passou a ser valor, e um
            # simbolo escrito a mao no molde voltaria a fixa-la na lingua errada.
            if isinstance(no, ast.Constant) and isinstance(no.value, str):
                v = no.value
                if "\u20ac" in v or "\u00a3" in v:
                    moeda_literal.append(f"{rel}:{no.lineno}")
            # `"%.2f" % x` — o `parent` nao existe no ast, por isso o teste e
            # feito sobre o BinOp, e nao sobre a constante: a versao anterior
            # desta verificacao passava sempre sem olhar para nada.
            if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Mod) \
                    and isinstance(no.left, ast.Constant) and isinstance(no.left.value, str):
                if any(t in no.left.value for t in ("%.1f", "%.2f", "%.3f", "%.4f", "%.6f")):
                    float_a_mao.append(f"{rel}:{no.lineno}")
            if isinstance(no, ast.Attribute) and no.attr == "strftime":
                if rel not in STRFTIME_PERMITIDO:
                    strftime_fora.append(f"{rel}:{no.lineno}")
    verificar("nenhum simbolo de moeda em texto fora do formato.py",
              not moeda_literal, ", ".join(moeda_literal))
    verificar("nenhum %-format de decimal fora do formato.py",
              not float_a_mao, ", ".join(float_a_mao))
    verificar("strftime so nos ficheiros declarados",
              not strftime_fora, ", ".join(strftime_fora))


def main() -> int:
    test_numero()
    test_neutro()
    test_percentagem()
    test_moeda()
    test_datas()
    test_locale()
    test_nada_formata_fora_do_modulo()

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
