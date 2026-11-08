# Changelog

Newest first. One line per change, grouped by the version users see in
`plugin.yaml`. The version in `plugin.yaml` and in `dashboard/manifest.json` is
the same number the panel shows and the catalogue lists; a bump of the catalogue
pin is a new PR whose diff a reviewer reads (catalogue rule 4), and this file is
what that reviewer reads first.

Older history (before 3.45.0) lives in `git log` — the repository was published
with one automatic commit per change, each with a per-file summary.

## 3.47.12 — 2026-11-08

- **Botão de sonda de ligação na página de um modelo.** Ao criar uma rota para um
  provider/modelo novo não havia forma de saber, antes de gravar, se aquele par
  responde: o `Validar` só verifica a forma da configuração e o primeiro sinal de
  uma chave errada era o turno seguinte a falhar. Passa a haver um icon **⚡
  Testar ligação**, junto dos campos `provider` e `modelo`, que faz **uma**
  chamada mínima ao provider (`max_tokens: 8`, mensagem `ping`) e mostra o
  resultado ao lado: `✓ respondeu em N ms · HTTP 200 · provider/modelo` ou
  `✗ falhou · HTTP 401 · <motivo do provider>`.
- **A sonda não grava nada e não lê o catálogo.** Endpoint novo
  `POST /modelo/testar` (`dashboard/api.py`), com `sondar_rota()` a resolver a
  credencial por `seams.resolver_provider` — a mesma via pública do Hermes, nunca
  o ambiente nem um ficheiro. A chave não sai na resposta (e é substituída por
  `***` se um provider a ecoar numa mensagem de erro).
- **Falhar a ligação não é um erro do painel.** A resposta é 200 com `ok:false` e
  o motivo — a API do painel respondeu, foi o provider que não. Um 5xx aqui faria
  o ecrã mostrar um erro do painel em vez do erro que o dono precisa de ler.
  Provider servido por subprocesso (`openai-codex`) e falta de credencial dizem
  exactamente isso, em vez de um «falhou» mudo.
- **O resultado é limpo a cada tecla.** Mudar o `provider` ou o `modelo` apaga o
  verde anterior: um ✓ ao lado de um par diferente é a mentira mais fácil de
  escrever neste painel. O botão fica desligado enquanto não houver os dois
  campos, para não se gastar um pedido a descobrir que falta um.
- Medido a 2026-11-08 no processo do agente: `deepseek/deepseek-flash` →
  `ok=true, http=200, 881 ms`; modelo inexistente → `http=400` com a lista que o
  provider aceita; provider desconhecido → `sem credencial … neste processo`.
  Bateria: 218/218 no auto-teste do painel (14 novas), 354/354 em node (10
  novas), 30/30 ficheiros Python, `test_idioma` e `test_chaves_fechadas` verdes.

## 3.47.11 — 2026-11-08

- **Dois termos genéricos juntos deixaram de associar a tarefa.** A 3.47.10
  exigia companhia e aceitava-a na forma de um segundo termo qualquer da mesma
  tarefa, o que deixava passar `a entidade e o sensor do quadro` e `explica as
  automações e as entidades deste documento`. Agora a regra é uma só: sem um
  termo **específico** da tarefa não há atribuição; os genéricos ficam na lista
  `termos` como contexto e nunca como prova. Medido a 2026-11-08 sobre 838
  mensagens de utilizador de 48 h em `state.db` com a config viva do perfil
  `default`: 36 atribuições antes, 36 depois, **0 perdidas** — nenhuma mensagem
  real dos últimos dois dias se apoiava em dois genéricos; a regra fecha um
  buraco que a amostra não estava a exercer.
- **Guardas**: `test_tarefa_prova.py` ganha `test_dois_genericos_nao_detectam`
  (com o caso reportado) e `test_especifico_com_generico_detecta`; a asserção
  antiga, que exigia detecção com dois genéricos, foi invertida.

## 3.47.10 — 2026-11-08

- **A atribuição de tarefa ficou auditável — e o termo genérico deixou de
  chegar sozinho.** O registo de utilização dizia `origem: tarefa` e nada mais:
  nem qual tarefa, nem se vinha deste turno, nem **que termo** a tinha fixado. O
  dono leu `tarefa` num turno de trabalho no model manager, sem actividade em
  Home Assistant, e não havia como responder. O ledger passa a levar
  `tarefa_termo`, `tarefa_termos`, `tarefa_texto_chars` e `tarefa_texto_sha`
  (digest do texto já sem contexto injectado), a herança transporta a prova, e
  `nivel_origem` separa a origem do **nível** da origem do **modelo** (que é o
  que `origem` sempre disse).
- **`sensor`, `entidade` e `automação` são termos genéricos**: aparecem em
  trabalho de plugin, de código e de documentação. Sozinhos associavam a tarefa
  e mandavam o turno para o modelo dela. Agora só contam com companhia da mesma
  tarefa na mesma mensagem. Medido a 2026-11-08 sobre 588 mensagens de 24 h de
  `state.db`: 88 turnos atribuídos a uma tarefa → **9**; os 79 que saíram
  tinham apenas termos genéricos.
- **O painel diz a proveniência na célula**: `tarefa · home-assistant ·
  «sensor»`, e `herdada 3t` quando o turno apenas herda; o `title` leva o nível
  e o digest, para a linha ser reproduzível. Antes a célula mostrava só
  `tarefa`.
- **O contexto anexado deixou de poder classificar o pedido**: num wrapper de
  pesquisa o pedido vem depois de `</context>` e o documento anexado não é texto
  do utilizador. Medido: neutro nas 588 mensagens de 24 h (0 mudanças) — fica
  como defesa.
- **Guardas**: `test_logica.js` 337 → 344 (rótulo e detalhe da origem);
  `test_tarefa_prova.py` novo, 10 testes (genérico sozinho, dois
  genéricos, prova e digest estáveis, memória injectada, documento anexado,
  herança a transportar a prova, turno de plugin sem tarefa); Python 30/30
  ficheiros rc 0; painel `204/204`; `test_painel_estado.py` 100.

## 3.47.9 — 2026-11-08

- **O campo `provider` de uma rota passou a texto livre com sugestões.** Era uma
  lista fechada com os providers que a configuração já usava: para criar a
  primeira rota de um provider que o Hermes serve — com credencial própria —
  não havia caminho nenhum no painel, e o dono ficou bloqueado ao adicionar um
  modelo. O backend nunca recusou um provider novo (`_normalizar_entrada` só
  exige que não venha vazio), logo o defeito era só do formulário. Agora é um
  `<input>` com `datalist`, alimentado por `opcoes.providers` (os da
  configuração) e por `opcoes.providers_conhecidos` (os do registo do núcleo,
  juntados por `_providers_do_nucleo()`, fail-open). Sugerir não é proibir: o
  nome escrito entra na entrada do catálogo tal e qual.
- **Guardas**: `test_logica.js` 331 → 337 verificações (o campo deixou de ser
  `Seletor`, as sugestões juntam as duas fontes sem repetir, e um nome fora das
  sugestões chega ao `onChange` e sobrevive ao `formParaEntrada`);
  `test_painel_estado.py` 93 → 100 (validar e gravar aceitam um provider fora
  das listas, e o valor fica no catálogo). Painel `204/204`. O `index.js` troca
  a chave de ajuda «lista fechada: os providers que a configuração já usa» por
  «escreve o nome — a lista sugere os da configuração e os que o Hermes
  conhece», nas duas línguas (dicionário mantém as 479 chaves).

## 3.47.8 — 2026-11-08

- **Licence**: dual `MIT OR Apache-2.0` (`LICENSE`, `LICENSE-MIT`,
  `LICENSE-APACHE`), declared in `plugin.yaml` as `license:`. A public
  repository without a licence is cloneable but not usable, and the catalogue
  rule 2 lives on a repository people can actually adopt.
- **Manifest description cut from 6 239 to 376 characters.** It was 96 % of
  `plugin.yaml`; the panel and `hermes plugins list` show one line, and the
  detail already lives in the README, which is the page the catalogue renders.
- **CI on the repository** (`.github/workflows/tests.yml`): the same test battery
  run locally, on Python 3.11 and 3.13, plus `node --check` and the panel logic
  tests, plus the licence/manifest guards. A pin bump no longer reaches a PR
  without its own green run.
- **`test_catalogo.py`**: the catalogue entry, the manifest and the panel must
  agree — entry never ahead of the code, `name`, `requires_hermes` and
  `capabilities` identical, `sha` 40 hex, the three screenshot URLs carrying the
  pinned sha, and, when the pinned commit is in the clone, the entry's `version`
  equal to the `version` at that commit. This divergence happened twice; nothing
  failed either time.
- This changelog.

## 3.47.7 — 2026-11-07

- **Saving the configuration in the panel no longer fails with an opaque
  "conflicts with the current state on the server" in English.** The server sent
  the reason under `erro` only, which the dashboard client does not read (it
  looks for `detail`, `message`, `error`); the client also detected the conflict
  with `indexOf("409")`, and the core `ApiError` carries `.status` with a human
  sentence and no number. The 409 body now carries `detail` (FastAPI convention,
  `erro` kept for compatibility) and the client branches on `e.status === 409`,
  so the "reload the current version" path actually runs.

## 3.47.6 — 2026-11-07

- **Language per request.** `X-VMM` (and `VMM_IDIOMA`) travel with every dashboard
  call, sent from the single `pedir()` helper, and resolve through a
  `ContextVar`: `_FORCAR` → request → `VMM_IDIOMA` → config. A `ContextVar`
  rather than a module variable, so two concurrent requests in different
  languages do not overwrite each other.

## 3.47.5 — 2026-11-07

- Field labels in the configuration screen translated, with a coverage check in
  the panel self-test.

## 3.47.4 — 2026-11-07

- Table headers translated, with a coverage check that fails when a header is
  missing from one language.

## 3.47.3 — 2026-11-07

- Elapsed time ("5 min ago" / "há 5 min") translated as whole phrases with
  `{n}` / `{u}` substitution instead of one branch per language, which used to
  produce "5 ago min" and empty translations.

## 3.47.2 — 2026-11-07

- Option labels in the panel translated; the self-test now checks every label in
  both languages.

## 3.47.1 — 2026-11-07

- The panel's version comes from `plugin.yaml` (the same file the loader and the
  catalogue read), asserted by test — the manifest and the code cannot disagree
  in silence.

## 3.47.0 — 2026-11-07

- **Catalogue rule 9 closed with no exception.** `seams.py` resolves credentials
  and endpoints through `agent.credential_pool.load_pool`, which is Hermes's own
  resolution, instead of the non-public
  `hermes_cli.runtime_provider.resolve_runtime_provider`. `get_active_profile_name`
  gave way to `HERMES_PROFILE` and `reload_gateway_plugins` disappeared with the
  `vmm recarregar` command. `hermes plugins validate` reports `no core override`
  with no private-call warning; one undocumented module import remains, the
  subscription fallback, and it is an issue rather than a patch.

## 3.46.0 — 2026-11-07

- Formatting guard widened from a list of forbidden forms to the class: numeric
  f-string specifiers (including specs built at runtime), `%`-format,
  `format()`, `strftime`, currency symbols by Unicode category `Sc`, translation
  templates with a specifier. Exemptions are declared per file and per type with
  a written reason, and an exemption whose site disappeared fails the test.

## 3.45.1 — 2026-11-07

- Closed the formatting sites that escaped the guard (the guard was green; the
  code was not).

## 3.45.0 — 2026-11-07

- Formatting of values in a single module (`formato.py`), with the locale passed
  explicitly: dates, numbers, percentages and currencies follow the same language
  as the rest of the text. The ledger and diagnostics stay deliberately neutral
  (decimal point, no grouping) so recorded evidence is comparable across
  sessions.
