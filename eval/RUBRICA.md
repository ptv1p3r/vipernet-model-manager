# Rubrica de rotulagem do nível de complexidade

Documento de referência para rotular o nível (`leve` / `media` / `alta`) dos
prompts que servem de gold set ao classificador do Vipernet Model Manager.
Sem esta rubrica, dois rotuladores produzem dois conjuntos incompatíveis e o
número final mede a discordância entre pessoas, não a qualidade do classificador.

## 0. O que se rotula, e o que não se rotula

Unidade: **um prompt de utilizador** (um turno). Não é a decisão do plugin —
um turno gera dezenas de decisões no laço de ferramentas (medido: 6 214
decisões para 144 prompts, razão 43:1).

Alvo: **o nível mínimo de modelo que resolve o pedido com qualidade aceitável**.
Não é «quão difícil o texto parece» nem «quão complicado foi responder». É uma
pergunta de custo: qual é o modelo mais barato que faz isto bem.

Corolário: o rótulo é sobre o **pedido**, não sobre o histórico. Não se rotula
pelo modelo que respondeu, pela resposta que saiu, nem pelo trabalho que deu.

## 1. Regra do máximo

Três eixos independentes. O nível é o **máximo** dos três, nunca a média:

    nivel = max(raciocinio, contexto, especialidade)

A razão é simples: qualquer eixo pode forçar o modelo sozinho. Um pedido
trivial («extrai a data deste PDF») sobre 200 páginas exige janela grande; um
pedido conceptualmente simples que só existe em imagem exige multimodal.

### 1.1 Raciocínio

| nível | âncoras |
|---|---|
| `leve` | saudação, confirmação, agradecimento; pedir esclarecimento; extrair um valor; sim/não; reformular; correr um comando já conhecido; responder a pergunta factual de uma linha |
| `media` | resumir texto longo; traduzir; redigir email; corrigir um erro com sintoma claro; diagnosticar com mensagem de erro dada; alterar um ficheiro identificado; escrever um teste para uma função nomeada; atualizar/limpar um sistema vivo; plano de uma tarefa concreta |
| `alta` | desenhar arquitetura ou decidir trade-offs; depurar sem reprodução nem mensagem; migrar com rollback e compatibilidade; analisar em conjunto vários ficheiros/serviços; otimizar um algoritmo; provar correção; fechar um parecer com consequências |

Regra de bolso: `media` é «sei o que fazer, preciso de executar bem»; `alta` é
«tenho de descobrir o que fazer antes de executar».

### 1.2 Contexto

Estimar tokens de entrada (≈4 caracteres por token; um log de 40 KB ≈ 10 mil
tokens). Só promove, nunca despromove:

- até 24 mil tokens: não força nada;
- 24 mil a 100 mil: sobe **um** nível;
- acima de 100 mil: `alta` por necessidade de janela, independentemente do resto.

O número que manda é o contexto **que o pedido obriga a carregar**, não o que
aparece colado. Um log colado para extrair uma linha é `leve` (o modelo só
precisa de uma linha), mesmo que o turno seja grande.

### 1.3 Especialidade

- imagem, áudio ou vídeo → exige multimodal;
- JSON estrito ou function calling → exige suporte nativo;
- uso de ferramentas com efeito → exige tool calling fiável;
- nenhuma destas → não força.

Especialidade e raciocínio não se somam: uma fotografia de um recibo para
extrair o total é `leve` com exigência de multimodal.

## 2. Contra-âncoras (erros já medidos — não repetir)

Estes seis padrões causaram 7 dos 11 erros de `media` medidos no conjunto cego
de 31 casos, e estão aqui para não voltarem:

1. **Avaria não é complexidade.** «aquilo deu o berro depois do update» é
   `media`. A urgência é do utilizador; o modelo não fica mais caro por isso.
2. **Jargão não é complexidade.** «o cert do nginx expirou ontem, renova pf» é
   `media` (sintoma claro, ação conhecida), não `alta`.
3. **Pedir esclarecimento é `leve`.** «isso não me parece bem», «falta aqui
   qualquer coisa» — o trabalho seguinte é que será rotulado, não este turno.
4. **Planeamento é `media` quando implica preparar trabalho** («vou pô-lo a
   correr amanhã, depois vejo se pia»), e `leve` quando é só intenção vaga.
5. **Manutenção toca sistema vivo → `media`.** «limpar», «arrumar», «atualizar»
   são `media` sobre um serviço em produção e `leve` quando é higiene local
   (apagar um ficheiro temporário).
6. **Continuação herda.** «continua», «vai com isso» herdam o nível do turno
   anterior. Não são `alta` por serem exigentes nem `leve` por serem curtos.

Bónus, medido e contra-intuitivo: **comprimento não é nível**. Há prompts de
800+ caracteres que são `leve` (colar um log para extrair um valor) e prompts
de 30 caracteres que são `alta` («resolve o failover do VIP»).

## 3. Ambiguidade, confiança e adjudicação

Todo o rótulo leva `confianca`:

- `alta`: o caso encaixa numa âncora sem discussão;
- `media`: defensável, mas admite o vizinho — registar a alternativa em `motivo`;
- `baixa`: dois rotuladores competentes discordariam.

Regras:

- Empate → **nível inferior**, porque o custo do erro assimétrico (pagar caro
  por um pedido simples) é maior do que o de degradar um pedido médio. Mas o
  empate fica marcado `baixa`, para não contaminar as métricas de qualidade.
- Discordância entre dois rotuladores → terceiro decide; sem terceiro, o caso
  sai do conjunto (não se inventa rótulo).
- Antes de rotular os 253: 40 casos rotulados em duplicado por dois
  rotuladores, com **kappa ≥ 0,60** exigido. Abaixo disso a rubrica está
  ambígua e corrige-se a rubrica, não os rótulos.

## 4. Vieses a evitar

- Gíria, erro ortográfico e variante regional **não baixam** a complexidade.
  Um pedido mal escrito pode ser `alta`; um pedido polido pode ser `leve`.
- Formalidade, comprimento e cortesia **não sobem** o nível.
- Idioma não é sinal: pt-PT com termos técnicos em inglês é o registo normal.
- Não rotular pelo que o modelo respondeu. Se o classificador disse `alta` e o
  rotulador acha `media`, o rótulo é `media` — é isso que se está a medir.
- Não usar o perfil (`trabalho` vs `pessoal`) como atalho: o nível depende do
  pedido, não de quem o faz.

## 5. Splits, contaminação e selagem

- Divisão **dev 60% / val 20% / test 20%**, por hash do `id` (não por data), e
  estratificada, para que os três conjuntos tenham a mesma mistura de níveis.
- `test` abre-se **uma vez por versão**. O resultado, com o sha256 do ficheiro
  de rótulos e a versão do classificador, fica registado antes de qualquer
  alteração seguinte.
- O `gold.jsonl` (60 casos) está **contaminado**: foi usado para afinar
  vocabulário. Só serve de `dev`. Nunca de `test`.
- O `gold2.jsonl` (31 casos) foi o cego da medição anterior; agora está visto.
  Serve de `val`, não de `test`.
- O `test` tem de ser escrito depois da última alteração ao classificador, e
  não pode ter sido olhado.

## 6. Reprodutibilidade

- Heurística: determinística por construção; duas corridas no mesmo ficheiro
  têm de dar o mesmo sha256 de previsões. Se não der, há estado escondido.
- Modelo: `temperature: 0`, `top_p: 1`, prompt de sistema congelado por
  sha256, **três repetições**, reportar mediana e intervalo. O
  `inception/mercury-3` não é determinístico a `temperature 0` (concordância
  medida 0,86–0,91), logo uma corrida única mede ruído.
- Todos os parâmetros vão no registo do resultado, não só no código: sem isso,
  o número não é reproduzível por outra pessoa.

## 7. Formato do rótulo

Um JSON por linha, com o mesmo `id` do ficheiro de amostra:

```json
{"id":"a1b2c3d4e5f6","rotulo":"media","confianca":"alta",
 "motivo":"diagnostico com sintoma claro; sem exigencia de janela",
 "rotulador":"proldan","ts":"2026-11-02T21:40:00+00:00"}
```

Campos: `id` (obrigatório, sha do prompt), `rotulo` (`leve|media|alta`),
`confianca` (`alta|media|baixa`), `motivo` (obrigatório se `confianca` não for
`alta`), `rotulador`, `ts`. O validador `rotulos.py` recusa rótulos fora do
domínio, ids ausentes da amostra e falta de motivo.

## 8. Métricas e critérios de aceitação

Medir, no `test`, sempre por classe:

- **macro-F1** (média não ponderada dos F1 das três classes) — a métrica que
  decide. `accuracy` esconde `media`, que é a classe que interessa corrigir.
- **matriz de confusão 3×3**, com as contagens, não só os totais.
- **cobertura e precisão de `media`**, separadas: ≥0,70 em cada.
- **custo por decisão** (USD por mil turnos) e **p95 de latência**.
- **consistência entre paráfrases**: mesmo caso em seis registos diferentes;
  é a vantagem medida do modelo sobre a heurística (67–100% contra 17%).
- **taxa de banda de indecisão**: fracção do tráfego real em score 1–2
  (medido: 363 de 2 323 decisões, 16%). É o número que decide se a cascata
  compensa.

Critério de aceitação da cascata (heurística + modelo só na banda 1–2):
só entra em produção se melhorar o macro-F1 em **≥5 pontos percentuais** sobre a
heurística sozinha no `test`, mantendo o custo incremental declarado.

## 9. O que esta rubrica não cobre

- **Viés demográfico, jailbreak e alucinação**: não há casos, e sem casos não há
  número. O `eval/` mede a forma (JSON válido, nível) e não a segurança. Dizer
  que está avaliado seria falso.
- **Tom de voz e criatividade**: exigem juiz humano; a avaliação automática não
  chega, e não há segundo rotulador para esses eixos.
- **Outro provider**: com o `gpt-sol` em HTTP 402, só há medições de um modelo.
  Toda a conclusão sobre «o modelo» é, hoje, uma conclusão sobre o mercury-3.
