# Pedido de hook: trocar o modelo do agente vivo com as credenciais resolvidas pelo core

> Texto para a issue em `NousResearch/hermes-agent`, escrito segundo a secção
> *Asking for a hook*: «descreva o que precisa e porquê; preferimos acrescentar
> uma costura a listar um patch». Corrigido a 2026-11-07 23:4x WET: a versão
> anterior deste rascunho dizia que o plugin só tinha
> `hermes_cli.runtime_provider.resolve_runtime_provider`, o que deixou de ser
> verdade na 3.47.0 — o `seams.py` usa hoje `agent.credential_pool.load_pool` e
> `hermes_cli.auth.resolve_external_process_provider_credentials`, e o validador
> não assinala nenhum import privado.

## Título

Plugin hook: `ctx.switch_model(provider, model)` com a credencial resolvida pelo core

## O que falta

Um plugin de routing por complexidade escolhe provider e modelo **por turno** e
tem de os instalar no agente vivo. Hoje a única via é
`agent.switch_model(model, provider, api_key=..., base_url=..., api_mode=...)`:
a assinatura exige que o chamador traga já a chave, o endpoint e o `api_mode`.

Para os obter, um plugin reconstrói a resolução de credenciais do core a partir
de duas superfícies públicas. Funciona — mas é duplicação: cada plugin que troca
de rota repete a mesma escada, e a escada muda com o core.

## Estado medido

Plugin `vipernet-model-manager` 3.47.6 sobre Hermes 0.23.0, perfil `default`:

- `hermes plugins validate` → `no core override`, zero avisos de import de
  módulo não documentado e zero de chamada privada. Não há patch de core.
- `agent.credential_pool.load_pool(provider)` devolve `runtime_api_key` e
  `runtime_base_url` para as rotas de API.
- `hermes_cli.auth.resolve_external_process_provider_credentials(provider)`
  devolve, para providers `external_process`, um `api_key` que é um
  **placeholder** (`pconfig.id`) e não um token: o subprocesso detém a
  autenticação real (`hermes_cli/auth.py`). Logo nada de credencial de
  subscrição passa pelo plugin.
- `api_mode` pode ir vazio: `agent/agent_runtime_helpers.py:_resolve_switch_destination`
  preenche-o com `determine_api_mode(...)`.

O que continua fora do alcance do plugin é a rota de **subscrição** (Codex /
ChatGPT): o plugin não a activa nem converte uma rota `api` nela, porque a
regra 11 proíbe que o token lhe chegue.

## Proposta

1. `ctx.switch_model(provider: str, model: str, *, reasoning_effort: str | None = None) -> bool`
   — o core resolve credencial, endpoint e `api_mode` internamente (a lógica de
   `resolve_runtime_provider` + `agent.switch_model` fica do lado do core) e
   devolve se a troca aconteceu. Para providers `external_process`/OAuth o core
   monta o cliente e nada passa pelo plugin.
2. Alternativa mais estreita: documentar `agent.switch_model` como superfície
   estável para plugins, com contrato de compatibilidade, e aceitar que cada
   plugin resolva a credencial por `load_pool`.

## Consumidor concreto

`vipernet-model-manager` — middleware `llm_request`, hooks `post_api_request`,
`api_request_error` e `post_tool_call`: escolhe a rota do turno dentro de um
catálogo declarado pelo utilizador e troca o modelo do agente. É o consumidor
concreto que a secção de hooks pede. O pedido **não é bloqueante**: o plugin
funciona hoje pelas superfícies públicas; a costura serviria para deixar de
duplicar a resolução de credenciais.
