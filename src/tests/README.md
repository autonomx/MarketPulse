# Python test suite

Pytest configuration lives in the repository root `pyproject.toml`. Run commands
from the repository root; the default collection scope is `src/tests`.

```powershell
# Collect the full suite
python -m pytest --collect-only

# Run the full suite
python -m pytest

# Run focused tests
python -m pytest src/tests/app/test_feature_matrix.py -q

# Run the browser test runner
python scripts/run_playwright_tests.py --suite smoke
```

## Database tests

PostgreSQL integration tests require an explicitly configured disposable
database through `OMNIX_TEST_DATABASE_URL`. The same URL is used by the
architecture and PostgreSQL CI jobs. Never point tests at an operator database.

## Test layout

Each catalog module has a directory named after the last part of its Python
package (`agent_runtime/`, `assistant_memory/`, `hermes/` for `app.apps.rpg.edge.hermes`).
New tests for a module go there; existing tests move when that module's work
touches them (PA-2.5). Run one module's tests, including the characterization
scenarios that list it in their `MODULES` tuple, with:

```powershell
python scripts/test_module.py rpg -q
python scripts/test_module.py rpg --list
```

- `agent_runtime/`, `app/`, `api/`, `persistence/`, and `unit/` contain focused
  contract, unit, and integration tests.
- `functional/`, `regression/`, and `rpg/` cover end-to-end behavior and RPG
  runtime characterization.
- `e2e/` contains browser and live-service checks.
- `conftest.py` provides shared fixtures. Browser-only helpers are used by the
  browser suite; API tests use the current application clients.

## Quarantine

`quarantine.toml` is the explicit, shrinking inventory for known collection
problems. Each entry records its reason, category, owning work package, and date.
Collected quarantined tests use strict `xfail`; collection-level entries are
limited to their listed files. Remove an entry when its owner fixes the issue.
The outcome inventory is maintained in `docs/roadmap/test-triage-2026-09-27.csv`.
