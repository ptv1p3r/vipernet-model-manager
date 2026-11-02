# Conformidade com o catálogo de plugins do Hermes

Regra a regra, o que está verificado, **como** foi verificado, e o que ainda
impede uma submissão. A autoridade é `plugin-catalog/README.md` do
`NousResearch/hermes-agent`; esta página segue a numeração dessa lista.

Versão do plugin a que se refere: **3.11.3** (`plugin.yaml` ==
`dashboard/manifest.json`). Hermes medido: **v0.22.5 (2026.9.24)**.

## Como reproduzir

```sh
hermes plugins validate /opt/data/plugins/vipernet-model-manager --install-deps
```

É o comando exacto do CI do catálogo, corrido contra a árvore que se vai pinar.
Resultado à data: `Validation passed`, com **um** aviso (regra 9, ver abaixo).

## Regra a regra

| # | Regra | Estado | Evidência |
|---|---|---|---|
| 1 | Gate humano por PR | processo | n/a ao código |
| 2 | Repositório público clonável (GitHub/GitLab) | **BLOQUEADO** | vive num Gitea interno (`gitea.home.server`), privado e não alcançável de fora |
| 3 | Pinos por SHA de 40 caracteres | processo | o espelho publica num commit por passagem; o pin da entrada aponta para ele |
| 4 | Sem código que se actualiza a si próprio | conforme | nenhum `git pull`/`download`/auto-update no código de execução; `subprocess` só em `selftest.py` e `dashboard/selftest.py`, para correr `node --check` e o próprio CLI |
| 5 | Bumps de SHA são PRs novos | processo | n/a ao código |
| 6 | Capacidades declaradas == registadas | conforme | `provides_hooks: [post_api_request]`, `provides_middleware: [llm_request]`, `hooks: [post_api_request]`; o validador confirma as três. `optional_env` declara as três variáveis que o plugin lê — nota: em 0.22.5 esse campo só é consumido para `kind: platform`, pelo que num `standalone` vale como declaração para o revisor, não como campo do instalador |
| 7 | Scanner de instalação | conforme | `security scan — safe`; nada de `dangerous` nem `caution` |
| 8 | Superfície do SDK sem patch | conforme | o painel é um plugin de **dashboard web** (não `desktop/plugin.js`). Sem `MutationObserver`, `Object.defineProperty`, escrita em `prototype`, `eval`, `new Function`, `import()` dinâmico nem injecção de `<script>` em `dist/index.js` (33 475 B) |
| 9 | Só superfícies públicas | **1 aviso** | `seams.py:45` → `hermes_cli.runtime_provider.resolve_runtime_provider`. Ver a secção seguinte |
| 10 | Política de dependências | conforme | nenhuma dependência Python declarada; `python dependencies — none declared` |
| 11 | Credenciais vêm do Hermes | conforme | o plugin não abre `.env`, `auth.json`, chaveiro nem cofre; não guarda chaves na config. As variáveis que lê (`VMM_DESATIVADO`, `VIPERNET_MODEL_MANAGER_OFF`, `VMM_DIR`, `HERMES_PROFILE`, `HERMES_HOME`) não são credenciais — são interruptores, um override de testes e nomes de perfil. `agent.secret_scope` está na lista de módulos públicos do validador |
| 12 | Aprovações e execução não assistida | conforme | não lança processos, não aprova nada, não contorna guardas; sem decisão o pedido segue intacto (fail-open) |
| 13 | Divulgação de comportamento de risco | conforme | secção «Efeitos declarados» no README: que pedidos HTTPS faz e a quem, que ficheiros escreve, que não há telemetria, e que o `eval/extrair_amostra.py` lê o `state.db` (fora do caminho do turno, e recusa escrever no repo) |
| 14 | Metadados de compatibilidade verdadeiros | conforme | `requires_hermes: ">=0.22.0"` em SemVer, não mais recente do que 0.22.5; `version` do manifesto == `plugin.yaml` |
| 15 | Sem skins nem forks de plugins incluídos | conforme | plugin original, standalone |
| 16 | Uma entrada por linhagem | conforme | sem fork de plugin listado |
| 17 | Sem payload executável não inspeccionável | conforme | nenhum binário, `.pyc`, `.pyo`, `.so`, arquivo ou `.whl` na árvore; `file` não detecta ELF/Mach-O/PE/Wasm |
| 18 | Gestores de passwords vêm do fabricante | n/a | não regista `browser_vault` |

## Regra 9: a única exceção, e porque não se resolveu

`seams.py` isola **uma** dependência de módulo interno do Hermes:
`resolve_runtime_provider`, necessária para trocar o modelo do agente vivo —
`agent.switch_model` exige `api_key`, `base_url` e `api_mode` já resolvidos.

Tentou-se substituir pela via pública e mediu-se caso a caso, no perfil
`default`, contra o resolver privado:

| provider | privado | via pública (`providers` + `agent.secret_scope`) |
|---|---|---|
| `deepseek` | endpoint + chave (`source: manual`) | endpoint e `api_mode` iguais; a chave **não** se reproduz — está na config, não no ambiente |
| `openrouter` | endpoint + chave (`source: env:OPENROUTER_API_KEY`) | reproduz-se por inteiro |
| `inception` (custom) | provider `custom`, `base_url` da config | perfil público devolve `base_url` vazio |
| `openai-codex` | OAuth (`auth_type: oauth_external`) | sem token — e a regra 11 proíbe que o token chegue ao plugin |

Conclusão: reimplementar as escadas por fora duplicaria a resolução de
credenciais do core (o que a regra 9 quer evitar) e deixaria a subscrição por
fazer. A costura fica declarada, degradável por `seam_disponivel()`, e o
caminho prescrito para a fechar é pedir o hook — texto pronto em
`docs/issue-seam-switch-model.md`.

## A rota sancionada que existe: `agent.plugin_llm`

Medido em 0.22.5, porque é a alternativa óbvia e um revisor vai perguntar por
ela. `agent.plugin_llm` está na allowlist pública e expõe `complete`,
`complete_structured`, `acomplete` e `acomplete_structured`, com
`provider=` e `model=` **só** se o utilizador autorizar
(`plugins.entries.<id>.llm.allow_provider_override` / `allow_model_override`,
mais allowlists), e `json_mode`/`json_schema` para resposta estruturada. As
capacidades correspondentes no manifesto são `llm.provider_override` e
`llm.model_override` (`hermes_cli/plugin_capabilities.py`), descritas como
«uses your credentials» — ou seja, o Hermes põe a credencial e o plugin nunca
lhe toca.

**Serve para o classificador, não para o routing.** Duas limitações, ambas
medidas na assinatura:

1. `complete()` aceita `messages, provider, model, temperature, max_tokens,
   timeout, agent_id, profile, purpose, task` — **não** aceita
   `reasoning_effort`. Migrar o classificador para esta rota perderia o
   parâmetro `esforco` (3.6.1), que é o que mantém o `mercury-3` em 665 ms/68
   tokens com JSON.
2. É uma chamada **do plugin**, não do turno: não muda o modelo que serve o
   agente. O routing continua a precisar de `agent.switch_model` com
   credencial resolvida — o seam da regra 9.

Fica registado como o caminho correcto para o classificador **quando** a API
tiver `reasoning_effort`, e como argumento no pedido de hook (o hook pedido
resolve os dois casos de uma vez). Migrar agora seria trocar a gestão de
credenciais do classificador — que já é conforme, porque a chave vem do
resolver do Hermes e não de um ficheiro — por uma regressão funcional no
`esforco`.

## O que falta para submeter

1. **Repositório público** (regra 2). É o bloqueio duro: o catálogo só aceita
   URL `https://` que qualquer pessoa clone, em GitHub ou GitLab. Publicar o
   espelho exige decisão do dono sobre o que vai: `config.example.json` vai, a
   config de produção não (e o `.gitignore` já a exclui); o `eval/` vai (é
   código e rubrica, sem conversa real — a amostra vive fora do repo).
2. **Pedir o hook** de `ctx.switch_model` (regra 9) e, quando existir, trocar
   `seams.py` por ele. Sem isso a entrada leva um aviso que um revisor pode
   transformar em recusa.
3. **Screenshots do painel** para o campo `screenshots:` da entrada — o
   catálogo pede imagens para tudo o que tem UI.
