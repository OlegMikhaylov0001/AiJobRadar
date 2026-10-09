import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence

_FLAG = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")  # regional-indicator pair = one flag
_ONLY = re.compile(r"\bonly\b")
_PUNCT = re.compile(r"[^\w\s&/.-]")


def normalize_place(token: str) -> str:
    s = unicodedata.normalize("NFKC", _FLAG.sub("", token)).casefold()
    s = _ONLY.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split()).strip(" .-")


def _alternation(aliases: Iterable[str]) -> str:
    # Longest first, so "united states of america" wins over "united states".
    return "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))


class Gazetteer:
    """Place aliases -> canonical codes, plus one regex group that finds any alias in text."""

    def __init__(self, places: Mapping[str, Sequence[str]], case_sensitive: Iterable[str]) -> None:
        sensitive = set(case_sensitive)
        self._lookup: dict[str, str] = {}
        cs: list[str] = []
        ci: list[str] = []
        for code, aliases in places.items():
            for alias in aliases:
                key = normalize_place(alias)
                existing = self._lookup.get(key)
                if existing is not None and existing != code:
                    raise ValueError(f"alias {alias!r} maps to both {existing} and {code}")
                self._lookup[key] = code
                (cs if alias in sensitive else ci).append(alias)
        self.codes = frozenset(places)
        # Short codes such as "US" are also ordinary words ("contact us only"): match them
        # case-sensitively inside an otherwise case-insensitive pattern.
        parts = []
        if cs:
            parts.append(f"(?-i:{_alternation(cs)})")
        if ci:
            parts.append(_alternation(ci))
        self.place_group = f"(?P<place>{'|'.join(parts)})"

    def canonical(self, token: str) -> str | None:
        return self._lookup.get(normalize_place(token))
