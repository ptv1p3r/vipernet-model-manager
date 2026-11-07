# Conformidade com o catálogo de plugins do Hermes

Regra a regra, o que está verificado, **como** foi verificado, e o que ainda
impede uma submissão. A autoridade é `plugin-catalog/README.md` do
`NousResearch/hermes-agent`; esta página segue a numeração dessa lista.

Versão do plugin a que se refere: **3.47.6** (`plugin.yaml` ==
`dashboard/manifest.json`). Hermes medido: **v0.23.0 (2026.9.24)**, instalação
`docker`.

## Como reproduzir

```sh
git clone https://github.com/ptv1p3r/vipernet-model-manager /tmp/vmm
git -C /tmp/vmm checkout <sha pinado>
hermes plugins validate /tmp/vmm --install-deps
```

É o comando exacto do CI do catálogo, corrido contra o commit que se pina.
Resultado à data: `Validation passed`, 16 verificações verdes e **zero avisos** —
`security scan — safe`, `no core override`, capability probe, hooks e middleware
declarados a casar com o registo.

## Regra a regra

| # | Regra | Estado | Evidência |
|---|---|---|---|
| 1 | Gate humano por PR | processo | n/a ao código; falta abrir o PR |
| 2 | Repositório público clonável (GitHub/GitLab) | **conforme** | `https://github.com/ptv1p3r/vipernet-model-manager`, público, espelho automático do `main` do Gitea. Provado por clone anónimo: `env -i HOME=<vazio> GIT_TERMINAL_PROMPT=0 git -c credential.helper= clone` → rc 0, HEAD `35ce9401` = `main` do Gitea, 3.47.6 |
| 3 | Pinos por SHA de 40 caracteres | conforme | `sha` de 40 hex, nunca tag nem ramo; o commit tem de existir no `main` do GitHub — o espelho preserva o sha do Gitea |
| 4 | Sem código que se actualiza a si próprio | conforme | nenhum `git pull`/download/auto-update no caminho de execução; `subprocess` só em `selftest.py` e `dashboard/selftest.py`, para correr `node --check` e o próprio CLI |
| 5 | Bumps de SHA são PRs novos | processo | n/a ao código |
| 6 | Capacidades declaradas == registadas | conforme | `provides_hooks: [post_api_request, api_request_error, post_tool_call]`, `provides_middleware: [llm_request]`; o validador confirma as quatro (`declared hooks/middleware — matches registrations`). `optional_env` declara as variáveis não-secretas que o plugin lê — declaração para o revisor, porque em 0.23.0 o instalador só lê esse campo em `kind: platform` |
| 7 | Scanner de instalação | conforme | `security scan — safe`; sem `dangerous` nem `caution` |
| 8 | Superfície do SDK sem patch | conforme | é um plugin de **dashboard web** (não `desktop/plugin.js`). Em `dashboard/dist/index.js` (192 419 B) zero ocorrências de `MutationObserver`, `Object.defineProperty`, escrita em `prototype`, `eval`, `new Function`, `import()` dinâmico ou injecção de `<script>` |
| 9 | Só superfícies públicas | **conforme, sem exceção** | `no core override — no runtime rebinds of Hermes core`, sem aviso de import ou de chamada privada. Ver a secção seguinte |
| 10 | Política de dependências | conforme | nenhuma dependência Python declarada (`python dependencies — none declared`) |
| 11 | Credenciais vêm do Hermes | conforme | o plugin não abre `.env`, `auth.json`, chaveiro nem cofre, e não guarda chaves na config; a resolução passa por `agent.credential_pool.load_pool` e `agent.secret_scope`. As variáveis que lê (`VMM_DESATIVADO`, `VIPERNET_MODEL_MANAGER_OFF`, `VMM_DIR`, `HERMES_PROFILE`, `HERMES_HOME`) são interruptores, um override de testes e nomes de perfil — não credenciais |
| 12 | Aprovações e execução não assistida | conforme | não lança processos, não aprova nada, não contorna guardas; sem decisão o pedido segue intacto (fail-open) |
| 13 | Divulgação de comportamento de risco | conforme | secção «Efeitos declarados» no README: que pedidos HTTPS faz e a quem, que ficheiros escreve em `plugin-data`, que não há telemetria, e que `eval/extrair_amostra.py` lê o `state.db` (fora do caminho do turno, e recusa escrever no repo) |
| 14 | Metadados de compatibilidade verdadeiros | conforme | `requires_hermes: ">=0.22.0"` em SemVer, não mais recente do que 0.23.0; `version` do manifesto == `plugin.yaml` == pin |
| 15 | Sem skins nem forks de plugins incluídos | conforme | plugin original, standalone |
| 16 | Uma entrada por linhagem | conforme | sem fork de plugin listado |
| 17 | Sem payload executável não inspeccionável | conforme | 75 ficheiros, nenhum binário por cabeçalho (ELF/Mach-O/PE/Wasm), nenhum `.pyc`/`.pyo`/`.so`/`.whl`/arquivo; 54 `.py`, 8 `.md`, 2 `.js`, 2 `.yaml`, 2 `.json`, 1 `.css` |
| 18 | Gestores de passwords vêm do fabricante | n/a | não regista `browser_vault` |

## Regra 9: de exceção declarada a conforme

Até à 3.46.x o plugin dependia de **uma** superfície não pública,
`hermes_cli.runtime_provider.resolve_runtime_provider`, para entregar ao
`agent.switch_model` uma chave, um endpoint e um `api_mode` já resolvidos —
`switch_model` não os resolve sozinho.

A 3.47.0 fechou-a: `seams.py` passou a usar `agent.credential_pool.load_pool`,
que é a resolução do próprio Hermes. O validador deixou de ter o que assinalar,
e não sobra import de módulo interno não documentado nem chamada privada a
objecto do core.

Porque não bastava reimplementar as escadas por fora: mediu-se, no perfil
`default`, a via pública `providers.get_provider_profile` +
`agent.secret_scope.get_secret` contra o resolver privado, caso a caso.

| provider | via pública | resultado |
|---|---|---|
| `deepseek` | endpoint e `api_mode` iguais | a chave **não** se reproduz — está na config, não no ambiente |
| `openrouter` | reproduz-se por inteiro | chave em `env:OPENROUTER_API_KEY` |
| `inception` (custom) | perfil público devolve `base_url` vazio | falha |
| `openai-codex` | OAuth (`auth_type: oauth_external`) | a regra 11 proíbe que o token chegue ao plugin |

Duplicar essas escadas por fora era exactamente o que a regra desaconselha.
`load_pool` resolve-as dentro do core, logo chaves de sessão, chaves em config e
subscrições nunca ficam na posse do plugin.

## A rota pública que existe: `agent.plugin_llm`

Fica registada porque é a alternativa óbvia e um revisor vai perguntar por ela.
`agent.plugin_llm` expõe `complete`, `complete_structured`, `acomplete` e
`acomplete_structured`, com `provider=` e `model=` só se o utilizador autorizar
(`plugins.entries.<id>.llm.allow_provider_override` / `allow_model_override`), e
`json_mode`/`json_schema`.

**Serve para o classificador, não para o routing.** `complete()` não aceita
`reasoning_effort` — migrar o classificador para esta rota perderia o parâmetro
`esforco`, que é o que mantém o `mercury-3` em 665 ms/68 tokens com JSON — e é
uma chamada do plugin, não do turno: não muda o modelo que serve o agente.
Migrar agora seria trocar a gestão de credenciais do classificador (já conforme,
porque a chave vem do resolver do Hermes) por uma regressão funcional.

## O que falta para submeter

1. **Screenshots do painel** para o campo `screenshots:` da entrada. É a única
   lacuna material: o catálogo pede imagens para tudo o que tem UI, o painel é a
   cara do plugin, e não existe `assets/` no repositório. Têm de ser capturadas
   na versão pinada e com URLs que embutam o sha — a reposição do pin obriga a
   re-pinar as imagens.
2. **Abrir o PR** em `NousResearch/hermes-agent` a acrescentar
   `plugin-catalog/vipernet-model-manager.yaml`. Nenhum PR foi aberto até hoje.
   A descrição do PR leva a divulgação da regra 13 e as capturas.
