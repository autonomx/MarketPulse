import pytest

from app.apps.trading.binding_authority import (
    PurposeBoundBinding,
    binding_can_execute,
    infer_binding_purpose,
    require_execution_binding,
)


def test_replay_binding_is_never_execution_authority():
    binding = "replay:TLYS:fe29d4dcdb"
    assert infer_binding_purpose(binding) == "REPLAY"
    assert binding_can_execute(binding) is False
    with pytest.raises(ValueError, match="execution_binding_purpose_invalid:REPLAY"):
        require_execution_binding(binding)


def test_legacy_live_execution_binding_remains_compatible():
    binding = "equity:TLYS:fe29d4dcdb"
    assert infer_binding_purpose(binding) == "EXECUTION"
    assert require_execution_binding(binding) == binding


def test_purpose_bound_binding_rejects_prefix_conflict():
    with pytest.raises(ValueError, match="binding_purpose_conflicts"):
        PurposeBoundBinding(binding_id="research:TEST:1", purpose="EXECUTION")
