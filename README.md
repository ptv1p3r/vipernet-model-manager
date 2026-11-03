# vipernet model manager

Escolhe **provider + modelo** em cada turno a partir da complexidade que o turno
mostra — não a partir de uma etiqueta que o dono escreveu — dentro do catálogo
que o dono declarou **para o perfil que está a ser servido**.

Três frases que resumem o contrato:

1. A **complexidade é medida** (última mensagem + estado do trabalho): tamanho do
   texto próprio, verbos de engenharia, blocos de código, tracebacks, caminhos,
   turnos e ferramentas usadas. `complexidade: alta` continua a existir como
   override explícito, mas o caminho normal é a análise.
2. O **catálogo é fechado e é do dono**. Fora das entradas declaradas não há
   rotas. Cada entrada diz que níveis de complexidade serve. Uma entrada cuja
   credencial não resolve é saltada, com registo.
3. Cada **perfil** decide se usa o plugin, como usa (estratégia) e com que lista.
   Um perfil ausente da config não é tocado.

## Analise de complexidade (3.5)

Duas vias, e a segunda e opcional:

1. **Heuristica** (sempre disponivel, sem custo): pontua sinais independentes e
   corta em dois limiares — `leve` (< media), `media` (>= 3) e `alta` (>= 6).
2. **Modelo classificador** (opt-in): um modelo escolhido pelo dono responde
   `leve`/`media`/`alta`. Definido globalmente ou por perfil, com interruptor
   proprio em cada ambito. Sem modelo definido, ou com o interruptor em baixo,
   vale a heuristica — o comportamento nao muda.

Ordem de decisao por turno, do mais forte para o mais fraco:
**marcador explicito** (`complexidade: alta` na mensagem) **> associacao de
tarefa > modelo classificador > heuristica**. As tres primeiras fontes so
decidem o nivel; a rota (provider+modelo) sai sempre do catalogo do perfil.

O bloco vive em `analise.classificador` (global) e em
`perfis.<nome>.analise.classificador` (por perfil). O bloco do perfil
**substitui por inteiro** o global — e o que permite a um perfil desligar
(`{"activo": false}`) o que a config global liga. Um perfil que nao declare o
bloco herda-o; e o que se grava nunca e uma copia do global (uma copia fixava o
perfil ao valor do dia e deixava de acompanhar mudancas do global).

```json
"classificador": {
  "activo": true, "provider": "inception", "modelo": "mercury-3",
  "timeout_s": 12.0, "max_tokens": 320, "cache_ttl_s": 600,
  "janela_mensagens": 8, "max_chars": 4000
}
```

**Fail-open por desenho**: erro de rede, `HTTP != 200`, resposta sem um dos tres
niveis, ou `timeout_s` excedido devolvem `None` — decide a heuristica e o motivo
fica em `classificador_erro` no ledger. O disjuntor abre de duas maneiras
diferentes, porque as falhas nao custam o mesmo: **um timeout suspende a
primeira** (custa o `timeout_s` inteiro em latencia do turno, e a heuristica
responde em 0 ms; esperar tres vezes para ouvir o que a primeira ja disse e pagar
3x por nada — medido a 2026-11-03: quatro falhas em 45 s no perfil `pessoal` a
8 s de timeout), e tres falhas de outro tipo (5xx, resposta ilegivel) suspendem
como antes. A resposta e descarregada por `max_tokens` (320) e a mesma ultima
mensagem nao e paga duas vezes dentro de `cache_ttl_s`.

**Uma falha e uma falha, nao uma por pedido do turno**: o nivel fica pinado no
turno, e o erro tambem. So o evento do primeiro pedido carrega
`classificador_erro`; os seguintes levam `classificador_erro_herdado` e nao
contam para a taxa. Sem isto, um turno de 14 pedidos (medido a 2026-11-03, perfil
`default`) aparecia como 14 falhas no cartao e na contabilidade.

**Orcamento de saida e modelos de raciocinio** (medido 2026-11-02): o
`deepseek-flash` pensa antes de responder. Com `max_tokens: 12`, os 12 tokens de
saida foram todos `reasoning_tokens` e o `content` voltou vazio — o classificador
falhava por orcamento, nao por qualidade. Com 320, a mesma mensagem devolve
`alta` em 1,3 s (196 tokens de entrada, 59 de saida, ~0,00008 USD). Um modelo de
raciocinio cortado a meio ainda entrega o nivel: quando o `content` vem vazio,
le-se o `reasoning_content` e vale o ultimo nivel que o modelo escreveu.

Ligar/desligar, sem editar JSON:

```sh
vmm classificador                            # estado efectivo por perfil
vmm classificador on --provider inception --modelo mercury-3
vmm classificador off --perfil pessoal       # desliga so neste perfil
vmm classificador on --perfil trabalho --provider openrouter --modelo x/y
```

**O provider do classificador tem de existir no perfil servido** (medido
2026-11-03). O classificador resolve-se com o `config.yaml` do **perfil**, não
com o do processo: um `inception/mercury-3` que funciona no `default` falha no
`pessoal` com `AuthError: Unknown provider 'inception'` enquanto o bloco
`providers.inception` não estiver declarado lá — `hermes -p pessoal config set
providers.inception.{name,base_url,key_env,api_mode,default_model}`, com
`key_env: INCEPTION_API_KEY` e `api_mode: chat_completions`. Um erro destes é
`sem_efeito` com `motivo` e suspensão de 1 h, não falha transitória: não abre o
disjuntor nem conta para a taxa. A chave vem das fontes de segredos para o
processo do gateway, não do `.env` do perfil.

Estado actual: o global é `inception/mercury-3` e os três perfis herdam-no. Um
perfil que queira outro modelo declara o seu próprio bloco — a partir daí fica
`próprio do perfil` e deixa de acompanhar o global, que é o preço de não ter uma
cópia a fingir de herança.

No painel, a seccao **Analise da complexidade** (dentro do editor) tem uma linha
por ambito — global e cada perfil — com tres estados por perfil: *herdar do
global*, *desligado (heuristica)* e *decide um modelo* (com provider e modelo).
Grava pelo mesmo `/config`, depois de validado.

Sinais da heuristica e o que os separa:

- **Verificar nao e analisar.** "testa o plugin", "adiciona um teste" — o verbo e
  confirmacao, nao trabalho de analise; `_ANALISE` ignora verbos de verificacao.
- **Manutencao separada de trabalho.** `corrigir`, `adicionar`, `atualizar` e
  `revisar` valem 3 (`manutencao`), nao 5 (`trabalho`) — um typo no README nao
  compra um modelo caro. Mas se o alvo for **sistema vivo** (automacao, servico,
  instalacao, container), volta a valer `trabalho`: e producao, nao manutencao fina.
- **Planeamento e relatorio tem categoria propria.** `planeamento` (5): plano,
  janela, rollback, proposta de migracao. `relatorio` (3): resumo/relatorio
  recorrente, que produz mas e barato.
- **Diagnostico baixou de 6 para 4** (`auditoria`): "porque o inversor nao liga"
  e trabalho de analise, mas nao do peso de uma decisao de arquitetura (6).

Medicao que fixou os limiares (2026-11-02, dois holdouts, 42 casos):

| variante | holdout1 (24) | holdout2 (18) | total |
|---|---|---|---|
| `media=3` (em vigor) | 24/24 | 17/18 | **41/42 (98%)** |
| `media=4` | 20/24 | 14/18 | 34/42 (81%) |
| `media=3` + regra dos 2 sinais | 21/24 | 14/18 | 35/42 (83%) |

A regra dos dois sinais (`media_sinais: 2`) fica implementada e **inactiva**: so
actua quando a chave existe nos limiares. `test_holdout.py` versiona o holdout2
com baseline 17/18 — o unico erro conhecido ("NPM ou Traefik, justifica") e
discutivel no rotulo e **nao se calibra contra ele**.

## v4.3 — o que separa agora

Correccoes de conceito, cada uma nascida de um caso real falhado:

- **`justificacao` (3)**: "justifica" pede razao, nao desenha. Antes valia 6 e
  mandava "NPM ou Traefik? justifica" para o modelo caro.
- **`explicacao_conceito` (2)**: "explica como funciona X" e raciocinio de
  dificuldade media.
- **`avaria_de_servico` (+3)**: sintoma sobre um processo que devia estar a
  correr (`_SERVICO_AVARIADO`). Uma lampada que nao acende nao e isto.
- **Objecto vivo restrito**: sairam `ha`, `bateria`, `inversor`, `dashboard` e
  `api`. `ha\b` casava com o verbo portugues "ha" (texto normalizado sem
  acentos) e promovia um typo a trabalho de producao.
- **Verbos genericos** (`prepar`, `gera`, `faz`, `produz`): herdam o peso do
  objecto; "gera o relatorio mensal" e relatorio (3), nao engenharia (5).
- **`mudar` em pergunta nao e ordem**: so `muda`/`mude` contam como manutencao.
- **Vocabulario de infra**: `lxc`, `proxmox`, `portainer`, `backup`, `retencao`,
  `snapshot`, `vlan`, `skills`, `n8n`, `sqlite`, `postgres`, `firmware`,
  `ota`, `consumo`, `standby`, `energia`, `seguranca`, `exposicao`, `homelab`.

### Protocolo de medicao (importante)

Os holdouts `test_holdout{,2,3,4}.py` somam 79 casos. Cada vez que se afina o
vocabulario contra um deles, esse conjunto **deixa de ser amostra independente**.
A ordem que se cumpriu: medir cego, corrigir conceitos, medir num conjunto novo.

| conjunto | casos | antes da v4.3 | depois |
|---|---|---|---|
| holdout2 | 22 | 22/22 (treino) | 22/22 |
| holdout3 (cego na altura) | 20 | 12/20 (60%) | 20/20 |
| holdout4 (cego na altura) | 19 | **11/19 (57%)** | 19/19 |

O numero honesto e o **57% cego**, nao os 100%. As 8 falhas eram todas de
vocabulario — nenhuma de limiar — e e por isso que o gargalo do classificador
nao e o `media`, e sim o alcance lexical. Uma frase nova com palavras que nao
estao nas regexes cai em `leve`. O caminho para sair disto nao e mais regex:
e um classificador que generalize (modelo local pequeno ou um modelo gratuito
de contexto grande a responder `leve|media|alta`).


## Painel no dashboard (3.3)

O plugin traz um painel próprio no dashboard do Hermes, na tab **Model Manager**
(`/model-manager`). Fica em `dashboard/`:

| ficheiro | papel |
|---|---|
| `manifest.json` | declara a tab (`position: end`), o `entry`, o `css` e o `api` |
| `api.py` | backend montado em `/api/plugins/vipernet-model-manager/` |
| `dist/index.js` | IIFE que usa `window.__HERMES_PLUGIN_SDK__` e `__HERMES_PLUGINS__.register` |
| `dist/style.css` | geometria do painel (cores e tipografia vêm do tema do host) |

O painel mostra, por perfil: o interruptor, a estratégia, o **indicador da análise
de complexidade**, as associações de tarefa, o catálogo com a complexidade e o
esforço de cada modelo, e o último modelo servido; em baixo, o registo das últimas
respostas (quando, perfil, modelo, provider, nível, origem, superfície, sessão).

Escreve o interruptor (global ou por perfil) e a configuração inteira (editor JSON,
`POST /config`, com `POST /validar` antes), sempre por `gestor.gravar` — validação,
backup e `os.replace`, com releitura independente antes de responder OK. O log é
leitura.

O host descobre painéis por `<plugins root>/<nome>/dashboard/manifest.json`; a cache
de descoberta do processo do dashboard **não** se invalida sozinha quando aparece um
painel novo — depois de instalar, reiniciar o contentor do dashboard (ou forçar
`/api/dashboard/plugins/rescan`). Alterações ao `api.py` também só entram no
arranque: o router é montado uma vez. A config do agente é lida a quente (TTL 5 s).

### Indicador da análise por cartão (3.7)

Cada cartão de perfil responde a uma pergunta só: **a complexidade deste turno está
a ser decidida por um modelo — e qual — ou pela heurística?** A barra à esquerda e o
chip dizem o modo; o texto diz o resto:

| o que se vê | significado |
|---|---|
| `modelo · inception/mercury-3` + `esforço baixo · herdado do global` | o perfil não declara bloco e usa o global |
| `modelo · inception/mercury-3` + `próprio do perfil` | o perfil declara o seu próprio classificador |
| `heurística` + `classificador desligado — decide a heurística` | existe bloco, mas `activo: false` |
| `heurística` + `classificador sem efeito (…) — decide a heurística` | bloco declarado que não passa a validação |
| `heurística` + `sem modelo definido` | ninguém declarou nada, em nenhum âmbito |
| `últimos N turnos: 22 modelo · 3 heurística` | evidência do ledger, não da config |
| `⚠ o modelo falhou no último turno — caiu na heurística: …` | o classificador está ligado mas falhou |

As duas últimas linhas são o que distingue **declarado** de **a decidir**: um
classificador ligado que erra cai na heurística em silêncio, e a config continuaria a
dizer "ligado". A contagem vem dos eventos `resposta` do perfil (últimos 25) e o
motivo do erro do último evento com `classificador_erro`.

Uma só casa para a resposta: `gestor.classificador_efectivo(cfg, perfil)` — usada
pelo cartão (`/estado`) e pelo `vmm classificador ver`. Resolve a herança e a
validação; o painel não reimplementa nenhuma das duas.


Auto-teste do painel (não toca na config viva):

```sh
VMM_DIR=$(mktemp -d) /opt/hermes/.venv/bin/python dashboard/selftest.py
```

## Instalação

```sh
ln -sfn /opt/data/plugins/vipernet-model-manager/cli.py /opt/data/bin/vmm
hermes plugins enable vipernet-model-manager            # no perfil que o deve usar
hermes -p pessoal  plugins enable vipernet-model-manager
hermes -p trabalho plugins enable vipernet-model-manager
```

`hermes plugins enable` recarrega o gateway sozinho. A config é lida a quente
(cache de 5 s), portanto editar o catálogo **não** exige reiniciar nada.

## Config

Ficheiro vivo: `/opt/data/plugin-data/vipernet-model-manager/config.json`
(override por `VMM_DIR`, útil para ensaios). Esquema:

```json
{
  "versao": 3,
  "activo": true,
  "registar": true,
  "analise": {"janela_mensagens": 8, "varredura_max_mensagens": 60,
               "limiares": {"media": 3, "alta": 7}, "descer_apos": 2,
               "fixar_nivel_por_turno": true},
  "perfis": {
    "trabalho": {
      "activo": true,
      "estrategia": "rodizio",
      "rodizio": {"por": "sessao"},
      "analise": {},
      "catalogo": [
        {"id": "deepseek-flash", "provider": "deepseek", "modelo": "deepseek-flash",
         "complexidade": ["leve", "media"], "acesso": "api"},
        {"id": "gpt-sol", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
         "complexidade": "media", "acesso": "api"},
        {"id": "gpt-sol-sub", "provider": "openai-codex", "modelo": "gpt-6.1-sol",
         "complexidade": "media", "acesso": "subscricao"}
      ]
    }
  }
}
```

- `activo` (topo) — interruptor global; `registar` — escrever o ledger.
- `perfis.<nome>.activo` — interruptor daquele perfil.
- `estrategia`: `rodizio` (alterna no nível) ou `prioridade` (primeira entrada
  elegível). `rodizio.por`: `turno` ou `sessao`.
- `complexidade` por entrada: `"leve"`, `["leve","media"]`, `["alta"]`… Níveis:
  `leve`, `media`, `alta`.
- `acesso` por entrada: `api` ou `subscricao`. **Explicito ou inferido do
  provider** (openai-codex → `subscricao`, os restantes → `api`); é rótulo +
  validação + registo, nunca reescreve `provider`/`modelo`. Incompatibilidades
  (`subscricao` com provider que não openai-codex, ou `api` com openai-codex)
  são recusadas.
- `analise` do perfil sobrepõe-se ao global chave a chave (limiares, janela,
  `descer_apos`).
- Perfil `"*"` serve de omissão para perfis não listados; um perfil exacto ganha
  sobre o `*`.
- Sem perfil na config e sem `*`: **não gerido** — o turno segue intacto.

### Política do nível `alta` (decisão do dono, 2026-11-03)

O nível `alta` resolve a **`deepseek/deepseek-flash` nos três perfis**, e é a
única rota de `alta` declarada no catálogo. A consequência tem de ser lida antes
de mexer: **não existe no catálogo um modelo acima do flash**, logo `alta` não
significa «modelo melhor» — significa «o modelo pago, sem descer aos free». O
ganho do classificador é de **custo, não de qualidade**: em `leve`/`media` a rota
pode rodar para um modelo free; em `alta` não desce.

Quem editar o catálogo, duas regras:

- **Não acrescentar `alta` a outra entrada sem decisão explícita do dono.** Se um
  modelo free declarar `alta`, o rodízio passa a poder servir `alta` com um free e
  o nível deixa de garantir o flash. Foi o que aconteceu antes de 2026-11-03: 224
  eventos de `alta` servidos por `thinkingmachines/inkling:free` no ledger, todos
  com `motor` ausente (histórico anterior ao marcador), e zero depois.
- **`deepseek-v4-pro` está proibido** (decisão do dono, 2026-11-03) — fora do
  catálogo, fora dos catálogos por omissão do `gestor.py` e fora do juiz de
  `eval/aprender.py` sem `--juiz-permitir-v4-pro` (guard que devolve rc 2).

Se `deepseek` falhar, `alta` não tem segunda rota: a degradação desce um nível e
o turno segue num modelo de `media`, com `degradado_de` no ledger. É o
comportamento desejado — mas é uma dependência de um só provider, e deve ser
lembrada quando se decidir o catálogo.

### Juiz do ciclo de aprendizagem

O juiz que rotula o corpus de treino é **`inception/mercury-3`** (decisão do dono,
2026-11-03): alinha com o classificador e tira o deepseek de todas as funções do
ciclo. `--juiz-provider deepseek` continua disponível como alternativa, e o
`deepseek-v4-pro` continua bloqueado pelo guard de rc 2. O juiz **não** decide o
nível de nenhum turno — só rotula o corpus — e não é o mesmo que o modelo da
resposta.

Duas coisas medidas, que quem mexer aqui tem de saber:

- **O `mercury-3` limita pedidos por minuto.** Medi a 2026-11-03: 130 casos um a
  um com 4 threads deram `HTTP 429 too many requests per minute`, e a corrida
  seguinte devolveu **0/130** — o limite é por minuto e o arranque em rajada
  esgota-o. Vinte pedidos espaçados de 3 s passam todos. Por isso o juiz rotula
  **em lotes de 10** (`_etiquetar_lote`, `--juiz-lote`, por omissão 10 quando o
  provider é `inception`): 13 pedidos, 130/130 respondidos em todas as corridas,
  ~100 s. Um 429 espera e repete o **mesmo** lote; tratá-lo como falha definitiva
  apagava o corpus e o portão fechava por falta de casos comuns, não por
  discordância.
- **O `mercury-3` ainda não tem autoridade de juiz.** Kappa contra os 130 rótulos
  do gold, em 5 corridas: fronteira `alta` **0,443 / 0,490 / 0,479** (esforço
  baixo) e **0,506 / 0,450** (médio); fronteira `baixa` 0,587 / 0,517 / 0,452 e
  0,517 / 0,617. O portão exige **0,60** por fronteira operacional. Fica abaixo
  em todas as corridas, e a precisão da classe `alta` (0,38–0,43) mostra o erro:
  chama `alta` a pedidos que o gold rotula `media`. O `deepseek-flash` media
  0,599–0,631 em `alta` e 0,483–0,545 em `baixa`, com estabilidade 0,877 contra
  0,754–0,808 do mercury. **Nenhum dos dois passa o portão**: o bloqueio real é o
  gold (40 rótulos humanos, 2 de `alta`), não a escolha do juiz.

### Subscrição vs API

A mesma família de modelos tem duas rotas de acesso distintas, e o plugin não
as converte uma na outra:

- **Subscrição ChatGPT/Codex** → `provider: openai-codex`, `modelo: gpt-6.1-sol`,
  `acesso: subscricao`. O transporte é o canal de subscrição (Codex), não uma
  chave de API; sem esse canal autenticado a rota não resolve e é saltada.
- **OpenRouter** → `provider: openrouter`, `modelo: openai/gpt-6.1-sol`,
  `acesso: api`. Usa a chave de API do OpenRouter.

Adicionar a rota de subscrição sem autenticar o canal não a ativa: a entrada
existe, mas `resolver_rota` falha e a middleware salta-a (registo em
`sem_rota`).

## Esforço de raciocínio por rota (3.6)

Cada entrada do catálogo pode declarar `esforco`: `baixo`, `medio` ou `alto`
(a escada do Hermes — `low`/`medium`/`high` — também é aceite, porque o JSON é
editado por três vias e recusar o inglês seria uma armadilha, não uma
validação). O campo é **opcional e não tem valor por omissão**: ausente quer
dizer «não declaro nada», não «médio», para que uma rota que não precisa não
arraste o esforço de outra.

```json
{"id": "flash", "provider": "deepseek", "modelo": "deepseek-flash",
 "complexidade": ["leve", "media"], "acesso": "api", "esforco": "baixo"}
```

Como é aplicado — e porque não é uma linha só:

- **No agente**, em `agent.reasoning_config` (`{"enabled": true, "effort": …}`),
  que é o que governa os turnos seguintes. Chamado **depois** de
  `switch_model`, porque a troca volta a resolver o valor do `config.yaml` e
  apagaria o que fosse posto antes. O valor de origem fica guardado e é
  **reposto** quando a rota seguinte não declara esforço — o agente é o mesmo
  objecto de turno para turno, e sem essa reposição uma rota contaminava todas
  as seguintes.
- **No payload do próprio turno**: o `api_kwargs` é construído **antes** da
  middleware, logo mudar o agente só valeria para o turno seguinte. A escrita
  usa o perfil do provider de destino — o mesmo objecto que o Hermes usa para
  construir o pedido —, pelo que a forma é a do fio certo:
  `thinking` + `reasoning_effort` no deepseek, `reasoning` no OpenRouter, e
  **nada** num modelo sem raciocínio (medido: `deepseek-v3` devolve `({}, {})`).
  Os campos de raciocínio do provider anterior são removidos do payload; sem
  isso, um turno que viesse do OpenRouter levava um `reasoning` que o deepseek
  recusa.
- **No classificador** o esforço entra plano no corpo (é HTTP directo, sem SDK,
  logo `extra_body` não existe ali). Com esforço declarado, o `max_tokens` do
  classificador tem um **piso de 1200**: o raciocínio sai do mesmo orçamento e
  os 320 tokens por omissão podem ser todos gastos a pensar — medido, a primeira
  classificação com `esforco: baixo` falhou com
  `resposta sem nivel reconhecivel (length 320tok)` e caiu para a heurística.

Testado por `selftest.py` (21 verificações novas): vocabulário, validação no
catálogo, forma por provider, limpeza do payload, reposição do valor de origem
e o corpo do classificador.

## Resposta em JSON e classificação em lote (3.9)

O classificador pede a resposta como **objecto JSON** (`{"nivel": "media"}`) em
vez de uma palavra solta, e a configuração tem três valores para `formato_json`:

| valor | comportamento |
|---|---|
| `auto` (por omissão) | pede `response_format: {"type": "json_object"}`; se o provider recusar com **400/404/415/422**, repete **o mesmo pedido sem o campo** e segue |
| `on` | pede sempre; um provider que recuse dá erro, sem segunda tentativa sem formato |
| `off` | nunca pede; só texto (o parse tolerante continua a valer) |

Porque é que `auto` degrada em vez de ter uma lista de providers: uma lista
envelhece a cada modelo novo, e o custo de errar é uma chamada. Os códigos foram
escolhidos — **401/402/429/5xx não se resolvem a retirar um campo**, e esses
falham à primeira.

Medido no `inception/mercury-3` a 2026-11-02, 10 mensagens por variante:

| variante | latência mediana | tokens saída | json limpo | divergências (`length`) | acertos |
|---|---|---|---|---|---|
| **json + esforço baixo** | **665 ms** | **68** | **10/10** | **0** | **9/10** |
| json + esforço médio | 1029 ms | 373 | 9/10 | 1 (aos 4314 ms) | 7/10 |
| texto + esforço médio | 553 ms | 76 | — | 0 | 9/10 |
| texto + esforço baixo | 548 ms | 60 | — | 1 | 9/10 |

Duas conclusões que contrariam o conselho genérico «comece sempre em *medium*»:
neste modelo, **médio é pior** para esta tarefa (cinco vezes os tokens, o dobro
da latência, dois acertos a menos) — é classificação de três classes, não
raciocínio difícil; e o **JSON estabiliza a resposta** (zero divergências contra
uma). O `esforco` do classificador fica, por isso, em `baixo` na configuração
viva, e mudar é um comando.

`forma()` regista no ledger como a resposta veio — `json` (objecto com nível),
`texto` (palavra solta, sujeita a falso positivo por citação do prompt) ou
`raciocinio` (conteúdo vazio, só pensamento). Sem esta distinção, um acerto por
palavra solta parecia igual a um JSON limpo.

### Lote

`classificar_lote()` manda N pedidos num só, com uma lista numerada e uma
resposta `{"niveis": [{"i": 1, "nivel": …}]}`. Serve para medir um corpus
inteiro e para lotes grandes; o turno normal continua a classificar um pedido,
com cache.

```
vmm classificador lote "ola" "explica o cache do nginx" "desenha a arquitetura do AAD"
```

Medido a 2026-11-02 com 8 pedidos reais: **551 ms**, 447→130 tokens, 8/8
acertos, formato json aceite. Com 10 pedidos: 2,5 s contra 6,6 s um a um (2,7×
mais rápido) mas ~1,6× os tokens — o lote paga-se em latência, não em dinheiro.
O orçamento acompanha o lote (`max(1200, 120 × N)`); um lote que volte sem
níveis é repetido uma vez sem formato e sem esforço.

O `_ler_lote` aceita as três formas que um modelo devolve na prática —
`{"niveis":[{"i":N,"nivel":…}]}`, `{"niveis":["leve",…]}` e `{"1":"leve"}` — e
**alinha sempre ao tamanho da entrada**, com `None` no que faltar: quem chama
não tem de adivinhar quantos vieram.

Uma armadilha que já custou uma sessão: o prompt **tem de conter a palavra
`json`**, porque o DeepSeek recusa `response_format: json_object` com
`400 Invalid request: Prompt must contain the word 'json'` — medido. Está
travado por teste.

## Triagem do pedido: janela, modalidade, JSON e latência (3.11)

A análise de complexidade diz **quanto raciocínio** o turno pede. Não diz se o
modelo escolhido *consegue* servir o turno. Um `leve` com 200 páginas anexadas
continua a ser `leve` na pontuação e não cabe em nenhuma janela de 32k; um
pedido com uma fotografia não se resolve num modelo só de texto.

A triagem é um filtro de **impossibilidade**, aplicado antes de se tentar a
credencial: cada rota pode declarar o que consegue, e o que ela declarou não
conseguir nunca é tentado. Uma rota que não declara nada é permissiva — declarar
é o que dá a garantia, não declarar não proíbe. A única proibição explícita é
`json_nativo: false`.

| campo da rota | tipo | efeito |
|---|---|---|
| `contexto_max` | inteiro > 0 | recusa quando a estimativa de entrada excede a janela |
| `modalidades` | lista de `texto`, `imagem`, `audio`, `ficheiro` | recusa quando o pedido traz uma modalidade que não está na lista |
| `json_nativo` | booleano | `false` recusa pedidos com `response_format` json |
| `latencia_p95_ms` | inteiro > 0 | recusa quando excede `latencia_max_ms` do perfil |

O que o pedido exige é medido em `analise.necessidades()`:

- **tokens de entrada** — estimativa de 4 caracteres por token sobre todas as
  mensagens; serve para decidir se cabe, não para faturar;
- **modalidades** — detetadas nas partes de `content` (`image_url` → imagem,
  `input_audio` → áudio, `file`/`document` → ficheiro);
- **JSON** — só quando o pedido traz `response_format` de tipo `json_object` ou
  `json_schema`. Falar de JSON não é exigir JSON, e o teste fixa esse negativo.

Três consequências, todas com teste:

1. **Texto grande sobe o nível.** Passando `contexto_grande_tokens` (24 000 por
   omissão), um `leve` é promovido a `media` e o ledger regista
   `triagem: contexto Ntok`. Não é "mais complexo" — é maior do que cabe.
2. **Descer de nível não resolve capacidade.** Se a rota do nível não cabe,
   `degradar_nivel` desce e volta a filtrar; se nada no nível couber, e só
   então, `escolher_por_janela` procura em todo o catálogo a rota viável com
   maior `contexto_max` e o ledger regista `escolhido_por_janela`. Um nível
   *vazio* (sem rota, ou sem credencial) **não** dispara este recurso: aí a
   resposta certa é não trocar, e o `selftest` trava isso.
3. **A recusa fica registada.** A rota preterida aparece no evento `troca` como
   `preteridas: ["id:inviavel:<razão>"]` — `contexto 128003>32000`,
   `sem modalidade: imagem`, `sem json nativo`, `latencia 3000>1500` — em vez
   de desaparecer; no evento `sem_rota` a mesma lista chama-se `falhadas`.

```json
{"id": "janela-grande", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
 "complexidade": ["alta"], "contexto_max": 1000000,
 "modalidades": ["texto", "imagem"], "json_nativo": true, "latencia_p95_ms": 2500}
```

**O que não está feito, e não se afirma:** ordenar por custo (a ordem do
catálogo já é a preferência do dono; reordenar em silêncio sobrepunha-se a uma
decisão explícita), medir TTFT (só existe `latencia_p95_ms` declarada, não
medida), e reaproveitar decisões para perguntas *semelhantes* (o cache do
classificador é por texto normalizado e `cache_ttl_s`; semelhança difusa
arriscava reutilizar a classificação errada).

## Tarefas por perfil (3.2)

No mesmo JSON, `perfis.<perfil>.tarefas` define como reconhecer cada tarefa;
`catalogo[].tarefas` associa um ou mais tipos de tarefa ao modelo. Exemplo
parcial (acrescentar ao perfil e à entrada já existentes, não substituir o catálogo):

```json
{
  "tarefas": {
    "arquitetura": {
      "termos": ["análise de arquitetura", "SA Analysis", "architecture review"],
      "prioridade": 10
    }
  },
  "catalogo": [{
    "id": "gpt-sol", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
    "complexidade": ["alta"], "tarefas": ["arquitetura"]
  }]
}
```

A associação de tarefa ganha à complexidade, aos marcadores e à histerese.
Sem associação, fica a análise anterior. Se nenhuma rota associada resolver
credenciais, tenta a análise por complexidade e regista
`origem=analise_fallback_tarefa`, com `falhadas_tarefa`.
Havendo vários modelos associados usa a estratégia do perfil. Havendo tipos de
tarefa sobrepostos ganha `prioridade` maior; em empate, a frase mais comprida
(e depois o identificador, para desempate determinístico).

A detecção é heurística por frases literais (sem distinguir acentos/maiúsculas),
não um classificador semântico. Só avalia o último pedido do utilizador, sem
memória injectada, mensagens do assistente ou resultados de ferramentas.
Assim uma tarefa nova volta imediatamente à análise, sem herdar a histerese
da associação anterior. Limitação: «continua» sozinho não herda a tarefa;
menções citadas ou negadas à tarefa podem ainda casar com um termo.

`vmm simular trabalho "Faz uma análise de arquitetura"` mostra a tarefa e as
rotas associadas sem chamar providers. O ledger inclui `tarefa` e `origem`;
nenhum texto da conversa é guardado. Regressão: `python3 test_tarefas.py`.
Os identificadores de modelo do exemplo são os da config existente: estes
testes offline não provam disponibilidade nem resposta real do provider.

## CLI `vmm`

```
vmm status [--json]                  estado global e por perfil
vmm on|off [--perfil NOME]           liga/desliga (sem --perfil: global)
vmm perfis                           geridos vs. existentes na máquina
vmm perfil add NOME [--copiar-de X]  acrescenta perfil (cópia opcional)
vmm perfil rm NOME                   deixa de ser gerido
vmm rota list [PERFIL] [--json]
vmm rota add PERFIL PROVIDER MODELO --complexidade leve,media [--id ID] [--acesso api|subscricao]
vmm rota rm PERFIL ID
vmm simular PERFIL "texto"           classifica e mostra as candidatas
vmm verificar [PERFIL]               resolve credenciais de cada rota (sem as imprimir)
vmm classificador [ver|on|off] [--perfil NOME] [--provider P] [--modelo M]
                 [--esforco baixo|medio|alto|-] [--formato auto|on|off]
vmm classificador lote "texto1" "texto2" ...   N pedidos num só pedido (contexto longo)
vmm log [-n N] [--perfil NOME] [--provider P] [--sessao S] [--evento E]
vmm caminho                          caminhos de config e ledger
vmm recarregar                       recarrega plugins no gateway
```

`rota add` sem `--acesso` infere a rota de acesso do provider; com
`--acesso` explicito valida a compatibilidade (e recusa `api` com
openai-codex ou `subscricao` com outro provider). `vmm verificar [PERFIL]`
resolve cada rota **no contexto do perfil alvo** (home + escopo de segredos
próprios), por isso um perfil com `.env` próprio não aparece sem credenciais
só por o CLI correr fora do gateway; imprime apenas tamanhos e fontes, nunca
chaves.

Códigos de saída: `0` ok · `1` há problema · `2` uso inválido · `3` não
encontrado.

## Escolha, por turno

Um **turno** é uma mensagem do utilizador e tudo o que ela provoca — no laço de
ferramentas, dezenas de pedidos ao provider. O nível e a rota decidem-se **uma
vez**, no primeiro pedido, e o resto do turno reutiliza-os.

1. resolve o perfil do turno (o gateway multiplexado serve vários no mesmo
   processo);
2. aplica os interruptores (global, perfil) — se algum está em baixo, nada muda;
3. **verifica o pin do turno**: se já há decisão para esta `turn_id`, reutiliza
   nível, tarefa, origem e rota e salta os passos 4 a 7. Sem decisão (primeiro
   pedido), segue;
4. classifica a complexidade e aplica **histerese**: sobe já, desce só depois de
   `descer_apos` **turnos** abaixo do nível corrente (evita trocar de rota e
   estragar a cache por causa de uma mensagem curta);
5. percorre o catálogo do perfil na ordem da estratégia, saltando entradas cujo
   provider não tem credencial no escopo desse perfil;
6. troca o agente vivo (`agent.switch_model`): cliente HTTP, chave, `base_url`,
   `api_mode` e contabilidade mudam juntos — não é só reescrever o campo `model`;
7. grava o pin do turno (nível, tarefa, origem e a rota escolhida).

Porque é que isto é uma correção e não uma optimização: medido no ledger real a
2026-11-02, **63 de 167 turnos (37%) trocavam de nível a meio**. Três causas, e
o pin mata as três: a janela de mensagens cresce a cada pedido e a pontuação
muda com ela; a histerese contava **pedidos** em vez de turnos; e o classificador
por modelo corria por pedido, e não é determinístico (concordância 0,86–0,91).

Duas coisas continuam a poder mudar a decisão dentro do turno, de propósito, e
ficam **declaradas** no ledger com `excecao`:

- `capacidade` — o contexto deixou de caber na rota ou no nível pinado (um
  pedido de 128k tokens não cabe numa janela de 32k: forçá-lo era mentir);
- `rota` — a rota pinada morreu (falhou há pouco, credencial em baixo); troca-se
  **dentro do mesmo nível**.

Sem esse campo, "estável" seria uma mentira confortável: a prova conta as
mudanças silenciosas e as declaradas em separado.

Desligar: `analise.fixar_nivel_por_turno: false` devolve o comportamento
anterior, sem tocar em código. Nota de semântica: com o pin ligado, `descer_apos`
conta **turnos** (era a intenção desde o início); desligado, volta a contar
pedidos, como antes.

Fail-open em toda a linha: sem decisão, sem rota ou com excepção, o pedido segue
intacto e a razão vai para `uso.err`.

## Ledger

`uso.jsonl` (append-only, uma linha JSON por evento) e `uso.err`. Eventos:
`troca`, `sem_troca`, `sem_rota`, `falha_troca`, `reset`, `desligado`,
`perfil_desligado`, `perfil_nao_gerido`, `sem_catalogo`, `resposta`.
O `resposta` regista o que **de facto respondeu**: `provider` e `modelo`
efectivos do agente (não o escolhido no catálogo), `respondido_por`
(`response_model` que o provider devolveu), `perfil`, `sessao`, `superficie`
(origem do turno; `desconhecido` se o hook não a entrega), `acesso`,
`nivel`/`tarefa`/`origem` da decisão, e a correlação `request_id`/`turn_id`/
`task_id`. Quando a middleware escolheu algo, `escolhido` guarda
provider+modelo escolhidos — assim a distinção **escolhido vs efectivo** fica
explícita, sem inventar nenhum dos dois a partir da config.

Cada evento de decisão leva quatro campos que existem para responder a "quantas
vezes este turno mudou de modelo", sem junção nenhuma:

- `turno` — a chave do turno (`turn_id` do core, ou um hash de sessão+texto; o
  texto nunca entra);
- `passo` — o `api_call_count` do pedido dentro do turno;
- `pin` — `novo` (decidiu) ou `reutilizado` (herdou a decisão do primeiro);
- `excecao` + `excecao_motivo` — só quando a decisão mudou por necessidade
  (`capacidade`, `rota`);
- `fonte_turno` — `turn_id` ou `hash`, para se saber quanto vale a prova.

A verificação aritmética que apanha um pin que não está a pegar:
`pin: reutilizado` tem de ser igual a `pedidos − turnos`.


O ledger **não** leva texto de conversa nem credenciais: só perfil, sessão,
superfície, nível, score, nomes de sinais, providers, modelos, acesso,
tempos e tokens. A escrita é uma linha por `write` com `O_APPEND` e o
ficheiro nasce `0600`, para coexistir com o CLI e o gateway a escrever em
processos diferentes.

## Limites conhecidos

- Importa funções internas do Hermes (`resolve_runtime_provider`,
  `get_active_profile_name`, `reload_gateway_plugins`, e os ContextVars de
  home/escopo de segredos para o `verificar`) — é a única via para resolver a
  credencial da rota no perfil certo. Pode partir num upgrade do Hermes; o
  selftest apanha-o (`Validate` avisa, não falha).
- A **contabilidade de custo** do Hermes continua a ler o modelo da sessão: a
  fatura interna não segue a troca. A prova vive no ledger.
- Modelos free do OpenRouter têm limites próprios (alguns recusam chat directo,
  outros impõem ritmo); a rota é saltada quando falha, mas o catálogo é
  responsabilidade do dono. `vmm verificar` mostra as credenciais, não a
  disponibilidade do modelo — para isso há que chamar o modelo.
- A rota de **subscrição** (openai-codex) só funciona com o canal Codex
  autenticado; o plugin não o ativa nem converte uma rota `api` nela.
- Histerese e rodízio vivem em memória do processo: um restart do gateway
  reinicia a contagem (o catálogo e os interruptores não).

## Avaliação medida (e não acreditada)

O plugin decide qual modelo serve cada turno; por isso o classificador é medido
como qualquer outro componente com consequências: contra um referencial, com
parâmetros fixos e com números datados. Ver `eval/README.md` para as sete
regras e `eval/medir.py` para a implementação.

```sh
P=/opt/hermes/.venv/bin/python
$P eval/medir.py --runners heuristica            # sem rede
$P eval/medir.py --repeticoes 3                  # + determinismo + variantes
$P eval/medir.py --gold eval/gold2.jsonl         # conjunto de teste cego
$P eval/medir.py --revisao                       # CSV para confirmação humana
```

Dois conjuntos, papéis diferentes: `gold.jsonl` (60 casos, usado durante o
desenvolvimento — é **regressão**) e `gold2.jsonl` (31 casos, escrito depois e
**nunca usado para afinar** — é o teste cego). O banco recusa `--afinar` com
qualquer um dos dois.

Resultado do teste cego de 2026-11-02 (3 repetições, temperatura 0, top_p 1):

| runner | acerto | macro-F1 | p50 | p95 | sem nível |
|---|---|---|---|---|---|
| heurística | 67,7% | 0,686 | 0 ms | 0 ms | 0 |
| `inception/mercury-3` | 67,7% | 0,586 | 902 ms | 1506 ms | 0 |

O que estes números dizem, sem maquilhagem:

- O **100%** que o corpus de treino dava não generaliza. Em dados novos ambos
  ficam por dois terços.
- O **modelo não bate a heurística** em acerto e é pior em macro-F1. A sua
  vantagem é de forma, não de substância: resiste melhor a mudanças de registo.
- Ambos **sub-chamam `media`** (cobertura 0,36 na heurística e 0,18 no modelo):
  o nível intermédio é onde os dois se perdem, e é o mais frequente no uso real.
- A heurística é **fortemente sensível à forma**: o mesmo conteúdo técnico em
  registos diferentes dá 17% de consistência, contra 67–100% do modelo. Um
  router que muda de modelo por causa do tom da frase é um router pior.
- O `mercury-3` é um LM de difusão: com `temperature: 0` **não é
  determinístico** — concordância 0,86–0,91 em 3 repetições e 7–9 casos
  instáveis. Para um classificador isso conta e está declarado.

Por isso o **default continua a ser a heurística**: é gratuita, determinística e
tem o mesmo acerto. O classificador por modelo é opt-in, e a razão para o ligar
não é acerto — é robustez ao registo. Corrigir a cobertura de `media` é trabalho
seguinte, e tem de ser feito num conjunto de desenvolvimento e validado num
**terceiro** conjunto cego, não nestes dois.

## Prontidão para o catálogo público

A matriz completa, regra a regra (18 regras, com a evidência de cada uma e os
dois bloqueios que faltam), está em **`docs/CATALOGO.md`**. Resumo aqui do que
muda o desenho, não do que é processo:

Regras verificadas à data desta versão, com a evidência onde ela existe:

- **Só superfícies públicas**, e uma exceção declarada. O plugin usa middleware,
  `ctx.register_*`, `agent.switch_model`, `agent.reasoning_config` e o SDK do
  dashboard. Depende de **uma** superfície não pública —
  `resolve_runtime_provider`, para trocar o modelo do agente vivo — isolada em
  `seams.py`, com a regra 9 do catálogo citada e a indicação do sítio único a
  mudar quando existir seam público. As outras duas que existiam saíram:
  `get_active_profile_name` foi substituída por `HERMES_PROFILE`, e
  `reload_gateway_plugins` desapareceu com o comando `vmm recarregar`
  (recarregar plugins é operação do anfitrião). Não há patch a código do Hermes
  nem a markup do Desktop em runtime. `hermes_constants` e `agent.secret_scope`
  não contam para esta exceção: ambos estão na lista de módulos públicos do
  próprio validador.

  **Porque a exceção não se resolveu sozinha, medido.** Tentou-se substituir o
  seam pela via pública (`providers.get_provider_profile` +
  `agent.secret_scope.get_secret`) e mediu-se caso a caso contra o resolver
  privado, no perfil default: `deepseek` e `openrouter` reproduzem-se (perfil
  dá `base_url` e `api_mode`; a chave vem do `env_vars` declarado), mas
  `deepseek` resolve por `source: manual`, ou seja a chave está na config e não
  no ambiente; `inception` chega como provider `custom` com `base_url` vazio
  (a config dos providers custom manda); e `openai-codex` é OAuth, onde a
  própria regra 11 proíbe que o token chegue ao plugin. Reimplementar essas
  quatro escadas por fora é duplicar a resolução de credenciais do Hermes —
  exactamente o que a regra desaconselha — e falharia na subscrição. Fica uma
  costura declarada, degradável (`seam_disponivel()`), e o caminho correcto
  para a fechar é o que a documentação do catálogo prescreve: pedir o hook. O
  texto do pedido está em `docs/issue-seam-switch-model.md`.
- **Validação local no commit que se publica**:
  `hermes plugins validate /opt/data/plugins/vipernet-model-manager` → rc 0.
- **`requires_hermes`** declarado em `plugin.yaml` (`>=0.22.0`), nunca mais
  recente do que a release instalada.
- **Sem segredos**: o plugin não lê credenciais; usa o resolver do Hermes. A
  config não guarda chaves. Nada de `subprocess`, `eval`, `exec` nem escrita
  fora de `/opt/data/plugin-data/vipernet-model-manager/`. As três variáveis de
  ambiente que o plugin **lê** (`VMM_DESATIVADO`,
  `VIPERNET_MODEL_MANAGER_OFF`, `VMM_DIR`) não são credenciais — são
  interruptores e um override de directoria de testes — e estão declaradas em
  `optional_env` no `plugin.yaml`, para a capacidade declarada casar com o que
  o código faz. Nota medida, para não se ler o `optional_env` como mais do que
  é: nesta versão do Hermes (0.22.5) o manifesto aceita o campo, mas o único
  consumidor (`config._platform_manifest_env_entries`) só o lê para plugins
  `kind: platform`; num plugin `standalone` a declaração **não** é apresentada
  ao instalador — vale como declaração explícita para quem revê o código, que
  é o que a regra 6 pede. A regra 11 fala de segredos: ler
  `HERMES_PROFILE`/`HERMES_HOME` para saber que perfil se serve não é ler um
  cofre, e nenhum `.env`, `auth.json`, chaveiro ou cofre é aberto por este
  plugin.
- **Efeitos declarados** (o que o plugin faz na rede e no disco, para quem vai
  ler o código antes de o instalar): faz pedidos HTTPS **aos providers que o
  dono configurou** — é a razão de existir, e o classificador opcional faz um
  pedido por turno quando está ligado; escreve `config.json`, `uso.jsonl` e
  `uso.err` em `plugin-data`; o painel serve rotas HTTP no *plugin host*. Não
  abre sockets próprios, não recolhe telemetria, não envia nada para fora além
  dessas chamadas aos providers.
- **Leitura fora dos dados do plugin, só em ferramenta de avaliação**. O
  `eval/extrair_amostra.py` lê o `state.db` do Hermes (tabela `messages`, em
  modo `ro`) para juntar a cada decisão do ledger o prompt que a originou. Não
  corre no caminho do turno, não é invocado pelo middleware nem pelo painel, e
  **recusa-se a escrever dentro do repositório** (rc 2): a amostra contém
  conversa real e fica fora do repo. O que se publica é o código e a rubrica.
- **Versionamento**: `plugin.yaml` e `dashboard/manifest.json` acompanham o
  código que publicam; o painel mostra `versao` lida do manifest, não uma
  constante escrita à mão.

Uma ressalva honesta antes de submeter: o catálogo pede repositório em GitHub
ou GitLab, e este vive num Gitea interno. Publicar lá exige um espelho público
e uma decisão do dono sobre o que vai com ele (o `config.json` de produção não
vai; o `config.example.json` vai).

## Auto-teste

```sh
P=/opt/hermes/.venv/bin/python
$P /opt/data/plugins/vipernet-model-manager/selftest.py              # 190
$P /opt/data/plugins/vipernet-model-manager/test_analise.py          # 40
$P /opt/data/plugins/vipernet-model-manager/test_tarefas.py          # 16
$P /opt/data/plugins/vipernet-model-manager/test_classificador.py    # 55
$P /opt/data/plugins/vipernet-model-manager/test_triagem.py          # 51
$P /opt/data/plugins/vipernet-model-manager/test_turno.py            # 23
$P /opt/data/plugins/vipernet-model-manager/test_eval.py             # 32
$P /opt/data/plugins/vipernet-model-manager/test_holdout.py          # 18
$P /opt/data/plugins/vipernet-model-manager/eval/test_amostra.py     # 22
$P /opt/data/plugins/vipernet-model-manager/eval/test_aprender.py    # 55
$P /opt/data/plugins/vipernet-model-manager/dashboard/selftest.py    # 79 (exige fastapi)
node /opt/data/plugins/vipernet-model-manager/dashboard/test_logica.js  # 35
```

O nível fixado por turno tem ainda duas provas próprias:

- `test_turno.py` (23 verificações, offline) — um turno sintético de seis
  pedidos, com `decidir` a correr uma só vez, cinco `pin: reutilizado`, a
  aritmética `reutilizados == pedidos − turnos`, e os casos negativos: dois
  turnos com o mesmo texto, a chave por hash sem `turn_id`, a rota morta
  (`excecao: rota`), o contexto que deixa de caber (`excecao: capacidade`) e o
  interruptor desligado.
- `eval/replay_turno.py` — o replay sobre turnos **reais** do `state.db`:
  reconstrói a janela de cada pedido, corre o middleware verdadeiro com as
  rotas substituídas e compara o modo `hoje` com o modo `pin`. Medido a
  2026-11-02: 156 turnos, 1 109 pedidos, **38 turnos instáveis em `hoje`** e
  **0 em `pin`**, com `953 = 1 109 − 156` reutilizações.


190 verificações no selftest, 55 no classificador por modelo (servidor HTTP
local a sério, sem mocks), 32 no banco de avaliação (métricas, percentis,
congelamento do gold, recusa de afinar, consistência entre registos) e mais 79
em quatro holdouts rotulados à mão, 79 no painel — incluindo a lógica pura do
editor corrida em node —, 51 na triagem por necessidades, 22 na
amostragem/rotulagem, 55 no ciclo de aprendizagem, 23 no nível fixado por turno
e 16 de tarefas.
