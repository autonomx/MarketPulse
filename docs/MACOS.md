# Building and checking MarketPulse on macOS

Roadmap Phase 11. Everything macOS needs is in the repository. A Mac stages its own runtime, because the bundled
Python installs its packages itself. Apple silicon builds `macos-arm64`; an Intel Mac builds `macos-x64`.

## Status (2026-10-17)

The first Mac run was on Apple silicon (macOS 26). It produced an unsigned `MarketPulse-0.1.2-arm64.dmg` and `.zip`.

**Done on a Mac:**

- `stage.py all` stages Python 3.11.17, PostgreSQL 17.11 and ibapi 10.50.2, prunes, and passes the import check.
- Both checks under [Build](#build) pass. The staged `postgres` is a universal binary (arm64 and x86_64).
- `npm --prefix desktop run dist` writes the dmg and zip. With no identity in the keychain it skips signing and
  notarization, as designed.
- `npm --prefix desktop run test` passes 20 of 20 against `build/stage`. These cover: first run through the splash
  to the charts, window sets, navigation rules, drag regions, refused permissions, a notification while every window
  is hidden, delivery after a simulated sleep, the crash view, the Omnix import page, quit leaving no backend
  process, signed in on the next launch, and `--quit`.
- The backend's Keychain item (service "MarketPulse", account "secret-file-key") is created through
  Security.framework, so the ctypes Keychain backend works.
- One fix: `stage.py` hashed ibapi's files in an order that depends on the platform, so a pin made on Windows always
  failed on a Mac. It now sorts in Windows order on every platform, and the pin is unchanged.

**Still to do:**

1. **Integration tests on a Mac.** Run `pytest src/tests_marketpulse` against a scratch PostgreSQL (see
   [Tests](#tests)).
2. **The Phase 11 gate**, on the built dmg and preferably on a clean Mac or user account (see
   [the gate](#phase-11-gate-the-phase-9-checks-on-a-mac)). It covers install, notifications from the installed app,
   both Keychain items, quit, restart, upgrade and removal. The shell's "MarketPulse Safe Storage" item is still
   unchecked: the tests run the unpackaged Electron, which never creates it.
3. **Signing and notarization.** A "Developer ID Application" certificate and notarization credentials are needed.
   Then check that the hardened runtime works and that `dist` signs every binary under `Resources/runtime` (see
   [below](#mac-behaviours-that-have-not-been-checked-on-a-mac-yet)).
4. **An Intel build** (`macos-x64`), on an Intel Mac.
5. **Releasing:** add the signed dmg and zip to a GitHub release, then check that the updater moves one macOS build
   to the next. After that, the README can offer a macOS download.

## What you need

- Xcode Command Line Tools (`xcode-select --install`), git, and Node 22.12 or newer (with nvm: `nvm install 22`).
- Any Python 3.11+ to run `build/stage.py`. The app's own Python is downloaded and pinned. The python.org installer's
  Python ships no CA certificates, so downloads fail with `CERTIFICATE_VERIFY_FAILED`. Run its
  `Install Certificates.command`, or set `SSL_CERT_FILE` to a CA bundle such as certifi's `cacert.pem`.
- The ibapi 10.50.2 sources at `build/cache/ibapi/ibapi-10.50.2/`. `build/cache` is not in git, and `stage.py`
  refuses any other files because their tree hash is pinned in `build/pins.json`. Either copy the folder from the
  Windows build machine, or rebuild it from IB's official download:

  ```sh
  brew install msitools
  curl -LO "https://interactivebrokers.github.io/downloads/TWS%20API%20Install%201050.02.msi"
  msiextract -C twsapi "TWS API Install 1050.02.msi"
  cd "twsapi/TWS API/source/pythonclient" && python3 setup.py sdist    # needs setuptools
  tar -xzf dist/ibapi-10.50.2.tar.gz -C <repo>/build/cache/ibapi
  curl -o <repo>/build/cache/ibapi/ibapi-10.50.2/LICENSE-GPL-3.0.txt https://www.gnu.org/licenses/gpl-3.0.txt
  ```

  The sdist leaves out IB's Visual Studio `ibapi.pyproj`. What remains matches the pin. The Mac/Unix zip
  (`twsapi_macunix.1050.02.zip`) has the same files.
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

The lockfiles resolve from the public npm registry. If your `~/.npmrc` points at a private registry, set
`npm_config_registry=https://registry.npmjs.org/` for these commands.

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
- If the supervisor is killed (no Job Object on macOS), PostgreSQL keeps running. The next start reconnects to it
  instead of reporting a busy port.
- Python's bytecode cache goes to the data folder, so nothing writes inside the signed app bundle.
- The menu-bar icon is a template image, and the title bar leaves room for the traffic lights.

Checked on 2026-10-17: the Keychain backend (`app/security/keychain.py`) calls Security.framework through ctypes,
in an unsigned run.
