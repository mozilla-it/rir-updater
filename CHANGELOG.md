# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Add every behavioral change as a bullet under `## [Unreleased]`. On release,
rename that heading to the version and date, and bump `version` in `pyproject.toml`.

## [Unreleased]

## [0.2.0] - 2026-07-15

### Fixed
- **RADb responses are now parsed correctly.** The RADb REST API selects its
  output format via a `?format=` query parameter that defaults to `text` (RPSL)
  and ignores the `Accept: application/json` header. The client now sends
  `?format=json` on every request; previously every RADb GET returned RPSL and
  `resp.json()` raised `JSONDecodeError`, which blocked all RADb writes.
- **A single object failure no longer aborts the whole run.** Each route/ROA
  operation is wrapped so an `ApiError` on one object is recorded and reported
  (rendered as a `!` line in the summary) while the remaining objects still sync.
- **RADb transient failures are retried.** Idempotent RADb requests (GET/PUT/
  DELETE) retry on connection drops, timeouts, and 5xx with exponential backoff.
  A create (POST) whose response is lost is not blindly retried; instead the
  client re-checks whether the object was committed before deciding.

### Changed
- The CLI now exits non-zero when any per-object failure was recorded, so callers
  and CI can detect partial failures even though the run completes.
