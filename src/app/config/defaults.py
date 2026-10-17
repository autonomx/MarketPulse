"""Canonical browser/provider defaults formerly held in app.shared."""
from __future__ import annotations

from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "lmstudio",
    "audio_provider_tts": "faster-qwen3-tts",
    "audio_provider_stt": "parakeet",
    "global_system_prompt": """You are Maya, a warm, friendly, emotionally aware AI. Keep responses short (1-3 sentences for voice, 5 for text), match the user's emotional tone, avoid filler and tangents. Be clear and concise, admit uncertainty when needed, and maintain a natural, human-like presence.""",
    "lmstudio": {"base_url": "http://localhost:1234", "direct": False},
    "openrouter": {"api_key": "", "model": "openai/gpt-4o-mini", "context_size": 128000, "thinking_budget": 0},
    "cerebras": {"api_key": "", "model": "llama-3.3-70b-versatile"},
    "llamacpp": {"base_url": "http://localhost:8180", "model": "", "download_location": "server", "auto_start": False},
    "faster-qwen3-tts": {
        "model_name": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "model_dir": "",
        "device": "cuda",
        "dtype": "bfloat16",
        "max_seq_len": 2048,
        "chunk_size": 12,
        "temperature": 0.9,
        "top_k": 50,
        "top_p": 1.0,
        "do_sample": True,
        "repetition_penalty": 1.05,
        "xvec_only": True,
        "non_streaming_mode": True,
        "append_silence": True
    },
    "fish-s2-pro": {
        "port": 5102,
        "checkpoint_dir": "",
        "code_dir": "",
        "python": "",
        "half": False,
        "compile": False,
        "max_seq_len": 12288,
        "startup_timeout_s": 600,
        "request_timeout_s": 600,
        "temperature": 0.7,
        "top_p": 0.7,
        "repetition_penalty": 1.2,
        "max_new_tokens": 0,
        "chunk_length": 200,
        "normalize": True
    },
    "parakeet": {"base_url": "http://127.0.0.1:5201"},
    "image": {
        "enabled": False,
        "provider": "flux_klein",
        "auto_unload_on_disable": True,
        "chat": {
            "auto_generate_images": False,
            "style": "",
        },
        "story": {
            "auto_generate_scene_images": False,
            "auto_generate_cover_images": False,
            "style": "story",
        },
        "mock": {
            "enabled": True,
        },
        "flux_klein": {
            "enabled": False,
            "repo_id": "black-forest-labs/FLUX.2-klein-4B",
            "variant": "distilled",
            "base_repo_id": "black-forest-labs/FLUX.2-klein-base-4B",
            "download_dir": "image",
            "local_dir": "",
            "device": "cuda",
            "torch_dtype": "bfloat16",
            "enable_cpu_offload": False,
            "prefer_local_files": True,
            "allow_repo_fallback": False,
            "num_inference_steps": 3,
            "guidance_scale": 1.0,
            "cuda_empty_cache_after_generate": False,
            "width": 768,
            "height": 768,
            "portrait_width": 512,
            "portrait_height": 768,
            "scene_width": 768,
            "scene_height": 512,
        },
    },
    "rpg_visual": {
        "enabled": False,
        "provider": "mock",
        "auto_unload_on_disable": True,
        "flux_klein": {
            "enabled": False,
            "repo_id": "black-forest-labs/FLUX.2-klein-4B",
            "variant": "distilled",  # distilled | base
            "base_repo_id": "black-forest-labs/FLUX.2-klein-base-4B",
            "download_dir": "image",
            "local_dir": "",
            "device": "cuda",
            "torch_dtype": "bfloat16",
            "enable_cpu_offload": False,
            "prefer_local_files": True,
            "allow_repo_fallback": False,
            "num_inference_steps": 3,
            "guidance_scale": 1.0,
            "portrait_width": 512,
            "portrait_height": 768,
            "scene_width": 768,
            "scene_height": 512,
            "item_width": 1024,
            "item_height": 1024
        }
    },
}


DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant."
