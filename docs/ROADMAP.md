# MarketPulse Roadmap

**Revision 5, 2026-10-17.** Plan for MarketPulse: a standalone, self-contained desktop copy of the Omnix trading app,
Windows first, macOS next. Review history is in §10; implementation status in §11.

## 1. Goal

MarketPulse is the Omnix trading app (charts, scanners, alerts, paper trading, strategies, research, LLM-assisted
analysis) installed as one desktop program. It needs no Docker, no separately installed PostgreSQL or Python, and no
Omnix checkout. Omnix stays the place where trading is developed; MarketPulse takes in Omnix's changes through a
mechanical sync.

## 2. Owner decisions

| ID | Decision |
|----|----------|
| D-1 | **Fork, with Omnix as authority.** Most trading work keeps landing in Omnix. MarketPulse mirrors it and adds only what a desktop product needs. |
| D-2 | **Fully self-contained desktop app.** PostgreSQL, Python and the web UI ship inside the installer and run on the user's machine. |
| D-3 | **LLM features included**: AI shadow/research analysis, structured LLM calls, provider settings, trading's own Hermes research client. |
| D-4 | **Accounts included**: sign-in, accounts, guests, invites, Google sign-in, as in Omnix. |
| D-5 | **Windows first; macOS after** the Windows release is stable. The owner has a Mac. |
| D-6 | **Source is Omnix `main`** (`9113b6f59e` when this was written). TradingView parity is already on `main` (merge `6cb7e50a9c`). |
| D-7 | **TradingView Desktop is an architecture reference only.** Its `package.json` says `"license": "Proprietary"`. No code, configs, icons, sounds or other assets from `tmp/tradingview` go into MarketPulse. |
| D-8 | **`ibapi` is bundled in the installer**, staged from Interactive Brokers' official TWS API distribution (§5, MP-5.1). |
| D-9 | ~~The research handoff and climatology move to a separate data-only repository~~ **Superseded 2026-10-17:** MarketPulse imports them from Omnix's own repository, `autonomx/omnix` (public), as Omnix does. S-11 is not needed. |
| D-10 | **MarketPulse looks like TradingView Desktop, or closely resembles it**: layout, density, palettes, menus and dialogs, and a frameless window with the tabs in the title bar (§4.11). It is built from our own code and assets under our own name and logo (D-7). This goes beyond Omnix's parity rule, "TradingView is a reference for **features and behaviour only**" (`TRADINGVIEW_PARITY_ROADMAP_2026-10-08.md:11`). The owner chose to build it in Omnix too (D-11). |
| D-11 | **Omnix trading takes the TradingView-like look too** (Q-5 option a). The look is built once in Omnix (W-3) and synced. The parity rule is amended to allow visual resemblance with our own assets, and an Omnix DECISIONS entry makes the §4.11 screen list and screenshot baselines the acceptance test. |

## 3. Facts the plan rests on

Measured on Omnix `main` `9113b6f59e`, 2026-10-17. Each row was checked in the code.

| Area | Fact |
|------|------|
| Trading backend | `src/app/apps/trading`: 321 Python files, ~115k lines. Its own third-party imports are light (pydantic, fastapi, httpx, cryptography, websockets, `ibapi`). |
| Gateway dependency set | `requirements/gateway.in` also pulls numpy, onnxruntime, tokenizers, pandas, Pillow, pypdf, python-docx, soundfile, pydub, python-kasa, huggingface_hub, which the composition root needs. v1 ships this set; trimming is Phase 12. |
| `ibapi` | Not in `gateway.lock.txt`. Omnix expects a "locally installed official ibapi package" (`ibkr_runtime.py:134-143`). |
| Composition root | Not trading-clean. `composition/gateway/app_factory.py:18-24` imports `app.platform.chat` and `app.apps.rpg.edge.replay` at module level. `kernel_routes/__init__.py` imports assistant_tools, chat, rpg replay and voice. `production.py` and `runtime_composition.py` import live_voice, assistant_memory, agent_runtime and desktop_companion. Importing only `app.apps.trading.feature` is clean, but the gateway that serves it is not. |
| Migrations | 40 kernel migrations plus one folder per feature (`apps/{trading,rpg,audiobook}`, `platform/{agent_runtime,assistant_memory,chat,companion_activity}`). Discovery by convention: `migration_roots()` finds `migrations/` next to a `feature.py`. Version stems are unique across folders, in one global namespace (`migrations.py:121-127`). |
| Schema is not separable | Kernel migrations alter feature-owned tables without `IF EXISTS`: `0012` alters `omnix_rpg_{campaigns,turns,participants,interactions,snapshots}`, `0095` reads audiobook tables, `0106` enables RLS on ~90 rpg/audiobook/agent_runtime tables. Kernel + trading alone does not migrate. Existing migrations are checksummed and cannot be edited, so **the full Omnix schema ships**. |
| DB roles | `0101` grants to a role named `omnix_app`, which must exist first. Omnix runs migrations as the owner (`OMNIX_MIGRATION_DATABASE_URL`) and the runtime as `omnix_app` with NOBYPASSRLS (`deploy/postgres/initdb/10-omnix-runtime-role.sh`). Compose sets `timezone=UTC`. Plain PostgreSQL 17, no extensions. |
| Features | `app.runtime.feature_catalog` loads features lazily by import string. `OMNIX_FEATURES` (`app/config/runtime.py:279-281`, default `all`) selects them. The `hermes` feature is `app.apps.rpg.edge.hermes` and depends on RPG; trading's Hermes use is its own sidecar client plus `hermes_research_api`. |
| Process roles | Schedulers, including every trading monitor and the handoff importer, run **inside the gateway** when its role is `worker`, the default (`config/runtime.py:198-199`, `composition/gateway/feature_registry.py:189-196`). `app.composition.worker` is the *job* worker (durable job pools only). `scripts/run_omnix_gateway.py` starts that job worker itself (`gateway_cluster.start_local_job_worker`), supports `--managed-stdin`, `--api-replicas` and `--app`, and sets up the service token and `OMNIX_RUN_TOKEN_KEY`. |
| Static web | The gateway does not serve the SPA. In Omnix, nginx serves it and adds the CSP and security headers, and proxies `/api`, `/events`, `/health` and `/ready` (`deploy/docker/nginx/omnix.conf:110-145`). |
| Secrets | `security/provider_secret_store.py` calls DPAPI `CryptProtectData` directly; on other OSes it refuses ("requires an operating-system credential store"). Paths default under `%LOCALAPPDATA%\Omnix` and can be moved with `OMNIX_PROVIDER_SECRETS_PATH`; `app/security/secrets.py` has `OMNIX_SECRET_STORE` / `OMNIX_SECRET_STORE_PATH`. The service token and install credential live there too, and rotating the install credential revokes sessions (`auth/service.py:831`). |
| Writable paths | `app/runtime/paths.py` resolves `resources/` from the source tree (`parents[3]`) and creates `resources/data/…` when imported. The blob store, trade logs (`trade_logging.py:41`) and agent logs write there. |
| Child Pythons | `trading/scripts_service.py:102-143` starts user-script workers with `sys.executable -m …` and a `PYTHONPATH`. |
| Alerts | The web app polls (`useTradingAlerts.ts:22-28`). Server delivery is a durable outbox with channels `webhook`, `email`, `push` (`alerts_delivery.py:79-84`), restricted by a CHECK constraint (`0142_trading_notification_deliveries.sql`). `claim_due` leases due rows of every channel (`alerts_delivery.py:254`), and the in-gateway monitor fails a channel with no sender as `channel_unavailable` (`:647`). Senders implement the `NotificationSender` protocol (`:198`; `EmailSender`, `PushSender`, `WebhookSender`). There is no event stream to subscribe to. Push titles say "Omnix alert". |
| Environment defaults | `OMNIX_ENV` defaults to `development`, which makes registration `open` with guests on (`auth/settings.py:143-151`) and API docs public. `OMNIX_LOCAL_JOB_WORKER` defaults on only for development/local (`gateway_cluster.py:28-39`). `OMNIX_ALLOWED_ORIGINS` defaults to the Vite ports 5173/4173 (`runtime/net.py:19-22`). The gateway port defaults to 8000 and the job-worker metrics port to 8090, the same as a local Omnix. |
| Request guard + CSRF | Unsafe requests need an allowed `Origin`, an `X-Omnix-Client` header (`security/request_guard.py:66-79`) and `X-Omnix-CSRF`. The web adds them only through `installViewApiFirewall` (`app/viewApiScope.ts:131-145`), which imports `moduleManifests` at runtime (`:3`), and so every feature's `module.ts`. The CSRF cookie name is hard-coded as `omnix_csrf` (`:83`). |
| Owner sign-in | Auth modes are `local` (accounts, with optional Google), `oidc` and `disabled`. The owner is `user:local`, provisioned at bootstrap with the DPAPI install credential (`auth/__init__.py:45-51`). `issue_login_code` works only in `local` mode and always signs in `user:local` (`auth/service.py:862-875`); it is reached through `python -m app.security login-code`. |
| Test fixtures | Trading tests read 159 tracked files under `resources/trading/{indicator_goldens,script_corpus,drawing_alert_levels}` (for example `tests/trading/test_trading_scripts_corpus.py:29`). |
| Auth | Password accounts, sessions, guests, invites, Google OIDC + PKCE (`auth/oidc.py`). `google_callback` (`auth/routes.py:456-469`) gives the browser that finished the flow a signed-in session. Cookie names are fixed (`omnix_session`, `omnix_google_tx`), and cookies ignore the port, so two apps on `127.0.0.1` share cookie names. Cookies are `Secure` only when configured or when the request came over HTTPS. There is an owner-only one-time login code (`issue_login_code`, `/api/auth/local/callback`, 60 s, single use). |
| Research handoff | `prospective_gap_inputs.fetch_from_github` uses the `gh` CLI and `OMNIX_TRADING_PROSPECTIVE_GAP_GITHUB_REPOSITORY` (default `autonomx/omnix`). |
| Trading web | `web/src/features/trading`: 562 files, ~91k lines. First-hop imports leave the feature only for `api/*`, `shared/*`, `events/bus`, `app/{modules,moduleManifest}` and test helpers. But `features/trading/module.ts` → `app/moduleManifest` and `TradingWorkspace.tsx` → `app/modules` → `modulesManifest.ts` reach every feature module, ~970 files. `test/renderWithProviders.tsx` needs `@mantine/core` and `design/theme`. Omnix already has `tsconfig.trading.json` / `npm run typecheck:trading`. |
| Trading tabs and windows | The trading app owns its own tabs and Ctrl+T/W/Tab/1-9 (`InstallAppNote.tsx:17`). Window sets live in localStorage and coordinate over BroadcastChannel (`tradingWindowSets.ts`, `windowPresence.ts`), so state is per origin and the port is part of the origin. |
| Branding | ~92 non-test web lines and ~71 non-test Python lines in trading mention "Omnix", some visible, for example "Install Omnix Trading" and "Omnix alert". |
| Tests | ~650 trading test files under `src/tests`. Root `src/tests/conftest.py:121-160` has an autouse fixture that imports ~15 `app.platform.*` modules and `app.composition.runtime_composition`. Pytest markers and `--strict-markers` live in Omnix's root `pyproject.toml`. |
| TradingView shell (reference) | Electron 41.7.1. A thin main process, small separate renderers (window, tab strip, splash, menu, toast, tooltip, crash and error views), contextBridge preloads, declarative `nav-rules.json` (open-external / popup / tab by URL pattern), `config.json` (window sizes, load timeouts), OS credential storage, local LevelDB cache. |

## 4. Architecture

### 4.1 What MarketPulse is

MarketPulse v1 is **all of Omnix's `src/app` and `web/src`, run with `OMNIX_FEATURES=trading`**, inside a desktop
supervisor, with a MarketPulse web entry that registers only trading. This is how Omnix's own Docker image ships the
gateway (`deploy/docker/gateway.Dockerfile`). The composition root, the schema and the test fixtures all need the full
tree (§3), and cutting them before v1 would put hard refactors of Omnix on the critical path. Shrinking the mirror is
Phase 12, after v1 ships.

```
MarketPulse/
  src/app/…                  MIRRORED: all of Omnix src/app (never edited here)
  src/tests/…                MIRRORED: root conftest*, fixtures, quarantine file, trading test files
  scripts/run_omnix_gateway.py, scripts/gateway_cluster.py   MIRRORED
  resources/trading/{indicator_goldens,script_corpus,drawing_alert_levels}/   MIRRORED test fixtures
                             (not the research inbox/state files; bundled read-only)
  requirements/*.in, *.lock.txt                              MIRRORED
  web/src/…                  MIRRORED: all of Omnix web/src
  web/{vite,vitest}.config.*, tsconfig*.json, eslint config, package-lock.json   MIRRORED
  src/marketpulse/           OWNED: desktop ASGI wrapper (static SPA + headers), desktop auth handoff,
                             bootstrap/env, diagnostics
  src/tests_marketpulse/     OWNED: tests for src/marketpulse
  web/marketpulse/           OWNED: entry (main.tsx), vite.marketpulse.config.ts, tsconfig.marketpulse.json
  desktop/                   OWNED: Electron app (TypeScript): main, supervisor, preload, shell pages
  build/                     OWNED: runtime staging scripts, pinned versions + SHA-256 checksums
  sync/                      OWNED: omnix-manifest.toml, omnix-sync.json, sync tool
  pyproject.toml, web/package.json   DERIVED (see §4.2)
  docs/
```

### 4.2 Sync model: mirror, derived, owned

- **Mirrored** paths are byte-identical to Omnix at the commit in `sync/omnix-sync.json`, listed as include/exclude
  globs in `sync/omnix-manifest.toml`. Nobody edits a mirrored file in MarketPulse.
- **Derived** files are produced by the sync tool from Omnix files, and the tool's `check` regenerates them and diffs:
  - `pyproject.toml` takes `[tool.pytest.ini_options]`, `requires-python` and the ruff/mypy settings from Omnix, plus a
    MarketPulse `[project]` block.
  - `web/package.json` takes dependency versions from Omnix's `web/package.json` and `package-lock.json`, plus
    MarketPulse scripts.
- **Owned** paths belong to MarketPulse. They hold new files only and never shadow a mirrored module path.
- **Rules:**
  1. A change to mirrored code is made in Omnix first, usually as a seam (an env var, a port, a lazy import), then
     synced. Phase 1 lists the seams v1 needs.
  2. **MarketPulse never authors migrations.** Version stems are one global namespace across Omnix and MarketPulse;
     schema changes MarketPulse needs land as Omnix migrations (AGENTS.md "Persistence").
  3. Generated contracts (`web/src/api/generated/*`, `features/*/api/generated.ts`) are mirrored as generated by Omnix.
- **`sync/sync_from_omnix.py`**:
  - `sync --omnix <checkout> --to <commit>` replaces every mirrored path with the tree at `<commit>`. It is a tree
    copy, not patch replay, so it is deterministic. It regenerates derived files, records per-file SHA-256s, and prints
    the Omnix log for the range for the commit body.
  - `check` fails on any drift in mirrored or derived files.
  - Commit format: `sync: omnix <old>..<new>`, followed by the gate set in §6.
- **Cadence**: on demand, and before every release.

### 4.3 Runtime process model

```
MarketPulse.exe (Electron main: shell + supervisor), all children in one Windows Job Object (kill-on-close)
  ├─ pg_ctl → postgres      bundled PG 17, %LOCALAPPDATA%\MarketPulse\pgdata, 127.0.0.1:<pg-port>
  ├─ python scripts\run_omnix_gateway.py --managed-stdin --api-replicas 0 --host 127.0.0.1 --port <http-port>
  │        --app marketpulse.desktop_app:app
  │     ├─ gateway (role worker: HTTP API + all schedulers/monitors), 127.0.0.1:<http-port>
  │     │   wrapped by marketpulse.desktop_app: serves web/dist, SPA fallback, nginx-equivalent headers
  │     └─ job worker (started by run_omnix_gateway via gateway_cluster; OMNIX_LOCAL_JOB_WORKER=1)
  └─ notification listener (in Electron main), 127.0.0.1:<notify-port>, shared secret (§4.9)
  windows → http://127.0.0.1:<http-port>/
```

- **Startup order:**
  1. Single-instance lock.
  2. First run only: `initdb --auth=scram-sha-256 --pwfile=<temp file>` (UTF-8, `timezone=UTC`, listen `127.0.0.1`;
     the temp file is deleted right after). Then create the `omnix_app` role with NOBYPASSRLS, and pick and persist
     the PostgreSQL, HTTP, job-worker metrics and notify ports.
  3. Start PostgreSQL through `pg_ctl`, which runs `postgres` correctly even from an elevated parent; wait for
     `pg_isready`.
  4. If migrations are pending: stop PostgreSQL, snapshot `pgdata` (directory copy), start again.
  5. `python -m app.persistence.cli migrate` as the owner role (`OMNIX_MIGRATION_DATABASE_URL`).
  6. Start `run_omnix_gateway.py` with the runtime role (`OMNIX_DATABASE_URL`, user `omnix_app`). Wait for `/ready`.
  7. Open windows.

  The splash shows each step. A failure shows the error view with the cause, the last log lines, Retry, and Open logs
  folder.
- **Shutdown:** close stdin of `run_omnix_gateway` (managed-stdin), then `pg_ctl stop -m fast`. If Electron dies, the
  Job Object kills every child. This needs a small native helper or N-API module (`desktop/native/job`) because Node
  has no Job Object API.
- **Ports are chosen once and kept.** The HTTP port is part of the origin, and window sets, layouts and other
  localStorage state are per origin. If the port is busy at startup, the error view says so and offers "Choose a new
  port (resets window layout)". The app never moves silently.
- **Python**:
  - Bundled CPython 3.11 with a `python311._pth` listing `<root>\src` and `<root>\scripts` (for `run_omnix_gateway.py`'s
    `from gateway_cluster import …`), with `import site` enabled.
  - A `._pth` file puts the interpreter in isolated mode: `PYTHON*` variables are ignored and the script's own folder
    is not added to `sys.path`. Every import root must therefore be in the `._pth`, and `-I` is not needed.
  - `scripts_service` user-script workers start with `sys.executable` and get the same `._pth`. Phase 5 checks that
    their imports resolve.
  - The bundled PostgreSQL `bin` goes first on `PATH`, because `persistence/cli.py` finds `pg_dump` with
    `shutil.which`.
- **Background:** closing the last window hides MarketPulse to the tray, and the backend keeps running. Quit from the
  tray stops everything. "Start at login" is on by default, so the 04:00–09:30 ET importer and premarket monitors run.
  Sleep pauses everything; monitors recover from durable PostgreSQL state, as in Omnix.
- **Not shipped:**
  - IBKR TWS / IB Gateway (`ibapi` is bundled, D-8).
  - LLM models: cloud endpoints or a user-run LM Studio / llama.cpp.
  - A Hermes sidecar (optional, off by default).

### 4.4 Desktop shell

The TradingView patterns that fit, written fresh. TradingView's native tab strip does **not** fit: the trading app
already owns tabs and their shortcuts, and every native tab would join the window-set BroadcastChannel as another
window.

| Piece | MarketPulse implementation |
|-------|----------------------------|
| Windows | One frameless `BrowserWindow` per trading window, all on the `persist:marketpulse` session. On Windows: `titleBarStyle: 'hidden'` + `titleBarOverlay` (native minimize, maximize and close, colored from the theme). On macOS: `hiddenInset` traffic lights. The trading app's own tab bar is drawn in the title-bar area (W-4, §4.11). App shortcuts (Ctrl+T/W/Tab/1-9) go to the page, not to Electron menu roles. |
| Window sets | `window.open` to a trading URL opens a new `BrowserWindow` on the same session. Bounds and display are saved per window key and restored. |
| Splash / error / crash views | Local pages in the app bundle: startup steps, service failure with cause and logs, renderer crash with Reload. Same palette and type as the trading UI (§4.11). |
| Navigation rules | Declarative `nav-rules.json` + schema. Gateway origin stays in the window; trading popups become windows; allowlisted external hosts go to `shell.openExternal`; everything else is denied. |
| Menu, tray | App menu (Edit/View/Window/Help, with no shortcuts that collide with the page's), tray with alert badge and Show / Quit. |
| Preload bridge | One preload exposing `window.marketPulse` = `{ version, notify, openWindow, onUpdateStatus, revealLogs, sessionChanged, requestLoginCode, setTitleBarOverlay }`. `requestLoginCode` works only before the first-run marker exists (§4.7). The web app runs unchanged in a browser when it is absent (W-2). |
| Updates | `electron-updater` (Phase 9). |

### 4.5 Data locations (Windows)

| Path | Contents |
|------|----------|
| `%LOCALAPPDATA%\Programs\MarketPulse\` | Per-user install, read-only at runtime: Electron, `resources\{python,postgres,app,web}` |
| `%LOCALAPPDATA%\MarketPulse\pgdata\` | PostgreSQL cluster |
| `%LOCALAPPDATA%\MarketPulse\snapshots\` | `pgdata` snapshots before migrations (keep 3), plus daily `pg_dump` (keep 7) |
| `%LOCALAPPDATA%\MarketPulse\resources\` | Writable resources, through `OMNIX_RESOURCES_DIR` (S-2): blobs, trade logs, caches. Bundled read-only resources stay in the install directory. |
| `%LOCALAPPDATA%\MarketPulse\logs\` | Rotating logs per process |
| `%LOCALAPPDATA%\MarketPulse\secrets\` | DPAPI-protected: provider keys (`OMNIX_PROVIDER_SECRETS_PATH`), secret store (`OMNIX_SECRET_STORE=dpapi`, `OMNIX_SECRET_STORE_PATH`), service token, install credential, `OMNIX_RUN_TOKEN_KEY`, DB role passwords |

Every Omnix default path under `%LOCALAPPDATA%\Omnix` is overridden, so MarketPulse never reads or rotates the
owner's Omnix secrets on the same machine. Phase 3 gates this with a test. Uninstall leaves
`%LOCALAPPDATA%\MarketPulse` unless the user ticks "Remove my data".

### 4.6 Security model

- **Electron:**
  - `contextIsolation`, `sandbox` and `webSecurity` on; `nodeIntegration` off.
  - The permission handler allows notifications and clipboard write only.
  - `will-navigate` and `setWindowOpenHandler` enforce the navigation rules.
  - Fuses: RunAsNode off, NodeOptions off, ASAR integrity on, OnlyLoadAppFromAsar on, cookie encryption on.
- **Secrets:** use Omnix's own DPAPI stores at MarketPulse paths (§4.5). The supervisor generates the DB role
  passwords at first run, stores them DPAPI-protected, and passes them to children in environment variables at spawn.
  Nothing is in plain text on disk. macOS needs a Keychain backend in Omnix first (S-8).
- **Database:**
  - The cluster uses `scram-sha-256` for every connection; a Phase 5 test checks that a connection without a
    password is refused.
  - Migrations, backup and restore run as the owner role. The runtime runs as `omnix_app` with NOBYPASSRLS, as in
    Omnix, so RLS (`0106`) is in force. Migrations always run as the same owner role, because default privileges are
    per grantor.
  - `persistence/cli.py backup` dumps with `--no-acl`, so the supervisor re-applies the `0101` grants after any
    restore.
- **HTTP:**
  - Sign-in is required for every request (Omnix default since 2026-10-06).
  - `OMNIX_ENV=production` with `OMNIX_AUTH_REGISTRATION=invite`: no open registration, no public API docs.
  - Listeners bind to `127.0.0.1` only, and `OMNIX_ALLOWED_ORIGINS` is exactly `http://127.0.0.1:<http-port>`.
  - `marketpulse.desktop_app` sets the same CSP, `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options` and
    `Permissions-Policy` that nginx sets in Omnix.
- **Cookies:** Omnix's cookie names stay; the web code hard-codes `omnix_csrf`. Inside the `persist:marketpulse`
  session nothing collides with Omnix. The only cookie the system browser sees is the desktop Google transaction
  cookie, which has its own name (S-5).

### 4.7 Accounts in a desktop app

- Accounts, guests and invites work as in Omnix, in the local database.
- **First run only:** bootstrap provisions the owner, `user:local`, as in Omnix.
  1. After `/ready`, the supervisor runs `python -m app.security login-code` with the gateway's exact environment.
     It needs no DPAPI access; the code is stored hashed under system scope.
  2. It opens the first window at `/api/auth/local/callback?code=…&next=/trading`.
  3. The window asks the owner to set an email and a first password.
     - Omnix's `_reauthenticated` (`auth/service.py:384-410`) refuses a first password without a credential for any
       account that has an install credential, and `user:local` always has one.
     - The install credential is readable only from an interactive console (`security/__main__.py:39-40`).
     - So this step needs seam S-10: with `OMNIX_AUTH_LAUNCHER_FIRST_PASSWORD=1`, the owner may set a *first* password
       when the session came from a login code (`auth_method="local"`) less than `REAUTHENTICATION_WINDOW_SECONDS`
       ago.

  The supervisor writes a "first run done" marker in the data directory **only after the password is stored**. Until
  then, every launch issues a fresh code, so a crash mid-step cannot lock the owner out. If the owner takes longer
  than the 600 s window, the step offers "Get a new sign-in code" (`requestLoginCode` on the bridge), which the
  supervisor honors only while no marker exists. After the marker exists, the
  supervisor never issues a login code again; on later launches, or after sign-out, people sign in normally, so
  whoever sits at the PC does not become the owner. A lost password is recovered from an interactive console with
  `python -m app.security show-install-credential`, which MarketPulse documents with the bundled interpreter path.
- **Google sign-in** cannot run inside Electron (Google rejects embedded user agents). The flow, built on S-5:
  1. The window POSTs to a desktop Google start route; the request guard checks Origin and the client header. The
     gateway returns a random `nonce` to the window only, plus a one-time `begin` URL that does not contain the nonce.
     Electron opens the `begin` URL in the system browser. It is visible on the process command line, so it must not
     carry the nonce.
  2. The `begin` route sets the desktop transaction cookie (its own name, browser-bound, 600 s like Omnix's) and
     redirects to Google, using a Google *Desktop* OAuth client (any loopback port, PKCE).
  3. Google redirects to the desktop callback. It finishes the OIDC exchange, checks the transaction cookie, **does
     not** give the system browser a session, and marks the start request as done for that identity. The browser shows
     "You can return to MarketPulse."
  4. The window POSTs its nonce to the handoff route. On success the gateway issues the session on the window's own
     cookie jar. The nonce is single-use, bound to its start request, and expires after 600 s.

  `invite` and `remember` carry through the flow, and account linking (`google/link`) gets the same desktop variant.
  There is no `marketpulse://` protocol handler, so a web page cannot inject an attacker's login (login CSRF).

  **Accepted residual risk:** a local process running as the same Windows user could read the `begin` URL from the
  browser's command line and finish the flow with its own Google account. Such a process already has the user's full
  access, so this is accepted.

  **Google client:** a Google *Desktop* OAuth client's secret ships inside the installer and is not confidential, as
  is normal for installed apps; PKCE protects the flow. `OMNIX_GOOGLE_REDIRECT_URI` is the desktop callback on the
  persisted HTTP port.
- **Other devices** using this install's accounts over the LAN are out of scope for v1.

### 4.8 LLM

- The full provider layer is present (full mirror). MarketPulse settings expose the chat/structured providers:
  OpenRouter, Cerebras, OpenAI-compatible, LM Studio, llama.cpp. No model ships in the installer.
- Trading's Hermes research client and `hermes_research_api` work against an optional sidecar. The RPG `hermes`
  feature is not enabled.
- AI shadow analysis stays research-only with no order authority (AGENTS.md "Trading"), and the AI budget applies as
  in Omnix.

### 4.9 Alerts and notifications

- Omnix seam S-6 adds a `desktop` outbox channel: an Omnix migration widens the channel CHECK constraint, and a
  `DesktopSender` (a `NotificationSender`, alongside `EmailSender` and `PushSender`) is configured by
  `OMNIX_TRADING_DESKTOP_NOTIFY_URL` and `OMNIX_TRADING_DESKTOP_NOTIFY_SECRET`.
- **Channel wiring:**
  - `desktop` is added to the `DeliveryChannel` Literal, `OUTBOX_CHANNELS`, `AVAILABLE_ALERT_CHANNELS`
    (`alerts_channels.py:27`) and the margin-call enqueue (`paper_margin_notifications.py:99`).
  - Enqueue takes `OUTBOX_CHANNELS ∩ alert.notification_channels`, so an alert uses `desktop` only when the user
    selects it. The MarketPulse UI preselects it for new alerts.
  - `DesktopSender` joins the dict built by the delivery monitor's lazy `senders` property (`alerts_delivery.py:621-628`).
    With its URL unset, `desktop` rows fail as `channel_unavailable`, as for any missing sender.
  - It posts only to the fixed loopback URL from its environment, never through the webhook `url_policy`, which
    refuses loopback.
- **Payload:** the claim select and the sender payload carry `created_at`, `event_kind`, `workspace_id` and a
  navigation target (symbol, interval, alert id). `ClaimedDelivery` lacks `created_at` and the target today
  (`alerts_delivery.py:172-186`), and the listener should not need extra API calls to display a notification.
- **Flow:** the gateway's existing delivery monitor claims `desktop` rows like any other channel, and the sender POSTs
  them to the supervisor's loopback listener (§4.3) with the shared secret. The supervisor returns 200 after
  `Notification.show()`. Lease, attempt fencing, backoff and `max_attempts` stay unchanged; there is no external claim
  API.
- **Who sees what:**
  - The main process learns the signed-in account with `session.fromPartition('persist:marketpulse').fetch('/api/auth/session')`
    at startup, and again on a bridge event the web app raises when the session changes.
  - The listener shows a notification with content only when its `workspace_id` belongs to that account.
  - For another workspace it shows a content-free notice ("An alert fired for another account") and answers 200.
    There is no in-memory queue: that account sees the alert after signing in, in the in-app alert and notification
    history, which is already durable in PostgreSQL. One person's alerts are never shown to another person on a
    shared PC.
- **Semantics:** delivery is **at least once**. A crash after `show()` and before the 200 shows it twice. A delivery
  whose attempts run out while the listener cannot answer (for example a hung main process) is failed, as for any
  channel.
- **After sleep:** the monitor sends at most 4 rows per 10 s pass (`MAX_SENDS_PER_PASS`), so a backlog arrives in
  pieces. The listener debounces: deliveries whose `created_at` is older than a staleness limit, arriving within a
  short window, are collected into one summary ("12 alerts while you were away"). Each row is still answered 200 and
  recorded as delivered; the summary is presentation only.
- Clicking a notification focuses or opens the matching chart window.
- In the desktop app the web feature hides Web Push setup and shows the desktop channel instead (W-2).

### 4.10 Research handoff and climatology

- **Data-only repository (D-9).** The scheduled research process publishes the premarket handoff
  (`prospective_gap_inbox/YYYY-MM-DD.json`) and climatology (`prospective_gap_state/climatology.json`) to a separate
  private repository, working name `autonomx/trading-research-data`; the owner picks the final name. Both Omnix and
  MarketPulse import from it. Omnix no longer tracks those files, as already happened with the journal.
- **Same contract.** The file formats (`prospective-gap-scheduler-handoff-v1`, `prospective-gap-climatology-state-v1`),
  the 09:20 ET deadline, "publish once, never rewrite a published session", and "never publish a state older than the
  previous one" all carry over unchanged. Only the repository changes.
- **Transport.** S-7 adds an HTTP transport to `fetch_from_github`: the GitHub REST contents API with a token from the
  secret store, used only when `gh` is missing. Omnix keeps `gh`.
- **Token scope.** The token is a fine-grained, read-only (`contents: read`) token on the data repository only, so it
  exposes no Omnix source. The import stays an **owner-only** setting.
- **Migration** is S-11 in Phase 1.

### 4.11 Look and feel (D-10)

- **Built in Omnix (D-11), then synced.** Omnix trading gets the same look, and there is one UI to maintain. W-3
  starts with three Omnix document changes:
  - the parity roadmap's line 11 amended to allow visual resemblance with our own assets;
  - a DECISIONS entry with the screen list and screenshot baselines below as acceptance, answering WP-9.8's objection
    (`DECISIONS.md:269`) to restyling with no metric behind it;
  - a note that parity decision D-8 ("native desktop wrapper: not needed") is superseded by MarketPulse.

  Option (b), a MarketPulse-only theme stylesheet, was rejected: its selectors would break whenever Omnix's mirrored
  DOM changes.
- **Covered:**
  - layout: top header toolbar, left drawing toolbar, right widget bar with watchlist, details, alerts and object
    tree, bottom panels, and the tab bar in the title bar;
  - density and spacing, palettes in dark and light, typography, menus, dialogs, tooltips and toasts;
  - the desktop chrome: splash, error and crash views, and the title-bar overlay colours.
- **Method:**
  - The owner runs TradingView Desktop locally as the visual reference. Side-by-side screenshots of a fixed screen
    list (chart, symbol search, indicators dialog, alert dialog, watchlist, settings, context menus, both themes) are
    stored only on the owner's machine, never in a repository.
  - Each screen is matched by eye and then held by Omnix's existing browser-check method: mocked APIs plus Playwright
    screenshots, compared against a committed baseline of *our own* UI. Baselines are recorded on Windows, where the
    gates run, because rendering depends on the OS and fonts.
  - Palette and spacing values come from matching by eye only, never from reading TradingView's CSS or `app.asar`
    (D-7).
- **Never copied:** the TradingView name, logo, icons, sounds, fonts, or any CSS or markup taken from TradingView's
  app or site. Icons are our own set. Fonts are open-licensed system or bundled fonts. MarketPulse has its own name and
  logo, so it cannot be mistaken for a TradingView product.

## 5. Phases

Each work package ends at its gate. A gate passes only when it runs after the last code change in that package.

### Phase 0: Bootstrap the MarketPulse repo

- **MP-0.1** Layout (§4.1), owned `README.md`, `CONTRIBUTING.md` (the mirror rules from §4.2), a `NOTICE` for bundled
  third-party licenses (Electron/Chromium, PostgreSQL, CPython, npm and Python dependencies).
- **MP-0.2** `sync/omnix-manifest.toml` first draft.

**Gate:** the skeleton builds; the README records D-1…D-11 and the mirror rules.

### Phase 1: Seams in Omnix

These land in **Omnix**, with Omnix tests. With the new settings unset, Omnix behaves exactly as today, with two
deliberate exceptions the owner approved: S-11 changes Omnix's default research repository (D-9), and W-3 changes
Omnix's look (D-11).

- **S-1 Trading-only boot test.** An Omnix test boots `create_production_app` with `OMNIX_FEATURES=trading` against
  the scratch test DB, then checks `/health`, `/ready`, `/api/auth/session` and one trading route. This guards
  MarketPulse's way of running Omnix from the authority side.
- **S-2 `OMNIX_RESOURCES_DIR`.** `app/runtime/paths.py` resolves *writable* resources (data, blobs, trade logs, agent
  logs, caches) from this variable when set. Bundled *read-only* resources (fixtures, corpora) keep resolving from the
  source tree. Default unchanged. Audit callers that build write paths from `parents[N]`.
- **S-3 Product identity.** Visible "Omnix" strings in the trading UI come from `web/src/shared/productIdentity.ts`;
  backend visible strings ("Omnix alert") come from `OMNIX_PRODUCT_NAME`. Defaults stay "Omnix". Internal ids (window
  names, storage keys, cookie names by default) do not change.
- **S-4 (dropped in revision 3)** A cookie prefix is not needed (§4.6), and the web's hard-coded `omnix_csrf` would
  break with one.
- **S-5 Desktop Google login.** The start, begin, callback and handoff routes and the link variant from §4.7, in the
  auth service, off unless `OMNIX_AUTH_DESKTOP_HANDOFF=1`. Tests:
  - the system browser gets no session;
  - the begin URL does not contain the nonce;
  - start and handoff are POST-only;
  - the nonce is single-use and expires;
  - the handoff fails for a nonce bound to another start request;
  - `invite` and `remember` are honored.
- **S-6 `desktop` outbox channel** for alerts and margin calls (§4.9):
  - an Omnix migration for the CHECK constraint;
  - the channel lists and margin-call enqueue from §4.9;
  - `created_at` and the navigation target in the claim and payload;
  - `DesktopSender`, off unless its URL is set.

  Tests: delivered on 200; retried on a connection error; failed after `max_attempts`; payload fields present; the
  secret is sent and never logged; loopback-only target.
- **S-7 HTTP transport for the GitHub handoff/climatology fetch**, used only when `gh` is absent; token from the
  secret store.
- **W-1 Injectable module list.** `installViewApiFirewall` (and anything else in `app/` that imports `moduleManifests`
  at runtime) takes the module list as an argument. Omnix's `main.tsx` passes the full manifest list; the MarketPulse
  entry passes `[tradingModule]`. Trading's own imports of `app/modules` are type-only and disappear from the bundle,
  so they need no change. `typecheck:trading` stays green.
- **W-2 Desktop bridge.** `web/src/shared/desktopBridge.ts` returns `null` in a browser. `alertNotificationsApi`,
  `InstallAppNote` and window-set opening use it: in the desktop app they hide Web Push and the install note and open
  windows through the bridge. The auth session hook reports sign-in and sign-out through `sessionChanged` (§4.9).
- **S-8 (before Phase 11)** A macOS Keychain backend for `provider_secret_store` and the secret store.
- **S-10 Launcher first password.** `OMNIX_AUTH_LAUNCHER_FIRST_PASSWORD=1` lets `user:local` set a *first* password
  without a credential when the current session's **stored record** has `auth_method="local"` and was issued less
  than `REAUTHENTICATION_WINDOW_SECONDS` ago. Two details:
  - Compare against the stored session record, not the principal string (`session:local`).
  - `login_with_install_credential` also records `"local"`; that is harmless, because that caller already proved the
    credential.

  The check goes inside `_reauthenticated`, only when `stored_password is None`, before the line that refuses accounts
  with an install credential. `set_password`'s existing-password branch is unchanged. Off by default, so Omnix is
  unchanged. Tests:
  - allowed within the window;
  - refused after it;
  - refused once a password exists;
  - refused for a password or Google session.
- **S-11 Research data repository (D-9).** An owner action plus an Omnix change.
  - **Nothing to migrate.** Omnix `main` tracks no `prospective_gap_*` files: the inbox was never committed, and
    `climatology.json` was removed in `ab39ca516a`. Its last state is the seed in migration `0124`, which stays as it
    is because migrations are checksummed.
  - **Steps:**
    1. The owner creates the private data repository. The first climatology published there must not be older than
       the state Omnix already holds (AGENTS.md rule).
    2. Change Omnix's default `OMNIX_TRADING_PROSPECTIVE_GAP_GITHUB_REPOSITORY` to the data repository. The scheduled
       import, the CLI `--github` path and the climatology fetch all go through `fetch_from_github` and this one
       variable (`prospective_gap_inputs.py:142-152`, `:210`, `:268`). Paths stay the same, so `HANDOFF_PATH` does not
       change. Imports are keyed on session date + SHA-256, so the new source string causes no conflict.
    3. Rewrite AGENTS.md "Trading research process", `docs/trading/PROSPECTIVE_GAP_END_TO_END_RUNTIME.md` and
       `PROSPECTIVE_GAP_V42_SHADOW.md` to publish there.
    4. Give the scheduled research routine push access to the data repository and repoint it.
  - The only test monkeypatches `fetch_from_github`, so no test changes are needed beyond the new default.

  Cut over on a weekend, outside the 04:00–09:30 ET import window. Gate: Omnix's manual
  `import-handoff --github <last session>` and `import-climatology` both read from the data repository, and the next
  scheduled import on a trading day succeeds.
- **W-3 TradingView-like look (§4.11, D-11).** It starts with the parity roadmap and DECISIONS changes from §4.11.
  Then the visual work on Omnix's trading UI, screen by screen from the §4.11 list, with Playwright screenshot
  baselines of our own UI. It can run in parallel with Phases 2–8.
- **W-4 Title-bar mode.** In the desktop app (W-2 bridge present):
  - **Tab bar moves to the top.** `TradingSessionTabs` renders *below* the header and command bar today
    (`TradingWorkspace.tsx:488-642`), with `TradingWindowRestore` between them; title-bar mode moves it to the top of
    the page. This is a layout change, not just CSS.
  - **Drag regions:** `-webkit-app-region: drag` on the empty space, `no-drag` on tabs and buttons, so tab
    drag-and-drop still works. Drag regions swallow all mouse events, including right-click.
  - **Window controls:** it reserves the `titleBarOverlay` width (`navigator.windowControlsOverlay` where available,
    a fixed inset otherwise) and sets the overlay colours with `setTitleBarOverlay` on the bridge.
  - **Pages without the tab bar** (login, settings, not-found) get a plain drag strip, so a frameless window can
    always be moved.
  - Popped-out window-set windows use the same mode. In a browser nothing changes.

**Gate (in Omnix):** each seam's tests pass; S-1 passes; S-11's import gate passes; Omnix's trading pytest,
`typecheck:trading`, vitest for trading, and lint pass on `main`. The look (W-3) is not part of this gate. It runs in parallel with its own per-screen checks, and Phase 9's release needs it done.

### Phase 2: Mirror and sync tooling

- **MP-2.1** `sync_from_omnix.py`: `sync` and `check`, with mirrored and derived files (§4.2).
- **MP-2.2** First sync from Omnix `main` after Phase 1.
- **MP-2.3** Mirrored tests: root `conftest*.py`, fixtures, `quarantine.toml`, every trading test file, and the
  `resources/trading` fixture folders from §4.1.
  - The **trading test set** is defined once: the sync tool writes `sync/trading-tests.txt` from the manifest's test
    globs, and both the Phase 2 count comparison and §6 item 2 run exactly that list.
  - Tests that need services MarketPulse does not run (GPU, live providers) keep their markers and run only where
    Omnix runs them.

**Gate:**
- `sync check` is clean.
- `python -m pytest src/tests -q --co` collects every mirrored test without errors.
- Collected count is printed and compared with Omnix's trading set.

### Phase 3: Headless server (run from the source checkout)

- **MP-3.1** `src/marketpulse/env.py` builds the child environment.
  - **It keeps only** an allowlist of system variables Windows Python needs: `SystemRoot`, `windir`, `TEMP`, `TMP`,
    `USERPROFILE`, `LOCALAPPDATA`, `APPDATA`, `COMPUTERNAME`, `NUMBER_OF_PROCESSORS`, `PROCESSOR_ARCHITECTURE`, `PATH`
    rebuilt as the bundled PostgreSQL `bin` + `System32` + the system `PATH`, and `HTTP_PROXY`, `HTTPS_PROXY`,
    `NO_PROXY` when set.
  - **It drops** every inherited `PYTHON*` and `OMNIX_*` variable.
  - **It sets:**
    - `OMNIX_FEATURES=trading`, `OMNIX_ENV=production`, `OMNIX_AUTH_MODE=local`, `OMNIX_LOCAL_JOB_WORKER=1`,
      `OMNIX_AUTH_REGISTRATION=invite`, `OMNIX_AUTH_LAUNCHER_FIRST_PASSWORD=1`;
    - `OMNIX_GOOGLE_CLIENT_ID`, `OMNIX_GOOGLE_CLIENT_SECRET` and `OMNIX_GOOGLE_REDIRECT_URI` when Google sign-in is
      configured (all three or none; `auth/settings.py:117-121`);
    - `OMNIX_ALLOWED_ORIGINS=http://127.0.0.1:<http-port>`;
    - DB URLs: `OMNIX_MIGRATION_DATABASE_URL` (owner, for migrate, backup and restore) and `OMNIX_DATABASE_URL`
      (`omnix_app`);
    - the persisted ports, including `OMNIX_JOB_WORKER_METRICS_PORT`;
    - `OMNIX_RESOURCES_DIR` and every secret path from §4.5;
    - `OMNIX_PRODUCT_NAME=MarketPulse`, `OMNIX_AUTH_DESKTOP_HANDOFF=1`;
    - the `DesktopSender` URL and secret;
    - `OMNIX_TRADING_PROSPECTIVE_GAP_HANDOFF_IMPORT=0` until the owner configures the import.

  The gateway command line also passes `--host 127.0.0.1 --port <http-port>`. A test asserts the allowlist, the
  drops, the whole MarketPulse set and the command line.
- **MP-3.2** `marketpulse.desktop_app`: a **raw ASGI** wrapper around `app.composition.production:app`. It forwards
  `lifespan` events to the wrapped app (`ProductionApplication` initializes on lifespan; a Starlette `Mount` would not
  run it). It serves the built web app with SPA fallback and the §4.6 headers, and passes `/api`, `/events`, `/health`
  and `/ready` through. Tests: lifespan startup reaches the gateway; SPA fallback; headers present.
- **MP-3.3** Run the §4.3 gateway command against a fresh scratch PostgreSQL 17 database with roles set up as in
  §4.3. The Docker test container is fine in development, but never Omnix's live 5432.

**Gate:**
- A fresh DB migrates (full schema).
- `/ready` is green with only `trading` enabled.
- The trading pytest set passes; PG tests run on the scratch DB with `OMNIX_TEST_DATABASE_URL` set literally.
- Known Omnix baseline failures are listed, not hidden.
- `src/tests_marketpulse` passes, including a test that no path in the child environment points under
  `%LOCALAPPDATA%\Omnix`.

### Phase 4: Web app

- **MP-4.1** `web/marketpulse/main.tsx` and `vite.marketpulse.config.ts`:
  - QueryClient and providers as `renderWithProviders` uses them (Mantine stays; it is in the mirrored deps);
  - router: trading routes, login, settings, not-found;
  - product identity "MarketPulse";
  - registers the trading module only;
  - calls `installViewApiFirewall([tradingModule])` first (W-1), as Omnix's `main.tsx` does.
- **MP-4.2** Settings for MarketPulse: account, sessions, guests and invites, LLM providers, market data (Alpaca,
  IBKR), research handoff (owner only). Import individual section components (`AccountSection`,
  `AiProvidersSettings`, `IbkrSettings`, `ResearchCredentialSettings`, …), which each reach only a few files. Never
  import `features/settings/module`, which reaches the whole app.
- **MP-4.3** `tsconfig.marketpulse.json` covering `web/marketpulse` + `features/trading`.

**Gate:**
- `npm --prefix web run typecheck:trading`, `tsc -p tsconfig.marketpulse.json`, trading vitest, and the MarketPulse
  build all pass.
- The bundle report shows no rpg, audiobook, assistant or voice chunks (needs W-1).
- The mirrored Playwright `trading-terminal.spec.ts` passes against the MarketPulse build. It uses an owned
  `playwright.marketpulse.config.ts` that serves the MarketPulse entry, plus a mock for `/api/auth/session` next to
  Omnix's `/api/trading/**` mocks, so the spec does not stop at the login page.

### Phase 5: Embedded runtime (Windows x64)

- **MP-5.1 Python.** `build/python.ps1` stages python-build-standalone CPython 3.11 (pinned + SHA-256), installs
  `requirements/gateway.lock.txt` with hashes, writes `python311._pth`, and pre-compiles `.pyc`.
- **MP-5.1b `ibapi` (D-8).** `build/ibapi.ps1` installs `ibapi` from Interactive Brokers' official TWS API
  distribution, not from PyPI's unofficial uploads, into the bundled site-packages. `ibkr_runtime` does a plain
  `import ibapi` / `from ibapi.client import EClient` (`ibkr_runtime.py:138-156`), so site-packages works.
  - **Source:** IB ships Windows as an MSI. Extract it with `msiexec /a` (administrative extract, no install), or take
    `source/pythonclient` from the Mac/Unix zip, which is the same pure-Python client.
  - **Install:** build a wheel and install it with `--no-deps`.
  - **Pinning:** pin the version and the SHA-256 of the download, and keep the archive in a private build cache,
    because IB removes old builds.
  - **Dependencies:** check that the pinned version works with the locked `protobuf` (`gateway.lock.txt`); recent
    `ibapi` versions use protobuf.
  - Before any build leaves the owner's machines, the owner confirms that IB's API license allows redistributing it
    inside an installer.
  - The license text goes into `NOTICE`.
  - If redistribution is not allowed, the fallback is a first-run download from IB with the user accepting IB's
    license.
- **MP-5.2 PostgreSQL.** `build/postgres.ps1` stages the official PostgreSQL 17.x Windows x64 binaries zip (pinned +
  SHA-256). It keeps `bin` (postgres, initdb, pg_ctl, pg_isready, pg_dump, pg_restore), `lib` and `share`, and drops
  pgAdmin, docs, include and symbols.
- **MP-5.3 Supervisor** (`desktop/src/main/supervisor/`): the §4.3 order, health waits, restart with backoff (3
  tries, then the error view), persisted ports, log rotation, snapshots, the Job Object helper.

**Gate:**
- In the staged runtime, `python -c "import app, gateway_cluster, marketpulse, ibapi"` succeeds, and a
  `scripts_service` user-script worker starts.
- On a clean Windows user account with no Python, PostgreSQL or Docker installed, a script runs the supervisor
  headless: it migrates, `/ready` is green, and it stops cleanly.
- A PostgreSQL connection without a password is refused.
- Killing the supervisor process leaves no `postgres` or `python` processes after 10 s (Job Object).
- A busy HTTP port produces the error view's cause code, not a silent move.

### Phase 6: Electron shell

- **MP-6.1** Scaffolding: Electron at the newest stable major when work starts (41.x at the time of writing),
  TypeScript, esbuild for main and preload.
- **MP-6.2** Frameless windows with `titleBarOverlay` and W-4 title-bar mode, splash, error and crash views in the
  §4.11 look, menu, nav rules, window-set windows with saved bounds (§4.4).
- **MP-6.3** Preload bridge and W-2 wiring.
- **MP-6.4** Hardening (§4.6), with tests that forbidden navigation, popups and permissions are refused.
- **MP-6.5** Playwright-for-Electron smoke suite:
  - startup through splash to sign-in, then the chart;
  - a window set opens two windows that see each other over BroadcastChannel;
  - an external link opens externally;
  - the window can be dragged by the title-bar area but not by a tab, and the window controls are not covered;
  - quit leaves no processes.

**Gate:** the smoke suite passes on the embedded runtime from Phase 5.

### Phase 7: Accounts in the desktop

- **MP-7.1** First-run owner sign-in through the one-time login code, then the required email and password step
  (§4.7). After that: sign-out, sessions, guests and invites inside the shell.
- **MP-7.2** Desktop Google login (§4.7, S-5) with a Google Desktop OAuth client.
- **MP-7.3** Sessions persist across restarts (`persist:` partition, cookie encryption fuse).

**Gate:**
- Password and Google sign-in work end to end on Windows.
- Replayed, expired and foreign nonces are rejected (S-5 tests rerun in MarketPulse).
- A guest cannot reach owner-only settings, including the research handoff token.
- After first run, restarting the app or signing out never signs anyone in as the owner without a password or Google.
- Killing the app during the first-password step, then relaunching, issues a fresh login code and the step completes.

### Phase 8: Alerts, tray, background

- **MP-8.1** Loopback notification listener in the main process for `DesktopSender` (S-6): it checks the secret,
  shows the native notification, then returns 200. It also does click-through and the after-sleep summary. A dev-mode
  Start menu shortcut with the AppUserModelID makes toasts work before Phase 9's installer.
- **MP-8.2** Tray, hide-on-close, start at login, alert badge.
- **MP-8.3** Optional "Keep the computer awake during market hours" (`powerSaveBlocker`), off by default.
- **MP-8.4** Research handoff import from a desktop install (S-7), tested against a fixture repository.

**Gate:**
- An alert that fires while every window is hidden raises a native notification, and clicking it opens the alert's
  chart.
- An alert for another account's workspace is not shown to the signed-in account.
- With the listener down, a delivery is retried and shown once the listener is back within its attempts. A request
  without the secret is refused.
- Simulated suspend (pause the children, advance past a monitor interval, resume) recovers monitors without
  duplicate fills or notifications.
- The fixture-repo import is idempotent.

### Phase 9: Windows packaging, updates, recovery (first release)

- **MP-9.1** electron-builder NSIS, per-user install, AppUserModelID, Start menu shortcut, uninstall that keeps data
  unless the user chooses otherwise.
- **MP-9.2** Code signing: unsigned builds show SmartScreen warnings. Pick Azure Trusted Signing or an OV/EV
  certificate before a build goes beyond the owner's machines.
- **MP-9.3** Updates: `electron-updater` from a public release feed (a separate public releases repo or HTTPS
  bucket), with no token in the app. On update: stop services, snapshot `pgdata`, install, start, migrate. If
  migration fails: restore the snapshot and point the user to the previous installer, kept in the release feed.
  electron-updater has no automatic rollback.
- **MP-9.4** PostgreSQL major version pinned to 17. A future major upgrade is its own work package.
- **MP-9.5** "Export diagnostics": logs, versions, Omnix sync commit, no secrets.
- **MP-9.6** Size report. v1 ships the full gateway dependency set (numpy, onnxruntime, pandas, …). The installer is
  expected to be several hundred MB; this is tracked, not a gate.

**Gate:**
- On a clean Windows 11 machine: install, first run, sign-in, chart, alert notification.
- Upgrading from the previous build migrates and keeps data.
- A forced migration failure restores the snapshot, and the app starts on the old schema with the old build. The
  failure is injected through a test-build-only hook (`MARKETPULSE_TEST_FAIL_MIGRATION`) that release builds do not
  contain.
- Uninstall behaves as described.

### Phase 10: Data import from Omnix (optional)

- **MP-10.1** Tenant-exempt market reference data (`0154`) and caches from an Omnix DB or `resources/cache`. Read-only
  on the Omnix side, dry-run first, idempotent.
- **MP-10.2** Account-owned trading data (paper accounts, alerts, layouts, scripts), remapped to the MarketPulse
  owner's tenant and user ids, after a dry-run report. Only if the owner wants it.

**Gate:** import into a scratch MarketPulse DB; re-run is a no-op; counts and spot checks match.

### Phase 11: macOS

- **MP-11.1** Runtime staging for macOS arm64 (and x64 or universal): python-build-standalone and PostgreSQL macOS
  binaries. Data under `~/Library/Application Support/MarketPulse`. The Job Object equivalent is a process group plus
  parent-death watch.
- **MP-11.2** Keychain secret backend (S-8).
- **MP-11.3** Shell differences: menu bar, dock, login items, `hiddenInset` traffic lights with W-4's left inset.
- **MP-11.4** Developer ID signing, hardened-runtime entitlements for the bundled `postgres` and `python`,
  notarization. Built on the owner's Mac.

**Gate:** the Phase 9 gate repeated on a clean macOS machine.

### Phase 12: Slim the mirror (after v1)

- Shrink the composition root, conftest and gateway dependency set until a trading-only mirror boots and tests. This
  takes Omnix refactors (lazy composition imports, a trading-only conftest path, optional heavy deps).
- The full schema still ships, because kernel migrations need it (§3).

**Gate:** S-1 and the full §6 set pass on the reduced mirror; the installer is smaller.

## 6. MarketPulse gate set (after every sync and before every release)

1. `python sync/sync_from_omnix.py check`
2. `OMNIX_FEATURES=trading python -m pytest <paths listed in sync/trading-tests.txt> src/tests_marketpulse -q --tb=short`
   (PG tests on a scratch DB; trading-only, as MarketPulse runs Omnix and because other features' code is not mirrored)
3. `ruff check src/marketpulse` (mirrored code is linted in Omnix)
4. `npm --prefix web run typecheck:trading`, MarketPulse tsconfig, trading vitest, MarketPulse build + bundle check
5. Headless supervisor start on a fresh data dir (Phase 5 gate script)
6. Electron smoke suite
7. Release only: the Phase 9 install, upgrade and recovery checks

## 7. Owner questions

| # | Question | Answer |
|---|----------|--------|
| Q-1 | `ibapi`: bundle it, or use the user's own TWS API install? | **Bundle it** (D-8). The license check is part of MP-5.1b. |
| Q-2 | Research handoff: keep importing from `autonomx/omnix`, or use a separate data-only repository? | First a separate repository; on 2026-10-17 the owner chose **Omnix's own repository** (D-9) |
| Q-3 | Look, icons and brand colours | **Look like TradingView Desktop, or closely resemble it** (D-10, §4.11), with MarketPulse's own name, logo and icons |
| Q-4 | Final name of the data repository, and the MarketPulse logo | Logo supplied; no data repository (D-9) |
| Q-5 | Should Omnix trading take the TradingView-like look too, or should the look live only in MarketPulse? | **Omnix too** (D-11): built once in Omnix, with the parity rule amended and a DECISIONS acceptance entry |

## 8. Risks

| # | Risk | Mitigation |
|---|------|------------|
| R-1 | An Omnix change breaks trading-only boot. | S-1 runs in Omnix; the §6 gate set runs after each sync. |
| R-2 | Someone edits mirrored files in MarketPulse. | `sync check` in the gate set; CONTRIBUTING rules. |
| R-3 | PostgreSQL on user machines: antivirus, stale `postmaster.pid`, busy port. | `pg_ctl` handles stale pids; persisted port with an explicit error; logs named in the error view. |
| R-4 | BroadcastChannel across `BrowserWindow`s on one session in the chosen Electron version. | MP-6.5 tests it; fallback is a main-process relay through the bridge. |
| R-5 | Installer size from the full gateway dependency set. | Accepted for v1; Phase 12. |
| R-6 | Omnix secrets touched by MarketPulse on the owner's PC. | All paths overridden (§4.5) and tested in Phase 3. |
| R-7 | Trading research presented as authoritative. | No change to trading authority (AGENTS.md "Trading"). |
| R-8 | A close resemblance to TradingView is mistaken for, or claimed to be, a TradingView product, or reuses its protected material. | Own name, logo, icons, sounds and fonts; no material taken from TradingView's app or site (§4.11); reference screenshots stay off-repo. Before public distribution, the owner gets a trademark and trade-dress check. |
| R-9 | `ibapi` redistribution is not allowed. | MP-5.1b fallback: first-run download from IB. |
| R-10 | The data-repo cutover misses a premarket session. | S-11 cuts over on a weekend; the manual import commands stay as the fallback. |

## 9. Out of scope for v1

- Remote or LAN access to a MarketPulse install.
- Bundling LLM models or TWS.
- A native tab strip.
- Any widening of trading or order authority.
- Linux packages.

## 10. Review log

**Round 1** (independent reviewer, read-only, against Omnix `9113b6f59e`): 3 blockers, 14 major, 5 minor. All
accepted after the key claims were re-checked in the code. Revision 2 changes:

- **Blockers:**
  - Full `src/app` mirror instead of a kernel subset: the composition root needs the platform.
  - Full schema: kernel migrations alter RPG, audiobook and agent_runtime tables.
  - Root conftest mirrored with the platform.
- **Major:**
  - W-1 web module leaf and a MarketPulse entry.
  - Process model is `run_omnix_gateway.py` with schedulers in the gateway, not a separate worker.
  - Static SPA served by an owned ASGI wrapper with nginx-equivalent headers.
  - Secrets use Omnix's DPAPI stores at MarketPulse paths; no "master key"; Keychain seam before macOS.
  - `OMNIX_RESOURCES_DIR` seam.
  - `._pth` instead of `-I`.
  - `desktop` outbox channel instead of a nonexistent alert stream.
  - Google sign-in through nonce polling, with no protocol handler and no browser session; cookie prefix seam.
  - No native tab strip.
  - Port fixed at first run.
  - `pg_ctl` plus a Job Object.
  - `ibapi` question.
  - Handoff token exposure.
  - Hermes scoped to trading's client.
- **Minor:**
  - `omnix_app` role, owner-role migrations, UTC.
  - `pgdata` snapshot rollback.
  - Deterministic gates (fixture repo, simulated suspend, dev AUMID shortcut).
  - Derived `pyproject.toml` and `package.json`.
  - S-7 keeps `gh` first in Omnix.
  - "MarketPulse never authors migrations" rule.

**Round 2** (same reviewer, on revision 2): round-1 fixes confirmed, except four that were only partly done. 8 major
and 5 minor findings. All accepted after the code-backed claims were re-checked. Revision 3 changes:

- **`._pth` means isolated mode.** `src` and `scripts` both go in the `._pth`, with an import check in the staged
  runtime.
- **Complete child environment** (MP-3.1): `OMNIX_ENV=production`, `OMNIX_LOCAL_JOB_WORKER=1`, invite-only
  registration, exact `OMNIX_ALLOWED_ORIGINS`, `--host`/`--port`, persisted metrics port, handoff import off until
  configured. A test asserts all of it.
- **W-1 reworked** into an injectable module list for `installViewApiFirewall`. The MarketPulse entry installs the
  firewall. S-4 is dropped (hard-coded `omnix_csrf`), and the Google transaction cookie gets its own name.
- **Alerts:** a `DesktopSender` in the gateway POSTs to a loopback listener in the supervisor, reusing the outbox
  lease and retry. Delivery is stated as at least once.
- **First-run owner sign-in** through `login-code` once, then a forced email and password step. Never auto-signed in
  again.
- **`resources/trading` fixtures mirrored**, with S-2 splitting read-only and writable resources.
- **`initdb --auth=scram-sha-256`**, plus a test that passwordless connections are refused.
- **Google flow tightened:** nonce never in the opened URL; a `begin` route sets the browser-bound transaction cookie;
  POST start and handoff; 600 s; `invite`, `remember` and a link variant carried through.
- **Minor:**
  - raw ASGI wrapper that forwards lifespan;
  - owned Playwright config with an auth-session mock;
  - settings reused per section;
  - backup and restore use the owner URL and re-apply `0101` grants;
  - a test-build-only migration-failure hook.

**Round 3** (same reviewer, on revision 3): round-2 fixes confirmed. 3 major and 6 minor findings. All accepted after
re-checking `_reauthenticated` (`auth/service.py:384-410`), `show-install-credential`'s console requirement,
`ClaimedDelivery`, `MAX_SENDS_PER_PASS` and `AVAILABLE_ALERT_CHANNELS`. Revision 4 changes:

- **First password:** new seam S-10. The marker is written only after the password is stored; `next=/trading`;
  console recovery documented.
- **Child environment** built from a system-variable allowlist (`SystemRoot`, `TEMP`, proxies, …) instead of from
  scratch. `OMNIX_AUTH_MODE=local` and the Google variables added.
- **`desktop` channel:**
  - wired into every channel list and the margin-call enqueue;
  - payload gains `created_at` and a navigation target;
  - per-workspace display on shared PCs;
  - debounced after-sleep summary;
  - no `url_policy`.
- **Minor:**
  - S-9 → S-8 reference;
  - Phase 3 title;
  - one definition of the trading test set;
  - Google residual risk and Desktop client secret stated.
- **Confirmed by the reviewer:** `OMNIX_ENV=production` changes nothing else that affects a desktop install.

**Round 4** (same reviewer, on revision 4): **no blockers or majors remain.** All round-3 findings were confirmed
resolved; `exchange_login_code` records `auth_method="local"` (`auth/service.py:888`). Three minor points, applied in
the final text:

- **S-10 defined precisely:** the stored session record, not the principal string. It sits inside
  `_reauthenticated` before the install-credential refusal, and the first-run step has a "new sign-in code" action.
- **Alert display:** the in-memory "queue for another account" was dropped for a content-free notice plus the durable
  in-app history. The main process learns the signed-in account from the session partition and a bridge event.
- **Formatting fix** in MP-2.3.

Both the author and the reviewer consider the roadmap ready to execute.

**Owner decisions after round 4** (revision 5): Q-1 → D-8 (bundle `ibapi`), Q-2 → D-9 (separate data-only
repository), Q-3 → D-10 (TradingView-like look). Revision 5 adds:

- §4.10 rewritten for the data repository;
- §4.11 Look and feel;
- S-11 (data-repo migration), W-3 (look), W-4 (title-bar mode);
- MP-5.1b (`ibapi` staging and license check);
- frameless windows in §4.4 and MP-6.2, plus a smoke test for the title-bar area;
- R-8 to R-10.

**Round 5** (same reviewer, on the revision-5 additions): one major and four minor findings, all verified. Revision 5
was updated in place:

- **Major: D-10 versus Omnix's parity rule.** The parity rule is "features and behaviour only", and `DECISIONS.md:269`
  rejected a restyle with no metric behind it. Building the look in Omnix changes Omnix's UI, which the owner has not
  decided. Recorded as Q-5 with options (a) and (b); W-3 is conditional on (a).
- **S-11 corrected.** No files to migrate or untrack. Every reader goes through `fetch_from_github` and one variable.
  The docs to update are listed, and the routine needs push access. The Phase 1 opening names its deliberate
  exceptions.
- **W-4 is a layout move:** `TradingSessionTabs` moves above the header. The bridge gains `setTitleBarOverlay`, and
  pages without the tab bar get a drag strip.
- **MP-5.1b:** MSI administrative extract or the zip's `pythonclient`, a wheel installed with `--no-deps`, a protobuf
  compatibility check, a private build cache.
- **§4.11:** baselines recorded on Windows; values matched by eye only.

Apart from Q-5, the reviewer and the author found no blockers or majors.

**Owner decision after round 5:** Q-5 → D-11, option (a). Omnix takes the look too: it is built once in Omnix (W-3),
the parity rule is amended, and a DECISIONS entry with the §4.11 screen list and screenshot baselines is the
acceptance test. Option (b) was dropped from §4.11. **No blockers, majors or blocking owner questions remain.**
Q-4 (data repo name, logo) is open and blocks nothing.

## 11. Implementation status (2026-10-17)

Written for the owner and for whoever builds MarketPulse next. MarketPulse branch `implementation` (not pushed);
Omnix seams on branch `marketpulse-seams` (17 commits on `main`, not merged: merging is the owner's call).

### Status by phase

| Phase | Status | Evidence |
|-------|--------|----------|
| 0 Bootstrap | Done | Repo, licences, brand from the supplied logo (`brand/`, `build/render_brand.py`). |
| 1 Omnix seams | Done | S-1, S-2, S-3, S-5, S-6, S-7, S-8, S-10 and W-1 to W-4 are committed on `marketpulse-seams`, each with tests. S-4 was dropped in revision 3. S-11 is not needed: the owner kept Omnix's own public repository for research data (D-9), which MarketPulse's mirrored default already reads without a token. |
| 2 Mirror and sync | Done | `sync/sync_from_omnix.py`; mirror at Omnix `6e0cfd3392`, 3351 files after Phase 12, `check` clean. |
| 3 Headless server | Done | Full schema migrates on PostgreSQL 17.11; trading-only `/ready`; `src/tests_marketpulse` env tests. |
| 4 Web app | Done | MarketPulse build (trading only), 15 Playwright e2e tests, own screenshot baselines. |
| 5 Embedded runtime | Done | Staged-runtime gate: ready from nothing, password-only PostgreSQL, user-script worker, no process left 0.6 s after a killed supervisor. |
| 6 Electron shell | Done | 12 smoke tests (first run, window sets over BroadcastChannel, navigation lock, drag regions, permissions, crash view, quit, `--quit`, restart). |
| 7 Accounts | Done; Google off by the owner's choice | Password flow end to end; guest refusal test. Google: the shell's button opens the system browser, and the real gateway sends it to Google with the client, PKCE and loopback callback (`desktop/tests/google.spec.ts`). The callback and handoff legs pass against a local identity provider (mirrored S-5 tests). Google stays off for now (owner, 2026-10-17): it appears only when a build carries `google-oauth.json`. |
| 8 Alerts, tray, background | Done | Notification while hidden, click-through to the chart, taskbar/Dock badge, simulated sleep with exactly-once delivery, keep-awake option. |
| 9 Windows packaging | Done; signing waits for a certificate | NSIS per-user installer. Install, update over a running app and uninstall checked in a scratch folder. Migration rollback test. electron-updater end to end against a local feed (`desktop/scripts/update-feed-builds.mjs`): 0.1.0 downloaded 0.1.1, installed it on quit, and 0.1.1 started on the same data. Signing turns on from the environment (Azure Artifact Signing or a certificate file), checked with a throwaway self-signed certificate. Updates come from `autonomx/MarketPulse` releases; the repository is public (2026-10-17), so installs read them without a token. |
| 10 Omnix data import | Done | File › Import from Omnix…: market reference data (MP-10.1) and, optionally, one workspace's documents, alerts and paper accounts as the owner's (MP-10.2). Dry run first, read-only on Omnix, each item whole or not at all, idempotent. Checked on a copy of the real schema under row-level security (336 paper accounts, 5770 rows). |
| 11 macOS | Ready; the Mac run is deferred (owner) | Per-platform pins and staging, mac targets, entitlements, notarization switch, tray template, Dock, Keychain (S-8), cross-platform tests. Not yet run on a Mac: see `docs/MACOS.md`. |
| 12 Slim the mirror | Done | Omnix refactors (owner approved): RPG's replay routes mount only with RPG, the chat store takes the desktop companion's hook only when it is enabled, and the persistence conftest follows `OMNIX_FEATURES`. A new S-1 test checks in a fresh process that a trading-only boot imports nothing of those features but their declarations. The mirror drops the code of rpg, story, audiobook, desktop_companion, character_interactions, live_speech, companion_activity and the vendored TTS. Each keeps its `feature.py`, declarations and migrations, because the full schema ships. Platform research (trading uses it), characters and image (the test conftest wires them) stay. Mirror 4572 to 3351 files; app source in the build 29 to 15 MB. The trading set, the staged gates, the Electron suite and an installed run pass on the reduced mirror. Dependencies and build-only files were pruned earlier (sizes below). |

### Changes from the plan

- **The supervisor is Python, not TypeScript.** `marketpulse.supervise` runs the §4.3 order, rotates logs, takes
  snapshots, restores, and puts its children in a kill-on-close Job Object on Windows. The Electron side
  (`desktop/src/main/supervisor.ts`) is a thin client over JSON lines on stdin and stdout. This replaced the planned
  native Job Object helper. On macOS a killed supervisor can leave PostgreSQL running; the next start reconnects to
  it.
- **`build/stage.py` replaces the `.ps1` scripts.** One cross-platform script with per-platform pins
  (`build/pins.json`) stages Python, PostgreSQL and the app, prunes, checks imports, and caps path lengths.
- **No precompiled bytecode.** Python writes its cache to the data folder (`PYTHONPYCACHEPREFIX`), so nothing writes
  into the install folder. That matters for NSIS removal of long paths and for the signed macOS bundle. First launch
  reaches ready in about 30 s.
- **First-run state comes from the database** ("the owner has no password"), not a marker file. A kill during the
  first-password step therefore gets a fresh login code on the next launch.
- **No handoff-import switch in the child environment.** The research repository, `autonomx/omnix`, is public, so
  MarketPulse imports handoffs and climatology without a token, as Omnix does. If the `gh` CLI is installed and
  signed in, it is used instead.
- **W-2's `sessionChanged` bridge was dropped.** Nothing needed it once first-run state came from the database.
- **Installer shutdown.** electron-builder's own check force-killed PostgreSQL. `customCheckAppRunning` now runs
  `MarketPulse --quit`, which stops the backend cleanly; it waits up to a minute before forcing. The install folder
  is capped at 112 characters so every file stays under Windows' 260-character limit.
- **Additions found while building:** log rotation (gateway output rotated while it runs), a renderer crash view, an
  alert badge, cross-platform test helpers.

### Findings for the owner

- **ibapi is GPL-3.0-or-later.** Bundling it is allowed with its licence and source; both ship under
  `licenses/ibapi`. The Python client is only in IB's "Latest" API release; the staged copy is version 10.50.2 from
  your install. Separately, IB's terms forbid passing IB market data on to third parties without their approval.
- **Provider status reads are open to guests** (Omnix's design): they show "configured" and a masked key, never the
  key. Writes and the research token are owner-only, and a test pins this behaviour.
- **Node 22.12 or newer** is recommended. 22.9 needs `--experimental-require-module`, which the `dist` script passes.
- **Two test-isolation bugs in Omnix** are fixed on `marketpulse-seams`. The margin-call email test used a
  five-minute window and left pending deliveries behind; another test's delivery monitor then claimed those first
  (`1d51102844`, `5eb3f34459`).

### Versions and sizes

Electron 44.5.1, electron-builder 26.15.3, CPython 3.11.17 (python-build-standalone 20261001), PostgreSQL 17.11,
ibapi 10.50.2.

| | Before Phase 12 | Now |
|-|-----------------|-----|
| Staged runtime | 637 MB | 260 MB |
| Windows installer | 238 MB | 161 MB |
| Installed | 878 MB | 571 MB |

### Verification at the last commit

- Trading test set plus `src/tests_marketpulse`, trading-only on the reduced mirror: 2189 tests (2149 + 40), none failed, 2 skipped.
- Electron: 20 tests (13 smoke, 7 unit).
- Web: both typechecks clean; vitest 1348 tests in 192 files; 15 Playwright e2e tests.
- Installer: install, boot and uninstall in a scratch folder.

PostgreSQL tests ran on scratch clusters only.

### Owner actions

1. A Windows signing identity: Azure Artifact Signing, or an OV certificate on a hardware token or cloud HSM; then
   set the variables in `desktop/scripts/dist.mjs`. Developer ID and notarization for macOS.
2. Google sign-in: off for now; when wanted, add the OAuth Desktop client as `desktop/config/google-oauth.json`.
3. Confirm the ibapi bundling decision above.
4. Build and check on your Mac (`docs/MACOS.md`), when the rest is done.
5. Merge `marketpulse-seams` (20 commits) into Omnix `main`. Until then MarketPulse syncs from that branch.
6. Before public distribution: the trademark and trade-dress check from R-8.
