# Contributing to MarketPulse

## The mirror rule

Most of this repository is a mirror of Omnix. `sync/omnix-manifest.toml` lists the mirrored paths and
`sync/omnix-sync.json` records the Omnix commit and a SHA-256 of every mirrored file.

- **Do not edit a mirrored file.** Make the change in Omnix (usually as a seam: an environment setting, a port, a
  lazy import), then run `python sync/sync_from_omnix.py sync --to <ref>`.
- **Do not add database migrations.** Schema changes are Omnix migrations; migration versions are one namespace.
- **Do not hand-edit derived files** (`pyproject.toml`, `web/package.json`). Edit `sync/templates/` and sync.
- MarketPulse code goes in owned paths only: `src/marketpulse/`, `src/tests_marketpulse/`, `web/marketpulse/`,
  `desktop/`, `build/`, `sync/`, `docs/`. An owned file never shadows a mirrored module path.

`python sync/sync_from_omnix.py check` fails on any drift; it is the first item of the gate set below.

## A sync

One commit per sync, titled `sync: omnix <old>..<new>`, with the Omnix log for the range (the tool prints it) in the
body. Then run the gate set.

## Gate set (after every sync and before every release)

1. `python sync/sync_from_omnix.py check`
2. `OMNIX_FEATURES=trading python -m pytest $(python sync/sync_from_omnix.py tests) src/tests_marketpulse -q`, with
   PostgreSQL tests on a scratch database (`OMNIX_TEST_DATABASE_URL`), never a live one; trading-only, as MarketPulse
   runs Omnix and because other features' code is not mirrored
3. `ruff check src/marketpulse src/tests_marketpulse`
4. `npm --prefix web run typecheck:trading`, `typecheck:marketpulse`, `test:trading`, `build`, then
   `python build/stage.py web`, which refuses a build carrying other features' chunks (the bundle check)
5. `npm --prefix desktop run test` (Electron smoke suite on the staged runtime)
6. Release only: the install, upgrade and recovery checks in docs/ROADMAP.md Phase 9

## TradingView

TradingView Desktop is an architecture and look reference only. Never copy its code, CSS, markup, icons, sounds,
fonts or other assets, and never read values out of its application files. Reference screenshots stay off-repo.
