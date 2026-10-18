# Building and checking MarketPulse on macOS

Roadmap Phase 11. Everything macOS needs is in the repository. A Mac stages its own runtime, because the bundled
Python installs its packages itself. Apple silicon builds `macos-arm64`; an Intel Mac builds `macos-x64`.

## Status (2026-10-18)

**First release: 0.1.2.** The first macOS build, `MarketPulse-0.1.2-arm64.dmg` and `.zip` for Apple silicon, is on
the [v0.1.2 release](https://github.com/autonomx/MarketPulse/releases/tag/v0.1.2) next to the Windows installer. It
is built from `4c079f1`, later than the tag, and is signed ad hoc, not notarized: Gatekeeper rejects a downloaded
copy, and users allow it once under System Settings › Privacy & Security › Open Anyway (the README says how).

**Released: 0.1.3**, for both platforms. The macOS dmg and zip and the Windows installer are on the
[v0.1.3 release](https://github.com/autonomx/MarketPulse/releases/tag/v0.1.3), all built from `12e3b07`. It has
every fix below; on the installed Mac app, a click on an ETH
alert's notification opened the ETH chart. The Windows build passed the sync check, `src/tests_marketpulse` and the
Electron suite (21 of 21) before it was published. The installed check on Windows (0.1.2 upgraded to 0.1.3, then a
real notification click) was not run.

**Released: 0.1.4**, for both platforms. The Windows installer was published first; the macOS dmg, zip and
`latest-mac.yml` joined the [v0.1.4 release](https://github.com/autonomx/MarketPulse/releases/tag/v0.1.4) after,
built from `049f202` like the Windows files. On a Mac,
Restart to Update isn't offered (the build is ad hoc signed): MarketPulse says when a version is out, and Check for
Updates… opens the download page. Checked on the Mac before the upload:

- `sync check` passes (mirror at Omnix `406dc03a93`, 3355 files). The manifest's default Omnix path is the Windows
  machine's, so on a Mac pass `--omnix` with a local Omnix checkout.
- `src/tests_marketpulse` passes (45, plus the Windows-only Job Object test skipped), and the Electron suite passes
  25 of 25.
- `codesign --verify --deep --strict` passes on the app, and `latest-mac.yml` says 0.1.4 and names the zip.
- Installed over 0.1.3, 0.1.4 opened on the charts with the existing data, signed in, and Check for Updates… in the
  menu-bar icon's menu said MarketPulse is up to date (Version 0.1.4).

**Released: 0.1.5**, for both platforms. As with 0.1.4, the Windows installer was published first and the macOS dmg,
zip and `latest-mac.yml` joined the [v0.1.5 release](https://github.com/autonomx/MarketPulse/releases/tag/v0.1.5)
after, built from `12fb467` like the Windows files, and it is the latest release that installed apps check. Omnix's
`main` was rewritten on 2026-10-18 (authors only), so the Mac's Omnix clone was reset to `origin/main`
(`e959672316`) rather than pulled: a pull merges the old history back in. Checked on the Mac before the upload:

- `sync check --omnix <the Mac's Omnix clone>` passes (mirror at Omnix `e959672316`, 3360 files).
- `src/tests_marketpulse` passes (45, plus the Windows-only Job Object test skipped), and the Electron suite passes
  26 of 26, the new text-size test included.
- `codesign --verify --deep --strict` passes on the app, and `latest-mac.yml` says 0.1.5 and names the zip.

And on the installed app, 0.1.5 over 0.1.4:

- The first start waited for macOS's Keychain prompt ("MarketPulse wants to use your confidential information
  stored in 'MarketPulse Safe Storage'"): the item's access follows the app's signature, and every ad hoc build has
  a new one, so each Mac upgrade asks for the login password once (Always Allow). After the prompt was allowed, that
  first start ended by itself without a window (exit status 0); the next start was normal.
- It opened on the charts with the existing data, signed in, and Check for Updates… in the menu-bar icon's menu said
  MarketPulse is up to date (Version 0.1.5).
- The gear beside the window controls opens the new Settings page, with the sidebar (Appearance, Account, Market
  data & brokers, AI providers, Research handoffs).
- Settings › Appearance › Text size at 120% zooms every page, the chart included (it draws at the zoomed scale).
  ⌘= and ⌘− step it by 5% and ⌘0 resets it to 100%, each checked one press at a time. It was left at 100%.
- Settings › Market data & brokers has the Alpaca section (API key ID and secret key) and the list of what each data
  source is for.

**Checked on a Mac** (Apple silicon, macOS 26):

- `stage.py all` stages Python 3.11.17, PostgreSQL 17.11 and ibapi 10.50.2, prunes, and passes the import check.
  Both checks under [Build](#build) pass; the staged `postgres` is a universal binary.
- `dist` writes the dmg and zip, signed ad hoc with the hardened runtime. The bundle, Python and PostgreSQL pass
  `codesign --verify --deep --strict`, also after the app has run: nothing writes inside the bundle.
- `npm --prefix desktop run test` passes 21 of 21, and `pytest src/tests_marketpulse` passes (45, plus the Windows-only
  Job Object test skipped).
- The gate below, steps 1 to 7, on the installed app:
  1. Install from the dmg, first run, owner set, charts open.
  2. With every window closed, an alert raises a notification and a Dock badge. Clicking it brings MarketPulse to the
     front, but on the charts as they were, not on the alert's symbol (see below).
  3. Both Keychain items exist.
  4. Quit from the menu-bar icon (a template image that looks right): no process left.
  5. The next start opens the charts, signed in.
  6. `0.1.3-gate.1` installed over 0.1.2 starts on the same data, signed in, with PostgreSQL shut down cleanly. No
     migration was pending, so the migration path was not exercised here.
  7. With the app in the Trash, the data folder and both Keychain items stay.

**Fixed while checking:**

- An unsigned build did not start at all: setting the fuses rewrites the Electron binaries, and Apple silicon kills a
  bundle whose signature no longer matches (exit 137). Without a Developer ID, `dist` now signs ad hoc.
- `stage.py`'s ibapi tree hash depended on the platform; it now sorts files in Windows order everywhere.
- Notification clicks were lost once the Notification was garbage-collected (Windows too). Shown notifications are
  now kept until clicked or closed.
- With every window gone, the Dock, the tray's Show and a second launch did nothing; they now reopen the charts.
- A second Quit while stopping ended the shell first and orphaned the supervisor for 180 s; every quit now waits.
- Two integration tests only passed on Windows.

**Still to do:**

1. **Developer ID signing and notarization.** Until then a Mac build cannot update itself: checked with a local
   feed, 0.1.2 downloaded the 0.1.3 zip and Squirrel.Mac refused it ("code failed to satisfy specified code
   requirement(s)"), because an ad hoc signature never matches another build's. From 0.1.3, an ad hoc signed Mac
   build downloads nothing and says "MarketPulse x.y.z is available", with a link to the release; users install
   the dmg. 0.1.2 Mac installs download the zip once and fail silently. Then check the hardened runtime with a real
   identity and the updater from one macOS build to the next.
2. **An Intel build** (`macos-x64`), on an Intel Mac.
3. **Quit takes about 20 s** (the gateway drains, then PostgreSQL stops), and nothing shows while it does. That is
   why a user chooses Quit twice.
4. **Bytecode in the bundle.** Builds with the dmg target carry 20 standard-library `.pyc` files (`functools`, `re`,
   `encodings`, …) inside the bundle, although electron-builder's filter drops `__pycache__` from the staged runtime
   and zip-only builds have none. They are inside the signature and nothing writes to them at run time, so they are
   harmless; the cause is not found yet.

## Finishing a release on Windows

The Mac files go on a draft release first. On the Windows build machine, at the draft's target commit:

```
git pull                                                    # main at the draft's target commit
npm --prefix web ci && npm --prefix web run build
python build/stage.py all
npm --prefix desktop ci && npm --prefix desktop run dist
gh release upload v0.1.3 desktop/release/MarketPulse-Setup-0.1.3.exe desktop/release/MarketPulse-Setup-0.1.3.exe.blockmap desktop/release/latest.yml
gh release edit v0.1.3 --draft=false                        # publishes it, creates the tag, and makes it Latest
```

Publishing makes it the release that installed apps check: Windows apps update themselves on their next quit, and
0.1.3 Mac apps announce the next version.

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

  Without them the build is signed ad hoc and not notarized. It runs on the Mac that built it; on any other Mac,
  Gatekeeper asks the user to allow it once (System Settings › Privacy & Security › Open Anyway).

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

With a scratch PostgreSQL for the integration tests (never a database that holds real data), migrated first:

```sh
export OMNIX_TEST_DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:PORT/marketpulse_test
OMNIX_DATABASE_URL=$OMNIX_TEST_DATABASE_URL OMNIX_FEATURES=trading PYTHONPATH=src:scripts python3 -m app.persistence.cli migrate
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
4. Quit from the menu bar icon. `pgrep -fl "MarketPulse|postgres -D"` shows nothing within about 30 seconds.
5. Start again: the charts open signed in. Data lives under `~/Library/Application Support/MarketPulse`.
6. Upgrade: install a newer build over this one (or let the updater apply the zip). Data is kept and migrations run.
7. Remove: drag the app to the Trash. The data folder stays until you delete it.

## Mac behaviours that have not been checked on a Mac yet

- The hardened runtime with a Developer ID (`desktop/build-resources/entitlements.mac.plist`). The ad hoc build runs
  with it; a real identity adds library validation by team, which `disable-library-validation` should cover.
- Notarization, which fails if electron-builder misses a binary inside `Resources/runtime`. The ad hoc build signs
  them all.
- If the supervisor is killed (no Job Object on macOS), PostgreSQL keeps running. The next start reconnects to it
  instead of reporting a busy port.
- The updater on macOS (Squirrel.Mac needs a properly signed app).

Checked on 2026-10-17: the Keychain backend (`app/security/keychain.py`) through ctypes, the bytecode cache in the
data folder (nothing writes inside the bundle), the menu-bar template image, and the title bar's room for the traffic
lights.
