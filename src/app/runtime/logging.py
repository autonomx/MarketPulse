"""Import-safe structured fields for runtime lifecycle and ownership transitions."""
from __future__ import annotations

import time
import json


def runtime_transition(logger, *, component, role, transition, owner_id=None,
                       started_at=None, error=None, level='info'):
    # Deliberately omit exception messages/tracebacks and connection metadata.
    fields = {
        'component': component, 'process_role': str(role), 'owner_id': owner_id,
        'transition': transition,
        'duration_ms': (time.monotonic() - started_at) * 1000 if started_at is not None else 0,
        'error_class': type(error).__name__ if error is not None else None,
    }
    getattr(logger, level)(json.dumps(fields, sort_keys=True), extra=fields)
