"""Shared lock for atomic transcript mutations across conversation features."""
from threading import RLock

CHAT_MUTATION_LOCK = RLock()
