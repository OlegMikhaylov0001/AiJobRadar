import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

_SPACES = re.compile(r"[ \t ]+")  # space, tab, non-breaking space
_LEGAL_SUFFIXES = frozenset(
    {
        "inc",
        "llc",
        "ltd",
        "limited",
        "gmbh",
        "corp",
        "corporation",
        "co",
        "plc",
        "ag",
        "sa",
        "bv",
        "oy",
        "ab",
        "srl",
        "sro",
        "ооо",
        "ао",
        "оао",
        "зао",
    }
)
_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_TITLE_NOISE = re.compile(r"\b(?:remote|wfh)\b")
# Same compound word written apart/hyphenated must compare equal ("full stack" == "fullstack").
_COMPOUNDS = re.compile(r"\b(full|back|front)[\s-]+(stack|end)\b")
_DROP_PARAMS = frozenset({"ref", "source", "src", "fbclid", "gclid", "u"})


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [_SPACES.sub(" ", line).strip() for line in soup.get_text("\n").splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):  # collapse runs of blank lines into one
            out.append(line)
    return "\n".join(out).strip()


def normalize_company(name: str) -> str:
    folded = re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", name).casefold())
    tokens = folded.split()
    kept = [t for t in tokens if t not in _LEGAL_SUFFIXES]
    return " ".join(kept or tokens)


def normalize_title(title: str) -> str:
    s = unicodedata.normalize("NFKC", title).casefold()
    s = _BRACKETED.sub(" ", s)
    s = _COMPOUNDS.sub(lambda m: m.group(1) + m.group(2), s)
    s = _TITLE_NOISE.sub(" ", s)
    s = re.sub(r"[^\w\s+#]", " ", s)  # keep c++ / c#
    return " ".join(s.split())


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _DROP_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    scheme = (parts.scheme or "https").lower()
    return urlunsplit((scheme, parts.netloc.lower(), path, urlencode(query), ""))


def content_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
