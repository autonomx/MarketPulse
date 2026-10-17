"""Generation settings a remote TTS caller may set, shared by the gateway facade and the TTS service."""
from __future__ import annotations

from typing import Any, Mapping

# The sampling and conditioning settings the local providers read (Qwen, then
# Fish S2 Pro, which also reads the audiobook delivery ``instruct``). Anything
# else (private hints) stays on the caller.
TTS_GENERATION_PARAMETER_KEYS = frozenset({
    "ref_text", "max_new_tokens", "min_new_tokens", "temperature", "top_k", "top_p",
    "do_sample", "repetition_penalty", "xvec_only", "non_streaming_mode",
    "append_silence", "parity_mode", "use_cuda_graphs",
    "chunk_length", "normalize", "seed", "instruct",
})


def generation_overrides(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    """The JSON-scalar generation settings in ``parameters``; other keys are dropped."""
    return {
        key: value for key, value in dict(parameters or {}).items()
        if key in TTS_GENERATION_PARAMETER_KEYS
        and (value is None or isinstance(value, (str, int, float, bool)))
    }
