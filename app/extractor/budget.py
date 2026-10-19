import re

MONEY = re.compile(
    r"(?P<currency>[$£€])\s?(?P<amount>\d[\d,]*(?:\.\d+)?)"
    r"|(?P<amount2>\d[\d,]*(?:\.\d+)?)\s?(?P<currency2>USD|EUR|GBP)",
    re.I
)

RANGE = re.compile(
    r"(?P<c1>[$£€])?\s?(?P<a1>\d[\d,]*)\s*(?:-|–|to)\s*(?P<c2>[$£€])?\s?(?P<a2>\d[\d,]*)",
    re.I
)

def extract_budget(text: str) -> str | None:
    m = RANGE.search(text)
    if m:
        c = m.group("c1") or m.group("c2") or ""
        return f"{c}{m.group('a1')}–{c}{m.group('a2')}"

    m = MONEY.search(text)
    if not m:
        return None

    currency = m.group("currency") or m.group("currency2") or ""
    amount = m.group("amount") or m.group("amount2")
    return f"{currency}{amount}"
