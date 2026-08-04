# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

CLI tool to sync IP routing policy objects to Regional Internet Registries. Manages IRR route/route6 objects in RIPE NCC, ARIN, and RADb, and RPKI ROAs in RIPE and ARIN.

## Language & Tooling

Python project managed with `uv`. Uses Ruff for linting/formatting, pytest for tests.

## Commands

```bash
uv sync                          # install dependencies
uv run rir-updater config.yaml   # dry-run (no writes)
uv run ruff check .              # lint
uv run ruff format .             # format
uv run pytest                    # run all tests
uv run pytest tests/ripe/        # run a single test file/directory
uv run pytest tests/ripe/test_client.py::TestSyncRoute::test_creates_when_not_exists  # single test
```

Always run `uv run ruff check . && uv run ruff format --check .` before committing. CI enforces both.

## Operator-local files (`local/`)

Operator-specific scripts, live job configs, and working notes live in the
git-ignored `local/` directory — **not** part of the universal tool, never
committed. See `local/CLAUDE.md` for what's there and why. Keep `src/` generic;
put anything that hardcodes our ASNs/maintainers/prefixes or is a throwaway job
config in `local/`.

## Architecture

### Entry point and orchestration

`src/rir_updater/main.py` is the sole CLI entry point (registered as `rir-updater` in `pyproject.toml`). It:
1. Parses CLI args (`--commit`, `--production`, `--registry`, `--setup-test`, `--setup-ote`)
2. Resolves credentials via `credentials.py`
3. Instantiates the appropriate registry clients
4. For each registry, publishes ROAs first (`sync_roas`), then iterates route objects (`sync_route` / `delete_route`) — ROAs must exist before RPKI-aware consumers (RADb) will accept the routes
5. Automatically mirrors every RIPE/ARIN route change to RADb when a `radb` config section exists (tracked via `mirrored_prefixes` to avoid double-syncing)
6. Accumulates results in `Summary` and calls `summary.print_jira()`

### Configuration (`config.py`)

Pydantic models validate the YAML config on load. Key constraints:
- `RouteObject.prefix` must be strict CIDR (no host bits set)
- `RouteObject.origin` / `ROA.origin` must match `AS\d+` and are normalized to uppercase
- `ROA.max_length` must be ≥ the prefix length
- All three registry sections (`ripe`, `arin`, `radb`) are optional

### Credentials (`credentials.py`)

Secrets are stored as `op://vault/item/field` references in the YAML config and resolved at runtime via the `op` CLI. Never resolved until client instantiation. Raises `CredentialError` if `op` is not installed or the reference is invalid. RIPE DB auth is returned as a base64-encoded Basic auth string; RADb returns a `(username, password)` tuple.

### Registry clients

All three clients (`ripe/client.py`, `arin/client.py`, `radb/client.py`) share the same interface:
- `sync_route(route)` → `"created"` / `"updated"` / `"dry-run-create"` / `"dry-run-update"`
- `delete_route(route)` → `"deleted"` / `"not-found"` / `"dry-run-delete"`
- `sync_roas(roas)` → `{"added": N, "deleted": M}` (RIPE and ARIN only). Reconciles only the prefixes present in the config; ROAs for other prefixes are left untouched. In dry-run it still fetches current ROAs (read-only) and returns the real diff — it does not fake the counts.
- Context manager (`with Client(...) as c`) for httpx cleanup

**Update strategy**: always fetch the existing object first, then merge only the managed fields (`route`/`route6`, `origin`, `mnt-by`, `source`, `changed`, `descr`) while preserving all other attributes already on the object.

**RADb transient-failure handling**: the RADb client routes every request through `_send`, which retries idempotent verbs (GET/PUT/DELETE) on `httpx.TransportError` and 5xx with exponential backoff. A create `POST` is *not* retried (a lost response may still have committed server-side); instead, on a transport error the client re-checks existence with a GET before deciding `"created"` vs. re-raising.

**Key differences between clients:**

|              | RIPE                 | ARIN              | RADb                                 |
|--------------|----------------------|-------------------|--------------------------------------|
| Format       | JSON                 | XML (`CORE_NS`)   | JSON via `?format=json`†             |
| Auth         | HTTP Basic header    | `?apikey=` param  | HTTP Basic + `?password=` for writes |
| Env          | test DB / production | OTE / production  | production only                      |
| HTTP clients | two (DB + RPKI)      | one               | one                                  |

† RADb selects output format via a `?format=` query parameter that **defaults to `text` (RPSL)** and ignores the `Accept: application/json` header. The client sets `params={"format": "json"}` on the httpx client so every request returns JSON — without it, `resp.json()` raises `JSONDecodeError` on every GET.

### Error handling

Each client has a `_raise_for_status(resp, context)` helper that extracts human-readable error detail from the registry-specific error format before raising `ApiError`. Never check `resp.status_code` directly for error handling — use these helpers.

**Per-object isolation**: `main.py` wraps every `sync_route`/`delete_route`/`sync_roas` call in a `_try(summary, registry, label, fn)` helper. An `ApiError` from one object is recorded on the `Summary` (as an error entry) and the run continues with the next object — one failure never aborts the whole batch. Only `ApiError` is isolated; other exceptions still propagate. If any failure was recorded, `main()` exits non-zero (so CI/callers detect partial failures even though the run completed).

### Summary output (`summary.py`)

`Summary` accumulates results across all registries and renders a Jira `{code:diff}` block via `print_jira()`. Action → diff character mapping: `created`/`dry-run-create` → `+`, `deleted`/`dry-run-delete` → `-`, everything else → space. Per-object failures (recorded via `record_error`) render as `! <registry> <label> FAILED: <detail>` lines and drive `has_errors()`, which `main()` uses for its exit code.

### Exception hierarchy

```
RirUpdaterError
├── ApiError         (HTTP / registry API failures)
└── CredentialError  (1Password or auth issues)
```

`ConfigError` exists but is unused — Pydantic `ValidationError` is caught in `main()` instead.
