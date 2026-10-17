"""Pinned model downloads for the model service images (WP-11.1).

``catalog.json`` lists each model the TTS, STT and image services load by
default: its Hugging Face repository, a pinned commit and the SHA-256 of every
file the service reads. ``python -m app.models download <id>`` fetches those
files into the Hugging Face cache (a mounted volume in containers), checks each
digest and points the cache's ``main`` ref at the pinned commit, so a service
started with ``HF_HUB_OFFLINE=1`` loads exactly the verified files.
"""
