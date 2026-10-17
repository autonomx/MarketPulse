"""Phase 12.6 — Character Card Compatibility Layer package."""
from .character_cards import (
    export_canonical_character_card,
    import_external_character_card,
)

__all__ = [
    "export_canonical_character_card",
    "import_external_character_card",
]