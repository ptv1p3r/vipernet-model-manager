"""Classificador de complexidade — puro, sem rede, sem estado, testavel isolado.

Recebe as mensagens que vao seguir para o provider (o historico todo, tal como o
Hermes o monta) e devolve um nivel (`leve`|`media`|`alta`), a pontuacao e os
sinais que a produziram. Nao ve o pedido, nao ve o agente: e so texto.

Regras de desenho:

- **So a mensagem do utilizador conta para o tamanho.** O prompt de sistema
  desta instalacao tem ~38 KB e o contexto injectado (wiki, memoria, notas do
  gateway) entra no turno: pontuar isso daria sempre `alta`. O texto e cortado
  ao primeiro marcador de contexto injectado conhecido.
- **Sinais explicitos, nao um modelo.** Um LLM a classificar custaria uma
  chamada por turno e falharia em silencio; aqui cada ponto tem nome e o registo
  guarda os nomes, por isso a decisao e auditavel e reproduzivel.
- **Deterministico.** Mesmo texto, mesma pontuacao — o que permite testar sem
  rede e explicar ao dono porque e que aquele turno foi para o modelo caro.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

NIVEIS = ("leve", "media", "alta")

# Marcadores de contexto injectado que NAO sao texto do utilizador.
_CORTES = (
    "\n[conhecimento local",
    "\n## Mnemosyne Context",
    "\n[MEMORY",
    "\n[USER PROFILE",
    "\n[OUT-OF-BAND USER MESSAGE",
    "\n[CONTEXT COMPACTION",
    "\n<memory-context>",
)

# `complexidade: alta`, `Complexidade = MÉDIA`, `**complexidade:** alta`, `complexity=high`
_MARCA = re.compile(
    r"(?:complexidade|complexity)\s*[:=]\s*\*{0,2}\s*(leve|m[eé]dia|media|alta|auto|low|medium|high)\b",
    re.IGNORECASE,
)
_MARCA_TRADUZ = {"low": "leve", "medium": "media", "high": "alta",
                 "leve": "leve", "media": "media", "alta": "alta", "auto": "auto"}

_VERBOS = re.compile(
    r"\b(implement\w*|refactor\w*|refator\w*|arquitect\w*|arquitet\w*|desenh\w*|depur\w*|"
    r"diagnostic\w*|investig\w*|analis\w*|compar\w*|migr\w*|optimiz\w*|"
    r"otimiz\w*|corrig\w*|revis\w*|test\w*|integr\w*|document\w*|audit\w*|"
    r"debug\w*|redesign\w*|benchmark\w*|prov(?:ar|a|e|o|ou|ado|ada|ando|ei|aste|amos|aram)\b|"
    r"med(?:ir|e|iu|ido|ida|ição|imos|em|iram)\b)\b",
    re.IGNORECASE,
)
_TRACO_ERRO = re.compile(r"(Traceback \(most recent call last\)|\b[A-Z]\w*Error\b|\bexit code\b|"
                         r"\bat line \d+|\bpanic:|\bsegfault\b)", re.IGNORECASE)
_CAMINHO = re.compile(r"(?:^|\s)(?:/[\w.\-]+/[\w./\-]+|[A-Za-z]:\\\\|~/[\w./\-]+)")
_MULTI = re.compile(r"(e também|e depois|alem disso|além disso|por outro lado|"
                    r"\b1[.)]\s|\b2[.)]\s|\b3[.)]\s)", re.IGNORECASE)


def _sem_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def texto_de(conteudo: Any, profundidade: int = 0) -> str:
    """Texto de um `content` (string, lista de partes, dict) sem rebentar com None."""
    if profundidade > 6 or conteudo is None:
        return ""
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        return "\n".join(texto_de(p, profundidade + 1) for p in conteudo)
    if isinstance(conteudo, dict):
        for chave in ("text", "content", "input_text", "output_text"):
            if chave in conteudo:
                return texto_de(conteudo[chave], profundidade + 1)
    return ""


def limpar_contexto_injectado(texto: str) -> str:
    """Corta o texto ao primeiro bloco de contexto que o Hermes acrescenta."""
    corte = len(texto)
    for marca in _CORTES:
        pos = texto.find(marca)
        if pos != -1:
            corte = min(corte, pos)
    return texto[:corte]


def _marcadores(texto: str) -> list[str]:
    return [a.lower() for a in _MARCA.findall(texto)]


def marcador(mensagens: list[Any], maximo: int = 60) -> str | None:
    """Marcador explicito, com semantica de pega-e-solta:

    - marcador na **ultima** mensagem do utilizador manda (alta/media/leve/auto);
    - sem marcador na ultima, o ultimo marcador anterior vale (pegajoso), salvo
      se for `auto` (que so faz reset quando e a ultima coisa escrita);
    - `auto` apenas na ultima mensagem = reset; `auto` ja ultrapassado = cai para
      a analise.
    """
    users = [m for m in (mensagens or []) if isinstance(m, dict) and m.get("role") == "user"]
    users = users[-max(1, maximo):]
    if not users:
        return None
    ultima = _marcadores(texto_de(users[-1].get("content")))
    if ultima:
        alvo = _sem_acentos(ultima[-1]).lower()
        return _MARCA_TRADUZ.get(alvo, alvo)
    for msg in reversed(users[:-1]):
        achados = _marcadores(texto_de(msg.get("content")))
        if achados:
            alvo = _sem_acentos(achados[-1]).lower()
            traduzido = _MARCA_TRADUZ.get(alvo, alvo)
            return None if traduzido == "auto" else traduzido
    return None


def pontuar(mensagens: list[Any], janela: int = 8) -> dict[str, Any]:
    """Pontuacao de complexidade + sinais. Ver o docstring do modulo."""
    utilizadores = [m for m in (mensagens or [])
                    if isinstance(m, dict) and m.get("role") == "user"]
    if not utilizadores:
        return {"score": 0, "sinais": ["sem_texto"], "caracteres": 0,
                "turnos": 0, "ferramentas": 0}
    bruto = texto_de(utilizadores[-1].get("content"))
    texto = limpar_contexto_injectado(bruto).strip()
    sinais: list[str] = []
    score = 0

    n = len(texto)
    if n >= 2400:
        score += 4
        sinais.append("texto_muito_longo")
    elif n >= 900:
        score += 3
        sinais.append("texto_longo")
    elif n >= 300:
        score += 1
        sinais.append("texto_medio")

    verbos = {m.group(0).lower() for m in _VERBOS.finditer(texto)}
    if verbos:
        peso = min(len(verbos), 3)
        score += peso
        sinais.append(f"verbos_pesados={','.join(sorted(verbos)[:3])}")

    if "```" in texto:
        score += 2
        sinais.append("codigo")
    if _TRACO_ERRO.search(texto):
        score += 3
        sinais.append("erro_ou_traceback")
    if _CAMINHO.search(texto):
        score += 1
        sinais.append("caminho_de_ficheiro")
    if texto.count("?") >= 2:
        score += 1
        sinais.append("varias_perguntas")
    if _MULTI.search(texto):
        score += 1
        sinais.append("multi_pedido")

    turnos = len(utilizadores)
    if turnos >= 8:
        score += 2
        sinais.append(f"conversa_longa={turnos}")
    elif turnos >= 4:
        score += 1
        sinais.append(f"conversa_media={turnos}")

    janela_msgs = (mensagens or [])[-max(1, janela * 4):]
    ferramentas = sum(1 for m in janela_msgs if isinstance(m, dict)
                      and m.get("role") in ("tool", "function"))
    if ferramentas >= 6:
        score += 1
        sinais.append(f"muitas_ferramentas={ferramentas}")

    return {"score": score, "sinais": sinais, "caracteres": n,
            "turnos": turnos, "ferramentas": ferramentas}


def nivel_de_score(score: int, limiares: dict[str, Any] | None = None) -> str:
    """`score` -> nivel, com limiares configuraveis (`media`, `alta`)."""
    lim = limiares or {}
    try:
        media = int(lim.get("media", 3))
        alta = int(lim.get("alta", 7))
    except (TypeError, ValueError):
        media, alta = 3, 7
    if alta <= media:  # config incoerente nao pode apagar o nivel `alta`
        alta = media + 4
    if score >= alta:
        return "alta"
    if score >= media:
        return "media"
    return "leve"


def detectar_tarefa(mensagens: list[Any], regras: dict[str, Any]) -> str | None:
    """Classificacao heuristica do pedido actual, nunca do historico ou contexto injectado.

    Termos sao frases literais sem acentos e com fronteiras de palavra. Em
    sobreposicao ganha prioridade maior, depois frase mais especifica e id.
    """
    users = [m for m in mensagens if isinstance(m, dict) and m.get("role") == "user"]
    if not users:
        return None
    texto = _sem_acentos(limpar_contexto_injectado(texto_de(users[-1].get("content")))).lower()
    texto = " ".join(texto.split())
    achados = []
    for tid, regra in regras.items():
        for termo in regra.get("termos", []):
            termo = " ".join(_sem_acentos(termo).lower().split())
            if termo and re.search(r"(?<!\w)" + re.escape(termo) + r"(?!\w)", texto):
                achados.append((regra.get("prioridade", 0), len(termo), tid))
    return max(achados)[2] if achados else None


def decidir(mensagens: list[Any], *, limiares: dict[str, Any] | None = None,
            janela: int = 8, maximo: int = 60) -> dict[str, Any]:
    """Nivel final do turno: marcador explicito > pontuacao."""
    explicito = marcador(mensagens, maximo)
    if explicito in NIVEIS:
        return {"nivel": explicito, "origem": "marcador", "score": None, "sinais": ["marcador"]}
    if explicito == "auto":
        return {"nivel": "", "origem": "reset", "score": None, "sinais": ["marcador_auto"]}
    p = pontuar(mensagens, janela)
    return {"nivel": nivel_de_score(p["score"], limiares), "origem": "analise",
            "score": p["score"], "sinais": p["sinais"], "caracteres": p["caracteres"],
            "turnos": p["turnos"], "ferramentas": p["ferramentas"]}
