# Changelog

Newest first. One line per change, grouped by the version users see in
`plugin.yaml`. The version in `plugin.yaml` and in `dashboard/manifest.json` is
the same number the panel shows and the catalogue lists; a bump of the catalogue
pin is a new PR whose diff a reviewer reads (catalogue rule 4), and this file is
what that reviewer reads first.

Older history (before 3.45.0) lives in `git log` — the repository was published
with one automatic commit per change, each with a per-file summary.

## 3.47.21 — 2026-11-19

- **A página de cada perfil ganha o interruptor do ensaio em sombra.** O interruptor por perfil já
  existia no ficheiro e no CLI (`vmm sombra on|off|herdar --perfil P`), mas no painel só havia o
  global: para desligar o ensaio num perfil era preciso a linha de comando. A página do perfil tem agora
  um sexto bloco, «Ensaio em sombra», com um botão que liga ou desliga o ensaio **só nesse perfil** e,
  quando o perfil tem escolha própria, um segundo botão «Seguir o global» que o devolve ao interruptor
  de cima. O bloco mostra o estado em vigor, de onde vem (`próprio deste perfil`, `segue o global`,
  herança declarada, padrão) e o estado do interruptor global ao lado.
- **Não é um campo do formulário.** Grava de imediato pelo `POST /sombra` com `perfil` no corpo, o
  mesmo caminho do interruptor global e do CLI, porque é esse que deixa a linha no ledger de controlo
  (`aprendiz/sombra/controlo.jsonl`, âmbito `perfil:<nome>`). O bloco di-lo: não espera por «Gravar e
  voltar» e «Descartar» não o desfaz. O `rev` enviado é o que a página leu, e o `rev` devolvido passa a
  ser o da página, para o «Gravar e voltar» seguinte não acusar conflito por uma escrita do próprio
  painel; num conflito a página **não** adopta o `rev` fresco do `/estado`, senão o formulário lido
  antes da escrita alheia gravava por cima dela.
- **Regra inalterada:** um interruptor próprio ganha ao global nos dois sentidos. Desligado num
  perfil com o global ligado, só esse perfil pára; ligado num perfil com o global desligado, o ensaio
  corre nesse perfil. Vale a partir do turno seguinte, porque um turno em curso mantém o modelo com que
  começou.
- **O interruptor global fica onde estava**, na página de configuração, e ganha por baixo uma linha
  só de leitura com o estado de cada perfil: «ligado» no global não quer dizer ligado em todos.
- **Sem `api.py` novo e sem reinício.** O `/estado` já trazia `sombra.perfis.<nome>` e o `POST /sombra`
  já aceitava `perfil` e `herdar`; a alteração é do bundle e do CSS, servidos do disco a cada pedido.
  A versão anunciada por `/api/dashboard/plugins` e o `motor` do ledger continuam a dizer 3.47.20 até o
  contentor do painel e o gateway serem recarregados.
- Estado em que um perfil não vem no payload: o bloco diz que não veio e não desenha botão nenhum, em
  vez de mostrar «desligado».
- Prova: `test_sombra_painel.py` 60/60 (25 novas: o corpo com `perfil` mexe só nesse perfil, deixa o
  global e os outros intactos, escreve uma linha no ledger, recusa perfil desconhecido com 400 e `rev`
  velho com 409 sem escrever, e gravar o formulário a seguir preserva o bloco);
  `dashboard/test_logica.js` 445/445 (66 novas, entre elas a `Pagina` a correr com estado e um
  servidor falso que regista os pedidos); 10 mutantes do bundle, 10 apanhados. Bateria 2074/2075, com
  as duas falhas que já lá estavam antes desta versão (`test_auditoria.py`, quebra 161 por explicar na
  cadeia de produção; `test_idioma.py`, 24 chaves do CLI da 3.47.20 sem tradução). O `sha256` do bundle
  servido pelo dashboard é o do disco (`0d9dc2a0…`). Não foi visto num browser a sério: o cofre do
  browser estava bloqueado nesta sessão.

## 3.47.20 — 2026-11-19

- **O interruptor do ensaio em sombra ganha casa global (`sombra.global_control`) e rasto em ledger.**
  O bloco `sombra` tinha um `activo` anónimo: ligava-se e desligava-se sem ficar registado quem, quando
  e de que valor para que valor, e o painel e o CLI podiam responder coisas diferentes sobre o mesmo
  interruptor. `global_control` guarda `{activo, ts, por, motivo}` e passa a ser a casa do interruptor
  **global**; o `activo` do topo continua a ser lido (config antiga e perfis que só declaram o
  interruptor) e a validação **recusa** as duas chaves a discordarem, em vez de desempatar pela ordem de
  leitura. Uma só regra de leitura (`gestor.sombra_ligada`) serve o CLI, o painel e o middleware.
- **Herança por perfil explícita, com `herda: true`.** Ausente = o perfil declara o que quiser, como
  sempre; `herda: true` = o perfil obedece ao interruptor global **mesmo que traga um `activo` próprio**
  de uma versão anterior. Um perfil que declara `activo` ganha ao global e o `global_control` herdado sai
  do bloco fundido — sem esse corte, um perfil ligado explicitamente aparecia desligado porque o bloco
  global dizia o contrário (defeito apanhado pelo teste, não pelo uso).
- **Ledger de controlo, append-only, em `aprendiz/sombra/controlo.jsonl`.** Uma linha por gesto — `ts`,
  `acao` (`on`/`off`/`herdar`/`ttl`), `ambito` (`global` ou `perfil:<nome>`), `antes`, `depois`, `por`,
  `rev` da config — escrita com `flock` e `fsync` **depois** da gravação confirmada: um gesto que não
  chegou ao ficheiro não pode ficar registado como feito. `vmm sombra controlo --n N` lê-o de volta, e
  linha ilegível é ignorada em vez de esconder as outras (é no dia do acidente que se lê isto).
- **CLI:** `vmm sombra on|off [--perfil P] [--motivo M]`, `vmm sombra herdar --perfil P`,
  `vmm sombra controlo [--n N]`. O `estado` mostra o `global_control` e a origem efectiva de cada perfil
  (`global`, `proprio`, `herdado`, `padrao`). **Painel:** o `POST /sombra` escreve o mesmo bloco e regista
  o mesmo ledger, e o cartão expõe os últimos 5 controlos.
- **Config aplicada:** `sombra.global_control = {activo: true, ts: 2026-11-19T01:31:32+00:00,
  por: passagem:vmm}` — preserva o `activo=true` que já estava em vigor — e `perfis.trabalho.sombra =
  {herda: true}`. Medido: `vmm sombra off` no global põe os quatro perfis a `desligado`, o trabalho com
  origem `herdado`, e `on` restitui; as duas linhas ficam no ledger.
- Prova: `test_sombra_controlo.py` (15 testes: normalização, discordância, precedência, herança,
  concordância CLI↔middleware sobre a config viva, ledger), `test_sombra_painel.py` 35/35,
  `test_painel_estado.py` 120/120, `test_sombra.py` 35/35, `test_sombra_middleware.py` 25/25.

## 3.47.19 — 2026-11-19

- **O orçamento de saída passa a estar garantido no fio de destino, para os dois fios do Foundry.**
  A reconstrução do payload (`_payload_na_forma_do_destino`) cobre a troca de fio, mas só quando
  consegue reconstruir: um construtor que falhe (fail-open), um payload vindo de outro caminho ou um
  orçamento ausente no próprio fio deixavam a chamada sem `max_tokens` — e a Messages da Anthropic
  recusa-se a chamar sem ele (`TypeError: Messages.stream() missing 1 required keyword-only argument:
  'max_tokens'`), que o classificador do core lê como `context_overflow` e resolve reiniciando a
  sessão. A guarda nova, `_garantir_orcamento_saida`, corre **depois** do esforço e antes da
  resposta, nos três caminhos que devolvem um pedido (troca, troca só de modelo, rota já activa):
  `max_tokens` positivo para `anthropic_messages`, `max_output_tokens` positivo para
  `codex_responses`, os campos do outro fio removidos, e o valor herdado (`agent.max_tokens`, o
  orçamento do fio anterior) respeitado em vez de inventado. Tecto de recurso: 128 000, medido a
  2026-11-19 nos deployments — `/anthropic/v1/messages` responde 200 com 8 192, 64 000 e 128 000 e
  `/openai/v1/responses` responde 200 com `max_output_tokens` 128 000. O evento `troca` do ledger
  ganha `orcamento` quando o campo é preenchido (antes era um defeito silencioso). Provado ponta a
  ponta contra os dois deployments com o payload real do construtor do core: 200 nos quatro casos
  (claude directo, gpt directo, gpt→claude, claude→gpt); 12 verificações novas em
  `selftest.py::testes_orcamento_saida` (235/235 `PASS`).

## 3.47.18 — 2026-11-18

- **`key_cmd` passa a resolver credenciais de provider custom.** `seams.py::_de_config_custom`
  resolvia só `api_key` e `key_env`; um provider que guarda o **comando** que imprime a chave (o
  idiom `apiKeyHelper`, chave lida do cofre em tempo de pedido) aparecia como «chave curta (0 B)» e a
  rota era dada por `rota_indisponivel` sem nunca ser servida. A via nova, `_de_key_cmd`, chama a
  superfície pública `hermes_cli.auth_declared_providers.resolve_declared_api_key_provider`, que já
  mina o `key_cmd` com cache de token por `(perfil, comando)`. Corre **depois** de `api_key` e
  `key_env`: uma chave estática já resolvida não paga o custo de correr o comando. Medido com
  `foundry-edp` (`claude-fable-5-2`): `vmm verificar trabalho` passa de `chave curta (0 B)` a
  `ok … fonte=providers.foundry-edp/key_cmd; chave=84 B`, com o segredo só no cofre.
- **Três testes novos** em `test_sonda_provider_custom.py` (8 → 11): `key_cmd` resolve, `key_cmd` que
  falha não inventa chave, `key_env` ganha ao `key_cmd`.

## 3.47.17 — 2026-11-10

- **Novo painel «Ensaio em sombra — progresso», ao lado do registo de
  utilização.** Mostra a percentagem de cada braço, a percentagem do ensaio
  (a do braço que atrasa), a data de fim prevista, o ritmo por dia e os modelos
  em uso, com a sonda de vida de cada um. **Só aparece com o ensaio ligado**
  (`sombra.activo`, lido do `/estado`): desligado, o cartão não existe.

- **A percentagem conta chamadas de ferramenta, não turnos.** A medida primária
  selada é «taxa de falha por chamada medida por `exit_code`», e foi sobre
  chamadas (48 582) que `p0` e `n_por_braco` foram calculados. Medir turnos
  contra um `n` em chamadas dava 1,5% onde o valor real é 51,2% — e uma data de
  fim de mais de um ano em vez de dias. `sombra.contagens` faz a contagem na
  unidade do pré-registo e `sombra.projectar` deriva ritmo e data.

- **`GET /sombra/progresso`** lê o ledger na hora (cache por `mtime`/tamanho/
  selo) e devolve `sombra` + `progresso`. Sem pré-registo selado responde
  `estado: sem_selo` em vez de zeros; o painel diz que falta o payload novo
  enquanto o contentor do dashboard não for reiniciado.

- **Braço morto não é braço lento: sem data.** Com `todos_vivos: false` o
  estado é `travado`, o motivo nomeia o braço e o `HTTP` (404 no
  `nemotron-3-ultra` na medição desta passagem) e **não se apresenta data
  nenhuma**. A projeção só existe com os dois braços vivos.

- **A percentagem e a data medem-se, não se herdam.** O valor sai do ledger a
  cada leitura; um acumulado de sessão anterior não é repetido sem ser
  reproduzido, e a âncora do ritmo («média desde a primeira chamada») vai
  escrita ao lado da data.

- **Os testes do painel deixaram de depender do catálogo vivo.** O fixture de
  `test_sombra_painel.py` declara no catálogo copiado o slug do braço b que usa
  na re-selagem, e `test_painel_estado.py` cria a rota livre que remove. Antes,
  limpar um modelo do perfil (`lightning-free`) fazia a bateria falhar por um
  motivo que não era do código: as duas falhas foram reproduzidas iguais no
  commit `acdc240`, sem alterações locais. Bateria: 2000/2000 em 38 ficheiros.

## 3.47.16 — 2026-11-09

- **Cada modelo tem botão «Remover».** A página de uma rota do catálogo passa a
  ter, no fim das acções, um botão que a tira do perfil. Em duas etapas: o
  primeiro clique pede confirmação («Confirmar remoção» + «Cancelar»), porque é
  o único gesto do painel que tira uma coisa do perfil e não há desfazer no ecrã.
  Não aparece em rotas novas — uma rota que ainda não existe não tem o que
  remover — e fica desactivado enquanto há gravação, validação ou sonda em curso.

- **A remoção é recusada quando alguma superfície chama a rota.** `POST
  /modelo/remover` verifica `analise.superficies.<nome>.entrada` **antes** de
  escrever e devolve `ok: false` com a lista das regras que apontam para a rota.
  O `_escrever` já validaria e recusaria (a config fica sempre válida), mas a
  mensagem do schema diz o caminho e não diz que a cura é apontar essas regras a
  outra rota — e uma recusa que não diz o que fazer a seguir é meia recusa. A
  resposta vai com 200 e `ok: false` de propósito: o `fetch` do painel não
  entrega o corpo de uma resposta HTTP de erro.

- **O `pedir` já não deita fora a razão de um erro.** O painel mostrava
  «409: Conflict» para qualquer recusa com corpo, incluindo as que já existiam
  (gravar com `rev` velho, validação falhada). Passa a ler o corpo, põe a frase
  em `.detalhe` e o objecto em `.corpo`, e `.status` — que o `ehConflito` já
  procurava e nunca via num erro do `fetch` local. A mensagem antiga mantém-se
  quando não há corpo, para não partir nenhum chamador.

- Verificação: `python3 test_painel_estado.py` cobre os três desfechos (remove,
  recusa por referência, rota inexistente) mais os 400 de campos em falta e o
  409 de `rev` velho, contra a app FastAPI em memória — 120 verificações.
  `node dashboard/test_logica.js` passa a 365, com 11 a renderizarem a página
  (botão presente numa rota gravada, ausente numa rota nova, dois passos com
  cancelamento) e o dicionário em 497 chaves.

## 3.47.15 — 2026-11-08

- **A superfície fixada ganha também ao *pin* do turno.** O 3.47.14 fechou a
  porta do ensaio em sombra mas deixou a da frente aberta: a regra da superfície
  estava atrás de `if (entrada is None or rota is None)`, ou seja só corria
  quando não havia rota nenhuma. Num turno de vários pedidos, o primeiro
  decidia, gravava o `pin` e — medido no ledger de 2026-11-08 20:13:58 →
  20:14:02, `passo_api` 1 → 2 — os seguintes reutilizavam-no: `entrada` já não
  era `None`, a ordem do perfil nunca disparava, `origem` ficava em `modelo` e
  o ensaio aplicava por cima (12 de 13 respostas de cron ainda no braço depois
  do reload). A guarda cai: a regra corre sempre, exceto com marcador explícito
  do utilizador, e continua a ceder à capacidade, à rota morta há pouco e ao
  modelo que declara não servir a superfície — nos três casos com `excecao`
  registada. Quando substitui uma rota que já vinha do `pin`, o `pin` passa a
  `substituido_pela_superficie`, para o rasto não confundir uma reutilização com
  uma escolha da regra. Nove verificações novas em `test_turno.py`: três
  pedidos no mesmo turno com superfície `cron` fixada (entrada, origem e `pin`
  nos três) e a exceção legítima de um modelo que declara `superficies:
  {cron: false}`.

## 3.47.14 — 2026-11-08

- **A superfície fixada ganha ao ensaio em sombra.** O perfil `default` fixou a
  superfície `cron` na entrada `inception-mercury-3` e, de 43 respostas de cron
  depois disso, **42 saíram no braço** (`origem: sombra`) e uma só no mercury —
  a que apanhou o ensaio parado por `braco_sem_vida`. A config estava certa: o
  ensaio corria **depois** da superfície e substituía-a em silêncio. `_sombra_do_turno`
  passa a receber `superficie_fixada` (do `base["origem"] == "superficie"`, o
  sinal que a regra do perfil já deixava) e recusa com o motivo novo
  `superficie_fixada`, antes das prioridades já existentes (tarefa associada,
  marcador explícito). O ensaio continua a correr em todo o routing livre — só
  deixa de trocar o modelo que o dono mandou fixar. Quatro testes novos:
  recusa unitária, ordem das prioridades (superfície antes de tarefa e marcador),
  o sintoma no middleware (braço b não rouba a superfície, modelo fica no caro) e
  o caso negativo (sem regra, o ensaio aplica como antes).

## 3.47.13 — 2026-11-08

- **Provider custom resolve credencial.** O botão ⚡ Testar ligação respondia «sem
  credencial para inception neste processo» com a chave carregada e a rota a
  responder 200 (`mercury-3`, 847 ms medidos à mão). A pool de credenciais só
  tem o que passou por `hermes auth add`; um provider declarado em
  `providers.<name>` guarda a chave em `key_env` e o endpoint em `base_url`. O
  resolvedor passa a ter essa terceira via — `hermes_cli.config.get_compatible_custom_providers`
  para o par `base_url`/`key_env` (superfície pública; a privada
  `agent.custom_entry_pool` disparava o aviso da regra 9) e
  `agent.secret_scope.get_secret` para o segredo (honra o
  perfil activo; `UnscopedSecretError` cai para o ambiente, como no núcleo). A
  linha do resultado mostra a origem (`providers.inception/key_env`), para se
  saber de onde veio a credencial. Guardas: `test_sonda_provider_custom.py`
  (8 testes, sem rede).

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
