# Building and checking MarketPulse on macOS

Roadmap Phase 11. Everything macOS needs is in the repository, but it was written and tested on Windows: the steps
below are also its first real run. A Mac stages its own runtime, because the bundled Python installs its packages
itself. Apple silicon builds `macos-arm64`; an Intel Mac builds `macos-x64`.

## What you need

- Xcode Command Line Tools (`xcode-select --install`), git, and Node 22.12 or newer.
- Any Python 3.11+ to run `build/stage.py`. The app's own Python is downloaded and pinned.
- The ibapi 10.50.2 sources at `build/cache/ibapi/ibapi-10.50.2/`. Copy the folder from the Windows build machine
  (`build/cache` is not in git). `stage.py` refuses any other files: their tree hash is pinned in `build/pins.json`.
- For a build that leaves your Mac:
  - a "Developer ID Application" certificate in your login keychain;
  - notarization credentials, either an App Store Connect API key (`APPLE_API_KEY`, `APPLE_API_KEY_ID`,
    `APPLE_API_ISSUER`) or an Apple ID (`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`).

  Without them the build is unsigned and unnotarized: it runs only on your own Mac (right-click › Open the first time).

## Build

```sh
npm --prefix web ci
npm --prefix web run build            # the MarketPulse web app
python3 build/stage.py all            # downloads and checks Python and PostgreSQL, installs, prunes, checks imports
npm --prefix desktop ci
npm --prefix desktop run dist         # dmg + zip in desktop/release/, signed and notarized when credentials are set
```

`dist` builds for the architecture that was staged (`build/stage/build-info.json`). Before trusting the build, check
these on the staged runtime:

- `file build/stage/postgres/bin/postgres` lists your Mac's architecture. One pinned EDB zip serves both
  architectures.
- `build/stage/python/bin/python3 -c "import app, marketpulse, ibapi"` works from `build/stage`.

## Tests

With a scratch PostgreSQL for the integration tests (never a database that holds real data):

```sh
export OMNIX_TEST_DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:PORT/marketpulse_test
PYTHONPATH=src:scripts python3 -m pytest src/tests_marketpulse -q
npm --prefix desktop run test         # Electron smoke + unit suites against build/stage
```

## Phase 11 gate: the Phase 9 checks on a Mac

1. Open the dmg and drag MarketPulse to Applications. Start it. The splash screen gives way to the first-run page;
   set the owner's email and password; the charts open.
2. Allow notifications when macOS asks. Create a price alert and make it fire with every window closed: a
   notification appears and the Dock shows a badge. Clicking the notification opens the alert's chart.
3. Keychain Access shows two items: "MarketPulse Safe Storage" (the shell's database passwords) and service
   "MarketPulse", account "secret-file-key" (the backend's credential-file key).
4. Quit from the menu bar icon. `pgrep -fl "MarketPulse|postgres -D"` shows nothing within a few seconds.
5. Start again: the charts open signed in. Data lives under `~/Library/Application Support/MarketPulse`.
6. Upgrade: install a newer build over this one (or let the updater apply the zip). Data is kept and migrations run.
7. Remove: drag the app to the Trash. The data folder stays until you delete it.

## Mac behaviours that have not been checked on a Mac yet

- Hardened runtime with `desktop/build-resources/entitlements.mac.plist`. Python needs unsigned executable memory
  for ctypes callbacks and loads extension modules signed by the same identity.
- electron-builder signs the bundled Python and PostgreSQL binaries inside `Resources/runtime`. Notarization fails
  if it misses one.
- The Keychain backend (`app/security/keychain.py`) calls Security.framework through ctypes.
- If the supervisor is killed (no Job Object on macOS), PostgreSQL keeps running. The next start reconnects to it
  instead of reporting a busy port.
- Python's bytecode cache goes to the data folder, so nothing writes inside the signed app bundle.
- The menu-bar icon is a template image, and the title bar leaves room for the traffic lights.
