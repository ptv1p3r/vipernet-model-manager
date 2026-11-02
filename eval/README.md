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
