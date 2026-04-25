# rir-updater

CLI tool for syncing RIPE NCC route objects and RPKI ROAs from a YAML config file.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`) for credential access

## Installation

```bash
uv sync
```

## Configuration

Copy `config.example.yaml` and fill in your values:

```yaml
ripe:
  maintainer: "MAINT-AS12345"
  credentials:
    db_username: "op://vault/item/username"
    db_password: "op://vault/item/password"
    rpki_api_key: "op://vault/item/rpki-api-key"
    test_db_username: "op://vault/item/test-username"  # optional
    test_db_password: "op://vault/item/test-password"  # optional
  sso_emails:
    - "admin@example.com"
  routes:
    - prefix: "192.0.2.0/24"
      origin: "AS12345"
      description: "Example IPv4 prefix"
    - prefix: "2001:db8::/32"
      origin: "AS12345"
      description: "Example IPv6 prefix"
  roas:
    - prefix: "192.0.2.0/24"
      origin: "AS12345"
      max_length: 24
    - prefix: "2001:db8::/32"
      origin: "AS12345"
      max_length: 32
```

`roas` is optional. If omitted, only route objects are synced. ROA sync only manages prefixes explicitly listed — other ROAs in the account are left untouched.

## Credentials

Secrets are fetched from 1Password via the `op` CLI. The `credentials` block in the config file specifies the 1Password reference for each secret:

| Field | Used for |
|-------|----------|
| `db_username` | RIPE DB REST API username (production) |
| `db_password` | RIPE DB REST API password (production) |
| `rpki_api_key` | RIPE RPKI Management API key |
| `test_db_username` | RIPE test DB username (optional, overrides `db_username` in test mode) |
| `test_db_password` | RIPE test DB password (optional, overrides `db_password` in test mode) |

References use the `op://vault/item/field` format. You must be signed in to the 1Password CLI (`op signin`) before running the tool.

## Usage

```bash
# Dry-run against the RIPE test database (default)
uv run rir-updater config.yaml

# Dry-run against production
uv run rir-updater config.yaml --production

# Apply changes to production
uv run rir-updater config.yaml --production --commit

# Set up the RIPE test database with objects replicated from production
uv run rir-updater config.yaml --setup-test
```

### Test database bootstrap

The first time you use `--setup-test`, the mntner must be created manually via the RIPE web UI at [apps-test.db.ripe.net](https://apps-test.db.ripe.net) — the API does not allow creating the first mntner programmatically due to a circular person↔mntner dependency. The tool will print instructions if the mntner is not found.

aut-num objects cannot be created in the test DB via the API — all aut-nums require authorization from `TEST-DBM-MNT`, which is restricted to RIPE staff. `--setup-test` will warn and continue if aut-num replication fails. You must create the aut-num manually via the web UI using your SSO session; however this may also fail if your account is not authorized as `TEST-DBM-MNT`. In practice, the test DB is most useful for validating mntner/person setup and dry-run output rather than full end-to-end route sync.

`--setup-test` only replicates prerequisite objects. Run without `--setup-test` afterwards to sync route objects and ROAs.

## Development

```bash
uv run ruff check .   # lint
uv run ruff format .  # format
uv run pytest         # test
```
