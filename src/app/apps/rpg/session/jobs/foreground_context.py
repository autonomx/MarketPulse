"""Context-local submission identity shared by the explicit RPG turn pipeline."""
from contextvars import ContextVar


DIRECT_RPG_SUBMISSION_ID: ContextVar[str] = ContextVar(
    "omnix_direct_rpg_submission_id", default=""
)
