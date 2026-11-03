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
import urllib.parse
import urllib.request
from typing import Any, Callable

NIVEIS = ("leve", "media", "alta")

PADRAO: dict[str, Any] = {
    "activo": False,
    "provider": "",
    "modelo": "",
    # 8 s era margem de 1,5x sobre a latencia medida e falhava em pico. Medido a
    # 2026-11-03 no deepseek-flash, mesmo prompt, mesmo `esforco`: p50 1,5-2,9 s
    # e 5,3 s sob contencao (o classificador compete com o pedido grande do
    # proprio turno no mesmo provider). 12 s da ~2,3x o p95 observado. Subir
    # mais nao e gratis: o classificador corre ANTES do pedido do turno, logo
    # este e o tempo que o utilizador espera por uma decisao opcional. Um
    # timeout suspende de imediato (ver `_falhou`), o que limita o dano a uma
    # espera de 12 s por cada 5 minutos.
    "timeout_s": 12.0,
    # 12 tokens chegavam a um modelo que responde directamente e nao chegavam a
    # um de raciocinio: medido no deepseek-flash, os 12 tokens de saida eram
    # todos `reasoning_tokens` e o `content` vinha vazio. 320 deixa pensar e
    # ainda responder — a uma classificacao de uma palavra e sobra.
    "max_tokens": 320,
    "cache_ttl_s": 600,
    "janela_mensagens": 8,
    "max_chars": 4000,
    "esforco": "",
    # Variante do prompt do classificador (`PROMPTS`). O ciclo de aprendizagem
    # compara variantes e promove a vencedora gravando esta chave.
    "prompt_variante": "v1",
    # Forma da resposta. `auto` pede `response_format: json_object` e, se o
    # provider recusar (400/422) ou devolver algo que nao e json, repete o
    # pedido sem ele — nao depende de uma lista de providers que envelhece.
    # `on` obriga, `off` nunca pede.
    "formato_json": "auto",
    # Transporte do pedido: `chat` (chat/completions, qualquer LLM), `decisoes`
    # (modelos System One, que nao geram texto — devolvem escolha e confianca
    # tipadas) ou `auto`, que tenta o chat e muda sozinho quando o provider
    # responde que o modelo e de decisoes. Medido a 2026-11-03 no
    # `~typesafe/jev-latest`: o id existe mas recusa chat/completions com "is a
    # decisions model and cannot be used with the chat/completions endpoint", e
    # aceita o mesmo pedido em `POST /api/alpha/decisions`. Com `decisoes`,
    # `max_tokens`, `esforco` e `formato_json` nao se aplicam (o modelo nao gera
    # tokens livres) — ficam na config sem efeito, para a troca de transporte
    # nao apagar os valores.
    # `auto` e o default porque apontar o classificador a um modelo que o
    # plugin nunca viu nao deve exigir saber de que tipo ele e.
    "via": "auto",
    # Piso de confianca para aceitar a decisao do modelo. Abaixo disto o turno
    # nao e adivinhado: fica `sem_efeito` com o motivo e a heuristica decide,
    # que e o comportamento de escalonamento prescrito para modelos System One.
    # 0.0 aceita tudo (comportamento identico ao de um LLM classificador).
    "confianca_min": 0.0,
    # Forma do `state` no transporte de decisoes (`ESTADOS_DECISAO`). `medidas`
    # por omissao: medido a 2026-11-03, o transcrito cru dava kappa de alta
    # 0,3604 e 127 de 129 leituras abstinham em producao; com as medidas o mesmo
    # pedido subiu a 0,5479 e a confianca deixou de viver nos 0,18.
    "estado": "medidas",
    # --- Adaptacao a modelos desconhecidos ------------------------------
    # A partir daqui esta o que faz o plugin aceitar QUALQUER modelo como
    # classificador sem o utilizador ter de conhecer as particularidades do
    # provider. Todos os campos sao opcionais e o default preserva o
    # comportamento de um LLM de chat comum.
    #
    # Preset de comportamento (`PRESETS`). Vazio = inferir pelo id do modelo
    # (ex.: `~typesafe/...` e um modelo System One); `nenhum` = nao usar preset.
    "preset": "",
    # Temperatura do pedido. `None` significa NAO ENVIAR o campo — e o que
    # modelos de raciocinio (o1/o3/gpt-5 e afins) exigem: recusam `temperature`
    # com 400. Aceita-se `omitir`/`null`/`-` na config.
    "temperatura": 0.0,
    # Nome do campo do orcamento de saida. Alguns providers so aceitam
    # `max_completion_tokens`; a maioria usa `max_tokens`.
    "max_tokens_campo": "max_tokens",
    # Caminho HTTP do chat, quando o `base_url` da rota nao segue a convencao
    # (`/v1/chat/completions`). Ex.: `/api/v1/messages`. Vazio = derivar.
    "caminho": "",
    # Caminho do endpoint de decisoes, idem. Separado do `caminho` para um
    # override de chat nunca ser aplicado ao transporte errado.
    "caminho_decisoes": "",
    # Escape hatch: entra no corpo do pedido tal e qual, depois dos defaults e
    # antes nada (ganha aos defaults, perde para `model`/`messages`, que sao
    # obrigatorios). Serve para parametros que este plugin nao conhece.
    "parametros_extra": {},
    # Cabecalhos HTTP adicionais (ex.: `HTTP-Referer` do OpenRouter).
    "headers_extra": {},
}


#: Vocabulario do transporte. `decisoes` e o nome em portugues; aceitam-se as
#: formas em ingles porque a documentacao do provider so usa essas.
VIAS = {"chat": "chat", "decisoes": "decisoes", "decisions": "decisoes",
        "decision": "decisoes", "decisão": "decisoes", "decisões": "decisoes"}

#: Criterios do `choice` no transporte de decisoes. E a rubrica dos tres niveis
#: na unica forma que o endpoint aceita: mapa opcao -> descricao (uma lista de
#: `options` e recusada).
#:
#: A rubrica curta que aqui estava custava caro, e foi medido a 2026-11-03 nos
#: 250 casos do gold: kappa de alta 0,5599 com precisao 0,49 e 26 altas falsas.
#: Com esta rubrica (tres eixos, seis contra-ancoras, regra de bolso) os mesmos
#: casos deram 0,5952 / 0,606 / 13. O que faltava era o que a rubrica do
#: avaliador ja dizia e a versao curta tinha perdido na compressao: o nivel e o
#: MAXIMO de tres eixos, e as seis armadilhas estao nomeadas uma a uma.
CRITERIOS_DECISAO: dict[str, str] = {
    "leve": "Saudacao, confirmacao, agradecimento; pedir esclarecimento; extrair um valor; "
            "pergunta de sim/nao; reformular; correr um comando ja conhecido; pergunta "
            "factual de uma linha.",
    "media": "Resumir texto longo; traduzir; redigir email; corrigir erro com sintoma claro; "
             "diagnosticar com mensagem de erro dada; alterar um ficheiro identificado; "
             "escrever um teste para funcao nomeada; atualizar ou limpar um sistema vivo; "
             "plano de tarefa concreta.",
    "alta": "Desenhar arquitetura ou decidir trade-offs; depurar sem reproducao nem mensagem "
            "de erro; migrar com rollback e compatibilidade; analisar em conjunto varios "
            "ficheiros ou servicos; otimizar um algoritmo; provar correcao; fechar um "
            "parecer com consequencias.",
}

INSTRUCOES_DECISAO = (
    "Classifica o nivel MINIMO de modelo que resolve o pedido com qualidade aceitavel. "
    "O nivel e o MAXIMO de tres eixos: raciocinio, contexto e especialidade. "
    "Contexto so promove: 24k-100k tokens sobe um nivel, acima de 100k e alta. "
    "CONTRA-ANCORAS que NAO sao complexidade: (1) avaria ou urgencia nao e complexidade; "
    "(2) jargao tecnico nao e complexidade; (3) pedir esclarecimento e leve; "
    "(4) ambito aberto ('faz tudo', 'resolve isto') NAO e alta por si; "
    "(5) comprimento do texto NAO e nivel; (6) log ou artefacto colado NAO e nivel, "
    "conta o contexto que o pedido obriga a carregar. "
    "Regra de bolso: media e 'sei o que fazer, preciso de executar bem'; alta e 'tenho de "
    "descobrir o que fazer antes de executar'."
)

#: Modos de `state` para o transporte de decisoes.
#: `medidas` (por omissao) entrega ao modelo o pedido isolado, com um bloco de
#: factos neutros, e marca o transcrito anterior como "nao e o pedido".
#: `simples` entrega o transcrito tal e qual -- fica para comparar e para voltar
#: atras por configuracao, sem reverter codigo.
ESTADOS_DECISAO = ("medidas", "simples")

#: Presets de adaptacao por familia de modelo.
#:
#: Existem para o caso que custou mais tempo a descobrir: um modelo que nao e um
#: LLM de chat comum (ou que e de raciocinio) recusa o pedido por omissao com um
#: 400 que se le como falta de credencial. O preset e uma PISTA, nao uma prisao:
#: entra por baixo dos valores declarados, portanto o que o utilizador escrever
#: no bloco ganha sempre sobre o preset.
PRESETS: dict[str, dict[str, Any]] = {
    # LLM de chat comum: os defaults do PADRAO servem.
    "padrao": {},
    # Modelos System One (TypeSafe Jev e afins): nao geram texto, devolvem
    # escolha e confianca tipadas num endpoint proprio. `max_tokens`, `esforco`
    # e `formato_json` ficam sem efeito por desenho do modelo.
    "sistema-um": {"via": "decisoes"},
    # Modelos de raciocinio: recusam `temperature` e alguns so aceitam
    # `max_completion_tokens` como nome do orcamento de saida.
    "raciocinio": {"temperatura": None, "max_tokens_campo": "max_completion_tokens"},
}

#: Prefixos de id de modelo -> preset inferido. A inferencia evita que um
#: modelo novo exija configuracao para funcionar; quem souber melhor declara
#: `preset` a mao, e `preset: nenhum` desliga a inferencia.
_PRESET_POR_ID: tuple[tuple[str, str], ...] = (
    ("~typesafe/", "sistema-um"),
    ("typesafe/", "sistema-um"),
    ("o1", "raciocinio"),
    ("o3", "raciocinio"),
    ("o4", "raciocinio"),
    ("gpt-5", "raciocinio"),
)


def preset_para(modelo: str) -> str:
    """Preset inferido do id do modelo; `padrao` quando nada casa.

    O id pode chegar com prefixo de provider (`openrouter/~typesafe/jev-latest`)
    ou a seco (`jev-latest`), por isso a comparacao e por segmento e nao por
    `startswith` do id inteiro.
    """
    alvo = (modelo or "").strip().lower()
    if not alvo:
        return "padrao"
    for prefixo, nome in _PRESET_POR_ID:
        if alvo.startswith(prefixo) or f"/{prefixo}" in alvo:
            return nome
    return "padrao"



def _medidas_pedido(texto: str) -> str:
    """Factos verificaveis sobre o pedido, sem os interpretar.

    Nao diz "ambito aberto: sim" nem "parece complexo": os dois vicios medidos
    nos juizes foram exactamente ler ambito aberto e comprimento como nivel. So
    medidas que o modelo pode cruzar com a rubrica.
    """
    blocos = texto.count("```")
    artefactos = []
    if blocos:
        artefactos.append(f"{blocos // 2} bloco(s) de codigo")
    if any(s in texto for s in ("Traceback", "ERROR", "Exception", "error code",
                               "stack trace", "at line", "log:")):
        artefactos.append("stack trace / log de erro")
    if texto.lstrip().startswith(("{", "[")):
        artefactos.append("JSON ou lista colada")
    return (
        "[MEDIDAS DO PEDIDO]\n"
        f"caracteres: {len(texto)}\n"
        f"linhas: {texto.count(chr(10)) + 1}\n"
        f"tokens estimados: {len(texto) // 4}\n"
        f"artefactos colados: {', '.join(artefactos) if artefactos else 'nenhum'}\n"
        "[/MEDIDAS DO PEDIDO]\n\n"
    )


def _estado_decisoes(corpo_texto: str, modo: str = "medidas") -> str:
    """`state` do endpoint de decisoes.

    Medido a 2026-11-03, 250 casos, e a razao de ser desta funcao: o transcrito
    que o plugin enviava (sete mensagens anteriores de outros assuntos + o
    pedido) dava kappa de alta 0,3604 e precisao 0,339 -- e em producao 127 de
    129 leituras abstiveram por confianca baixa, com p50 0,18. As mesmas
    mensagens com o bloco de medidas deram 0,5479 / 0,525, e com o transcrito
    anterior marcado como contexto (nao como pedido) 0,5965.

    O modelo julga o pedido; o transcrito anterior fica como contexto curto e
    rotulado, para continuacoes ("continua", "faz isso") nao perderem o sentido.
    """
    if modo not in ESTADOS_DECISAO:
        modo = "medidas"
    if modo == "simples":
        return corpo_texto
    marca = "[pedido] "
    corte = corpo_texto.rfind(marca)
    if corte < 0:
        return _medidas_pedido(corpo_texto) + "[PEDIDO A CLASSIFICAR]\n" + corpo_texto
    anterior, pedido = corpo_texto[:corte].strip(), corpo_texto[corte + len(marca):]
    partes = [_medidas_pedido(pedido)]
    if anterior:
        # Duas anteriores no maximo, cortadas a 300 chars. Medido: sete
        # anteriores inteiras (a forma que o `texto()` produz com a janela de 8)
        # davam kappa de alta 0,5479; tres pares curtos davam 0,6528. O contexto
        # serve para "continua" e "faz isso" -- para isso chegam duas.
        linhas = [ln for ln in anterior.splitlines() if ln.startswith("[anterior] ")]
        linhas = [ln[:300] for ln in linhas[-2:]]
        if linhas:
            partes.append(
                "[CONTEXTO ANTERIOR, NAO E O PEDIDO A CLASSIFICAR]\n"
                + "\n".join(linhas)
                + "\n[/CONTEXTO ANTERIOR]\n\n"
            )
    partes.append("[PEDIDO A CLASSIFICAR]\n" + pedido)
    return "".join(partes)


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
# Mas 30 s e 1024 tokens nao chegam a um modelo que raciocina antes de escrever:
# medido a 2026-11-03, o juiz do ciclo (`deepseek-v4-pro`, esforco baixo) devolveu
# 1182 tokens — o teto — sem nivel reconhecivel, e no lote seguinte 71 de 90
# casos morreram em timeout aos 30 s. O teto tem de caber o raciocinio mais a
# resposta; quem decide o valor de cada uso e a config, nao o esquema.
_TIMEOUT_MIN, _TIMEOUT_MAX = 1.0, 180.0
_MAX_TOKENS_MIN, _MAX_TOKENS_MAX = 2, 4096
# Orcamento por omissao ate 3.5. Ficou gravado em configs antigas e trunca
# modelos de raciocinio (os 12 tokens gastam-se a pensar e o `content` vem
# vazio). 12 nao era alcancavel por nenhuma interface — so como omissao — logo
# um 12 gravado e a omissao antiga, e migra-se para o novo valor.
_MAX_TOKENS_ANTIGO = 12
_FALHAS_PARA_SUSPENDER = 3
_SUSPENSAO_S = 300.0
#: Campos da definicao que mudam o pedido. A impressao serve para a suspensao
#: por config errada caducar assim que a config e corrigida: sem isto, corrigir
#: `via` as 21:40 e continuar na heuristica ate as 22:35 parece o plugin mudo.
_CAMPOS_IMPRESSAO = ("provider", "modelo", "via", "preset", "caminho",
                     "caminho_decisoes", "estado", "formato_json", "esforco",
                     "confianca_min", "prompt_variante")
#: Um provider que nao existe no perfil servido nao e uma falha transitoria: e
#: config errada, e repetir nao a cura. Medido a 2026-11-03: o perfil `pessoal`
#: herdava um classificador global que nomeava o provider `inception`, que so
#: esta declarado em `default` e `trabalho`; cada pedido gastava tres tentativas
#: e escrevia um `AuthError` no cartao, 62 eventos em 82 s. Suspender por uma
#: hora (e nao 300 s) faz o erro aparecer uma vez e calar-se ate alguem corrigir
#: a config — que e a accao que o resolve.
_SUSPENSAO_INDISPONIVEL_S = 3600.0
#: Como se reconhece um provider indisponivel na excepcao que o core levanta.
#: O core embrulha o `AuthError` num `RuntimeError`, logo o tipo nao serve:
#: decide-se pela mensagem, que e o unico sinal que atravessa a camada.
_RE_INDISPONIVEL = re.compile(
    r"unknown provider|no such provider|provider .{0,40}not (?:found|configured|available)"
    r"|AuthError|no credential|sem credencial"
    # Config errada do lado do modelo. Medido a 2026-11-03: `typesafe/jev-latest`
    # sem til devolveu HTTP 400 "is not a valid model ID" e o plugin tratou-o
    # como avaria transitoria — tres tentativas e um aviso no cartao para um id
    # que nao existe. E depois, com o id certo, "is a decisions model and cannot
    # be used with the chat/completions endpoint": o modelo existe e o transporte
    # e que esta errado. As duas sao config, nao rede.
    r"|not a valid model|is a decisions model|cannot be used with the chat"
    r"|model .{0,60}not found|unknown model", re.I)

#: Piso de `max_tokens` quando o classificador declara esforco: o raciocinio sai
#: do mesmo orcamento e 320 tokens podem ser todos gastos a pensar. 1200 tambem
#: nao chegava: medido a 2026-11-03, `deepseek-v4-pro` gastou os 1200 a pensar e
#: devolveu um corpo sem nivel reconhecivel (o cartao do perfil mostrou
#: "ValueError: resposta sem nivel reconhecivel (length 1182tok)").
_MAX_TOKENS_COM_ESFORCO = 3000

PROMPT = (
    "Classificas a complexidade de um pedido feito a um agente de engenharia.\n"
    "Responde APENAS com um objecto json valido, sem texto fora dele e sem explicar:\n"
    '{"nivel": "<leve|media|alta>"}\n'
    "leve - conversa, duvida simples, listar, confirmar, uma pergunta directa;\n"
    "media - explicar um conceito, comparar duas opcoes, rever algo pequeno, "
    "um comando com contexto;\n"
    "alta - desenhar ou decidir arquitetura, diagnosticar avaria com evidencia, "
    "implementar ou refactorizar codigo, plano com janela e rollback, auditoria.\n"
    "Classifica o trabalho pedido, nunca a forma: registo informal, giria, "
    "abreviaturas ou erros ortograficos nao mudam o nivel."
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

#: Variantes do prompt do classificador, para o ciclo de aprendizagem poder
#: comparar e promover sem alterar codigo. `v1` e o prompt original; `v2` e a
#: rubrica com exemplos por classe; `v3` acrescenta a `v2` as contra-ancoras
#: que ja se mediu falharem (avaria nao e complexidade, pedir esclarecimento e
#: `leve`). A escolha vive na config (`analise.classificador.prompt_variante`).
PROMPT_V2 = (
    "Classificas a complexidade de um pedido feito a um agente de engenharia.\n"
    "Responde APENAS com um objecto json valido, sem texto fora dele e sem explicar:\n"
    '{"nivel": "<leve|media|alta>"}\n'
    "O nivel e o modelo MAIS BARATO que resolve o pedido bem: nao e quao dificil "
    "o texto parece nem o trabalho que deu responder.\n"
    "leve - conversa, confirmacao, agradecimento, pergunta directa de uma linha, "
    "extrair um valor, sim/nao, pedir esclarecimento.\n"
    "media - resumir ou traduzir texto, redigir, corrigir com sintoma claro, "
    "alterar um ficheiro identificado, escrever um teste para uma funcao nomeada, "
    "manter um sistema vivo, plano de uma tarefa concreta, explicar um conceito.\n"
    "alta - desenhar arquitetura ou decidir trade-offs, depurar sem reproducao nem "
    "mensagem de erro, migrar com rollback, analisar varios ficheiros ou servicos "
    "em conjunto, otimizar um algoritmo, provar correcao, auditoria de sistema.\n"
    "Exemplos de `media`, que e a classe mais frequente e a mais confundida:\n"
    "- \"o cert do nginx expirou ontem, renova pf\" -> media\n"
    "- \"escreve um teste para a funcao de normalizar slugs\" -> media\n"
    "- \"explica-me o que e um reverse proxy\" -> media\n"
    "Exemplos de `leve` e `alta` para contraste:\n"
    "- \"bom dia, ja esta?\" -> leve;  \"isso nao me parece bem\" -> leve\n"
    "- \"desenha a arquitetura do barramento e justifica o trade-off\" -> alta\n"
    "Classifica o trabalho pedido, nunca a forma: registo informal, giria, "
    "abreviaturas ou erros ortograficos nao mudam o nivel."
)

PROMPT_V3 = PROMPT_V2 + (
    "\nContra-ancoras (medidas em casos reais que falharam):\n"
    "- algo ter avariado, estar urgente ou o utilizador estar irritado NAO sobe o "
    "nivel; o que conta e o trabalho pedido;\n"
    "- pedir esclarecimento ou dizer que falta informacao e `leve`;\n"
    "- um pedido curto pode ser `alta` e um pedido longo pode ser `leve`.\n"
    "A mesma intencao escrita de outra maneira tem de dar o mesmo nivel."
)

PROMPTS: dict[str, str] = {"v1": PROMPT, "v2": PROMPT_V2, "v3": PROMPT_V3}


def _prompt_variante(definicao: dict[str, Any] | None) -> str:
    nome = str((definicao or {}).get("prompt_variante") or "v1")
    return PROMPTS.get(nome, PROMPT)


_cache: dict[str, tuple[float, dict[str, Any]]] = {}
# Cache negativo: a mesma mensagem gera varios `llm_request` no mesmo turno (um
# por volta de ferramentas. Sem isto, uma falha do modelo repete a chamada 2-3
# vezes — medido a 2026-11-02: 19 falhas em rajadas de 2-3 no mesmo segundo.
_cache_erro: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_ERRO_S = 60.0
_falhas: dict[str, int] = {}
_suspensos: dict[str, float] = {}
#: Impressao da definicao no momento em que o alvo foi suspenso por config
#: errada. Guardar isto (em vez de mudar a chave) mantem `estado()` legivel e
#: deixa a suspensao caducar sozinha quando a config muda.
_suspenso_impressao: dict[str, str] = {}
#: Transporte descoberto por alvo (`perfil\x1fprovider\x1fmodelo`) quando
#: `via: auto`. Vive so em memoria de proposito: e uma descoberta sobre o
#: provider, nao configuracao do utilizador, e o custo de a repetir e uma
#: chamada falhada uma vez por processo — escreve-la na config confundiria o
#: que o dono declarou com o que o plugin aprendeu.
_via_detectada: dict[str, str] = {}
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
    # O preset entra ANTES de tudo e por baixo: um modelo System One ou de
    # raciocinio recusa o pedido por omissao com um 400 que se le como falta de
    # credencial, e quem configura "qualquer modelo" nao tem de saber disso. O
    # que o utilizador declarou ganha sempre sobre o preset, porque o preset e
    # o primeiro dicionario e o bruto o segundo.
    pedido_preset = str(bruto.get("preset") or "").strip().lower()
    if pedido_preset in ("nenhum", "off", "nao", "não", "-"):
        nome_preset = ""
    elif pedido_preset:
        if pedido_preset not in PRESETS:
            erros.append(f"{prefixo}: preset deve ser " + ", ".join(sorted(PRESETS))
                         + f" (recebido {pedido_preset!r})")
            nome_preset = "padrao"
        else:
            nome_preset = pedido_preset
    else:
        nome_preset = preset_para(str(bruto.get("modelo") or ""))
    if nome_preset:
        bruto = {**PRESETS[nome_preset], **bruto}
    saida["preset"] = nome_preset
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
    # Variante do prompt: validada contra o vocabulario real (`PROMPTS`), para
    # que um nome invalido nao caia em silencio no prompt v1.
    variante = str(bruto.get("prompt_variante") or "v1").strip().lower()
    if variante not in PROMPTS:
        erros.append(f"{prefixo}: prompt_variante deve ser "
                     + ", ".join(sorted(PROMPTS)) + f" (recebido {variante!r})")
        variante = "v1"
    saida["prompt_variante"] = variante
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
    # Transporte. Um valor desconhecido nao pode cair em silencio no `chat`:
    # quem escreveu `decisoes` e estaria a falar com o endpoint errado.
    # `auto` tenta o chat e, quando o provider responde "is a decisions model",
    # muda de transporte sozinho — e o que permite apontar o classificador a um
    # modelo que o plugin nunca viu sem declarar `via`.
    bruto_via = str(bruto.get("via") or PADRAO["via"]).strip().lower()
    if bruto_via == "auto":
        saida["via"] = "auto"
    elif bruto_via in VIAS:
        saida["via"] = VIAS[bruto_via]
    elif bruto_via == "":
        saida["via"] = "auto"
    else:
        erros.append(f"{prefixo}: via deve ser auto, "
                     + ", ".join(sorted(set(VIAS.values())))
                     + f" (recebido {bruto_via!r})")
        saida["via"] = "chat"

    saida["confianca_min"] = _num(bruto.get("confianca_min"), 0.0, 1.0,
                                  float(PADRAO["confianca_min"]))
    # Forma do `state` no transporte de decisoes. `medidas` isola o pedido e
    # entrega factos neutros; `simples` manda o transcrito como esta. Um valor
    # desconhecido nao cai em silencio: quem pediu `simples` para comparar tem
    # de saber que nao foi aceite.
    bruto_estado = str(bruto.get("estado") or "medidas").strip().lower()
    if bruto_estado in ESTADOS_DECISAO:
        saida["estado"] = bruto_estado
    else:
        erros.append(f"{prefixo}: estado deve ser " + ", ".join(ESTADOS_DECISAO)
                     + f" (recebido {bruto_estado!r})")
        saida["estado"] = "medidas"
    # --- Adaptacao ao modelo: nada aqui e obrigatorio ---------------------
    # Temperatura. Um valor numerico vai no corpo; `omitir`/`null`/`-` faz o
    # campo NAO ser enviado, que e o que os modelos de raciocinio exigem (um
    # `temperature` num o1/o3 e um 400). O preset `raciocinio` ja poe isto.
    bruto_temp = bruto.get("temperatura", 0.0)
    if bruto_temp is None or (isinstance(bruto_temp, str)
                              and bruto_temp.strip().lower() in ("omitir", "omit", "null",
                                                                 "none", "nao", "não", "-", "")):
        saida["temperatura"] = None
    else:
        saida["temperatura"] = _num(bruto_temp, 0.0, 2.0, 0.0)
    # Nome do campo do orcamento de saida: a maioria dos providers usa
    # `max_tokens`, os mais recentes da OpenAI usam `max_completion_tokens`.
    bruto_campo = str(bruto.get("max_tokens_campo") or "max_tokens").strip().lower()
    if bruto_campo in ("max_tokens", "max_completion_tokens"):
        saida["max_tokens_campo"] = bruto_campo
    else:
        erros.append(f"{prefixo}: max_tokens_campo deve ser max_tokens ou "
                     f"max_completion_tokens (recebido {bruto_campo!r})")
        saida["max_tokens_campo"] = "max_tokens"
    # Caminho HTTP alternativo (ex.: `/api/v1/messages`). Vazio = derivar do
    # `base_url` da rota, como sempre. Um caminho comeca por `/` e substitui o
    # caminho inteiro da base, preservando esquema e host.
    for chave in ("caminho", "caminho_decisoes"):
        caminho = str(bruto.get(chave) or "").strip()
        if caminho and not caminho.startswith("/"):
            erros.append(f"{prefixo}: {chave} deve comecar por '/' (recebido {caminho!r})")
            caminho = ""
        saida[chave] = caminho
    # Parametros e cabecalhos extra: escape hatch para o que este plugin nao
    # conhece. Recusar um tipo errado aqui e melhor do que o deixar chegar ao
    # corpo do pedido e falhar com um 400 do provider que ninguem relaciona
    # com a config.
    for chave in ("parametros_extra", "headers_extra"):
        valor = bruto.get(chave)
        if valor is None:
            saida[chave] = {}
        elif isinstance(valor, dict):
            saida[chave] = {str(k): v for k, v in valor.items()}
        else:
            erros.append(f"{prefixo}: {chave} deve ser um objecto")
            saida[chave] = {}
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


def _impressao(definicao: dict[str, Any]) -> str:
    """Impressao dos campos que mudam o pedido — nao do texto a classificar."""
    h = hashlib.sha256()
    for campo in _CAMPOS_IMPRESSAO:
        h.update(f"{campo}={definicao.get(campo)!r}\x1f".encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _suspenso(base: dict[str, Any], restante: float) -> dict[str, Any]:
    """Alvo suspenso, dito em vez de calado.

    Devolver `None` aqui (como era) fazia o cartao dizer so "heuristica": o
    utilizador corrigia a config e nao via efeito nenhum durante uma hora. O
    turno segue pela heuristica na mesma — mas o ledger passa a dizer porquê.
    """
    return {
        "sem_efeito": "classificador_suspenso",
        "motivo": (f"classificador_suspenso: o classificador falhou e esta suspenso "
                   f"por mais {restante:.0f}s — os turnos seguem pela heuristica; "
                   f"mudar a config volta a tentar de imediato"),
        "classificador": {**base, "suspenso": True, "restante_s": round(restante, 1)},
        "origem": "modelo_suspenso",
    }


# --------------------------------------------------------------------------- transporte


def _origem(base: str) -> str:
    """Esquema + host (+ porta) de um URL, sem o caminho."""
    partes = urllib.parse.urlsplit(base)
    if partes.scheme and partes.netloc:
        return f"{partes.scheme}://{partes.netloc}"
    return ""


def _url(base_url: str, definicao: dict[str, Any] | None = None) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("rota sem base_url")
    # Override declarado pelo utilizador: um endpoint que nao segue a convencao
    # (`/api/v1/messages`, `/openai/v1/chat/completions`). Substitui o caminho
    # inteiro e preserva esquema e host, para a credencial da rota continuar a
    # valer para o mesmo destino.
    caminho = str((definicao or {}).get("caminho") or "").strip()
    if caminho:
        origem = _origem(base)
        return f"{origem}{caminho}" if origem else f"{base}{caminho}"
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1") or base.endswith("/beta"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _url_decisoes(base_url: str, definicao: dict[str, Any] | None = None) -> str:
    """Endpoint de decisoes a partir do `base_url` da rota.

    Medido a 2026-11-03: o OpenRouter expoe as decisoes em
    `https://openrouter.ai/api/alpha/decisions`, ou seja o mesmo prefixo do
    chat sem o `/v1`. Derivar do `base_url` em vez de o fixar mantem a rota a
    funcionar com um gateway compativel (proxy interno, AIHubMix) que publique
    o mesmo caminho — e falha com mensagem clara quando a base nao serve.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("rota sem base_url")
    caminho = str((definicao or {}).get("caminho_decisoes") or "").strip()
    if caminho:
        origem = _origem(base)
        return f"{origem}{caminho}" if origem else f"{base}{caminho}"
    if base.endswith("/alpha/decisions"):
        return base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    for sufixo in ("/v1", "/beta"):
        if base.endswith(sufixo):
            base = base[: -len(sufixo)]
            break
    return f"{base}/alpha/decisions"



#: Sinais de que o modelo recusa o transporte de chat por ser um modelo de
#: decisoes. Medido a 2026-11-03 no `~typesafe/jev-latest` via OpenRouter:
#: `HTTP 400`, "... is a decisions model and cannot be used with the
#: chat/completions endpoint. Use the /api/alpha/decisions endpoint instead."
#: So a direccao chat->decisoes e observada na pratica: um modelo de chat nao
#: recusa o endpoint de decisoes com um aviso, simplesmente nao o tem (404).
_RE_MODELO_DECISOES = re.compile(
    r"decisions?\s+model|decisions?\s+endpoint|/api/alpha/decisions", re.I)

#: Sinais de que o provider recusou um campo de amostragem. Modelos de
#: raciocinio devolvem 400 "Unsupported value: 'temperature'" / "'top_p'"; sem
#: esta degradacao, apontar o classificador a um deles exigia conhecer o preset.
_RE_AMOSTRAGEM = re.compile(r"temperature|top_p", re.I)

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
    cabecalhos = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {rota.get('api_key', '')}",
        # O UA por omissao do urllib e recusado por varias bordas (1010).
        "User-Agent": "vipernet-model-manager/classificador",
    }
    # Cabecalhos que o provider exige e este plugin nao conhece (ex.: o
    # `HTTP-Referer`/`X-Title` do OpenRouter para ranking, ou um header de
    # versao de API). Entram depois dos defaults, porque um pedido autenticado
    # sem o header da versao certa e um 400 indistinguivel de falta de acesso.
    for chave, valor in (definicao.get("headers_extra") or {}).items():
        cabecalhos[str(chave)] = str(valor)
    req = urllib.request.Request(
        _url(rota.get("base_url", ""), definicao),
        data=dados,
        method="POST",
        headers=cabecalhos,
    )
    inicio = time.monotonic()
    with urllib.request.urlopen(req, timeout=float(definicao["timeout_s"])) as resp:  # noqa: S310
        bruto = resp.read(200_000)
    ms = round((time.monotonic() - inicio) * 1000, 1)
    try:
        return {"json": json.loads(bruto.decode("utf-8", "replace")), "ms": ms}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"resposta nao e JSON ({type(exc).__name__})") from exc


def _pedir_decisoes(rota: dict[str, Any], definicao: dict[str, Any],
                    corpo_texto: str) -> dict[str, Any]:
    """Pedido ao endpoint de decisoes (modelos System One).

    O contrato nao tem nada a ver com `chat/completions`: nao ha mensagens nem
    `max_tokens`, ha `state` (o texto observado) e `questions` (perguntas
    tipadas). Medido a 2026-11-03 no `~typesafe/jev-latest`:
    `{"model", "state", "questions": {"nivel": {"type": "choice", "criteria":
    {opcao: descricao}, "instructions": "..."}}}` devolve
    `{"answers": {"nivel": {"choice": ..., "confidence": ..., "probabilities":
    {opcao: p}}}, "usage": {"cost": ...}, "model": "<id resolvido>"}`.

    O `criteria` e um mapa e nao uma lista de opcoes — testado: uma lista e
    recusada. A chave `nivel` e o nome que o resto do plugin ja le.
    """
    corpo = {
        "model": str(definicao["modelo"]),
        "state": _estado_decisoes(corpo_texto, str(definicao.get("estado") or "medidas")),
        "questions": {
            "nivel": {
                "type": "choice",
                "criteria": dict(CRITERIOS_DECISAO),
                "instructions": INSTRUCOES_DECISAO,
            }
        },
    }
    dados = json.dumps(corpo).encode("utf-8")
    cabecalhos = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {rota.get('api_key', '')}",
        "User-Agent": "vipernet-model-manager/classificador",
    }
    for chave, valor in (definicao.get("headers_extra") or {}).items():
        cabecalhos[str(chave)] = str(valor)
    req = urllib.request.Request(
        _url_decisoes(rota.get("base_url", ""), definicao),
        data=dados,
        method="POST",
        headers=cabecalhos,
    )
    inicio = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=float(definicao["timeout_s"])) as resp:  # noqa: S310
            bruto = resp.read(200_000)
    except urllib.error.HTTPError as exc:
        # Sem esta conversao o corpo do erro perde-se e a unica coisa que chega
        # ao filtro de config e "HTTP Error 400: Bad Request" — foi assim que o
        # "is not a valid model ID" ficou sem diagnostico.
        codigo, detalhe = _ler_http(exc)
        raise _RecusaHttp(codigo, detalhe) from None
    ms = round((time.monotonic() - inicio) * 1000, 1)
    try:
        return {"json": json.loads(bruto.decode("utf-8", "replace")), "ms": ms,
                "formato_pedido": "decisoes", "estado_chars": len(corpo["state"]),
                "estado_modo": str(definicao.get("estado") or "medidas")}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"resposta nao e JSON ({type(exc).__name__})") from exc


def _extrair_decisao(payload: dict[str, Any]) -> tuple[str, str, float | None, dict[str, Any]]:
    """(nivel, lido, confianca, probabilidades) da resposta de decisoes.

    Levanta `ValueError` quando nao ha escolha reconhecivel: o chamador trata
    isso como leitura falhada (e repete ou cai na heuristica), nunca como nivel
    inventado. A confianca e devolvida como veio — comparar com
    `confianca_min` e decisao do chamador, nao desta funcao.
    """
    if not isinstance(payload, dict):
        raise ValueError("resposta de decisoes nao e um objecto")
    respostas = payload.get("answers")
    if isinstance(respostas, list):  # tolera a forma por posicao
        respostas = respostas[0] if respostas else None
    if not isinstance(respostas, dict):
        raise ValueError("resposta de decisoes sem answers")
    escolha = respostas.get("nivel")
    if not isinstance(escolha, dict):
        # Alguns proxies devolvem `{"nivel": "alta"}` directamente.
        if isinstance(escolha, str):
            canonico = _canonico(escolha)
            if canonico in NIVEIS:
                return canonico, escolha.strip(), None, {}
        raise ValueError("resposta de decisoes sem escolha em answers.nivel")
    valor = escolha.get("choice") or escolha.get("value")
    if valor is None:
        raise ValueError("resposta de decisoes sem choice")
    lido = str(valor).strip()
    canonico = _canonico(lido)
    if canonico not in NIVEIS:
        raise ValueError(f"nivel nao reconhecido: {lido[:40]!r}")
    bruto_conf = escolha.get("confidence")
    confianca: float | None = None
    if isinstance(bruto_conf, (int, float)) and not isinstance(bruto_conf, bool):
        confianca = float(bruto_conf)
    elif isinstance(bruto_conf, str):
        try:
            confianca = float(bruto_conf)
        except ValueError:
            confianca = None
    if confianca is not None and not (0.0 <= confianca <= 1.0):
        confianca = None
    probabilidades = escolha.get("probabilities")
    probabilidades = probabilidades if isinstance(probabilidades, dict) else {}
    return canonico, lido, confianca, {k: v for k, v in probabilidades.items()
                                       if _canonico(str(k)) in NIVEIS}


def _classificar_decisoes(*, alvo: str, base: dict[str, Any], definicao: dict[str, Any],
                          corpo_texto: str, resolver_rota: Callable[[str, str], dict[str, Any]],
                          chave: str, ttl_erro: float, agora: float) -> dict[str, Any]:
    """Classificacao por transporte de decisoes, com escalonamento por confianca.

    Duas diferencas de fundo para o caminho do chat: nao ha repeticao (o modelo
    nao gera texto, logo nao ha resposta truncada a segunda tentativa — um erro
    aqui e erro) e a confianca e um campo de primeira classe. Abaixo de
    `confianca_min` o turno fica `sem_efeito` e a heuristica decide, que e o
    padrao prescrito para System One: agir so quando a confianca e alta e
    escalar o resto, em vez de aceitar uma escolha quase empatada.
    """
    provider = base["provider"]
    modelo = base["modelo"]
    perfil = base["perfil"]
    piso = float(definicao.get("confianca_min") or 0.0)
    rota: dict[str, Any] = {}
    try:
        rota = resolver_rota(provider, modelo)
        resposta = _pedir_decisoes(rota, definicao, corpo_texto)
        nivel, lido, confianca, probabilidades = _extrair_decisao(resposta["json"])
    except _RecusaHttp as exc:
        detalhe = f"HTTP {exc.codigo}: {exc.detalhe[:200]}"
        if _RE_INDISPONIVEL.search(detalhe):
            return _indisponivel(alvo, base, detalhe, chave=chave, ttl_erro=ttl_erro,
                                 definicao=definicao)
        return _falhou(alvo, base, f"HTTP {exc.codigo}", exc.detalhe[:200],
                       chave=chave, ttl_erro=ttl_erro)
    except Exception as exc:  # noqa: BLE001 - fail-open sempre
        detalhe = str(exc)[:200]
        if _RE_INDISPONIVEL.search(f"{type(exc).__name__}: {detalhe}"):
            return _indisponivel(alvo, base, detalhe, chave=chave, ttl_erro=ttl_erro,
                                 definicao=definicao)
        return _falhou(alvo, base, type(exc).__name__, detalhe,
                       chave=chave, ttl_erro=ttl_erro)
    payload = resposta["json"] if isinstance(resposta["json"], dict) else {}
    uso = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    classif = {
        **base,
        "via": "decisoes",
        "fonte_credencial": rota.get("source", ""),
        "ms": resposta["ms"],
        "cache": False,
        "lido": lido,
        "confianca": confianca,
        "probabilidades": probabilidades,
        "modelo_resolvido": str(payload.get("model") or ""),
        "custo_usd": uso.get("cost"),
        "formato_pedido": "decisoes",
        "tokens_entrada": uso.get("input_tokens"),
        "tokens_saida": uso.get("output_tokens"),
        # Tamanho e modo do `state` que foi mesmo enviado. Sem estes dois
        # numeros no ledger, uma mudanca de forma mede-se pelo `tokens_entrada`
        # e confunde-se com a rubrica: foi o que aconteceu a 2026-11-03.
        "estado_chars": resposta.get("estado_chars"),
        "estado_modo": resposta.get("estado_modo"),
        "preset": str(definicao.get("preset") or ""),
        "tentativas": 1,
    }
    if confianca is not None and confianca < piso:
        # Abstenção deliberada, e de propósito não guardada em cache negativa:
        # o cartão lê `cache_erro` como leitura falhada, e isto não é uma falha
        # — é o modelo a dizer que não sabe o suficiente para decidir sozinho.
        return {
            "sem_efeito": "confianca_baixa",
            "motivo": f"confianca_baixa: {confianca:.2f} < piso {piso:.2f} "
                      f"(o modelo escolheu {nivel})",
            "nivel_proposto": nivel,
            "classificador": {**classif, "abstencao": "confianca_baixa"},
            "origem": "modelo_abstencao",
        }
    saida = {"nivel": nivel, "origem": "modelo", "classificador": classif}
    with _lock:
        _falhas.pop(alvo, None)
        if int(definicao["cache_ttl_s"]) > 0:
            _cache[chave] = (agora + int(definicao["cache_ttl_s"]), saida)
            if len(_cache) > 500:
                _cache.clear()
    return saida


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
            {"role": "system", "content": prompt or _prompt_variante(definicao)},
            {"role": "user", "content": corpo_texto},
        ],
        # O nome do campo de orcamento nao e universal: os modelos recentes da
        # OpenAI so aceitam `max_completion_tokens` e recusam `max_tokens`.
        str(definicao.get("max_tokens_campo") or "max_tokens"):
            int(definicao["max_tokens"]) if orcamento is None else int(orcamento),
        "stream": False,
    }
    # `temperature` e `top_p` so entram quando ha temperatura declarada: um
    # modelo de raciocinio recusa-os com 400 e o preset `raciocinio` poe
    # `temperatura: None` exactamente para os omitir.
    temperatura = definicao.get("temperatura", 0.0)
    if temperatura is not None:
        corpo["temperature"] = float(temperatura)
        corpo["top_p"] = 1
    if formato_json is None:
        formato_json = _quer_json(definicao)
    if formato_json:
        corpo["response_format"] = {"type": "json_object"}
    extra_e, topo_e = _gestor().extras_esforco(provider, modelo, esforco_efectivo, session_id=perfil)
    corpo.update(extra_e)
    corpo.update(topo_e)
    campos_orcamento = ("max_tokens", "max_completion_tokens")
    for campo in campos_orcamento:
        if campo in corpo and int(corpo[campo]) < _MAX_TOKENS_COM_ESFORCO:
            if extra_e or topo_e:
                corpo[campo] = _MAX_TOKENS_COM_ESFORCO
    # Escape hatch por ultimo: o que o utilizador escreveu em `parametros_extra`
    # entra tal e qual. `model` e `messages` ficam de fora — trocar o modelo ou
    # o texto por configuracao nao e adaptacao, e sim perda de rasto do que foi
    # pedido.
    for chave, valor in (definicao.get("parametros_extra") or {}).items():
        if chave in ("model", "messages"):
            continue
        corpo[chave] = valor
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
    # Copia local: a degradacao abaixo mexe na definicao (tira `temperatura`), e
    # a definicao do chamador e partilhada — suja-la faria o proximo turno
    # nascer sem temperatura sem ninguem ter decidido isso.
    local = dict(definicao)
    while True:
        corpo = _corpo(provider, modelo, corpo_texto, local, perfil=perfil,
                       orcamento=orcamento, esforco=esforco, formato_json=pedir_json,
                       prompt=prompt)
        try:
            resposta = _pedir(rota, local, corpo)
        except urllib.error.HTTPError as exc:
            codigo, detalhe = _ler_http(exc)
            # Recusa de transporte primeiro: um 400 "is a decisions model" nao
            # se resolve a tirar `response_format`, e a degradacao de formato
            # consumia a resposta e escondia a causa — o segundo pedido ia ao
            # endpoint de chat outra vez. Este erro e definitivo para o
            # transporte, nao para o corpo do pedido.
            if _RE_MODELO_DECISOES.search(detalhe or ""):
                raise _RecusaHttp(codigo, detalhe) from None
            if pedir_json and codigo in _HTTP_SEM_FORMATO:
                pedir_json = False
                continue
            # Modelo de raciocinio que recusa os campos de amostragem: retira-os
            # e repete uma vez, em vez de exigir que quem configura saiba de
            # antemao qual e o modelo. Cada degradacao so acontece uma vez.
            if (local.get("temperatura") is not None and codigo in _HTTP_SEM_FORMATO
                    and _RE_AMOSTRAGEM.search(detalhe or "")):
                local["temperatura"] = None
                continue
            raise _RecusaHttp(codigo, detalhe) from None
        return {**resposta, "formato_pedido": "json" if pedir_json else "texto"}


def classificar(
    mensagens: list[Any],
    definicao: dict[str, Any] | None,
    resolver_rota: Callable[[str, str], dict[str, Any]],
    *,
    perfil: str = "",
    prompt: str | None = None,
) -> dict[str, Any] | None:
    """Nivel decidido pelo modelo, ou `None` para o chamador usar a heuristica.

    `resolver_rota(provider, modelo)` e injectado: o plugin passa o seu
    `resolver_rota(perfil, provider, modelo)`, que resolve a credencial no
    escopo do perfil servido. Assim este modulo nao sabe nada de segredos.

    `prompt` substitui o prompt de sistema (por omissao, a variante declarada em
    `definicao["prompt_variante"]`). Existe para o ciclo de aprendizagem poder
    avaliar um juiz com uma rubrica diferente da do avaliado — um juiz com o
    mesmo texto do classificador mede a concordancia do modelo consigo mesmo.
    """
    if not definicao:
        return None
    provider = str(definicao.get("provider") or "").strip()
    modelo = str(definicao.get("modelo") or "").strip()
    if not (provider and modelo):
        return None
    alvo = f"{perfil}\x1f{provider.lower()}\x1f{modelo}"
    base = {"provider": provider, "modelo": modelo, "perfil": perfil}
    agora = time.monotonic()
    # A suspensao vale para a definicao que falhou, nao para o alvo em abstracto:
    # corrigir `via`, o preset ou o endpoint tem de voltar a tentar ja. Uma
    # suspensao de 1 h que sobrevive a correccao transforma "ja corrigi" em
    # "continua na heuristica" e o utilizador nao tem como o distinguir.
    with _lock:
        fim = _suspensos.get(alvo, 0.0)
        impressao_suspensa = _suspenso_impressao.get(alvo)
    if fim > agora and (impressao_suspensa is None
                        or impressao_suspensa == _impressao(definicao)):
        return _suspenso(base, fim - agora)
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
    # O cache negativo segue o cache da definicao: com `cache_ttl_s: 0` o
    # utilizador pediu para nao guardar nada — nem sucessos nem falhas.
    ttl_erro = min(_TTL_ERRO_S, float(definicao["cache_ttl_s"]))
    # Transporte de decisoes: contrato proprio, resposta tipada, e nenhuma das
    # alavancas do chat (mensagens, response_format, max_tokens, effort). Fica
    # num caminho separado para o codigo do chat nao ganhar ramos mortos.
    # Com `auto`, parte-se do chat e so se muda quando o provider diz que o
    # modelo e de decisoes; a descoberta fica memorizada por alvo.
    via = str(definicao.get("via") or "auto")
    if via == "auto":
        with _lock:
            via = _via_detectada.get(alvo, "chat")
    if via == "decisoes":
        return _classificar_decisoes(
            alvo=alvo, base=base, definicao=definicao, corpo_texto=corpo_texto,
            resolver_rota=resolver_rota, chave=chave, ttl_erro=ttl_erro, agora=agora,
        )
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
                                          esforco=esforco, formato_json=formato_json,
                                          prompt=prompt)
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
                # A partir do orcamento que foi mesmo enviado, nao do declarado:
                # com o piso de esforco em 3000, `max(orcamento*4, piso)` dava
                # 3000 duas vezes e a segunda tentativa nao tinha mais folga
                # nenhuma — um retry que nao cresce nao e um retry.
                anterior = max(orcamento, _MAX_TOKENS_COM_ESFORCO) if esforco else orcamento
                esforco = ""
                formato_json = False
                orcamento = min(max(anterior * 4, _MAX_TOKENS_COM_ESFORCO), _MAX_TOKENS_MAX)
        resposta["ms"] = ms_total
    except _RecusaHttp as exc:
        detalhe = f"HTTP {exc.codigo}: {exc.detalhe[:200]}"
        # `auto`: o provider disse que o modelo nao fala chat. Muda de
        # transporte na mesma chamada e memoriza o achado, para o turno seguinte
        # ir directo ao endpoint certo e nao pagar a sonda outra vez.
        if str(definicao.get("via") or "") == "auto" and _RE_MODELO_DECISOES.search(exc.detalhe or ""):
            with _lock:
                _via_detectada[alvo] = "decisoes"
            try:
                return _classificar_decisoes(
                    alvo=alvo, base=base, definicao=definicao, corpo_texto=corpo_texto,
                    resolver_rota=resolver_rota, chave=chave, ttl_erro=ttl_erro, agora=agora,
                )
            except Exception:  # noqa: BLE001 - se a deteccao falhar, vale o erro do chat
                pass
        # Uma recusa HTTP tambem pode ser config (id de modelo inexistente,
        # transporte errado): o `_RecusaHttp` nao passava pelo filtro e um 400
        # definitivo era contado como avaria transitoria.
        if _RE_INDISPONIVEL.search(detalhe):
            return _indisponivel(alvo, base, detalhe, chave=chave, ttl_erro=ttl_erro,
                                 definicao=definicao)
        return _falhou(alvo, base, f"HTTP {exc.codigo}", exc.detalhe[:200],
                       chave=chave, ttl_erro=ttl_erro)
    except Exception as exc:  # noqa: BLE001 - fail-open sempre
        detalhe = str(exc)[:200]
        # Config errada nao e avaria: um provider que nao existe no perfil (ou
        # uma credencial que falta) nao melhora por ser retentado. Vai por um
        # caminho proprio, com suspensao de uma hora e sem `falhou` no cartao.
        if _RE_INDISPONIVEL.search(f"{type(exc).__name__}: {detalhe}"):
            return _indisponivel(alvo, base, detalhe, chave=chave, ttl_erro=ttl_erro,
                                 definicao=definicao)
        return _falhou(alvo, base, type(exc).__name__, detalhe, chave=chave, ttl_erro=ttl_erro)
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
            # Transporte e preset ficam no ledger: sem eles, trocar de modelo ou
            # de forma do pedido le-se como mudanca de qualidade e nao se sabe
            # o que mudou.
            "via": "chat",
            "preset": str(definicao.get("preset") or ""),
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
    # Transporte de decisoes: o lote do chat e um prompt unico com N linhas e uma
    # resposta JSON indexada; no endpoint de decisoes cada pergunta e um caso e o
    # mapeamento caso->resposta exigiria um nome por indice. Um pedido por caso
    # custa ~250 ms e ~2e-05 USD medidos, sem cache — que e precisamente o que
    # uma medicao precisa (um lote em cache media o cache, nao o modelo).
    if str(definicao.get("via") or "chat") == "decisoes" or (
            str(definicao.get("via") or "") == "auto"
            and preset_para(modelo) == "sistema-um"):
        sem_cache = {**definicao, "cache_ttl_s": 0}
        niveis_dec: list[str | None] = []
        motivos: list[str] = []
        custo_total = 0.0
        for um in pedidos:
            r = classificar([{"role": "user", "content": um}], sem_cache,
                            resolver_rota, perfil=perfil)
            nivel_um = (r or {}).get("nivel")
            niveis_dec.append(str(nivel_um) if nivel_um else None)
            if not nivel_um and r:
                motivo = r.get("motivo") or r.get("erro") or r.get("sem_efeito")
                if motivo:
                    motivos.append(str(motivo))
            custo_um = ((r or {}).get("classificador") or {}).get("custo_usd")
            if isinstance(custo_um, (int, float)):
                custo_total += float(custo_um)
        return {"niveis": niveis_dec, "erro": "; ".join(dict.fromkeys(motivos))[:300],
                "pedidos": len(pedidos), "via": "decisoes",
                "custo_usd": round(custo_total, 8)}
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


def _indisponivel(alvo: str, base: dict[str, Any], detalhe: str, *,
                  chave: str = "", ttl_erro: float = _TTL_ERRO_S,
                  definicao: dict[str, Any] | None = None) -> dict[str, Any]:
    """Provider inexistente no perfil: `sem_efeito`, nao `falhou`.

    A distincao importa porque o cartao do perfil le `falhou` como aviso de
    avaria e `sem_efeito` como "nao decide, e porquê". Um `AuthError` por
    config errada apresentado como avaria ensina o utilizador a ignorar o
    aviso — e depois o aviso que interessa tambem e ignorado.

    A suspensao guarda a impressao da definicao que falhou: mudar a config
    levanta-a de imediato, sem esperar a hora.
    """
    with _lock:
        _suspensos[alvo] = time.monotonic() + _SUSPENSAO_INDISPONIVEL_S
        if definicao is not None:
            _suspenso_impressao[alvo] = _impressao(definicao)
        else:
            _suspenso_impressao.pop(alvo, None)
        _falhas.pop(alvo, None)
    motivos = {
        "provider_indisponivel": "o provider nao existe no perfil servido",
        "modelo_indisponivel": "o modelo ou o transporte nao existe neste provider",
    }
    if re.search(r"unknown provider|no such provider", detalhe, re.I):
        chave_motivo = "provider_indisponivel"
    elif re.search(r"not a valid model|is a decisions model|cannot be used with the chat"
                   r"|model .{0,60}not found|unknown model", detalhe, re.I):
        chave_motivo = "modelo_indisponivel"
    else:
        chave_motivo = "credencial_ausente"
    saida = {
        "sem_efeito": chave_motivo,
        "motivo": f"{chave_motivo}: {motivos.get(chave_motivo, 'sem credencial utilizavel no perfil')}"
                  f" — {detalhe}"[:300],
        "classificador": {**base, "indisponivel": True, "motivo": chave_motivo,
                          "detalhe": detalhe[:200]},
        "origem": "modelo_indisponivel",
    }
    if chave and ttl_erro > 0:
        with _lock:
            _cache_erro[chave] = (time.monotonic() + ttl_erro, saida)
            if len(_cache_erro) > 500:
                _cache_erro.clear()
    return saida


def _falhou(alvo: str, base: dict[str, Any], tipo: str, detalhe: str,
            *, chave: str = "", ttl_erro: float = _TTL_ERRO_S) -> dict[str, Any]:
    with _lock:
        # Um timeout e a falha mais cara: custa o `timeout_s` inteiro em latencia
        # do turno. Medido a 2026-11-03, 8 s de espera por tentativa davam quatro
        # falhas em 45 s no perfil `pessoal` antes de o contador de tres chegar
        # ao limite. Esperar tres vezes para concluir o que a primeira ja diz e
        # pagar 24 s por nada: a heuristica responde em 0 ms e e o default
        # declarado. Logo um timeout suspende a primeira.
        esgotou = bool(re.search(r"timed?\s*out|timeout", tipo or "", re.I))
        n = _falhas.get(alvo, 0) + 1
        _falhas[alvo] = n
        if esgotou or n >= _FALHAS_PARA_SUSPENDER:
            _suspensos[alvo] = time.monotonic() + _SUSPENSAO_S
            # Avaria de transporte nao depende da config: a suspensao vale para
            # qualquer definicao do alvo.
            _suspenso_impressao.pop(alvo, None)
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
        _suspenso_impressao.clear()


def estado() -> dict[str, Any]:
    with _lock:
        agora = time.monotonic()
        return {
            "cache": len(_cache),
            "alvos_suspensos": {k: round(v - agora, 1) for k, v in _suspensos.items() if v > agora},
            "falhas": dict(_falhas),
        }
