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

## Analise de complexidade (3.4)

O classificador pontua sinais independentes e corta em dois limiares:
`leve` (< media), `media` (>= 3) e `alta` (>= 6). Quatro regras o mantem honesto:

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

A regra dos dois sinais (`media_sinais: 2`) fica implementada e **inativa**: so
actua quando a chave existe nos limiares. `test_holdout.py` versiona o holdout2
com baseline 17/18 — o unico erro conhecido ("NPM ou Traefik, justifica") e
discutivel no rotulo e **nao se calibra contra ele**.

## Painel no dashboard (3.3)

O plugin traz um painel próprio no dashboard do Hermes, na tab **Model Manager**
(`/model-manager`). Fica em `dashboard/`:

| ficheiro | papel |
|---|---|
| `manifest.json` | declara a tab (`position: end`), o `entry`, o `css` e o `api` |
| `api.py` | backend montado em `/api/plugins/vipernet-model-manager/` |
| `dist/index.js` | IIFE que usa `window.__HERMES_PLUGIN_SDK__` e `__HERMES_PLUGINS__.register` |
| `dist/style.css` | geometria do painel (cores e tipografia vêm do tema do host) |

O painel mostra, por perfil: o interruptor, a estratégia, as associações de tarefa,
o catálogo com a complexidade de cada modelo e o último modelo servido; em baixo,
o registo das últimas respostas (quando, perfil, modelo, provider, nível, origem,
superfície, sessão).

Escreve **só** o interruptor — global ou por perfil — por `gestor.gravar`
(validação, backup e `os.replace`), com releitura independente antes de responder OK.
Catálogo, tarefas e log são leitura: mudam-se no `config.json`, não no browser.

O host descobre painéis por `<plugins root>/<nome>/dashboard/manifest.json`; a cache
de descoberta do processo do dashboard **não** se invalida sozinha quando aparece um
painel novo — depois de instalar, reiniciar o contentor do dashboard (ou forçar
`/api/dashboard/plugins/rescan`). O plugin do agente não precisa de reinício: a config
é lida a quente.

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
               "limiares": {"media": 3, "alta": 7}, "descer_apos": 2},
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

1. resolve o perfil do turno (o gateway multiplexado serve vários no mesmo
   processo);
2. aplica os interruptores (global, perfil) — se algum está em baixo, nada muda;
3. classifica a complexidade e aplica **histerese**: sobe já, desce só depois de
   `descer_apos` avaliações abaixo do nível corrente (evita trocar de rota e
   estragar a cache por causa de uma mensagem curta);
4. percorre o catálogo do perfil na ordem da estratégia, saltando entradas cujo
   provider não tem credencial no escopo desse perfil;
5. troca o agente vivo (`agent.switch_model`): cliente HTTP, chave, `base_url`,
   `api_mode` e contabilidade mudam juntos — não é só reescrever o campo `model`.

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

## Auto-teste

```sh
PYTHONPATH=/opt/hermes python3 /opt/data/plugins/vipernet-model-manager/selftest.py
PYTHONPATH=/opt/hermes python3 /opt/data/plugins/vipernet-model-manager/test_tarefas.py
```

106 verificações no selftest e 16 testes de tarefas: validação do esquema
(incluindo o `acesso` e os casos negativos), semântica de perfil não
gerido/desligado, complexidade por modelo, rodízio, histerese, troca com
credencial do perfil, salto de rota sem credencial, resposta com provider/
modelo efectivos e correlação, `verificar` a resolver no contexto do perfil,
e o CLI a escrever config que o plugin volta a ler.
