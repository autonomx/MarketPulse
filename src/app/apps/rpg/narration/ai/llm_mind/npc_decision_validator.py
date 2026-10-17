_ALLOWED_INTENTS = {
    "observe", "support", "confront", "avoid",
    "investigate", "negotiate", "stabilize",
    "retaliate", "wait", "move",
}

_ALLOWED_ACTIONS = _ALLOWED_INTENTS


class NPCDecisionValidator:
    @staticmethod
    def validate(data: dict) -> dict:
        data = dict(data or {})

        intent = str(data.get("intent") or "wait")
        action_type = str(data.get("action_type") or "wait")

        if intent not in _ALLOWED_INTENTS:
            intent = "wait"
        if action_type not in _ALLOWED_ACTIONS:
            action_type = "wait"

        # critical fix: ensure intent and action_type are clamped to allowed values
        data["intent"] = intent
        data["action_type"] = action_type

        data["npc_id"] = str(data.get("npc_id") or "")
        data["tick"] = int(data.get("tick") or 0)

        return data