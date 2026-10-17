"""Text embeddings for Memory v2 retrieval: VoiceMem's multilingual-e5-small.

The model runs locally on CPU through ONNX Runtime (no PyTorch). It is not
bundled: download it once with

    python -m app.platform.assistant_memory.v2.embeddings download

which fetches a pinned revision from Hugging Face and checks each file's
SHA-256. Without the model, Memory v2 retrieves by words alone.
``OMNIX_MEMORY_EMBEDDING_MODEL_DIR`` moves it (default
``resources/models/multilingual-e5-small``); ``OMNIX_MEMORY_EMBEDDINGS=0``
turns embedding retrieval off.

E5 expects ``query: `` before a question and ``passage: `` before stored text;
vectors are mean-pooled over the attention mask and L2-normalized, so a dot
product is the cosine similarity.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Literal

from app.caching.bounded_cache import bounded_lru_cache
from app.config.env import environment
from app.runtime.paths import resources_models_root

logger = logging.getLogger(__name__)

MODEL_REPO = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_ID = f"{MODEL_REPO}@{MODEL_REVISION[:12]}"
DIMENSIONS = 384
MAX_TOKENS = 256
# Published paths in the repository, their local names and SHA-256.
MODEL_FILES = {
    "onnx/model.onnx": ("model.onnx", "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665"),
    "tokenizer.json": ("tokenizer.json", "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
}


def model_dir() -> Path:
    configured = environment().get("OMNIX_MEMORY_EMBEDDING_MODEL_DIR", "").strip()
    return Path(configured) if configured else resources_models_root() / "multilingual-e5-small"


def embeddings_enabled() -> bool:
    return environment().get("OMNIX_MEMORY_EMBEDDINGS", "1").strip().lower() not in {"0", "false", "no", "off"}


def model_installed(directory: Path | None = None) -> bool:
    root = directory or model_dir()
    return all((root / local).is_file() for local, _digest in MODEL_FILES.values())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(directory: Path | None = None) -> Path:
    """Fetch the pinned model files and verify them; a mismatch removes the file."""
    from huggingface_hub import hf_hub_download

    root = directory or model_dir()
    root.mkdir(parents=True, exist_ok=True)
    for remote, (local, expected) in MODEL_FILES.items():
        target = root / local
        if target.is_file() and _sha256(target) == expected:
            continue
        fetched = Path(hf_hub_download(MODEL_REPO, remote, revision=MODEL_REVISION))
        if _sha256(fetched) != expected:
            raise RuntimeError(f"memory_embedding_checksum_mismatch:{remote}")
        target.write_bytes(fetched.read_bytes())
    return root


class E5Embedder:
    """multilingual-e5-small on ONNX Runtime (CPU)."""

    def __init__(self, directory: Path) -> None:
        import onnxruntime
        from tokenizers import Tokenizer

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = onnxruntime.InferenceSession(
            str(directory / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding()

    def embed(self, texts: list[str], *, kind: Literal["query", "passage"]) -> Any:
        """L2-normalized float32 vectors, one row per text."""
        import numpy as np

        if not texts:
            return np.zeros((0, DIMENSIONS), dtype=np.float32)
        encoded = self.tokenizer.encode_batch([f"{kind}: {text}" for text in texts])
        ids = np.array([item.ids for item in encoded], dtype=np.int64)
        mask = np.array([item.attention_mask for item in encoded], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feeds)[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norms, 1e-9, None)).astype(np.float32)


@bounded_lru_cache(max_entries=1, ttl_seconds=86_400.0)
def _load_embedder(directory: str) -> E5Embedder:
    return E5Embedder(Path(directory))


def default_embedder() -> E5Embedder | None:
    """The process's embedder, or ``None`` when embeddings are off or the model is absent."""
    if not embeddings_enabled():
        return None
    directory = model_dir()
    if not model_installed(directory):
        return None
    try:
        return _load_embedder(str(directory))
    except ImportError:
        logger.warning("memory embeddings need onnxruntime and tokenizers; retrieving by words only")
        return None


def _main(argv: list[str]) -> int:
    command = argv[0] if argv else "status"
    if command == "download":
        root = download_model()
        sys.stdout.write(json.dumps({"ok": True, "model": MODEL_ID, "directory": str(root)}) + "\n")
        return 0
    if command == "status":
        sys.stdout.write(json.dumps({"model": MODEL_ID, "directory": str(model_dir()), "installed": model_installed(),
                                     "enabled": embeddings_enabled()}) + "\n")
        return 0
    sys.stderr.write("usage: python -m app.platform.assistant_memory.v2.embeddings [download|status]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "DIMENSIONS", "E5Embedder", "MODEL_ID", "default_embedder", "download_model", "embeddings_enabled",
    "model_dir", "model_installed",
]
