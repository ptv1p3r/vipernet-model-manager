"""Formato de valores por locale — a unica casa onde se formata.

A regra que este modulo existe para cumprir e uma so: **o valor e formatado na
apresentacao, nunca no armazenamento**. Um carimbo ISO no ledger ou um numero com
ponto decimal numa mensagem de diagnostico sao dados, e dados nao tem lingua; se
o formato depender de quem escreveu, a mesma linha le-se de maneira diferente
conforme a sessao que a produziu e deixa de se poder comparar. Quem apresenta
chama estas funcoes e passa o locale.

Consequencias, que sao desenho e nao efeito secundario:

* o locale e **argumento explicito** (`locale=`), resolvido por omissao a partir
  da mesma chave `idioma` que serve o painel, a API e o CLI — ha uma so fonte de
  locale, e o `i18n` e essa fonte. Nao se infere do browser nem do sistema;
* nao ha dependencias: para dois locales, tabelas deterministicas chegam e
  testam-se linha a linha. Uma biblioteca acrescentada teria de resolver sob as
  restricoes do core do Hermes, e nao se paga por isso;
* `neutro()` existe para o caso oposto — escrever um numero que fica gravado
  (ledger, diagnostico) com ponto decimal e sem agrupamento, para a prova
  armazenada ser estavel e legivel por maquina;
* nenhuma funcao devolve texto traduzido: devolve o **valor** ja formatado. A
  frase vem do `i18n` e o valor entra por marcador, porque concatenar fragmentos
  parte a ordem das palavras quando muda a lingua.

Separadores, medidos e nao escolhidos a gosto: em `pt-PT` o CLDR usa ESPACO
(U+00A0) como separador de milhares e virgula decimal — `1 234,56`. O ponto de
milhares e convencao do portugues do Brasil e nao se aplica aqui. `en` usa
virgula de milhares e ponto decimal — `1,234.56`. Os dois vivem em constantes:
mudar de ideia e uma linha, e o teste fixa o code point para nao haver duvida
sobre um caracter invisivel.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

#: O fuso desta instalacao. Explicito, nunca o do processo: a API do HA devolve
#: UTC e uma data apresentada em UTC ao dono e uma data errada.
FUSO = ZoneInfo("Europe/Lisbon")

#: Separador de milhares. `pt`: U+00A0 (CLDR pt-PT). `en`: virgula.
SEP_MILHARES = {"pt": "\u00a0", "en": ","}
#: Separador decimal.
SEP_DECIMAL = {"pt": ",", "en": "."}
#: Simbolo por moeda. `None` significa "sem simbolo conhecido": usa-se o codigo.
SIMBOLO_MOEDA = {"EUR": "\u20ac", "USD": "$", "GBP": "\u00a3"}
#: Em `pt` o simbolo vai depois do valor; em `en` antes.
MOEDA_DEPOIS = {"pt": True, "en": False}
#: Simbolo de percentagem (sufixo nas duas linguas).
SIMBOLO_PERCENTAGEM = "%"
#: Data curta. Dia-primeiro nas duas linguas, de proposito: o painel fixou
#: `en-GB` (mes-primeiro lia-se como outro dia) e o Python tem de dizer o mesmo
#: que o painel — divergencia de formato entre duas superficies da mesma
#: aplicacao e pior do que uma convencao discutivel.
DATA_CURTA = {"pt": "%d/%m/%Y", "en": "%d/%m/%Y"}
#: Meses por extenso, para a data longa. Indexado de 1 a 12.
MESES_LONGO = {
    "pt": ("janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
           "agosto", "setembro", "outubro", "novembro", "dezembro"),
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
}
#: Relogio de 24 horas nas duas linguas, pela mesma razao do `DATA_CURTA`: e o
#: que o painel faz com `en-GB`, e o Python acompanha.
HORA_12H = {"pt": False, "en": False}

#: Casas decimais por omissao ao formatar dinheiro.
CASAS_MOEDA = 2


def _i18n():
    """A instancia de `i18n` que o plugin tem em memoria.

    Resolve-se por tres vias (e sempre para a **mesma** instancia) porque este
    ficheiro e carregado tanto como parte do pacote do Hermes como por
    `spec_from_file_location` com a directoria fora do `sys.path`. Duas
    instancias davam duas linguas no mesmo processo.
    """
    ja = sys.modules.get("i18n")
    if ja is not None and hasattr(ja, "t") and hasattr(ja, "IDIOMAS"):
        return ja
    try:  # carregado como pacote do Hermes
        import i18n as mod  # type: ignore

        return mod
    except Exception:  # noqa: BLE001 - cai para o carregamento por caminho
        pass
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("i18n", Path(__file__).resolve().parent / "i18n.py")
    mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["i18n"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def locale(codigo: Any = None) -> str:
    """Locale em vigor: o que for passado, senao o da config (`i18n.idioma`)."""
    mod = _i18n()
    if codigo is None:
        return mod.idioma()
    return mod.normalizar(codigo)


def _tabela(escolha: dict[str, Any], codigo: str) -> Any:
    return escolha.get(codigo, escolha.get("en"))


def _agrupar(inteiro: str, sep: str) -> str:
    """Mete o separador de milhares em triplos, da direita para a esquerda."""
    if len(inteiro) <= 3:
        return inteiro
    partes: list[str] = []
    resto = inteiro
    while len(resto) > 3:
        partes.insert(0, resto[-3:])
        resto = resto[:-3]
    partes.insert(0, resto)
    return sep.join(partes)


def numero(valor: Any, casas: int | None = None, locale: Any = None) -> str:
    """Formata um numero no locale pedido.

    `casas=None` significa "as que forem precisas": inteiro sem decimais, float
    sem zeros a direita ate 6 casas. `None` devolve string vazia — uma celula
    vazia nao e um zero, e mostra-las iguais e o defeito classico de uma tabela.
    """
    if valor is None:
        return ""
    codigo = globals()["locale"](locale)
    sep_mil = _tabela(SEP_MILHARES, codigo)
    sep_dec = _tabela(SEP_DECIMAL, codigo)
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if num != num:  # NaN
        return ""

    if casas is None:
        if isinstance(valor, bool):
            num, casas = float(int(num)), 0
        elif float(num).is_integer():
            casas = 0
        else:
            texto = f"{num:.6f}".rstrip("0").rstrip(".")
            casas = len(texto.split(".")[1]) if "." in texto else 0
    texto = f"{abs(num):.{int(casas)}f}"
    if "." in texto:
        inteiro, decimal = texto.split(".", 1)
    else:
        inteiro, decimal = texto, ""
    inteiro = _agrupar(inteiro, sep_mil)
    saida = inteiro + (sep_dec + decimal if decimal else "")
    return ("-" if num < 0 and float(abs(num)) != 0 else "") + saida


def neutro(valor: Any, casas: int | None = None) -> str:
    """Numero para **guardar**, nao para mostrar: ponto decimal, sem agrupamento.

    Existe para o ledger e para o texto de diagnostico. E deliberadamente igual
    nas duas linguas: uma prova que muda de formato conforme quem a escreveu nao
    serve de prova.
    """
    if valor is None:
        return ""
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if num != num:
        return ""
    if casas is None:
        if float(num).is_integer():
            return str(int(num))
        return f"{num:.6f}".rstrip("0").rstrip(".")
    return f"{num:.{int(casas)}f}"


def percentagem(valor: Any, casas: int = 0, locale: Any = None) -> str:
    """Percentagem a partir do valor ja em pontos percentuais (83.4 -> 83,4%)."""
    if valor is None:
        return ""
    return numero(valor, casas, locale) + _tabela({"pt": SIMBOLO_PERCENTAGEM, "en": SIMBOLO_PERCENTAGEM}, globals()["locale"](locale))


def moeda(valor: Any, codigo_moeda: str = "EUR", casas: int | None = CASAS_MOEDA,
          locale: Any = None) -> str:
    """Dinheiro com simbolo, posicionado pela convencao do locale.

    Sem simbolo conhecido para o codigo, devolve o valor seguido do codigo ISO —
    melhor um rotulo explicito do que um simbolo inventado.
    """
    if valor is None:
        return ""
    loc = globals()["locale"](locale)
    texto = numero(abs(float(valor)) if _e_numero(valor) else valor, casas, loc)
    simbolo = SIMBOLO_MOEDA.get((codigo_moeda or "").upper())
    sinal = "-" if _e_numero(valor) and float(valor) < 0 else ""
    if simbolo is None:
        return f"{sinal}{texto} {(codigo_moeda or '').upper()}".strip()
    if _tabela(MOEDA_DEPOIS, loc):
        return f"{sinal}{texto}\u00a0{simbolo}"
    return f"{sinal}{simbolo}{texto}"


def moeda_iso(valor: Any, codigo_moeda: str = "USD", casas: int | None = 6,
              locale: Any = None) -> str:
    """Dinheiro com o codigo ISO a vista — `0,000123 USD` / `USD 0.000123`.

    Usado onde o valor e pequeno e a moeda nao pode ser adivinhada do simbolo
    (custos por chamada com seis casas). O codigo vai no valor, e nao no molde
    traduzido, para a frase nao carregar dado.
    """
    if valor is None:
        return ""
    loc = globals()["locale"](locale)
    texto = numero(abs(float(valor)) if _e_numero(valor) else valor, casas, loc)
    codigo = (codigo_moeda or "").upper()
    sinal = "-" if _e_numero(valor) and float(valor) < 0 else ""
    if _tabela(MOEDA_DEPOIS, loc):
        return f"{sinal}{texto}\u00a0{codigo}".strip()
    return f"{sinal}{codigo}\u00a0{texto}".strip()


def _e_numero(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _para_datetime(valor: Any) -> _dt.datetime | None:
    """Aceita datetime, date, epoch (s/ms) e texto ISO. Devolve no fuso da casa."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, _dt.datetime):
        mom = valor
    elif isinstance(valor, _dt.date):
        return _dt.datetime(valor.year, valor.month, valor.day, tzinfo=FUSO)
    elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
        seg = float(valor)
        if seg > 1e11:  # veio em milissegundos
            seg /= 1000.0
        try:
            mom = _dt.datetime.fromtimestamp(seg, tz=_dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(valor, str):
        texto = valor.strip()
        if texto.endswith(("Z", "z")):
            texto = texto[:-1] + "+00:00"
        try:
            mom = _dt.datetime.fromisoformat(texto)
        except ValueError:
            return None
    else:
        return None
    if mom.tzinfo is None:
        mom = mom.replace(tzinfo=FUSO)
    return mom.astimezone(FUSO)


def data(valor: Any, formato: str = "curta", locale: Any = None) -> str:
    """Data no locale pedido. `formato`: `curta` (07/11/2026) ou `longa`."""
    mom = _para_datetime(valor)
    if mom is None:
        return ""
    loc = globals()["locale"](locale)
    if formato == "longa":
        meses = _tabela(MESES_LONGO, loc)
        nome = meses[mom.month - 1] if isinstance(meses, tuple) else str(mom.month)
        if loc == "pt":
            return f"{mom.day} de {nome} de {mom.year}"
        return f"{mom.day} {nome} {mom.year}"
    return mom.strftime(_tabela(DATA_CURTA, loc))


def hora(valor: Any, locale: Any = None) -> str:
    """Hora no locale pedido: 24h em `pt`, 12h com AM/PM em `en`."""
    mom = _para_datetime(valor)
    if mom is None:
        return ""
    loc = globals()["locale"](locale)
    if _tabela(HORA_12H, loc):
        return mom.strftime("%I:%M %p").lstrip("0")
    return mom.strftime("%H:%M")


def data_hora(valor: Any, locale: Any = None, formato: str = "curta") -> str:
    """Data e hora separadas por espaco, ambas no locale pedido."""
    mom = _para_datetime(valor)
    if mom is None:
        return ""
    return f"{data(mom, formato, locale)} {hora(mom, locale)}"


def milissegundos(valor: Any, locale: Any = None) -> str:
    """Duracao em ms, arredondada a inteiro e agrupada. O texto 'ms' vem do molde."""
    if valor is None:
        return ""
    return numero(round(float(valor)), 0, locale)
