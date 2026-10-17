"""
Pytest configuration and fixtures for Omnix Playwright tests.

Provides page-object fixtures, API request context, application test clients,
console-error capture, and automatic screenshot-on-failure.
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from typing import TYPE_CHECKING
from datetime import datetime, timezone
from pathlib import Path

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

from tests.conftest_quarantine import apply_item_quarantine, collection_globs, should_ignore_collection

collect_ignore_glob = collection_globs()


@pytest.fixture
def legacy_test_persistence(monkeypatch):
    """Opt one test module into the explicitly isolated legacy test adapters."""

    from app.persistence.runtime import reset_persistence_mode_cache

    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.setenv("OMNIX_ALLOW_LEGACY_TEST_PERSISTENCE", "1")
    reset_persistence_mode_cache()
    try:
        yield
    finally:
        reset_persistence_mode_cache()


@pytest.fixture
def service_token(monkeypatch):
    token = "test-service-token-" + ("a" * 43)
    monkeypatch.setenv("OMNIX_SERVICE_TOKEN", token)
    return token


def pytest_configure(config) -> None:
    # Sign-in is on by default (WP-4.1). Tests that are not about sign-in run
    # without it, including module-scoped gateways built before any per-test
    # fixture; sign-in tests set OMNIX_AUTH_MODE (or pass settings) themselves.
    os.environ.setdefault("OMNIX_AUTH_MODE", "disabled")
    # The trading research call budget is process-wide and durable; tests that
    # are about it construct their own budget.
    os.environ.setdefault("OMNIX_TRADING_AI_DAILY_CALL_BUDGET", "0")
    # Before anything reads a database URL: each xdist worker gets its own database.
    from tests.conftest_databases import configure_worker_database

    configure_worker_database()


def pytest_unconfigure(config) -> None:
    from tests.conftest_databases import drop_worker_databases

    drop_worker_databases()


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:
    if should_ignore_collection(collection_path):
        return True
    return None

@pytest.fixture(autouse=True)
def isolated_operator_data_files(monkeypatch, tmp_path):
    # Legacy-test persistence writes JSON stores under resources/data by default;
    # keep every test (and every xdist worker) off the operator's files.
    monkeypatch.setenv("OMNIX_ASSISTANT_TURN_STORE_PATH", str(tmp_path / "assistant_turns.json"))
    # The provider key store is a DPAPI file in the user's profile; a test must
    # never read the developer's real keys (their presence changed trading
    # test outcomes on machines with Alpaca credentials).
    monkeypatch.setenv("OMNIX_PROVIDER_SECRETS_PATH", str(tmp_path / "provider-api-keys.dpapi"))
    # A document kind written without a registered shape fails the test (WP-5.9).
    monkeypatch.setenv("OMNIX_DOCUMENT_SCHEMAS_STRICT", "1")
    # The RPG narrative writer uses the app's provider unless told otherwise;
    # tests never call a real one (a test that wants a writer installs it).
    monkeypatch.setenv("OMNIX_RPG_NARRATIVE_WRITER_MODE", "deterministic")
    # Prompt budgets never ask a real provider for its model list (WP-5.7).
    monkeypatch.setattr("app.providers.model_catalog._default_provider", lambda _provider_id: None)


@pytest.fixture(autouse=True)
def isolated_secret_store():
    # Never touch the developer's real DPAPI file or keychain (WP-4.9).
    from app.security.secrets import install_secret_store
    from tests.support.secrets import MemorySecretStore

    store = MemorySecretStore()
    install_secret_store(store)
    try:
        yield store
    finally:
        install_secret_store(None)


@pytest.fixture(autouse=True)
def isolated_device_permits():
    # Composing the production app installs the process-wide device permit
    # service (WP-6.2); a later test in the same worker would otherwise queue
    # its local-model calls behind PostgreSQL permits it never configured.
    from app.persistence import device_permits

    installed = device_permits._DEFAULT_SERVICE
    try:
        yield
    finally:
        device_permits._DEFAULT_SERVICE = installed


@pytest.fixture(autouse=True)
def capability_runtime_installed():
    # Composition installs the assistant-tools runtime behind
    # app.capabilities.executor (WP-4.5); tests that call capability paths
    # without composing a gateway get the same runtime.
    from app.platform.assistant_tools.executor import execute_with_grant
    from app.capabilities.executor import CAPABILITY_RUNTIME
    from app.capabilities.registry import TOOL_DECLARATIONS
    from app.platform.chat.contracts import CHAT_RESEARCH
    from app.platform.research.assistant_tool import ResearchTool
    from app.platform.agent_runtime.chat_bridge import route_typed_chat_turn
    from app.platform.agent_runtime.contracts import SECURITY_INSTRUMENTS
    from app.platform.agent_runtime.feature import _RunWorkspaces
    from app.platform.assistant_tools.contracts import AGENT_RUN_WORKSPACES
    from app.platform.chat.contracts import TYPED_TURN_ROUTER
    from app.platform.characters.contracts import CHARACTER_SNAPSHOT_OBSERVERS
    from app.platform.characters.interaction import CharacterChatResolver
    from app.platform.chat.contracts import CHARACTER_RESOLVER, CHAT_MEMORY
    from app.platform.assistant_memory.chat_prompt import AssistantChatMemory
    from app.capabilities.executor import LIVE_AGENT_TOOLS
    from app.platform.assistant_tools.live_agent_proposals import AssistantLiveAgentTools
    from app.platform.live_voice.prompt.cache import CharacterSnapshotCacheObserver
    from app.apps.trading.assistant_tool import TradingMarketDataTool, TradingSecurityInstruments
    from app.platform.research.api import ChatResearchAdapter
    from app.runtime.ports import PortBinding, PortBindings, install_port_bindings
    from app.composition.runtime_composition import composition_port_bindings

    # Every feature is enabled by default, so tests see the ports composition binds.
    install_port_bindings(PortBindings.build([
        PortBinding(CAPABILITY_RUNTIME, execute_with_grant, owner="assistant-tools"),
        PortBinding(CHAT_RESEARCH, ChatResearchAdapter(), owner="research"),
        PortBinding(TOOL_DECLARATIONS, ResearchTool(), owner="research"),
        PortBinding(TOOL_DECLARATIONS, TradingMarketDataTool(), owner="trading"),
        PortBinding(TYPED_TURN_ROUTER, route_typed_chat_turn, owner="agent-runtime"),
        PortBinding(AGENT_RUN_WORKSPACES, _RunWorkspaces(), owner="agent-runtime"),
        PortBinding(SECURITY_INSTRUMENTS, TradingSecurityInstruments(), owner="trading"),
        PortBinding(CHARACTER_SNAPSHOT_OBSERVERS, CharacterSnapshotCacheObserver(), owner="live-voice"),
        PortBinding(CHARACTER_RESOLVER, CharacterChatResolver(), owner="characters"),
        PortBinding(CHAT_MEMORY, AssistantChatMemory(), owner="assistant-memory"),
        PortBinding(LIVE_AGENT_TOOLS, AssistantLiveAgentTools(), owner="assistant-tools"),
        *composition_port_bindings(),
    ]))
    yield


@pytest.fixture(autouse=True)
def postgres_local_identity(request):
    # Persistence tests truncate shared tables (cascading to workspaces) and
    # rebuild their own fixtures. Under xdist another PostgreSQL test can run
    # in that window, so re-ensure the local identity before each one.
    url = os.environ.get("OMNIX_TEST_DATABASE_URL")
    if not url or request.node.get_closest_marker("postgres") is None:
        yield
        return
    from app.persistence.authority import PostgresAuthorityError
    from app.persistence.config import DatabaseSettings
    from app.persistence.database import PostgresDatabase
    from app.persistence.identity_service import ensure_local_identity

    database = PostgresDatabase(DatabaseSettings(url=url, pool_min=1, pool_max=1))
    try:
        ensure_local_identity(database)
    except PostgresAuthorityError:
        pass  # Runtime writes are closed (cutover tests own that state).
    finally:
        database.close()
    yield


@pytest.fixture(autouse=True)
def isolated_runtime_configuration(monkeypatch):
    # Each test models a fresh process; production policy is immutable once bound.
    from copy import deepcopy

    from app.runtime import config as runtime_config
    from app.security import tenant_context as tenant_runtime
    from app.persistence.runtime import reset_persistence_mode_cache
    from app.settings import access as settings_access
    from app.settings.registry import core_setting_specs
    from app.platform.assistant_memory.persistence.settings_store import assistant_memory_setting_spec

    reset_persistence_mode_cache()
    monkeypatch.setattr(runtime_config, "_process_config", None)

    class _TestSettingsService:
        def __init__(self):
            self.specs = {spec.key: spec for spec in core_setting_specs()}
            self.values = {spec.key: deepcopy(spec.default) for spec in core_setting_specs()}
            self.revisions = {spec.key: 0 for spec in core_setting_specs()}

        def get(self, key):
            if key not in self.specs:
                return None
            return {
                "key": key,
                "value": deepcopy(self.values[key]),
                "revision": self.revisions[key],
                "updated_by": None,
                "updated_at": None,
            }

        def set(self, key, value, *, expected_revision=None):
            if key not in self.specs:
                raise KeyError(key)
            current = self.revisions[key]
            if expected_revision not in (None, current):
                from app.settings.service import SettingRevisionConflict
                raise SettingRevisionConflict(f"revision conflict for setting {key}")
            self.values[key] = deepcopy(value)
            self.revisions[key] = current + 1
            return self.get(key)

        def patch(self, patch):
            return {
                key: self.set(key, value, expected_revision=patch.revisions.get(key))
                for key, value in patch.values.items()
            }

        def subscribe(self, key, callback):
            return lambda: None

        def register_specs(self, specs):
            for spec in specs:
                if spec.key in self.specs and self.specs[spec.key] != spec:
                    raise ValueError(f"duplicate setting spec: {spec.key}")
                self.specs[spec.key] = spec
                self.values.setdefault(spec.key, deepcopy(spec.default))
                self.revisions.setdefault(spec.key, 0)

    tenant_runtime.reset_process_tenant_for_tests()
    tenant_runtime.install_process_tenant(tenant_runtime.local_tenant_context())

    test_service = _TestSettingsService()
    test_service.register_specs((assistant_memory_setting_spec(),))
    settings_access.reset_settings_service_for_tests()

    def _install_test_settings_service(service):
        # Production composition tests are allowed to replace the provider-free
        # fake exactly as a fresh process would install its real service.
        settings_access._SERVICE = service

    monkeypatch.setattr(settings_access, "install_settings_service", _install_test_settings_service)
    monkeypatch.setattr(settings_access, "_SERVICE", test_service)

    try:
        yield
    finally:
        reset_persistence_mode_cache()
        tenant_runtime.reset_process_tenant_for_tests()
        settings_access.reset_settings_service_for_tests()

# Add project roots to path for importing app modules
SRC_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SRC_DIR.parent
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(TESTS_DIR))

# ---------------------------------------------------------------------------
# Page-object imports
# ---------------------------------------------------------------------------
from pages.base_page import BasePage
from pages.chat_page import ChatPage
from pages.header_page import HeaderPage
from pages.podcast_page import PodcastPage
from pages.search_page import SearchPage
from pages.settings_page import SettingsPage
from pages.sidebar_page import SidebarPage
from pages.voice_clone_page import VoiceClonePage
from pages.voice_studio_page import VoiceStudioPage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("OMNIX_BASE_URL", "http://127.0.0.1:8001")
SCREENSHOTS_DIR = Path(__file__).parent / "reports" / "screenshots"
RUN_RETIRED_LEGACY_UI_TESTS = os.environ.get("OMNIX_RUN_RETIRED_LEGACY_UI_TESTS") == "1"
LEGACY_UI_STATIC_TEST_FILES = {
    Path("src/tests/e2e/test_js_variables.py"),
    Path("src/tests/functional/test_phase846_inspector_shell_smoke.py"),
    Path("src/tests/functional/test_phase847_inspector_polish_smoke.py"),
    Path("src/tests/regression/test_phase846_inspector_regression.py"),
    Path("src/tests/regression/test_phase847_inspector_polish_regression.py"),
    Path("src/tests/unit/test_js_variables.py"),
    Path("src/tests/unit/test_no_new_audio_per_chunk.py"),
}
LEGACY_UI_RETIREMENT_CONTRACT_TEST = Path("src/tests/api/test_legacy_ui_retirement.py")
LEGACY_UI_STATIC_SOURCE_MARKERS = (
    "src/static",
    "src\\static",
    "/static/",
    "../../static",
    "../../../static",
    "static/rpg",
)

# ---------------------------------------------------------------------------
# Pytest options
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--base-url-omnix",
        action="store",
        default=BASE_URL,
        help="Base URL for the running Omnix application",
    )
    try:
        parser.addoption(
            "--headed",
            action="store_true",
            default=False,
            help="Run Playwright browser tests with a visible browser",
        )
    except argparse.ArgumentError:
        # pytest-playwright registers this option when its plugin is installed.
        pass


def pytest_collection_modifyitems(config, items):
    apply_item_quarantine(items)
    if RUN_RETIRED_LEGACY_UI_TESTS:
        return

    legacy_skip = pytest.mark.skip(
        reason=(
            "classic src/static and src/templates browser UI retired in Phase 18; "
            "run with OMNIX_RUN_RETIRED_LEGACY_UI_TESTS=1 to inspect archived static-source checks"
        )
    )
    for item in items:
        relative_path = Path(str(item.fspath)).resolve().relative_to(PROJECT_ROOT)
        if relative_path == LEGACY_UI_RETIREMENT_CONTRACT_TEST:
            continue
        if relative_path in LEGACY_UI_STATIC_TEST_FILES:
            item.add_marker(legacy_skip)
            continue
        try:
            source = inspect.getsource(item.obj)
        except (OSError, TypeError):
            source = ""
        if any(marker in source for marker in LEGACY_UI_STATIC_SOURCE_MARKERS):
            item.add_marker(legacy_skip)


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_historical_app_imports(request):
    state = vars(request.module).get("_app_import_state")
    if state is None:
        yield
    else:
        with state.activate():
            yield


@pytest.fixture(scope="session")
def base_url(request):
    """Resolved base URL."""
    return request.config.getoption("--base-url-omnix")




# ---------------------------------------------------------------------------
# Page-object fixtures (function-scoped – fresh per test)
# ---------------------------------------------------------------------------

@pytest.fixture
def base_page(page: Page) -> BasePage:
    return BasePage(page)


@pytest.fixture
def chat_page(page: Page) -> ChatPage:
    return ChatPage(page)


@pytest.fixture
def sidebar_page(page: Page) -> SidebarPage:
    return SidebarPage(page)


@pytest.fixture
def header_page(page: Page) -> HeaderPage:
    return HeaderPage(page)


@pytest.fixture
def settings_page(page: Page) -> SettingsPage:
    return SettingsPage(page)


@pytest.fixture
def podcast_page(page: Page) -> PodcastPage:
    return PodcastPage(page)


@pytest.fixture
def voice_studio_page(page: Page) -> VoiceStudioPage:
    return VoiceStudioPage(page)


@pytest.fixture
def voice_clone_page(page: Page) -> VoiceClonePage:
    return VoiceClonePage(page)


@pytest.fixture
def search_page(page: Page) -> SearchPage:
    return SearchPage(page)


# ---------------------------------------------------------------------------
# Playwright API request context fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api_context(playwright):
    """Playwright APIRequestContext for headless API testing."""
    ctx = playwright.request.new_context(base_url=BASE_URL)
    yield ctx
    ctx.dispose()


# ---------------------------------------------------------------------------
# Mock data fixtures (shared with old tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm_response():
    return {
        "choices": [
            {
                "message": {"content": "Hello! This is a test response from the AI."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


@pytest.fixture
def mock_tts_response():
    import base64

    wav_data = b"RIFF" + (44).to_bytes(4, "little") + b"WAVE"
    return {
        "success": True,
        "audio": base64.b64encode(wav_data).decode("utf-8"),
        "sample_rate": 24000,
    }


@pytest.fixture
def mock_stt_response():
    return {
        "success": True,
        "segments": [{"text": "Hello world", "start": 0.0, "end": 1.0}],
        "duration": 1.5,
    }


@pytest.fixture
def sample_session_data():
    return {
        "title": "Test Chat",
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ],
        "system_prompt": "You are a helpful assistant.",
    }


@pytest.fixture
def sample_audiobook_text():
    return """
    Narrator: The sun was setting over the hills.
    Sofia: What a beautiful evening!
    Morgan: Indeed, it reminds me of home.

    They walked together along the path, enjoying the peaceful moment.

    Sofia: I wish moments like this could last forever.
    """


# ---------------------------------------------------------------------------
# Automatic screenshot on failure
# ---------------------------------------------------------------------------

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Take a screenshot when a browser test fails."""
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed:
        page: Page | None = item.funcargs.get("page")
        if page is not None:
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_name = item.name.replace("[", "_").replace("]", "")
            screenshot_path = SCREENSHOTS_DIR / f"FAIL_{safe_name}_{ts}.png"
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
                if hasattr(report, "extra"):
                    report.extra = getattr(report, "extra", [])
                # Attach path as user property for the custom report
                item.user_properties.append(("screenshot", str(screenshot_path)))
            except Exception:
                pass  # browser may already be closed


# ---------------------------------------------------------------------------
# JS static analysis helpers (used by test_js_variables)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def static_dir():
    return SRC_DIR / "static"


@pytest.fixture(scope="session")
def js_files(static_dir):
    """Collect all JavaScript files in the static directory."""
    files = []
    for root, _, filenames in os.walk(static_dir):
        for f in filenames:
            if f.endswith(".js"):
                files.append(Path(root) / f)
    return files
