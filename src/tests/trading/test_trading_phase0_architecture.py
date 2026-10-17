from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ADR = ROOT / "docs/architecture/ADR-0004-omnix-trading-terminal.md"
INVENTORY = ROOT / "docs/architecture/OMNIX_TRADING_PROTOTYPE_MIGRATION_INVENTORY.md"
BENCHMARK = ROOT / "docs/architecture/OMNIX_TRADING_SPIKE_BENCHMARK.md"


def test_phase0_decisions_and_evidence_contract_exist() -> None:
    for path in (ADR, INVENTORY, BENCHMARK):
        assert path.is_file(), path
    adr = ADR.read_text(encoding="utf-8")
    assert "go/no-go" in adr
    assert "PostgreSQL is authoritative" in adr
    assert "provider/feed identity" in adr
    assert "no MCP runtime dependency" in adr
    benchmark = BENCHMARK.read_text(encoding="utf-8")
    assert "four charts" in benchmark
    assert "5,000" in benchmark
    assert "exact recovery" in benchmark
    assert "pending local benchmark execution" in benchmark


def test_experimental_spike_is_not_production_routed() -> None:
    # The spike was deleted as unreachable (WP-9.10); trading routes only its workspace,
    # through its module manifest (WP-9.7).
    manifest = (ROOT / "web/src/features/trading/module.ts").read_text(encoding="utf-8")
    registry = (ROOT / "web/src/app/modulesManifest.ts").read_text(encoding="utf-8")
    router = (ROOT / "web/src/app/router.tsx").read_text(encoding="utf-8")
    workspace = (ROOT / "web/src/features/ModuleWorkspace.tsx").read_text(encoding="utf-8")
    assert "id: 'trading'" in manifest
    assert "module.TradingWorkspace" in manifest
    assert "tradingModule" in registry
    for source in (manifest, registry, router, workspace):
        assert "TradingChartSpike" not in source


def test_trading_sources_do_not_import_prototype_or_create_file_authority() -> None:
    roots = (ROOT / "web/src/features/trading", ROOT / "src/app/apps/trading")
    for trading_root in roots:
        for path in trading_root.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "tradingview_mcp" not in text
            assert "resources/data/trading" not in text


def test_spike_exit_policy_is_explicit() -> None:
    adr = ADR.read_text(encoding="utf-8")
    for requirement in (
        "retained only after",
        "deleted or remains under `features/trading/experimental`",
        "failed spike blocks",
        "cannot become production contracts",
    ):
        assert requirement in adr
