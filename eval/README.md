# Avaliação do classificador de complexidade

Este diretório mede o que o plugin decide, em vez de acreditar no que ele diz.
Compara sempre contra a **heurística** como *baseline*, corre o **modelo
classificador** configurado com parâmetros fixos e escreve um relatório com
números datados.

```
python3 eval/medir.py                                  # heurística + modelo da config
python3 eval/medir.py --repeticoes 3                   # + determinismo
python3 eval/medir.py --runners heuristica             # só a heurística (sem rede)
python3 eval/medir.py --provider inception --modelo mercury-3 --esforco baixo
python3 eval/medir.py --revisao                        # CSV para confirmação humana
```

Saída: `eval/reports/rel-<data>.json` (tudo) e `.md` (legível).

## As sete regras, e onde estão implementadas

1. **Métricas claras e objectivas** — acerto, macro-F1, precisão/cobertura/F1
   por classe, matriz de confusão, latência p50/p95, tokens de entrada e saída,
   casos sem nível. Alinhadas ao caso de uso: a tarefa é classificar esforço de
   trabalho em três níveis, não qualidade geral do modelo.
2. **Gold set congelado** — `gold.jsonl` tem o sha256 em `.gold.lock` no momento
   do congelamento. O banco **recusa `--afinar`**: o conjunto de teste nunca vira
   conjunto de treino. Se o `gold.jsonl` mudar, o lock avisa que os resultados
   anteriores deixam de ser comparáveis.
3. **Dados representativos e extremos** — cada caso tem `categoria`: `claro`,
   `ambigua`, `giria`, `typo`, `adversaria`, `extremo`, `misto`. Há gíria pt-PT
   e pt-BR, texto sem acentos, injectânea de prompt, vazio, emoji e ruído longo.
4. **Parâmetros fixos** — temperatura 0 e top_p 1 no corpo do pedido, o mesmo
   prompt de sistema (`analise_modelo.PROMPT`) e o mesmo `max_tokens`, em todas
   as corridas e todos os modelos. Reprodução: o relatório guarda modelo,
   esforço, formato, `max_tokens`, hora, sha256 do gold e a definição efectiva.
5. **Reprodutibilidade** — `--repeticoes N` repete cada caso e mede a
   concordância; o relatório lista os casos instáveis.
6. **Enviesamento de forma** — `variantes.jsonl` repete o mesmo conteúdo técnico
   em registos diferentes (cortês, seco, coloquial, pt-BR). A taxa de
   consistência mede se o modelo classifica a *forma* em vez da *substância*.
   O viés demográfico não se aplica: o classificador não vê pessoas.
7. **Humano no circuito** — `--revisao` escreve `revisao.csv` com o esperado, o
   que a heurística disse e o que o modelo disse. O dono confirma e grava
   `rotulos-humanos.json` (`{"g001": "leve", ...}`); a partir daí o relatório
   separa o acerto no **subconjunto confirmado por humano** do acerto nos
   rótulos do assistente.

## Limites declarados

- Os rótulos iniciais são do assistente, não do dono: são hipóteses
  documentadas, não verdade. A coluna `subconjunto_humano` fica vazia até haver
  confirmação.
- Custo por token só é calculado quando há tabela de preços; com modelos free o
  custo é zero e o número relevante é o de tokens.
- Uma corrida mede o modelo naquele dia, naquela versão do prompt. Mudar o
  prompt ou o modelo exige nova corrida — o relatório anterior não se reescreve.

## Amostra estratificada e rubrica de rotulagem

O `medir.py` responde «o classificador acerta nos 60 casos do gold?». A amostra
do tráfego real responde a outra pergunta: «onde erra, e em que proporção». São
ferramentas diferentes e ficam separadas.

```
python3 eval/extrair_amostra.py --saida /fora/do/repo/amostra.jsonl           # do ledger
python3 eval/extrair_amostra.py --fonte sessoes --saida /fora/repo/pool.jsonl # reserva
python3 eval/rotulos.py --amostra a.jsonl --rotulos r.jsonl --previsoes p.jsonl
```

- **`extrair_amostra.py`** — a unidade de amostragem é o **prompt de
  utilizador**, não a decisão. Medido: 6 214 decisões no ledger correspondem a
  **144 prompts** (razão 43:1), porque um turno gera dezenas de pedidos no laço
  de ferramentas. Amostrar decisões pesaria a amostra pela profundidade do laço,
  não pela intenção. O modo `--fonte sessoes` extrai da reserva os prompts
  distintos de todas as sessões (3 588 com ≥12 caracteres), estratificados por
  perfil e comprimento.
- **`RUBRICA.md`** — o que é `leve`/`media`/`alta`, a regra do máximo (raciocínio,
  contexto, especialidade), as contra-âncoras dos erros já medidos
  (avaria≠complexidade, jargão≠complexidade, continuação herda), a adjudicação
  com kappa ≥ 0,60, os splits dev/val/test e os critérios de aceitação.
- **`rotulos.py`** — valida os rótulos (domínio, id existente, motivo obrigatório
  quando a confiança não é alta), calcula macro-F1, precisão/cobertura por classe,
  matriz de confusão e kappa entre dois rotuladores. Complementa o `--revisao` do
  `medir.py`: aqui o rótulo é validado e a concordância medida, ali o caso entra
  no relatório.
- **`test_amostra.py`** — 22 verificações sem rede: recusa de escrita dentro do
  repo, recusa de rótulo fora do domínio, motivo obrigatório, métricas e kappa.

Duas propriedades que a medição fixou: **37% dos prompts (53 de 144) receberam
níveis diferentes ao longo do próprio turno** — o nível pode mudar a meio, o que
torna o custo imprevisível e pode trocar de modelo dentro de um turno; e o
ledger **não guarda texto**, por isso a amostra com conversa real fica fora do
repositório e o extractor recusa-se a escrevê-la lá (rc 2).

## O ciclo automático (`aprender.py`)

Uma vez por semana (job `b52071e298fa`, domingo 04:20), sem agente:
colhe os pedidos novos, rotula-os com o juiz calibrado, treina pesos para a
heurística, compara variantes do prompt do classificador e **só promove se os
portões passarem**.

```
python3 eval/aprender.py                        # ciclo completo
python3 eval/aprender.py --so-treinar           # sem juiz, sem rede
python3 eval/aprender.py --reverter             # desfaz a última promoção
python3 scripts/vmm-aprender.py --self-test     # prova o ciclo inteiro, offline
```

Portões, e cada um fecha por si:

1. **kappa do juiz ≥ 0,55** contra os rótulos de referência — sem isto, o juiz
   não tem autoridade para ensinar nada.
2. **ganho na validação** — os pesos novos têm de bater a heurística actual num
   conjunto que o treino não viu.
3. **F1 de `media`** não pode descer — era o pior buraco medido (0,36).

Promover grava `analise.pesos` e `analise.aprendiz` na config viva pelo
`gestor.gravar` (validação única, escrita atómica, backup). `--reverter` volta
aos pesos da promoção anterior, ou aos de origem do código quando só houve uma.

### Medições que fecharam os portões (2026-11-02)

| juiz | kappa | nota |
|---|---|---|
| 3 classes, zero-shot | **0,483** | `alta` 15/15; todo o erro em media↔leve |
| 2 fronteiras binárias | 0,288 | refutada a hipótese de que binário calibraria melhor |
| 3 classes + 9 âncoras few-shot | 0,395 / 0,432 | refutada; pior sem contaminação cruzada |

E a razão de fundo: **o juiz não é auto-consistente**. Com `temperature: 0` e
`top_p: 1`, o mesmo texto em 3 repetições só dá o mesmo nível em **7 de 12**
casos (0,583). Aprender com este juiz é aprender ruído, e é por isso que os
pesos nunca foram promovidos em produção — o portão está a fazer o seu trabalho,
não está avariado.

O que falta para desbloquear: **rótulos de referência independentes**. A fila
`--revisao` do `medir.py` e a rubrica do `rotulos.py` existem para isso; com 40
casos rotulados pelo dono mede-se o kappa real do segundo rotulador. Enquanto
não houver, o ciclo colhe, mede e relata — e não promove.

