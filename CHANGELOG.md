# Changelog

Newest first. One line per change, grouped by the version users see in
`plugin.yaml`. The version in `plugin.yaml` and in `dashboard/manifest.json` is
the same number the panel shows and the catalogue lists; a bump of the catalogue
pin is a new PR whose diff a reviewer reads (catalogue rule 4), and this file is
what that reviewer reads first.

## 3.49.0

- **Seleção global dos gráficos de utilização.** Em «Editar configuração», marque os gráficos pretendidos e use «Gravar e voltar». Sem `painel_graficos`, o painel apresenta os cinco; `[]` mantém o total e o período sem desenhar gráficos. O interruptor `painel_estatisticas` continua a gravar de imediato e não altera a seleção. O editor usa `POST /config` com `rev`; o gestor recusa identificadores desconhecidos e repetidos. Um backend anterior continua a mostrar os gráficos sem oferecer o novo seletor.

## 3.48.1

- **Uma pausa de quota deixa de ser um braço morto.** Um `HTTP 429` da quota diária de modelos free
  da OpenRouter (1000 pedidos por conta, reset às 00:00 UTC) era lido como slug retirado: o estado
  saía `travado`, o job ficava em falha e o ensaio só voltava na sonda seguinte — 12 h depois. Medido
  a 2026-11-19/20: a quota esgotava-se em ~2 h 46 e a sonda das 19:07 perdia as ~7 h que o reset já
  tinha aberto.
- **A sonda classifica a falha**: `viva`, `quota` (429 ou motivo que o diga) ou `morta`. A hora de
  reposição vem do `X-RateLimit-Reset`/`Retry-After` quando o provider a declara — nunca é inventada.
- **Estado novo `em_pausa`**, com `pausa_ate` e sem data de fim (o ensaio está parado, logo uma data
  seria uma promessa que ninguém cumpre). O middleware regista `quota_esgotada:<braço>` em vez de
  `braco_sem_vida`, mantendo o ensaio parado — atribuir turnos a um braço em 429 queimava o turno.
- **O job de renovação passa a correr de hora a hora**: a recuperação acontece dentro de uma hora do
  reset em vez de esperar a sonda seguinte, e um braço morto a sério é apanhado no mesmo intervalo.

## 3.48.0

- Add a usage statistics card beside the log: chart confirmed responses by model, profile, task, request surface and routing origin across retained detail, with counts, percentages and the observed date range.

Older history (before 3.45.0) lives in `git log` — the repository was published
with one automatic commit per change, each with a per-file summary.

## 3.47.24 — 2026-11-19

- **A troca de fio passa a ser reconstruida nos dois sentidos.** Um turno cuja base fala o fio
  `codex_responses` (o `gpt-6.2-astra` do `default`) e que caia num modelo de chat
  (`deepseek-flash`, `north-mini-code:free`) morria com `TypeError: Missing required arguments;
  Expected either ('messages' and 'model') ...`: o corpo ia com `input`/`instructions` e sem
  `messages`. Medido a 2026-11-19 no `default`, 6 de 12 turnos trocados nesse sentido desde o dia
  anterior. Era o que prendia o flash ao fundo do catalogo — a config aceitava a ordem e o primeiro
  turno de cada sessao falhava.
- **A conversao e do fio de origem, nao do destino.** `_mensagens_de_responses` traz `instructions`
  para uma mensagem de sistema e converte `function_call`/`function_call_output` em `tool_calls` e
  mensagens de ferramenta; `_mensagens_de_anthropic` faz o mesmo com `system` e com os blocos
  `tool_use`/`tool_result`. Antes, so as mensagens eram passadas ao construtor e o prompt de sistema
  perdia-se — a razao por que `_FIOS_RECONSTRUIVEIS` excluia `chat_completions`.
- **`api_mode` vazio deixa de desligar a reconstrucao.** O catalogo nao declara `api_mode` e ha rotas
  cujo `api_mode` nao vem no kwarg: a troca ficava com `fio: null` e o erro era o mesmo de antes. A
  forma do corpo (`input`/`instructions`, `system`, `messages`) diz o fio de origem e o `api_mode` do
  agente ja trocado diz o de destino. Sem nenhum dos dois, nada se inventa.
- **A gordura do fio anterior nao viaja.** O construtor do core reconstroi o corpo do zero, por isso
  `reasoning_effort`, `store` e o orcamento de saida do fio antigo desaparecem com a troca.
- **O `reasoning_content` e reconciliado depois de reconstruir.** O core aplica esse pad em
  `build_request_payload`; a reconstrucao acontece por fora desse caminho e o `deepseek-flash` em modo
  thinking respondia 400 «The `reasoning_content` in the thinking mode must be passed back to the API»
  numa sessao que alterna flash e Fable (medido no `trabalho`, 13:42:01). Chama-se
  `_reapply_reasoning_echo_for_provider` sobre as mensagens convertidas, idempotente e fail-open.
- Testes: `test_reconstrucao_fio.py` (10 verificacoes, com ida e volta pelos transports reais do core)
  e quatro verificacoes novas no `selftest.py` substituem a que dava o caso `anthropic -> chat` como
  «fica para o core».

## 3.47.23 — 2026-11-19

- **A tabela de modelos de cada perfil sai pela `ordem` declarada.** No cartão do perfil, a rota com a
  ordem mais baixa fica em cima e as que não declaram ordem ficam no fim. Duas rotas com a mesma ordem, e
  as que não declaram nenhuma, ficam entre si pela posição no ficheiro. A ordem lê-se como o `gestor` a
  lê (`_chave_ordem`): só um inteiro conta, o resto é «não declarado». O `0` é uma ordem e vem antes do
  `1`.
- **Só a tabela muda.** O ficheiro não é reordenado e o `catalogo` do `/estado` continua na posição do
  ficheiro: as regras de superfície e as sugestões do ensaio leem-no como antes. A escolha do modelo no
  turno não muda — o `gestor` já ordenava os candidatos por `ordem`, custo e `id`.
- **Depois de gravar vê-se logo.** A página do modelo já relia o `/estado` ao gravar; como a tabela
  ordena a cada desenho, a ordem nova aparece nessa releitura, sem recarregar a página. Entre o fecho da
  página e a resposta do `/estado` a tabela mostra ainda a ordem anterior.
- **O `/estado` passa a levar a `ordem`.** Medido no `/estado` vivo antes da alteração: as entradas do
  catálogo traziam `id`, `provider`, `modelo`, `complexidade`, `tarefas` e `acesso`, e mais nada. O cartão
  já tinha código para escrever «· ordem N» na linha, mas o valor nunca saía do servidor. Por isso esta
  alteração toca no `dashboard/api.py` e **só fica visível depois de reiniciar o contentor do
  dashboard**; até lá o bundle novo ordena uma lista sem ordens e a tabela fica como estava.
- **Por corrigir, fora desta alteração.** O `custo` e o `esforço` têm o mesmo problema: o cartão
  desenha-os e o `/estado` não os manda, logo a linha diz sempre «custo —» e a coluna «esforço» fica
  sempre vazia. E a ajuda do campo `ordem` na página do modelo diz «desempate entre custos iguais»,
  quando no `gestor` a ordem manda sobre o custo.
- Prova: `dashboard/test_logica.js` 482/482 (23 novas: a função pura, o cartão, e a `Pagina` a correr
  com um servidor falso em que se muda a ordem pela página do modelo, se apaga e se empata). Contra o
  bundle da 3.47.22 falham 14 dessas 23. `test_painel_estado.py` 128 (8 novas, 4 falham contra o `api.py`
  da 3.47.22). Bateria 2119/2120, com as mesmas duas falhas de antes (`test_auditoria.py`,
  `test_idioma.py`). Com o `api.py` do disco sobre uma cópia da config viva, os quatro perfis saem pela
  ordem declarada. **O controlo negativo por mutação não foi corrido** e não foi visto num browser.

## 3.47.22 — 2026-11-19

- **Uma recusa ao re-selar os braços volta a dizer porquê.** Desde a 3.47.17 o `catch` de
  `mudarBracos`, na `Pagina`, chamava `setErroLocal`, que é estado da `SecaoSombra` e não existe nesse
  âmbito. Quando o servidor recusava a re-selagem, o `catch` rebentava com `ReferenceError`. Medido com a
  página a correr: o bloco dos braços mostrava «setErroLocal is not defined» em vez do motivo, o estado
  não era relido, e o «a gravar» ficava preso a `true`, com o botão «Re-selar braços» desactivado até se
  recarregar a página. A re-selagem aceite não era afectada.
- **Correção:** o handler relê o estado, liberta o «a gravar» e relança o motivo, e é o `catch` de
  `reabrir()`, na secção, que o escreve ao lado do botão. Num conflito de versão (409) a frase é a do
  painel («O ensaio mudou noutro sítio — recarreguei o estado. Repita a re-selagem.»), porque o SDK
  entrega para o 409 uma frase genérica em inglês. As duas chaves de tradução já existiam; o dicionário
  fica em 549.
- **Porque não o banner de topo, como até à 3.47.16.** Medido sobre uma cópia com o código antigo:
  `setErro(...)` seguido de `return carregar()` deixa o banner a `null`, porque o `carregar()` com
  sucesso o limpa. O código antigo não rebentava, mas a mensagem também não ficava no ecrã.
- **Muda um comportamento:** depois de uma recusa a caixa «confirmo que reabro o ensaio» fica marcada
  (antes era desmarcada, porque o handler resolvia sempre). Corrigir o nome do modelo e repetir é um
  clique.
- **Por corrigir, fora desta alteração.** O interruptor global do ensaio tem o mesmo padrão
  `setErro` + `carregar()` e, medido, também fica sem mensagem depois de uma recusa; estratégia,
  superfícies e língua têm o mesmo código, por leitura. E um conflito em que o selo tenha mudado noutro
  sítio remonta a secção (a `key` é o selo), pelo que a mensagem desta correção se perde nesse caso — por
  leitura, o harness não remonta componentes.
- Prova: `dashboard/test_logica.js` 459/459 (14 novas: a `Pagina`, a `PaginaConfig`, a
  `SecaoClassificador` e a `SecaoSombra` a correr a sério, com o clique no botão e um servidor falso que
  recusa com 400, com 409 e depois aceita). Contra o bundle da 3.47.21 as mesmas 14 dão 6 falhas, todas
  no ramo da recusa. 9 mutantes do bundle, 9 apanhados, entre eles o defeito original e o código da
  3.47.16. Bateria 2088/2089, com as mesmas duas falhas de antes (`test_auditoria.py`, `test_idioma.py`).
  O `sha256` do bundle servido pelo dashboard é o do disco (`370cf152…`). Só bundle: sem `api.py` novo e
  sem reinício. Não foi visto num browser.

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
