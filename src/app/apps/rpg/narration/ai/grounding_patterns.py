from __future__ import annotations

_MONEY_AMOUNT_PATTERN = (
    r"(?:"
    r"\d+(?:,\d{3})*(?:\.\d+)?"
    r"|"
    r"a|an|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|half|quarter|couple|few|several"
    r"(?:[-\s]+(?:and\s+)?(?:a\s+)?(?:half|quarter))?"
    r")"
)

_CURRENCY_UNIT_PATTERN = (
    r"(?:"
    r"gold|silver|copper|coin|coins|gp|sp|cp|"
    r"gold\s+piece|gold\s+pieces|"
    r"silver\s+piece|silver\s+pieces|"
    r"copper\s+piece|copper\s+pieces"
    r")"
)

_MONEY_PHRASE_PATTERN = rf"{_MONEY_AMOUNT_PATTERN}\s*{_CURRENCY_UNIT_PATTERN}"

_PRICE_CONTEXT_PATTERN = (
    r"(?:"
    r"costs?|price|priced|rate|fee|charge|fare|toll|rent|"
    r"listed\s+at|goes\s+for|runs?|comes\s+to|"
    r"that(?:'ll| will)\s+be|that\s+is|that's|"
    r"it(?:'ll| will)\s+be|it\s+is|it's|"
    r"you(?:'ll| will)\s+need\s+to\s+pay|"
    r"pay|"
    r"for"
    r")"
)

_SERVICE_CONTEXT_PATTERN = (
    r"(?:"
    r"cot|bed|room|chamber|lodging|stay|night|meal|drink|ale|wine|"
    r"service|stabling|stable|horse|supplies|ration|rations|repair|"
    r"weapon|armor|armour|item|goods|wares|passage|ride|cart|boat|ferry"
    r")"
)

_PRICE_QUOTE_PATTERNS = [
    rf"\b{_PRICE_CONTEXT_PATTERN}\s+(?:is\s+|at\s+|of\s+|about\s+|around\s+|just\s+)?{_MONEY_PHRASE_PATTERN}\b",
    rf"\b{_MONEY_PHRASE_PATTERN}\s+(?:per|each|for|a|an|the)\s+\w+\b",
    rf"\b(?:the\s+)?{_SERVICE_CONTEXT_PATTERN}\s+(?:is|are|costs?|runs?|goes\s+for|comes\s+to)\s+{_MONEY_PHRASE_PATTERN}\b",
    rf"\bfor\s+(?:just\s+|only\s+|about\s+|around\s+)?{_MONEY_PHRASE_PATTERN}\b",
    rf"\b{_MONEY_PHRASE_PATTERN}\s+{_SERVICE_CONTEXT_PATTERN}\b",
]

_REWARD_PATTERNS = [
    rf"\b{_MONEY_PHRASE_PATTERN}\b",
    r"\bgain(?:ed|s)?\b.*\b(gold|silver|copper|coin|coins|xp|experience)\b",
    r"\breceiv(?:e|ed|es)\b.*\b(gold|silver|copper|coin|coins|xp|experience)\b",
    r"\breward(?:ed)?\b",
    r"\badds?\b.*\bto your inventory\b",
    r"\bhand(?:s|ed)?\b.*\b(gold|silver|copper|coin|coins)\b",
]

_COMBAT_PATTERNS = [
    r"\bkill(?:ed|s)?\b",
    r"\bslay(?:s|ing|ed)?\b",
    r"\bdie(?:s|d)?\b",
    r"\bdead\b",
    r"\bdeath\b",
    r"\bwound(?:ed|s)?\b",
    r"\binjur(?:y|ed|es)\b",
    r"\bdamage(?:d|s)?\b",
    r"\bbleed(?:s|ing)?\b",
    r"\bblood\b",
    r"\bbloodshed\b",
    r"\battack(?:ed|s)?\b",
    r"\bcombat\b",
]

_OBJECTIVE_COMPLETION_PATTERNS = [
    r"\bcomplete(?:d|s)?\b.*\bobjective\b",
    r"\bquest\b.*\bcomplete(?:d|s)?\b",
    r"\bmission\b.*\bcomplete(?:d|s)?\b",
    r"\bresolved\b.*\bquest\b",
]

_LOCATION_MOVE_PATTERNS = [
    r"\byou arrive at\b",
    r"\byou reach\b",
    r"\byou travel to\b",
    r"\byou enter\b",
    r"\byou leave for\b",
]

_UNSUPPORTED_DEBT_CLAIM_PATTERNS = [
    r"\byou\s+owe\s+me\b",
    r"\byou\s+owed\s+me\b",
    r"\bowe\s+me\s+(?:money|coin|coins|gold|silver|copper|payment|debt)\b",
    r"\bpay\s+me\b",
    r"\bpay\s+what\s+you\s+owe\b",
    r"\bsettle\s+(?:your\s+)?debt\b",
    r"\bdebt\b.*\b(?:pay|owed|owe|coin|gold|silver|copper)\b",
]

_CLEAR_DEBT_REFUSAL_PATTERNS = [
    r"\bdo\s+not\s+owe\b",
    r"\bdon't\s+owe\b",
    r"\bdoes\s+not\s+owe\b",
    r"\bnot\s+owe\b",
    r"\bno\s+debt\b",
    r"\bno\s+coin\s+changes\s+hands\b",
    r"\bdoes\s+not\s+hand\s+over\s+any\s+coin\b",
    r"\bno\s+payment\b",
    r"\bwon't\s+pay\b",
    r"\bwill\s+not\s+pay\b",
    r"\brefuse(?:s|d)?\b",
    r"\bunsupported\s+claim\b",
    r"\bunsupported\s+debt\s+claim\b",
]

_AMBIGUOUS_DEBT_RESPONSE_PATTERNS = [
    r"\bfifty\s+gold\?\b",
    r"\b\d+\s*(?:gold|silver|copper|coin|coins|gp|sp|cp)\?\b",
    r"\byou'?re\s+sure\s+about\s+that\b",
    r"\bare\s+you\s+sure\b",
    r"\bpay\s+me\s+now\?\b",
    r"\blet\s+me\s+think\s+about\s+what\s+i\s+owe\b",
    r"\bwhat\s+i\s+owe\s+you\b",
    r"\bconsider(?:ing)?\s+(?:the\s+)?debt\b",
    r"\bunder\s+discussion\b",
    r"\bwe'?ll\s+see\b",
    r"\bmaybe\b",
    r"\bperhaps\b",
]

_DEBT_CONFIRMATION_PATTERNS = [
    r"\backnowledges?\s+(?:the\s+)?debt\b",
    r"\bconfirms?\s+(?:the\s+)?debt\b",
    r"\badmits?\s+(?:the\s+)?debt\b",
    r"\baccepts?\s+(?:the\s+)?debt\b",
    r"\bagrees?\s+(?:that\s+)?(?:he|she|they|i)\s+owe",
    r"\byou(?:'re| are)\s+right\b.*\bowe\b",
    r"\bi\s+owe\s+you\b",
    r"\boutstanding\s+amount\b",
    r"\boutstanding\s+debt\b",
    r"\bvalid\s+debt\b",
    r"\breal\s+debt\b",
    r"\bpayment\s+is\s+due\b",
    r"\bpayment\s+to\s+the\s+player\b",
    r"\bputs?\s+.+\s+on\s+notice\s+regarding\s+(?:the\s+)?outstanding\s+amount\b",
]

_NEGATION_MARKERS = (
    " no ",
    " not ",
    " without ",
    " does not ",
    " do not ",
    " did not ",
    " never ",
    " cannot ",
    " can't ",
)
