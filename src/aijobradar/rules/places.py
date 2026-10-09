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


class Gazetteer:
    """Place aliases -> canonical codes, plus one regex group that finds any alias in text."""

    def __init__(self, places: Mapping[str, Sequence[str]], case_sensitive: Iterable[str]) -> None:
        sensitive = set(case_sensitive)
        self._lookup: dict[str, str] = {}
        all_aliases: set[str] = set()
        for code, aliases in places.items():
            for alias in aliases:
                key = normalize_place(alias)
                existing = self._lookup.get(key)
                if existing is not None and existing != code:
                    raise ValueError(f"alias {alias!r} maps to both {existing} and {code}")
                self._lookup[key] = code
                all_aliases.add(alias)
        self.codes = frozenset(places)
        # One alternation over every alias, longest first, across both kinds. That single order
        # is what makes "US/Canada" match the NORTH_AMERICA alias before the bare "US".
        # Short codes such as "US" are also ordinary words ("contact us only"), so each
        # case-sensitive alias is wrapped on its own in (?-i:...) inside the
        # case-insensitive pattern.
        ordered = sorted(all_aliases, key=lambda a: (-len(a), a))
        parts = [f"(?-i:{re.escape(a)})" if a in sensitive else re.escape(a) for a in ordered]
        self.place_group = f"(?P<place>{'|'.join(parts)})"

    def canonical(self, token: str) -> str | None:
        return self._lookup.get(normalize_place(token))
