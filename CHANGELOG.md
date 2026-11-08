# Changelog

Newest first. One line per change, grouped by the version users see in
`plugin.yaml`. The version in `plugin.yaml` and in `dashboard/manifest.json` is
the same number the panel shows and the catalogue lists; a bump of the catalogue
pin is a new PR whose diff a reviewer reads (catalogue rule 4), and this file is
what that reviewer reads first.

Older history (before 3.45.0) lives in `git log` — the repository was published
with one automatic commit per change, each with a per-file summary.

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
