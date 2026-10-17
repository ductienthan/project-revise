import re

_STOPWORDS = frozenset(
    "the and for with that this from into when then than have has had was were are not but all any can "
    "its our out who why how what which while after before about over under each every only also just".split()
)


def keywords(text: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOPWORDS)


def overlap(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))
