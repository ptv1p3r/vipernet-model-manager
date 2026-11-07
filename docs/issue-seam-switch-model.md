# Pedido de hook: trocar o modelo do agente vivo com credenciais resolvidas

> Rascunho de issue para `NousResearch/hermes-agent`, escrito segundo a secção
> *Asking for a hook* da página de submissão ao catálogo: «descreva o que
> precisa e porquê; preferimos acrescentar uma costura a listar um patch».
> Não foi aberta por mim — é texto para o dono do repositório publicar.

## Título

Plugin hook: `ctx.switch_model(provider, model)` com a credencial resolvida pelo core

## O que falta

Um plugin de routing por complexidade precisa de trocar o provider+modelo do
**agente vivo**, por turno. Hoje a única via é `agent.switch_model(model,
provider, api_key=..., base_url=..., api_mode=...)`, e essa assinatura exige que
o chamador já traga a chave, o endpoint e o `api_mode`.

Para os obter, um plugin só tem `hermes_cli.runtime_provider.resolve_runtime_provider`,
que não está na lista de módulos públicos do validador do catálogo (regra 9) —
logo o plugin leva um aviso de acoplamento que um revisor pode transformar em
recusa.

## Porque a via pública não chega — medido

Tentei resolver pela superfície pública (`providers.get_provider_profile` +
`agent.secret_scope.get_secret`), no perfil `default` desta instalação, e
comparei com o resolver privado:

| provider | resolver privado | via pública |
|---|---|---|
| `deepseek` | `base_url` + `api_mode` + chave (`source: manual`) | endpoint e `api_mode` reproduzem-se; a chave **não** — está na config, não no `env_vars` |
| `openrouter` | `base_url` + `api_mode` + chave (`source: env:OPENROUTER_API_KEY`) | reproduz-se por inteiro |
| `inception` (custom) | provider `custom`, `base_url` da config, chave própria | perfil devolve `base_url` vazio — a config de custom providers manda |
| `openai-codex` | OAuth (`auth_type: oauth_external`) | sem token; e a regra 11 proíbe que o token chegue ao plugin |

Duplicar estas escadas dentro do plugin significa reimplementar resolução de
credenciais do core — o oposto do que a regra 9 quer — e deixaria a troca para
subscrição por fazer.

## Proposta

Uma destas duas, por ordem de preferência:

1. `ctx.switch_model(provider: str, model: str, *, reasoning_effort: str | None = None) -> bool`
   — o core resolve credencial, endpoint e `api_mode` internamente (a lógica de
   `resolve_runtime_provider` + `agent.switch_model` fica do lado do core) e
   devolve se a troca aconteceu. Para providers OAuth, o core monta o cliente e
   o token nunca passa pelo plugin.
2. Alternativa mais estreita: documentar `resolve_runtime_provider` como
   público **apenas** para consumo de plugins, com contrato estável e sem
   devolver chaves de providers OAuth (nesse caso devolver um cliente ou um
   `credentialed_provider_request` já montado).

## Consumidor concreto

`vipernet-model-manager` (plugin de routing por complexidade, middleware
`llm_request` + hook `post_api_request`): escolhe a rota do turno dentro de um
catálogo declarado pelo utilizador e troca o modelo do agente. É exactamente o
consumidor concreto que a página de hooks pede.
