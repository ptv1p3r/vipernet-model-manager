"""Classificacao de complexidade feita por um **modelo**, quando o dono o declara.

O classificador heuristico (`analise.decidir`) e determinista, custa 0,1 ms e
nao sabe nada que nao esteja no seu vocabulario: uma frase com palavras que ele
nao conhece cai em `leve`. Este modulo acrescenta a alternativa: um modelo do
proprio catalogo le o pedido e responde `leve`|`media`|`alta`.

Regras de desenho (o que decide o resultado):

- **Desligado por omissao.** `analise.classificador.activo` tem de ser `true` e
  `provider`/`modelo` tem de estar preenchidos; sem isso devolve `None` e quem
  chama usa a heuristica, exactamente como antes.
- **Global ou por perfil.** A definicao global vive em `analise.classificador`;
  um perfil que declare o seu proprio `analise.classificador` substitui-a por
  inteiro (nao funde). Substituir — e nao fundir — e o que permite ao perfil
  desligar o que a config global liga: basta `{"activo": false}`.
- **Fail-open com rasto.** Qualquer erro (timeout, HTTP, credencial, resposta
  sem nivel) devolve `None` com `erro` preenchido; o turno segue na heuristica.
- **Uma chamada por mensagem de utilizador.** A chave do cache e o digest do
  perfil + provider + modelo + ultima mensagem do utilizador, com TTL. Dentro
  de um turno com ferramentas ha varios `llm_request`; sem cache cada um pagava
  uma chamada de classificacao.
- **Disjuntor.** Tres falhas seguidas no mesmo alvo suspendem-no por
  `_SUSPENSAO_S` segundos: um provider em baixo nao trava cada turno por
  `timeout_s`.
- **O texto nunca e escrito em log.** So vao contagens, tempos e nomes.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

NIVEIS = ("leve", "media", "alta")

PADRAO: dict[str, Any] = {
    "activo": False,
    "provider": "",
    "modelo": "",
    "timeout_s": 8.0,
    # 12 tokens chegavam a um modelo que responde directamente e nao chegavam a
    # um de raciocinio: medido no deepseek-flash, os 12 tokens de saida eram
    # todos `reasoning_tokens` e o `content` vinha vazio. 320 deixa pensar e
    # ainda responder — a uma classificacao de uma palavra e sobra.
    "max_tokens": 320,
    "cache_ttl_s": 600,
    "janela_mensagens": 8,
    "max_chars": 4000,
    "esforco": "",
    # Forma da resposta. `auto` pede `response_format: json_object` e, se o
    # provider recusar (400/422) ou devolver algo que nao e json, repete o
    # pedido sem ele — nao depende de uma lista de providers que envelhece.
    # `on` obriga, `off` nunca pede.
    "formato_json": "auto",
}


def _gestor():
    """`gestor` carregado a pedido: o gestor importa este modulo (limites e
    decisao do classificador), logo a importacao no topo seria um ciclo."""
    import sys as _sys

    modulo = _sys.modules.get("vmm_gestor")
    if modulo is not None:
        return modulo
    if __package__:
        from . import gestor as modulo  # type: ignore[import-not-found]
    else:
        import gestor as modulo  # type: ignore[import-not-found,no-redef]
    return modulo

# Limites duros: um `timeout_s` de 300 na config nao pode parar um turno 5 min.
_TIMEOUT_MIN, _TIMEOUT_MAX = 1.0, 30.0
_MAX_TOKENS_MIN, _MAX_TOKENS_MAX = 2, 1024
# Orcamento por omissao ate 3.5. Ficou gravado em configs antigas e trunca
# modelos de raciocinio (os 12 tokens gastam-se a pensar e o `content` vem
# vazio). 12 nao era alcancavel por nenhuma interface — so como omissao — logo
# um 12 gravado e a omissao antiga, e migra-se para o novo valor.
_MAX_TOKENS_ANTIGO = 12
_FALHAS_PARA_SUSPENDER = 3
_SUSPENSAO_S = 300.0
#: Piso de `max_tokens` quando o classificador declara esforco: o raciocinio sai
#: do mesmo orcamento e 320 tokens podem ser todos gastos a pensar.
_MAX_TOKENS_COM_ESFORCO = 1200

PROMPT = (
    "Classificas a complexidade de um pedido feito a um agente de engenharia.\n"
    "Responde APENAS com um objecto json valido, sem texto fora dele e sem explicar:\n"
    '{"nivel": "<leve|media|alta>"}\n'
    "leve - conversa, duvida simples, listar, confirmar, uma pergunta directa;\n"
    "media - explicar um conceito, comparar duas opcoes, rever algo pequeno, "
    "um comando com contexto;\n"
    "alta - desenhar ou decidir arquitetura, diagnosticar avaria com evidencia, "
    "implementar ou refactorizar codigo, plano com janela e rollback, auditoria."
)

#: Lote: as mesmas regras, N pedidos num so pedido. Medido no mercury-3 a
#: 2026-11-02: 10 pedidos em 2,5 s contra 6,6 s um a um, com 9/10 acertos.
#: Custa ~1,6x os tokens (a resposta leva os indices), logo serve para medir e
#: para lotes grandes — o turno normal classifica um pedido.
PROMPT_LOTE = (
    "Classificas a complexidade de pedidos feitos a um agente de engenharia.\n"
    "Recebes uma lista numerada. Responde APENAS com um objecto json valido:\n"
    '{"niveis": [{"i": 1, "nivel": "leve"}, ...]}\n'
    "Um objecto por pedido, pela ordem recebida, sem texto fora do json e sem explicar.\n"
    "leve - conversa, duvida simples, listar, confirmar, uma pergunta directa;\n"
    "media - explicar um conceito, comparar duas opcoes, rever algo pequeno, "
    "um comando com contexto;\n"
    "alta - desenhar ou decidir arquitetura, diagnosticar avaria com evidencia, "
    "implementar ou refactorizar codigo, plano com janela e rollback, auditoria."
)

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
# Cache negativo: a mesma mensagem gera varios `llm_request` no mesmo turno (um
# por volta de ferramentas). Sem isto, uma falha do modelo repete a chamada 2-3
# vezes — medido a 2026-11-02: 19 falhas em rajadas de 2-3 no mesmo segundo.
_cache_erro: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_ERRO_S = 60.0
_falhas: dict[str, int] = {}
_suspensos: dict[str, float] = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------- config


def _num(valor: Any, minimo: float, maximo: float, padrao: float) -> float:
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return padrao
    if n != n:  # NaN
        return padrao
    return max(minimo, min(maximo, n))


def normalizar(bruto: Any, prefixo: str = "analise.classificador") -> tuple[dict[str, Any], list[str]]:
    """Normaliza e valida o bloco; devolve `(definicao, erros)`. Nunca levanta."""
    erros: list[str] = []
    saida = dict(PADRAO)
    if bruto is None:
        return saida, erros
    if not isinstance(bruto, dict):
        return saida, [f"{prefixo} deve ser um objecto"]
    saida["activo"] = bool(bruto.get("activo", False))
    for chave in ("provider", "modelo"):
        valor = bruto.get(chave)
        saida[chave] = str(valor).strip() if valor is not None else ""
    if saida["activo"] and not (saida["provider"] and saida["modelo"]):
        erros.append(f"{prefixo}: activo exige provider e modelo")
    saida["timeout_s"] = _num(bruto.get("timeout_s"), _TIMEOUT_MIN, _TIMEOUT_MAX, PADRAO["timeout_s"])
    tokens = bruto.get("max_tokens")
    if tokens is not None and _num(tokens, -1, 10**9, -1) == _MAX_TOKENS_ANTIGO:
        tokens = None  # migracao da omissao antiga
    saida["max_tokens"] = int(_num(tokens, _MAX_TOKENS_MIN,
                                   _MAX_TOKENS_MAX, PADRAO["max_tokens"]))
    saida["cache_ttl_s"] = int(_num(bruto.get("cache_ttl_s"), 0, 86400, PADRAO["cache_ttl_s"]))
    saida["janela_mensagens"] = int(_num(bruto.get("janela_mensagens"), 1, 60,
                                         PADRAO["janela_mensagens"]))
    saida["max_chars"] = int(_num(bruto.get("max_chars"), 200, 40000, PADRAO["max_chars"]))
    # Esforco do modelo classificador. Vazio = nao declarado (o pedido sai como
    # sairia sem esta funcionalidade). A validacao do vocabulario e a do
    # catalogo — uma so casa, mesmo para os dois lados.
    saida["esforco"] = ""
    bruto_esforco = bruto.get("esforco")
    if bruto_esforco not in (None, ""):
        esforco, erro_e = _gestor().normalizar_esforco(bruto_esforco)
        if erro_e:
            erros.append(f"{prefixo}: {erro_e}")
        elif esforco:
            saida["esforco"] = esforco
    # Forma da resposta: `auto` (pede json e degrada sozinho), `on` ou `off`.
    bruto_formato = str(bruto.get("formato_json") or "auto").strip().lower()
    if bruto_formato in ("on", "sim", "true", "1"):
        saida["formato_json"] = "on"
    elif bruto_formato in ("off", "nao", "não", "false", "0"):
        saida["formato_json"] = "off"
    elif bruto_formato in ("auto", ""):
        saida["formato_json"] = "auto"
    else:
        erros.append(f"{prefixo}: formato_json deve ser auto, on ou off")
        saida["formato_json"] = "auto"
    return saida, erros


def efectiva(analise: dict[str, Any] | None) -> dict[str, Any] | None:
    """Definicao utilizavel do perfil, ou `None` quando e para usar a heuristica.

    O perfil recebe a `analise` ja resolvida pelo `gestor` (global fundida com a
    sobreposicao do perfil), portanto aqui a chave ja e a que vale: o perfil
    substituiu ou herdou. Nao ha segunda decisao a tomar.
    """
    if not isinstance(analise, dict):
        return None
    definicao, erros = normalizar(analise.get("classificador"))
    if erros or not definicao.get("activo"):
        return None
    if not (definicao.get("provider") and definicao.get("modelo")):
        return None
    return definicao


# --------------------------------------------------------------------------- texto


def texto(mensagens: list[Any], *, janela: int = 8, max_chars: int = 4000) -> tuple[str, str]:
    """(texto para o modelo, ultima mensagem do utilizador) — so o que importa.

    A ultima mensagem do utilizador e a unidade de decisao; as anteriores vao
    como contexto curto, para o modelo perceber continuacoes ("continua").
    """
    def limpar(c: Any) -> str:
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            partes = []
            for bloco in c:
                if isinstance(bloco, dict) and isinstance(bloco.get("text"), str):
                    partes.append(bloco["text"])
                elif isinstance(bloco, str):
                    partes.append(bloco)
            return " ".join(partes)
        return ""

    users = [(i, limpar(m.get("content"))) for i, m in enumerate(mensagens)
             if isinstance(m, dict) and m.get("role") == "user"]
    if not users:
        return "", ""
    ultima = users[-1][1].strip()
    anteriores = [t.strip() for _, t in users[-(janela + 1):-1] if t.strip()]
    partes = []
    for t in anteriores:
        partes.append(f"[anterior] {t[:400]}")
    partes.append(f"[pedido] {ultima}")
    return "\n".join(partes)[:max_chars], ultima[:max_chars]


def _digest(perfil: str, definicao: dict[str, Any], ultima: str) -> str:
    h = hashlib.sha256()
    h.update(perfil.encode("utf-8", "replace"))
    h.update(b"\x1f")
    h.update(f"{definicao.get('provider')}\x1f{definicao.get('modelo')}".encode("utf-8", "replace"))
    h.update(b"\x1f")
    h.update(ultima.encode("utf-8", "replace"))
    return h.hexdigest()


# --------------------------------------------------------------------------- transporte


def _url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("rota sem base_url")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1") or base.endswith("/beta"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


#: Codigos de erro que podem vir de um campo que o provider nao conhece.
#: Um 401/402/429/5xx nao se resolve a retirar `response_format`.
_HTTP_SEM_FORMATO = frozenset({400, 404, 415, 422})


class _RecusaHttp(Exception):
    """Erro HTTP do provider, ja lido, para o `except` de fora decidir."""

    def __init__(self, codigo: int, detalhe: str) -> None:
        super().__init__(f"HTTP {codigo}")
        self.codigo = codigo
        self.detalhe = detalhe


def _ler_http(exc: urllib.error.HTTPError) -> tuple[int, str]:
    """Codigo e corpo do erro; fecha sempre a resposta para nao deixar sockets."""
    detalhe = ""
    try:
        detalhe = exc.read(400).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        detalhe = ""
    finally:
        try:
            exc.close()
        except Exception:  # noqa: BLE001
            pass
    return int(getattr(exc, "code", 0) or 0), detalhe


def _quer_json(definicao: dict[str, Any]) -> bool:
    """`formato_json` em `on`/`off`/`auto`; `auto` pede e degrada sozinho."""
    return str(definicao.get("formato_json") or "auto").strip().lower() != "off"


def _pedir(rota: dict[str, Any], definicao: dict[str, Any], corpo: dict[str, Any]) -> dict[str, Any]:
    dados = json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(
        _url(rota.get("base_url", "")),
        data=dados,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {rota.get('api_key', '')}",
            # O UA por omissao do urllib e recusado por varias bordas (1010).
            "User-Agent": "vipernet-model-manager/classificador",
        },
    )
    inicio = time.monotonic()
    with urllib.request.urlopen(req, timeout=float(definicao["timeout_s"])) as resp:  # noqa: S310
        bruto = resp.read(200_000)
    ms = round((time.monotonic() - inicio) * 1000, 1)
    try:
        return {"json": json.loads(bruto.decode("utf-8", "replace")), "ms": ms}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"resposta nao e JSON ({type(exc).__name__})") from exc


# O modelo responde em portugues porque o prompt o pede, mas nem sempre: um
# `stop` sem nivel reconhecivel (medido a 2026-11-02, 9,4% das leituras) e quase
# sempre a mesma palavra noutra lingua. Aceitar as variantes e barato e nao
# muda a decisao — muda a taxa de falha.
_RE_NIVEL = re.compile(
    r"\b(leve|baix[ao]|low|m[eé]dia|medium|moderad[ao]|alta|elevad[ao]|high)\b",
    re.IGNORECASE,
)
_CANONICO = {
    "leve": "leve", "baixa": "leve", "baixo": "leve", "low": "leve",
    "media": "media", "média": "media", "medium": "media",
    "moderada": "media", "moderado": "media",
    "alta": "alta", "elevada": "alta", "elevado": "alta", "high": "alta",
}


def _canonico(palavra: str) -> str:
    return _CANONICO.get(palavra.strip().lower().replace("é", "e"), palavra.strip().lower())


def _extrair_nivel(payload: dict[str, Any]) -> tuple[str, str]:
    """(nivel, texto-lido). Aceita JSON {"nivel": ...} e texto livre curto.

    Modelos de raciocinio gastam o orcamento a pensar: medido no `deepseek-flash`
    com `max_tokens: 12`, os 12 tokens de saida eram todos `reasoning_tokens` e
    `content` vinha vazio. Por isso, quando o `content` esta vazio, le-se o
    `reasoning_content` — a ultima palavra de nivel que o modelo escreveu antes
    de ser cortado e o que ele ia responder. O orcamento por omissao foi subido
    (ver `_LIMITES`) para o caso normal terminar com `content` limpo.
    """
    escolhas = payload.get("choices")
    conteudo = ""
    raciocinio = ""
    if isinstance(escolhas, list) and escolhas:
        primeira = escolhas[0] if isinstance(escolhas[0], dict) else {}
        msg_bruta = primeira.get("message")
        msg: dict[str, Any] = msg_bruta if isinstance(msg_bruta, dict) else {}
        valor = msg.get("content")
        if isinstance(valor, list):
            conteudo = " ".join(b.get("text", "") for b in valor if isinstance(b, dict))
        elif isinstance(valor, str):
            conteudo = valor
        for campo in ("reasoning_content", "reasoning"):
            r = msg.get(campo)
            if isinstance(r, str) and r.strip():
                raciocinio = r.strip()
                break
    conteudo = (conteudo or "").strip()
    if conteudo.startswith("{"):
        try:
            obj = json.loads(conteudo)
            if isinstance(obj, dict):
                nome = _canonico(str(obj.get("nivel") or obj.get("complexidade") or ""))
                if nome in NIVEIS:
                    return nome, conteudo[:120]
        except json.JSONDecodeError:
            pass
    achado = _RE_NIVEL.search(conteudo[:400])
    if not achado and not conteudo and raciocinio:
        # cortado a meio do raciocinio: vale o ultimo nivel que ele escreveu
        achados = _RE_NIVEL.findall(raciocinio[-600:])
        if achados:
            nivel = _canonico(achados[-1])
            if nivel in NIVEIS:
                return nivel, "raciocinio:" + raciocinio[-120:]
    if not achado:
        # O `finish_reason` e os tokens dizem se o orcamento foi gasto a
        # raciocinar (`length`) ou se o modelo respondeu outra coisa: sem isso o
        # ledger diz «sem nivel reconhecivel» e nao se sabe onde olhar.
        detalhe = ""
        if isinstance(escolhas, list) and escolhas and isinstance(escolhas[0], dict):
            detalhe = str(escolhas[0].get("finish_reason") or "")
        uso = payload.get("usage") if isinstance(payload, dict) else None
        if isinstance(uso, dict) and uso.get("completion_tokens"):
            detalhe += f" {uso.get('completion_tokens')}tok"
        raise ValueError("resposta sem nivel reconhecivel"
                         + (f" ({detalhe.strip()})" if detalhe.strip() else ""))
    nivel = _canonico(achado.group(1))
    if nivel not in NIVEIS:
        raise ValueError(f"nivel fora do vocabulario: {nivel!r}")
    return nivel, conteudo[:120]


# --------------------------------------------------------------------------- API


def _corpo(provider: str, modelo: str, corpo_texto: str, definicao: dict[str, Any],
           *, perfil: str = "", orcamento: int | None = None,
           esforco: Any = None, formato_json: bool | None = None,
           prompt: str | None = None) -> dict[str, Any]:
    """Corpo do pedido de classificacao, com o esforco na forma plana.

    Este pedido e HTTP directo, nao passa pelo SDK, logo nao ha `extra_body` — os
    campos entram no corpo como o provider os espera (deepseek: `thinking` +
    `reasoning_effort`; OpenRouter: `reasoning`). Raciocinio e resposta partilham
    o mesmo `max_tokens`, daí o piso quando ha esforco declarado.

    `formato_json` pede `response_format: {"type": "json_object"}`. Medido no
    mercury-3: a resposta vem json limpo em 10/10 e sem uma unica divergencia,
    contra 1 em 10 sem o campo. O DeepSeek recusa o campo com 400 se o prompt
    nao contiver a palavra "json" — o `PROMPT` contem-na, e a segunda tentativa
    (sem formato) cobre o resto.
    """
    esforco_efectivo = definicao.get("esforco") if esforco is None else esforco
    corpo: dict[str, Any] = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": prompt or PROMPT},
            {"role": "user", "content": corpo_texto},
        ],
        "max_tokens": int(definicao["max_tokens"]) if orcamento is None else int(orcamento),
        "temperature": 0,
        "stream": False,
    }
    if formato_json is None:
        formato_json = _quer_json(definicao)
    if formato_json:
        corpo["response_format"] = {"type": "json_object"}
    extra_e, topo_e = _gestor().extras_esforco(provider, modelo, esforco_efectivo, session_id=perfil)
    corpo.update(extra_e)
    corpo.update(topo_e)
    if (extra_e or topo_e) and int(corpo["max_tokens"]) < _MAX_TOKENS_COM_ESFORCO:
        corpo["max_tokens"] = _MAX_TOKENS_COM_ESFORCO
    return corpo


def forma(payload: dict[str, Any]) -> str:
    """Como a resposta veio: `json` (objecto com nivel), `texto` ou `raciocinio`.

    Existe porque um acerto por palavra solta num texto que cita o proprio
    prompt nao e a mesma coisa que um json limpo — sem esta distincao o ledger
    nao deixa ver a qualidade da leitura.
    """
    escolhas = payload.get("choices") if isinstance(payload, dict) else None
    msg: dict[str, Any] = {}
    if isinstance(escolhas, list) and escolhas and isinstance(escolhas[0], dict):
        bruta = escolhas[0].get("message")
        msg = bruta if isinstance(bruta, dict) else {}
    valor = msg.get("content")
    conteudo = ""
    if isinstance(valor, str):
        conteudo = valor.strip()
    elif isinstance(valor, list):
        conteudo = " ".join(b.get("text", "") for b in valor if isinstance(b, dict)).strip()
    if conteudo.startswith("{"):
        try:
            obj = json.loads(conteudo)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            if _canonico(str(obj.get("nivel") or obj.get("complexidade") or "")) in NIVEIS:
                return "json"
            # Resposta de lote: o json valido tem `niveis` (lista), nao `nivel`.
            # Sem esta perna, um lote perfeito era reportado como "texto" e a
            # evidencia do ledger mentia sobre a forma da resposta.
            if isinstance(obj.get("niveis") or obj.get("resultados"), list):
                return "json"
    if conteudo:
        return "texto"
    return "raciocinio"


def _pedir_com_formato(
    rota: dict[str, Any], provider: str, modelo: str, corpo_texto: str,
    definicao: dict[str, Any], *, perfil: str = "", orcamento: int | None = None,
    esforco: Any = None, formato_json: bool | None = None, prompt: str | None = None,
) -> dict[str, Any]:
    """POST de classificacao, degradando `response_format` quando recusado.

    Um 400/404/415/422 com o campo ligado repete o pedido sem ele — e assim que
    se cobre um provider novo sem manter uma lista de quem aceita o quê. Devolve
    `formato_pedido` para o ledger poder dizer se o campo foi aceite.
    """
    pedir_json = _quer_json(definicao) if formato_json is None else bool(formato_json)
    while True:
        corpo = _corpo(provider, modelo, corpo_texto, definicao, perfil=perfil,
                       orcamento=orcamento, esforco=esforco, formato_json=pedir_json,
                       prompt=prompt)
        try:
            resposta = _pedir(rota, definicao, corpo)
        except urllib.error.HTTPError as exc:
            codigo, detalhe = _ler_http(exc)
            if pedir_json and codigo in _HTTP_SEM_FORMATO:
                pedir_json = False
                continue
            raise _RecusaHttp(codigo, detalhe) from None
        return {**resposta, "formato_pedido": "json" if pedir_json else "texto"}


def classificar(
    mensagens: list[Any],
    definicao: dict[str, Any] | None,
    resolver_rota: Callable[[str, str], dict[str, Any]],
    *,
    perfil: str = "",
) -> dict[str, Any] | None:
    """Nivel decidido pelo modelo, ou `None` para o chamador usar a heuristica.

    `resolver_rota(provider, modelo)` e injectado: o plugin passa o seu
    `resolver_rota(perfil, provider, modelo)`, que resolve a credencial no
    escopo do perfil servido. Assim este modulo nao sabe nada de segredos.
    """
    if not definicao:
        return None
    provider = str(definicao.get("provider") or "").strip()
    modelo = str(definicao.get("modelo") or "").strip()
    if not (provider and modelo):
        return None
    alvo = f"{perfil}\x1f{provider.lower()}\x1f{modelo}"
    agora = time.monotonic()
    with _lock:
        if _suspensos.get(alvo, 0.0) > agora:
            return None
    corpo_texto, ultima = texto(mensagens,
                                janela=int(definicao["janela_mensagens"]),
                                max_chars=int(definicao["max_chars"]))
    if not ultima:
        return None
    chave = _digest(perfil, definicao, ultima)
    with _lock:
        guardado = _cache.get(chave)
        if guardado and guardado[0] > agora:
            return {**guardado[1], "cache": True}
        guardado_erro = _cache_erro.get(chave)
        if guardado_erro and guardado_erro[0] > agora:
            return {**guardado_erro[1], "cache": True, "cache_erro": True}
    base = {"provider": provider, "modelo": modelo, "perfil": perfil}
    # O cache negativo segue o cache da definicao: com `cache_ttl_s: 0` o
    # utilizador pediu para nao guardar nada — nem sucessos nem falhas.
    ttl_erro = min(_TTL_ERRO_S, float(definicao["cache_ttl_s"]))
    try:
        rota = resolver_rota(provider, modelo)
        formato_json = _quer_json(definicao)
        esforco = definicao.get("esforco")
        orcamento = int(definicao["max_tokens"])
        tentativas = 0
        ms_total = 0
        resposta: dict[str, Any] = {}
        nivel: str | None = None
        lido = ""
        while True:
            tentativas += 1
            resposta = _pedir_com_formato(rota, provider, modelo, corpo_texto, definicao,
                                          perfil=perfil, orcamento=orcamento,
                                          esforco=esforco, formato_json=formato_json)
            ms_total += int(resposta.get("ms") or 0)
            try:
                nivel, lido = _extrair_nivel(resposta["json"])
                break
            except ValueError:
                # Raciocinio e resposta partilham o mesmo orcamento: um modelo
                # que pensa antes de responder gasta-o todo e devolve `content`
                # vazio com `finish_reason: length` (medido no mercury-3, que
                # precisa de ~200 tokens antes de dizer uma palavra). A segunda
                # tentativa desliga o esforco e multiplica o orcamento — e a via
                # que nao depende de o modelo mudar de comportamento.
                # Duas tentativas de leitura no maximo: a segunda ja custa 4x o
                # orcamento, e uma terceira so multiplicaria o gasto num modelo
                # que insiste em nao responder. A degradacao do `response_format`
                # e feita dentro de `_pedir_com_formato` e nao conta aqui.
                if tentativas >= 2:
                    raise
                esforco = ""
                formato_json = False
                orcamento = max(orcamento * 4, _MAX_TOKENS_COM_ESFORCO)
        resposta["ms"] = ms_total
    except _RecusaHttp as exc:
        return _falhou(alvo, base, f"HTTP {exc.codigo}", exc.detalhe[:200],
                       chave=chave, ttl_erro=ttl_erro)
    except Exception as exc:  # noqa: BLE001 - fail-open sempre
        return _falhou(alvo, base, type(exc).__name__, str(exc)[:200], chave=chave, ttl_erro=ttl_erro)
    uso = resposta["json"].get("usage") if isinstance(resposta["json"], dict) else None
    uso = uso if isinstance(uso, dict) else {}
    saida = {
        "nivel": nivel,
        "origem": "modelo",
        "classificador": {
            **base,
            "fonte_credencial": rota.get("source", ""),
            "ms": resposta["ms"],
            "cache": False,
            "lido": lido,
            "formato": forma(resposta["json"]),
            "formato_pedido": resposta.get("formato_pedido") or "",
            "tokens_entrada": uso.get("prompt_tokens"),
            "tokens_saida": uso.get("completion_tokens"),
            "tentativas": tentativas,
        },
    }
    with _lock:
        _falhas.pop(alvo, None)
        if int(definicao["cache_ttl_s"]) > 0:
            _cache[chave] = (agora + int(definicao["cache_ttl_s"]), saida)
            if len(_cache) > 500:
                _cache.clear()
    return saida


def classificar_lote(
    textos: list[str],
    definicao: dict[str, Any] | None,
    resolver_rota: Callable[[str, str], dict[str, Any]],
    *,
    perfil: str = "",
) -> dict[str, Any]:
    """Classifica N pedidos num so pedido — o modo de contexto longo.

    Serve para medir (um corpus inteiro numa chamada) e para lotes grandes. O
    turno normal usa `classificar`: um pedido por turno, com cache.

    Medido no mercury-3 a 2026-11-02, 10 pedidos: 2,5 s e 9/10 acertos, contra
    6,6 s um a um com os mesmos 9/10. Custa ~1,6x os tokens porque a resposta
    leva os indices, logo o lote paga-se em latencia e nao em dinheiro.

    Devolve `{"niveis": [nivel|None, ...], "erro": str, ...}` sempre alinhado ao
    tamanho da entrada: quem chama nao tem de adivinhar quantos vieram.
    """
    pedidos = [str(t or "") for t in (textos or [])]
    vazio: dict[str, Any] = {"niveis": [None] * len(pedidos), "erro": "", "pedidos": len(pedidos)}
    if not pedidos or not definicao:
        return vazio
    provider = str(definicao.get("provider") or "").strip()
    modelo = str(definicao.get("modelo") or "").strip()
    if not (provider and modelo):
        return {**vazio, "erro": "sem provider/modelo"}
    lista = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(pedidos))
    # O orcamento acompanha o lote: a resposta JSON leva um objecto por pedido e
    # ~100 tokens de raciocinio antes do primeiro. O piso cobre o raciocinio.
    orcamento = max(_MAX_TOKENS_COM_ESFORCO, 120 * len(pedidos))
    try:
        rota = resolver_rota(provider, modelo)
        resposta = _pedir_com_formato(rota, provider, modelo, lista, definicao,
                                      perfil=perfil, orcamento=orcamento, prompt=PROMPT_LOTE)
    except _RecusaHttp as exc:
        return {**vazio, "erro": f"HTTP {exc.codigo}: {exc.detalhe[:160]}"}
    except Exception as exc:  # noqa: BLE001 - fail-open sempre
        return {**vazio, "erro": f"{type(exc).__name__}: {str(exc)[:160]}"}
    nivelados = _ler_lote(resposta["json"], len(pedidos))
    if not any(nivelados):
        # Uma resposta cortada ou a divagar nao traz niveis: repete-se uma vez
        # sem formato e sem esforco, com o dobro do orcamento (medido: um lote
        # de 10 com max_tokens=600 devolve `finish_reason: length` e content vazio).
        try:
            resposta2 = _pedir_com_formato(rota, provider, modelo, lista, definicao,
                                           perfil=perfil, orcamento=orcamento * 2,
                                           esforco="", formato_json=False,
                                           prompt=PROMPT_LOTE)
            nivelados2 = _ler_lote(resposta2["json"], len(pedidos))
            if any(nivelados2):
                nivelados = nivelados2
                resposta = {**resposta2, "ms": resposta["ms"] + resposta2["ms"]}
        except Exception:  # noqa: BLE001 - fail-open: fica a primeira resposta
            pass
    uso = resposta["json"].get("usage") if isinstance(resposta["json"], dict) else None
    uso = uso if isinstance(uso, dict) else {}
    return {
        "niveis": nivelados,
        "erro": "" if any(nivelados) else "resposta sem niveis reconheciveis",
        "pedidos": len(pedidos),
        "provider": provider,
        "modelo": modelo,
        "ms": resposta["ms"],
        "formato": forma(resposta["json"]),
        "formato_pedido": resposta.get("formato_pedido") or "",
        "tokens_entrada": uso.get("prompt_tokens"),
        "tokens_saida": uso.get("completion_tokens"),
    }


def _ler_lote(payload: dict[str, Any], n: int) -> list[str | None]:
    """Niveis do lote, alinhados ao tamanho pedido.

    Aceita as tres formas que um modelo devolve na pratica:
    `{"niveis":[{"i":1,"nivel":"leve"}]}`, `{"niveis":["leve",...]}` e
    `{"1":"leve","2":"media"}`. O que faltar fica `None` — nunca se inventa.
    """
    saida: list[str | None] = [None] * n
    if not isinstance(payload, dict):
        return saida
    escolhas = payload.get("choices")
    if not (isinstance(escolhas, list) and escolhas and isinstance(escolhas[0], dict)):
        return saida
    msg = escolhas[0].get("message")
    conteudo = (msg or {}).get("content") if isinstance(msg, dict) else ""
    if not isinstance(conteudo, str) or not conteudo.strip():
        return saida
    try:
        obj = json.loads(conteudo.strip())
    except json.JSONDecodeError:
        return saida
    if not isinstance(obj, dict):
        return saida
    brutos = obj.get("niveis") or obj.get("resultados") or obj.get("pedidos")
    if isinstance(brutos, list):
        for posicao, item in enumerate(brutos):
            if posicao >= n:
                break
            if isinstance(item, dict):
                indice = item.get("i", item.get("indice", item.get("id")))
                valor = item.get("nivel") or item.get("complexidade")
                try:
                    posicao_real = int(indice) - 1 if indice is not None else posicao
                except (TypeError, ValueError):
                    posicao_real = posicao
                if 0 <= posicao_real < n:
                    canonico = _canonico(str(valor or ""))
                    if canonico in NIVEIS:
                        saida[posicao_real] = canonico
            else:
                canonico = _canonico(str(item or ""))
                if canonico in NIVEIS:
                    saida[posicao] = canonico
        return saida
    for chave, valor in obj.items():
        try:
            indice = int(str(chave).strip()) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= indice < n:
            canonico = _canonico(str(valor or ""))
            if canonico in NIVEIS:
                saida[indice] = canonico
    return saida


def _falhou(alvo: str, base: dict[str, Any], tipo: str, detalhe: str,
            *, chave: str = "", ttl_erro: float = _TTL_ERRO_S) -> dict[str, Any]:
    with _lock:
        n = _falhas.get(alvo, 0) + 1
        _falhas[alvo] = n
        if n >= _FALHAS_PARA_SUSPENDER:
            _suspensos[alvo] = time.monotonic() + _SUSPENSAO_S
            _falhas[alvo] = 0
    # O erro fica no valor devolvido: quem chama registra-o no ledger.
    saida = {"erro": f"{tipo}: {detalhe}"[:300],
             "classificador": {**base, "falhou": tipo},
             "origem": "modelo_falhou"}
    if chave and ttl_erro > 0:
        with _lock:
            _cache_erro[chave] = (time.monotonic() + ttl_erro, saida)
            if len(_cache_erro) > 500:
                _cache_erro.clear()
    return saida


def limpar_cache() -> None:
    with _lock:
        _cache.clear()
        _cache_erro.clear()
        _falhas.clear()
        _suspensos.clear()


def estado() -> dict[str, Any]:
    with _lock:
        agora = time.monotonic()
        return {
            "cache": len(_cache),
            "alvos_suspensos": {k: round(v - agora, 1) for k, v in _suspensos.items() if v > agora},
            "falhas": dict(_falhas),
        }
