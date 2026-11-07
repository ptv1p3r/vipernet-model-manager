"""Efeito por chamada de ferramenta, lido do `exit_code` que ja existe.

Porque este modulo existe, e o que **nao** faz
----------------------------------------------
O core tem a coluna `effect_disposition` em `messages` — e preenche-a so nos
caminhos de erro: `none` quando a chamada foi bloqueada, `unknown` quando
expirou. No sucesso fica nula. Medido a 2026-11-05: 55 de 84 120 mensagens de
ferramenta no perfil `default` (0,065%), 0 de 2 768 em `pessoal`, 4 de 18 218
em `trabalho` — e as 59 sao `unknown`/`none`, nenhuma e um sucesso.

Escrever nessa coluna a partir de um plugin seria escrever na tabela de um
modulo do core, que a regra 10 do catalogo recusa. Este modulo nao toca no
core: le o resultado que o hook publico `post_tool_call` ja entrega e deixa o
efeito no ledger do plugin. E a mesma informacao, noutra casa, sem costura
privada.

Porque a leitura e estrutural e nao lexical
-------------------------------------------
Uma primeira tentativa de contar falhas por palavras no output das ferramentas
deu 64,8% de «erro», com o `terminal` a 81% — porque as marcas casavam com
numeros e JSONs escritos no proprio output. Correspondencia lexical em output
de ferramenta nao serve. Aqui o `exit_code` so conta quando e **chave de
topo** de um objecto JSON: o conteudo de um `read_file` pode conter a string
`"exit_code"` mil vezes e nunca e lido, porque a chave de topo desse resultado
e `content`.

Limites
-------
`LIMITE_CHARS` evita pagar `json.loads` num resultado gigante (o maior do
ledger passa dos 200 KB); acima do limite a chamada conta como nao medida, e
nao como falha — um resultado grande nao e um resultado mau. Os prefixos de
erro do core (`Error executing tool '...'`) sao reconhecidos sem parse, porque
nao sao JSON.
"""

from __future__ import annotations

import json
from typing import Any

LIMITE_CHARS = 200_000

# O core sintetiza este texto quando a chamada expira, e um bloqueio de plugin
# devolve JSON com `error` — os dois sao falha sem `exit_code`.
_PREFIXO_ERRO_CORE = "Error executing tool '"
_PREFIXO_CANCELADO = "[Tool execution cancelled"

EFEITO_OK = "ok"
EFEITO_FALHA = "falha"
EFEITO_DESCONHECIDO = "desconhecido"


def _texto(resultado: Any) -> str | None:
    if isinstance(resultado, str):
        return resultado
    if isinstance(resultado, (bytes, bytearray)):
        try:
            return resultado.decode("utf-8", "replace")
        except Exception:
            return None
    return None


def classificar(resultado: Any) -> dict[str, Any] | None:
    """Devolve `{"exit_code": int|None, "efeito": str}` ou `None` se nao medivel.

    `None` e uma resposta legitima e frequente: significa «esta chamada nao
    declara efeito» — uma leitura de ficheiro, uma pesquisa. Gravar `None` como
    falha encheria o ledger de falsos negativos.
    """
    texto = _texto(resultado)
    if texto is None:
        return None

    despido = texto.lstrip()
    if despido.startswith(_PREFIXO_ERRO_CORE) or despido.startswith(_PREFIXO_CANCELADO):
        return {"exit_code": None, "efeito": EFEITO_FALHA}

    if not despido.startswith("{"):
        return None
    if len(despido) > LIMITE_CHARS:
        return None

    try:
        dados = json.loads(despido)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    if "exit_code" not in dados:
        return None

    valor = dados.get("exit_code")
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return {"exit_code": valor, "efeito": EFEITO_OK if valor == 0 else EFEITO_FALHA}
    if isinstance(valor, str):
        limpo = valor.strip()
        try:
            numero = int(limpo)
        except Exception:
            return {"exit_code": None, "efeito": EFEITO_DESCONHECIDO}
        return {"exit_code": numero, "efeito": EFEITO_OK if numero == 0 else EFEITO_FALHA}
    if valor is None:
        return {"exit_code": None, "efeito": EFEITO_DESCONHECIDO}
    return None


def efeito_do_status(status: Any) -> dict[str, Any] | None:
    """Só o erro se lê do `status`; o sucesso não.

    Um `status` de sucesso diz que a ferramenta correu, não que produziu o
    efeito. Presumir `ok` a partir dele gravaria `ok` para todas as chamadas do
    turno — um `read_file` passaria a «sucesso» e o sinal morria por diluição
    (medido no ensaio: 1 linha `read_file -> ok, exit_code None` antes desta
    correcção). O erro, esse, é afirmado pelo core quando sintetiza
    `Error executing tool` ou expira a chamada, e é uma medida e não uma
    presunção.
    """
    if not isinstance(status, str) or not status:
        return None
    if status in ("error", "timeout", "blocked", "cancelled", "failed"):
        return {"exit_code": None, "efeito": EFEITO_FALHA}
    return None
