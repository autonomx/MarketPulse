"""
Shared test helpers and constants for Omnix Playwright tests.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SRC_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = SRC_DIR.parent
STATIC_DIR = SRC_DIR / "static"
TEMPLATES_DIR = SRC_DIR / "templates"
RESOURCES_DIR = PROJECT_ROOT / "resources"

BASE_URL = os.environ.get("OMNIX_BASE_URL", "http://localhost:5000")

# ---------------------------------------------------------------------------
# JS analysis helpers (migrated from test_js_variables.py)
# ---------------------------------------------------------------------------

def get_js_files() -> list[Path]:
    """Return all ``.js`` files under ``src/static/``."""
    js_files: list[Path] = []
    for root, _, files in os.walk(STATIC_DIR):
        for f in files:
            if f.endswith(".js"):
                js_files.append(Path(root) / f)
    return js_files


def extract_global_vars(js_path: Path) -> dict[str, int]:
    """Extract top-level ``let/const/var`` declarations.

    Returns ``{var_name: line_number}``.
    """
    content = js_path.read_text(encoding="utf-8")
    global_vars: dict[str, int] = {}
    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        m = re.match(r"^(let|const|var)\s+(\w+)\s*=", stripped)
        if m:
            global_vars[m.group(2)] = i
    return global_vars


# Allow-list of common local variable names that are expected to appear
# across multiple files and do *not* represent true conflicts.
COMMON_LOCAL_VARS: set[str] = {
    "data", "response", "result", "text", "file", "url", "name", "buffer",
    "audio", "stream", "msg", "content", "reader", "div", "option", "select",
    "a", "span", "button", "input", "form", "label", "container", "header",
    "event", "events", "len", "offset", "output", "t", "html", "source",
    "duration", "currentTime", "startTime", "percent", "profile", "profiles",
    "decoder", "lines", "dataStr", "mins", "secs", "blob", "wavBuffer",
    "pcmBuffer", "combinedPcm", "view", "float32", "int16", "binaryString",
    "arrayBuffer", "uint8Array", "pcmView", "numChannels", "bitsPerSample",
    "bytesPerSample", "blockAlign", "byteRate", "dataSize", "bufferSize",
    "statusResponse", "statusData", "rect", "totalDuration", "totalLength",
    "apiKey", "model", "settings", "sampleRate", "pcmArrays", "totalTime",
    "fadeIn", "fadeOut", "fadeLength", "crossFadeLength", "numSamples",
    "pcm16", "infoEl", "statusEl", "pauseBtn", "resumeBtn", "playBtn",
    "personality", "nameInput", "voiceProfilesKey", "saved", "voiceCloneModal",
    "ttsSpeaker", "audioUrl", "formData", "streamingAudioElement", "systemPrompt",
    "audioBlob", "messageDiv", "contentDiv", "headerDiv", "message", "startTime",
    "podcastBtn", "date", "streamedContent", "voiceProfile", "sseBuffer",
    "selectedSpeaker", "thinkingContainer", "thinkingHeader", "thinkingContent",
    "headerHTML", "ttsSpeakerSelect", "messages", "isPlaying", "binary", "bytes",
    "audioBuffer", "streamingAudioContext", "totalStartTime", "conversationMessages",
    "avatarDiv", "contentEl", "msgDiv", "avDiv", "contDiv", "convMessages",
    "totalLatency", "llmLatency", "tokenSpeed", "tokensGenerated", "ttft",
    "tpft", "ttfa", "ttsGen",
}
