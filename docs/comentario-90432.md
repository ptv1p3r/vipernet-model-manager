# Comentario para o issue #90432 (rascunho, por publicar)

Issue: `NousResearch/hermes-agent#90432` — aberto, `needs-decision`, pede um hook transform
para `model`/`provider`/`base_url`. Nao se abre duplicado: comenta-se la com a evidencia do
consumidor concreto.

Estado: **rascunho**. Publicar exige autorizacao explicita do dono (escrita externa, accao a
accao). O comando de publicacao foi interrompido por falta de consentimento a 2026-11-04.

Corpo proposto (em ingles, a lingua do repositorio):

---

Concrete consumer for this seam, with numbers: `vipernet-model-manager` is a per-profile model
router that decides provider+model **once per turn** and has to make that choice effective before
the request is built.

- **Scale it runs at**: 3 profiles, 11 routes (5/6/2 by profile), 3 complexity levels;
  4 044 turns and 11 129 decision events over three days, **2,8 API calls per turn** — the choice
  must survive the whole tool-call loop, so a one-shot `agent.model = ...` assignment is not
  enough.
- **What it uses today**: `hermes_cli.runtime_provider.resolve_runtime_provider`
  (imported in `seams.py:45` and `eval/aprender.py:164`) to learn the resolved provider, plus
  `agent.switch_model(...)` to apply the choice. It works, but it is a **private core import**:
  every release can break it, and `hermes plugins validate` prints it as a catalog rule-9 warning
  on every run — a permanent, accepted-but-not-accepted debt.
- **What would remove the need**: an `api_request`-time transform that may **return** `model`,
  `provider` and `base_url`, applied after the session default is resolved and before the request
  is sent. Reading the resolved provider through `ctx` already works; what is missing is a
  supported way to *set* those three fields.
- **Why the three fields and not just the model**: the routes mix providers with different base
  URLs and auth (an on-prem endpoint next to a cloud one); pinning `model` alone lands on the wrong
  endpoint. Our seam resolves `(base_url, api_key)` per provider for exactly that reason.

Happy to document the failure modes we hit (cache invalidation when the choice is per-call instead
of per-session; hysteresis needed to avoid flapping between levels) if that helps the decision.
