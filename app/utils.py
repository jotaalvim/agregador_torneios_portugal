import unicodedata
from urllib.parse import urlparse

_DEACCENT_TABLE = str.maketrans(
    "àáâãäçèéêëìíîïñòóôõöùúûü",
    "aaaaaceeeeiiiinooooouuuu",
)


def deaccent(value: str) -> str:
    """Lowercase and strip accents, for accent-insensitive comparisons (e.g.
    search). NFC-normalizes first so precomposed ("á") and decomposed
    ("a" + combining acute) forms of the same character both translate."""
    value = unicodedata.normalize("NFC", value).strip().lower()
    return value.translate(_DEACCENT_TABLE)


def slugify(value: str) -> str:
    value = deaccent(value)
    return "".join(c if c.isalnum() else "-" for c in value).strip("-")


def is_safe_url(value: str | None) -> bool:
    """Only allow http(s) links. Blocks javascript:/data: URIs submitted
    through public forms and later rendered as href attributes."""
    if not value:
        return True
    return urlparse(value).scheme in ("http", "https")


def ics_fold(line: str) -> str:
    """A calendar line folded at 75 octets, as RFC 5545 asks: the rest goes on
    continuation lines that start with a space. A character is never split,
    since a multi-byte one cut in half would garble the text."""
    chunks, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > (74 if chunks else 75):  # a continuation's space counts
            chunks.append(current)
            current, size = "", 0
        current += char
        size += width
    chunks.append(current)
    return "\r\n ".join(chunks)


def ics_escape(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )
