"""Classificador de complexidade — puro, sem rede, sem estado, testavel isolado.

Recebe as mensagens que vao seguir para o provider (o historico todo, tal como o
Hermes o monta) e devolve um nivel (`leve`|`media`|`alta`), a pontuacao e os
sinais que a produziram. Nao ve o pedido, nao ve o agente: e so texto.

Regras de desenho (v2):

- **So a mensagem do utilizador conta para o tamanho.** O prompt de sistema
  desta instalacao tem ~38 KB e o contexto injectado (wiki, memoria, notas do
  gateway) entra no turno: pontuar isso daria sempre `alta`. O texto e cortado
  ao primeiro marcador de contexto injectado conhecido.
- **A complexidade mede-se pelo trabalho pedido, nao pela idade da sessao.**
  Na v1, `conversa_longa` somava +2 a partir do 8.o turno e nunca descia: ao
  fim de 40 turnos, "lista os ficheiros" ia para `media`. A v2 nao pontua a
  duracao da conversa. Pontua o que o turno pede, mais o *trabalho em curso*
  (ferramentas de escrita nas ultimas mensagens), que decai com a janela.
- **Continuacoes herdam.** "continua", "faz", "aplica" valem 0 por si: o nivel
  do turno e o do pedido que estao a continuar. Sem isto, o turno caro de uma
  implementacao era seguido de um turno barato a meio do mesmo trabalho.
- **Intencao antes de palavras soltas.** Produzir (implementa, escreve, migra),
  decidir (arquitetura, trade-off, ADR), auditar e depurar sao categorias
  distintas com pesos distintos. Na v1 todos os verbos vinham do mesmo saco e
  `implementa o parser com testes` valia menos que tres consultas seguidas.
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

# --- categorias de intencao -------------------------------------------------

# Produzir/alterar: o que justifica o modelo caro. Sem acentos (texto normalizado).
_TRABALHO = re.compile(
    r"\b(implement\w*|refactor\w*|refator\w*|reescrev\w*|reescreve\w*|"
    r"substitu\w*|convert\w*|otimiz\w*|optimiz\w*|"
    r"depur\w*|debug\w*|integr\w*|constru\w*|constroi\w*|cri\w*|cria\w*|"
    r"escrev\w*|escreve\w*|ger\w*|gera\w*|desenh\w*|desenha\w*|"
    r"mont\w*|monta\w*|produz\w*|entreg\w*|prepar\w*|prepara\w*|"
    r"projet\w*|projeta\w*|configur\w*|configura\w*|instal\w*|instala\w*|"
    r"deploy\w*|provision\w*|automatiz\w*|automatiza\w*)\b",
    re.IGNORECASE,
)
# Objetos de sistema vivo: "corrige a automacao" e trabalho, nao manutencao fina.
_OBJETO_VIVO = re.compile(
    r"\b(automa[\w]*|instala[\w]*|script[\w]*|servi[\w]*|servico[\w]*|"
    r"container[\w]*|contentor[\w]*|systemd[\w]*|docker[\w]*|"
    r"home[ -]?assistant|proxmox[\w]*|"
    r"modulo[\w]*|pipeline[\w]*)\b",
    re.IGNORECASE,
)
# Relatorio recorrente: produzir um resumo/relatorio de rotina.
_RELATORIO = re.compile(
    r"\b(relat[oó]rio\w*|relatorios?|resumo\w*|sum[aá]rio\w*|"
    r"di[aá]rio\w*|semanal\w*|mensal\w*)\b",
    re.IGNORECASE,
)
# Verbos que nao dizem nada sobre dificuldade: o peso vem do objeto que os segue.
_VERBOS_GENERICOS = ("prepar", "gera", "ger", "faz", "fazer", "produz")

# Manutencao pequena: corrigir, adicionar, atualizar, revisao fina.
_MANUTENCAO = re.compile(
    r"\b(adicion\w*|adiciona\w*|atualiz\w*|atualiza\w*|corrig\w*|corrige\w*|"
    r"acrescent\w*|acrescenta\w*|revis\w*|melhor\w*|melhora\w*|"
    r"renome\w*|renomeia\w*|troc\w*|typo\w*|ajust\w*|muda\b|mude\b|"
    r"poe\b|poem\b|mete\b|da uma olhada|vista de olhos)\b",
    re.IGNORECASE,
)
# Planeamento: decidir o futuro, com janela, rollback, proposta.
_PLANEAMENTO = re.compile(
    r"\b(plano\w*|plane\w*|planeia\w*|janela\w*|rollback\w*|propost\w*|"
    r"migr\w*|migracao\w*|estrat\w*|cronogr\w*|calend\w*|sequenc\w*)\b",
    re.IGNORECASE,
)
_ANALISE = re.compile(
    r"\b(analis\w*|analisa\w*|compar\w*|compar\w*|investig\w*|investiga\w*|"
    r"avali\w*|avalia\w*|revis\w*|rev\w*|med\w*|mede\w*|estud\w*|estuda\w*|"
    r"examin\w*|examina\w*|verific\w*|verifica\w*|valid\w*|valida\w*|"
    r"test\w*|testa\w*)\b"
)
# Auditar/diagnosticar: procura de defeito num sistema vivo.
_AUDITORIA = re.compile(
    r"\b(audit\w*|auditoria\w*|diagnostic\w*|diagnostica\w*|triag\w*|"
    r"root cause\w*|rca\w*)\b|"
    r"(o que (?:esta|está) mal|porque (?:e que )?falha|porque falhou|"
    r"o que se passa|encontra o problema|qual (?:e )?o problema|"
    r"descobr\w* a causa|descobre a causa|encontra a causa|"
    r"identific\w* a causa|a causa (?:de|disto|deste)|"
    r"investig\w*|o que (?:expo|expus)\w*|o que (?:esta|está) expost\w*)"
)
# Decidir/desenhar: o caso mais caro, pede raciocinio e alternativas.
_ARQUITETURA = re.compile(
    r"\b(arquitetur\w*|arquitectur\w*|trade-?off\w*|adr\b|parecer\w*|"
    r"decisao tecnica|decisão técnica|desenho tecnico|desenho técnico|"
    r"ladd\b|tsd\b|aad\b|abordagem\w*|"
    r"proposta de|avaliar opcoes|avaliar opções|comparar opcoes|comparar opções|"
    r"melhor opcao|melhor opção|viabilidade\w*|blueprint\w*)\b",
    re.IGNORECASE,
)
# "justifica" pede raciocinio, mas nao e prova de desenho: "justifica" sozinho
# valia 6 (o topo) e mandava "NPM ou Traefik? justifica" para o modelo caro.
_JUSTIFICACAO = re.compile(
    r"\b(justific\w*|"
    r"porque (?:e que )?(?:escolheste|escolheu|usaste|usou|optaste|preferiste|preferiu)|"
    r"porque (?:essa|aquela|esta) escolha|razao (?:para|de) (?:escolher|usar))\b",
    re.IGNORECASE,
)
# Pedido de explicacao de um conceito: "explica como funciona X". Raciocinio de
# dificuldade media — nao desenha nada, mas tambem nao e uma saudacao.
_EXPLICAR = re.compile(
    r"\b(explica\w*|explicar\w*|como funciona|como se faz|o que significa)\b",
    re.IGNORECASE,
)
# Avaria concreta: o modelo tem de raciocinar sobre evidencia.
_STACK = re.compile(
    r"(Traceback \(most recent call last\)|stack ?trace|\bexit code\b|"
    r"\bat line \d+|\bpanic:|\bsegfault\b|\b[A-Z]\w*(?:Error|Exception)\b|"
    r"\bexception\b)",
    re.IGNORECASE,
)
_AVARIA = re.compile(
    r"(\bfalha\b|\bfalhou\b|\bcrash\w*|\brebent\w*|\blento\b|\blenta\b|"
    r"\bengasga\w*|\binst[aá]vel\b|\bpreso\b|\btimeout\b|\bmorreu\b|"
    r"\bcaiu\b|foi abaixo|est[aá] (?:em baixo|offline)|\boffline\b|"
    r"est[aá] a demorar|demora (?:muito|demasiado|a responder)|"
    r"n[aã]o (?:funciona|devolve|arranca|responde|recuper\w*|sub\w*|volt\w*|"
    r"sincroniz\w*|lig\w*|carrega\w*|deteta\w*|aparece\w*|abre|passa)\b|"
    r"est[aá] a falhar|parou de|deixou de|deixaram de|passou a falhar|d[aá] (?:este |um )?erro|est[aá] mal)",
    re.IGNORECASE,
)
# Falha de servico: o objeto e um processo que devia estar a correr.
_SERVICO_AVARIADO = re.compile(
    r"\b(container\w*|contentor\w*|servi[cç]o\w*|docker\w*|systemd\w*|"
    r"podman\w*|nginx|portainer|n8n|home[ -]?assistant|daemon\w*)\b",
    re.IGNORECASE,
)
# Consulta hipotetica: "como se cria", "o que e", "qual e" — pergunta, nao pedido
# de producao. Sem isto, "como se cria um proxy?" contava como trabalho.
_CONSULTA = re.compile(
    r"\b(?:como (?:se|e que se|é que se)|o que (?:e|é)(?: que| um| uma)?|"
    r"qual (?:e|é)|quais (?:sao|são)|para que serve|existe[m]? .*\?)\b",
    re.IGNORECASE,
)
_EXPLICACAO = re.compile(
    r"\b(explica\w*|explicar\w*|explicita\w*|como funciona|como se faz|"
    r"porque|porqu[eê]|o que [eé] que|para que serve|qual a diferen[cç]a)\b"
)
_CODIGO = re.compile(r"```|^\s*(?:def |class |import |from |function |"
                     r"SELECT |CREATE |curl |docker |kubectl |ansible )",
                     re.MULTILINE)
_CAMINHO = re.compile(
    r"(?:^|[\s\"'(`])(?:/[\w.\-]+/[\w./\-]+|[A-Za-z]:\\\\|~/[\w./\-]+|"
    r"[\w/\-]+\.(?:py|json|ya?ml|sh|js|ts|sql|cfg|ini|toml|service|conf)\b)"
)
_DOMINIO = re.compile(
    r"\b(api|docker|kubernetes|k8s|yaml|json|python|javascript|typescript|sql|regex|"
    r"gateway|proxy|nginx|keepalived|dashboard|plugin|script|automa[cç][aã]o|c[oó]digo|"
    r"bateria|inversor|modbus|zigbee|mqtt|vlan|dns|tls|certificado|"
    r"home assistant|entidades?|sensores?|automa[cç][oõ]es|parser|testes|migra[cç][aã]o|config|logs?|ficheiros?|"
    r"base de dados|query|endpoint|deploy|commit|branch|repo|jira|confluence|"
    r"lxc|proxmox|portainer|pve\d?|backup\w*|reten[cç][aã]o|snapshot\w*|"
    r"unraid|zfs|raid|traefik|watchtower|adguard|"
    r"skills?|vlans?|servi[cç]os?|homelab|seguran[cç]a|exposi[cç][aã]o|"
    r"consumo|standby|energia|kwh|n8n|sqlite|postgres|firmware|ota\b|"
    r"diagrama|lat[eê]ncia|custo|pre[cç]o|tokens?|desempenho|performance|"
    r"\bpr\b|pull request|diff|patch|merge|commit)\b",
    re.IGNORECASE,
)
_LISTA = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*])\s+\S", re.MULTILINE)
_CONECTOR = re.compile(r"\b(e tamb[eé]m|al[eé]m disso|por outro lado|e depois|"
                       r"assim como|bem como)\b", re.IGNORECASE)
_DUAS_PARTES = re.compile(r"(?:,\s*e\s|\se\s(?:onde|como|quando|porque|que|qual)\s)",
                          re.IGNORECASE)
# Continuacao: curta e a apontar para o pedido anterior.
_CONTINUACAO = re.compile(
    r"^(?:continua\w*|segue\w*|avanc\w*|vai (?:com isso|em frente|la|para a frente)|"
    r"faz(?: isso| o mesmo| igual)?|aplica(?: isso| o mesmo)?|forca|força|bora|"
    r"corrige|resolve|executa|implementa|"
    r"podes (?:fazer|aplicar|avancar|continuar)|sim[,!.]?(?: faz| aplica| continua)?|"
    r"ok[,!.]?(?: faz| aplica| continua)?|vamos a isso|prossegue)\s*[.!]*$",
    re.IGNORECASE,
)
_FERRAMENTAS_ESCRITA = {
    "write_file", "patch", "str_replace_editor", "edit_file", "create_file",
    "apply_patch", "multiedit", "write", "create", "edit",
}

# Pesos. Todos substituiveis por config (`analise.pesos`) sem tocar no codigo.
PESOS = {
    "trabalho": 5,        # produzir/alterar
    "manutencao": 3,      # corrigir typo, adicionar teste, revisao pequena
    "planeamento": 5,     # plano, janela, rollback, proposta de migracao
    "relatorio": 3,       # relatorio/resumo recorrente: produzir, mas barato
    "analise": 2,         # analisar/comparar
    "auditoria": 4,       # auditar/diagnosticar sistema vivo
    "arquitetura": 6,     # decidir/desenhar (o topo)
    "justificacao": 3,    # "justifica": pede razao (media), nao desenha
    "explicacao_conceito": 2,  # "explica como funciona X"
    "erro_stack": 5,      # avaria com evidencia (traceback, excecao)
    "erro_avaria": 3,     # sintoma sem evidencia
    "avaria_de_servico": 3,  # sintoma sobre um servico que devia estar a correr
    "codigo": 2,          # bloco de codigo
    "caminho": 1,         # ficheiro concreto
    "dominio": 1,         # termos tecnicos (1, +1 se >=3 distintos)
    "multi": 2,           # lista numerada com 2+ itens
    "conector": 1,        # "e tambem", "alem disso"
    "duas_partes": 1,     # duas sub-perguntas
    "explicacao": 1,      # pedido de explicacao com 2+ partes
    "varias_perguntas": 1,
    "trabalho_em_curso": 2,
    "texto_medio": 1,
    "texto_longo": 3,
    "texto_muito_longo": 4,
}
LIMIARES = {"media": 3, "alta": 6}


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


def _pesos(pesos: dict[str, Any] | None) -> dict[str, int]:
    p = dict(PESOS)
    for chave, valor in (pesos or {}).items():
        try:
            p[chave] = int(valor)
        except (TypeError, ValueError):
            continue
    return p


def _e_continuacao(texto: str) -> bool:
    """Pedido curto que aponta para o turno anterior (sem conteudo proprio)."""
    t = texto.strip()
    return 0 < len(t) <= 120 and bool(_CONTINUACAO.match(t))


_SUFIXO_NOMINAL = re.compile(r"(?:cao|coes|mento|gem|dor|dura|ncia)$")


def _primeiro_verbo(padrao: re.Pattern[str], texto: str) -> str | None:
    """Primeiro verbo real do padrao — substantivos derivados nao contam.

    `instalacao`, `configuracao`, `migracao`, `automacao` casam com
    `instal\\w*`, `configur\\w*`, ... e davam trabalho de producao a uma simples
    mencao ("audita a instalacao do HA"). Um sufixo nominal desqualifica o match.
    """
    for m in padrao.finditer(texto):
        achado = m.group(0)
        if _SUFIXO_NOMINAL.search(achado):
            continue
        return achado
    return None


def _ferramentas_de_escrita(mensagens: list[Any], janela: int) -> int:
    """Chamadas de escrita/edicao recentes — o trabalho em curso, com decaimento."""
    recentes = (mensagens or [])[-max(1, janela * 4):]
    total = 0
    for m in recentes:
        if not isinstance(m, dict):
            continue
        for chave in ("tool_calls", "tool_call"):
            for chamada in (m.get(chave) or []) if isinstance(m.get(chave), list) else [m.get(chave)]:
                if not isinstance(chamada, dict):
                    continue
                nome = ((chamada.get("function") or {}).get("name")
                        or chamada.get("name") or "")
                if str(nome).lower() in _FERRAMENTAS_ESCRITA:
                    total += 1
        nome = m.get("name")
        if m.get("role") in ("tool", "function") and str(nome).lower() in _FERRAMENTAS_ESCRITA:
            total += 1
    return total


# Sinais que medem forma ou volume, nao intencao: nao contam para a regra
# dos dois sinais — um pedido grande nao e um pedido dificil.
_SINAIS_AMPLITUDE = {"texto_medio", "texto_longo", "texto_muito_longo"}
# Verificar nao e analisar: "testa o plugin" nao conta como evidencia.
_VERBOS_VERIFICACAO = {
    "testa", "teste", "testar", "test", "verifica", "verifique", "verificar",
    "verificacao", "valida", "valide", "validar", "examina", "examine", "examinar",
}

def _pontuar_texto(texto: str, pesos: dict[str, int], ferramentas_escrita: int,
                   turnos: int, ferramentas: int) -> tuple[int, list[str]]:
    """Nucleo da pontuacao, sobre texto ja limpo e normalizado."""
    sinais: list[str] = []
    score = 0
    n = len(texto)

    if n >= 2400:
        score += pesos["texto_muito_longo"]
        sinais.append("texto_muito_longo")
    elif n >= 900:
        score += pesos["texto_longo"]
        sinais.append("texto_longo")
    elif n >= 300:
        score += pesos["texto_medio"]
        sinais.append("texto_medio")

    # Categorias de intencao: cada uma contribui no maximo uma vez.
    tem_arquitetura = bool(_ARQUITETURA.search(texto))
    if tem_arquitetura:
        score += pesos["arquitetura"]
        sinais.append("arquitetura_decisao")
    elif _JUSTIFICACAO.search(texto):
        # Pedir justificacao soma, mas nao promove sozinho a `alta`.
        score += pesos["justificacao"]
        sinais.append("justificacao_pedida")
    elif _EXPLICAR.search(texto):
        # Explicar um conceito: raciocinio de dificuldade media.
        score += pesos["explicacao_conceito"]
        sinais.append("explicacao_conceito")
    if _AUDITORIA.search(texto):
        score += pesos["auditoria"]
        sinais.append("auditoria_diagnostico")
    # "como se cria", "o que e" — pergunta, nao pedido de producao.
    consulta = bool(_CONSULTA.search(texto))
    verbo_trabalho = None if consulta else _primeiro_verbo(_TRABALHO, texto)
    # "prepara/gera o resumo semanal" e produzir um relatorio, nao engenharia: o
    # verbo e generico e herda o peso do objeto (relatorio = 3, nao trabalho = 5).
    if (verbo_trabalho and verbo_trabalho.startswith(_VERBOS_GENERICOS)
            and _RELATORIO.search(texto)):
        verbo_trabalho = None
    if verbo_trabalho:
        score += pesos["trabalho"]
        sinais.append(f"trabalho_producao={verbo_trabalho}")
    elif not consulta and _primeiro_verbo(_ANALISE, texto):
        # Verificar nao e analisar: "testa/adiciona um teste" e confirmacao. Sem
        # isto, `adiciona um teste ao parser` somava analise (2) + manutencao (3)
        # + dominio (1) = 6 e comprava o modelo caro para uma tarefa de minutos.
        verbo = _primeiro_verbo(_ANALISE, texto)
        if verbo not in _VERBOS_VERIFICACAO:
            score += pesos["analise"]
            sinais.append(f"analise={verbo}")
    if _STACK.search(texto):
        score += pesos["erro_stack"]
        sinais.append("erro_com_evidencia")
    elif _AVARIA.search(texto):
        score += pesos["erro_avaria"]
        sinais.append("avaria_sem_evidencia")
        # Falha de um servico (nao arranca, caiu) pede diagnostico a serio:
        # um sintoma sobre um sistema vivo nao e o mesmo que "a luz nao acende".
        if _SERVICO_AVARIADO.search(texto):
            score += pesos["avaria_de_servico"]
            sinais.append("avaria_de_servico")

    # Amplitude.
    if _CODIGO.search(texto):
        score += pesos["codigo"]
        sinais.append("codigo")
    if _CAMINHO.search(texto):
        score += pesos["caminho"]
        sinais.append("caminho_de_ficheiro")
    distintos = {m.group(0).lower() for m in _DOMINIO.finditer(texto)}
    if distintos:
        score += pesos["dominio"] + (pesos["dominio"] if len(distintos) >= 3 else 0)
        sinais.append(f"dominio_tecnico={len(distintos)}")
    itens = len(_LISTA.findall(texto))
    if itens >= 2:
        score += pesos["multi"]
        sinais.append(f"lista_de_pedidos={itens}")
    elif _CONECTOR.search(texto):
        score += pesos["conector"]
        sinais.append("pedido_encadeado")
    varias = texto.count("?") >= 2
    duas = bool(_DUAS_PARTES.search(texto))
    if varias:
        # `varias_perguntas` e `duas_partes` medem o mesmo; contar os dois
        # punha uma consulta de duas perguntas em `media`.
        score += pesos["varias_perguntas"]
        sinais.append("varias_perguntas")
    elif duas:
        score += pesos["duas_partes"]
        sinais.append("duas_partes")
    if duas and _EXPLICACAO.search(texto):
        score += pesos["explicacao"]
        sinais.append("pedido_de_explicacao")

    # Manutencao pequena e planeamento — categorias proprias.
    if _MANUTENCAO.search(texto) and not _TRABALHO.search(texto):
        # Corrigir um objeto de sistema vivo (automacao, servico, instalacao)
        # e trabalho de producao, nao manutencao fina de texto.
        if _OBJETO_VIVO.search(texto):
            score += pesos["trabalho"]
            sinais.append("trabalho_producao=" + (_primeiro_verbo(_MANUTENCAO, texto) or "manutencao"))
        else:
            score += pesos["manutencao"]
            sinais.append("manutencao_increm")
    if _PLANEAMENTO.search(texto):
        score += pesos["planeamento"]
        sinais.append("planeamento_futuro")
    if (_RELATORIO.search(texto) and not verbo_trabalho
            and not _PLANEAMENTO.search(texto)):
        score += pesos["relatorio"]
        sinais.append("relatorio_recorrente")

    # Trabalho em curso: escreveu/alterou ficheiros nas ultimas mensagens.
    if ferramentas_escrita:
        score += pesos["trabalho_em_curso"]
        sinais.append(f"trabalho_em_curso={ferramentas_escrita}")

    return score, sinais


def pontuar(mensagens: list[Any], janela: int = 8,
            pesos: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pontuacao de complexidade + sinais do ultimo pedido do utilizador.

    Nao pontua a duracao da conversa: `turnos` e `ferramentas` sao devolvidos
    como informacao, nunca como pontos.
    """
    p = _pesos(pesos)
    utilizadores = [m for m in (mensagens or [])
                    if isinstance(m, dict) and m.get("role") == "user"]
    if not utilizadores:
        return {"score": 0, "sinais": ["sem_texto"], "caracteres": 0,
                "turnos": 0, "ferramentas": 0, "herdado_de": None}

    bruto = texto_de(utilizadores[-1].get("content"))
    texto = _sem_acentos(limpar_contexto_injectado(bruto)).strip()
    texto = " ".join(texto.split())
    turnos = len(utilizadores)
    janela_msgs = (mensagens or [])[-max(1, janela * 4):]
    ferramentas = sum(1 for m in janela_msgs if isinstance(m, dict)
                      and m.get("role") in ("tool", "function"))
    escrita = _ferramentas_de_escrita(mensagens, janela)

    score, sinais = _pontuar_texto(texto, p, escrita, turnos, ferramentas)
    herdado_de = None

    # Continuacao curta: herda o nivel do pedido que esta a continuar.
    if _e_continuacao(texto):
        for anterior in reversed(utilizadores[:-1][-6:]):
            texto_ant = " ".join(_sem_acentos(
                limpar_contexto_injectado(texto_de(anterior.get("content")))).split())
            if not texto_ant or _e_continuacao(texto_ant):
                continue
            score_ant, _ = _pontuar_texto(texto_ant, p, escrita, turnos, ferramentas)
            # Continuacao que manda corrigir/alterar sobre o objeto vivo do turno
            # anterior e trabalho de producao, nao manutencao fina: "audita o HA"
            # -> "corrige" e uma intervencao no sistema, com o peso de trabalho.
            alvo_vivo = bool(_MANUTENCAO.search(texto) and _OBJETO_VIVO.search(texto_ant))
            if alvo_vivo:
                score = score_ant + p["trabalho"] - p["manutencao"]
                herdado_de = texto_ant[:80]
                sinais = ["continuacao_herda",
                          "trabalho_producao=" + (_primeiro_verbo(_MANUTENCAO, texto) or "manutencao")]
            elif score_ant > score:
                score, herdado_de = score_ant, texto_ant[:80]
                sinais = ["continuacao_herda"] + [s for s in sinais if s != "sem_texto"]
            break

    return {"score": score, "sinais": sinais, "caracteres": len(texto),
            "turnos": turnos, "ferramentas": ferramentas, "herdado_de": herdado_de}


def sinais_independentes(sinais: list[str]) -> list[str]:
    """Categorias de evidencia independente de complexidade.

    Dois sinais fracos somavam um nivel pago: verificacao (2) + dominio (1) = 3
    = `media` para "testa o plugin". Contam-se categorias, nao pontos; volume e
    verbos de verificacao nao contam.
    """
    vistos: list[str] = []
    for s in sinais or []:
        bruto = str(s).strip()
        nome, _, valor = bruto.partition("=")
        nome, valor = nome.strip(), valor.strip()
        if not nome or nome in _SINAIS_AMPLITUDE or nome in _VERBOS_VERIFICACAO:
            continue
        # "analise=testa": o verbo e que decide, nao a categoria que o apanhou.
        if valor and valor in _VERBOS_VERIFICACAO:
            continue
        if nome not in vistos:
            vistos.append(nome)
    return vistos


def nivel_de_score(score: int, limiares: dict[str, Any] | None = None) -> str:
    """`score` -> nivel, com limiares configuraveis (`media`, `alta`)."""
    lim = dict(LIMIARES)
    for chave, valor in (limiares or {}).items():
        try:
            lim[chave] = int(valor)
        except (TypeError, ValueError):
            continue
    media, alta = lim["media"], lim["alta"]
    if alta <= media:  # config incoerente nao pode apagar o nivel `alta`
        alta = media + 3
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
            janela: int = 8, maximo: int = 60,
            pesos: dict[str, Any] | None = None) -> dict[str, Any]:
    """Nivel final do turno: marcador explicito > pontuacao (com heranca)."""
    explicito = marcador(mensagens, maximo)
    if explicito in NIVEIS:
        return {"nivel": explicito, "origem": "marcador", "score": None,
                "sinais": ["marcador"], "herdado_de": None}
    if explicito == "auto":
        return {"nivel": "", "origem": "reset", "score": None,
                "sinais": ["marcador_auto"], "herdado_de": None}
    ponto = pontuar(mensagens, janela, pesos)
    nivel = nivel_de_score(ponto["score"], limiares)
    # Regra dos dois sinais: `media` exige `media_sinais` categorias de evidencia
    # independente. Existe porque dois sinais fracos somavam um nivel pago.
    minimo = None
    for chave, valor in (limiares or {}).items():
        if chave == "media_sinais":
            try:
                minimo = int(valor)
            except (TypeError, ValueError):
                minimo = None
    if nivel == "media" and minimo and minimo > 0:
        if len(sinais_independentes(ponto["sinais"])) < minimo:
            nivel = "leve"
    return {"nivel": nivel, "origem": "analise",
            "score": ponto["score"], "sinais": ponto["sinais"],
            "caracteres": ponto["caracteres"], "turnos": ponto["turnos"],
            "ferramentas": ponto["ferramentas"], "herdado_de": ponto["herdado_de"]}
