"""Shared LayerLoom weave-token definitions."""

from __future__ import annotations
from typing import Iterable, List
import re

# Core token order.  ``n`` is neutral gray so ``g`` can remain green.
BASE_TOKENS = ("c", "m", "y")
CMYKW_TOKENS = ("c", "m", "y", "k", "w")
EXPANDED_TOKENS = ("n", "o", "v", "g")
ALL_TOKENS = CMYKW_TOKENS + EXPANDED_TOKENS
TOKEN_ALPHABET = "".join(ALL_TOKENS)
CMYKW_ALPHABET = "".join(CMYKW_TOKENS)

TOKEN_DISPLAY_NAMES = {
    "c": "cyan",
    "m": "magenta",
    "y": "yellow",
    "k": "black",
    "w": "white",
    "n": "gray",
    "o": "orange",
    "v": "violet",
    "g": "green",
}

TOKEN_HEX = {
    "c": "#3ac8dc",
    "m": "#c31996",
    "y": "#ffdf00",
    "k": "#141414",
    "w": "#f5f5f5",
    "n": "#8a8f92",
    "o": "#ff7a1a",
    "v": "#7257ff",
    "g": "#20bf63",
}

COLOR_OBJECT_LABELS = {
    token: f"all_{name}" for token, name in TOKEN_DISPLAY_NAMES.items()
}

PAT_TAG_RE = re.compile(rf"__PAT_([{TOKEN_ALPHABET}]+)__", re.IGNORECASE)

_ALLOWED = set(ALL_TOKENS)
_SEP_PATTERN = re.compile(r"[ ,._-]+")


def valid_token_set(alphabet: str | Iterable[str] | None = None) -> set[str]:
    if alphabet is None:
        return set(_ALLOWED)
    if isinstance(alphabet, str):
        return {ch.lower() for ch in alphabet}
    return {str(ch).lower() for ch in alphabet}


def token_is_valid(token: str | None, alphabet: str | Iterable[str] | None = None) -> bool:
    letters = set(str(token or "").lower())
    return bool(letters) and letters.issubset(valid_token_set(alphabet))


def sanitize_token_run(
    token: str | None,
    *,
    alphabet: str | Iterable[str] | None = None,
    default: str = "cmy",
) -> str:
    allowed = valid_token_set(alphabet)
    cleaned = "".join(ch for ch in str(token or "").lower() if ch in allowed)
    return cleaned or default

def split_tokens(name: str | None, *, strict: bool = False) -> List[str]:
    """
    Extract a sequence of tokens from a part name.

    - Allowed tokens: c, m, y, k, w, n, o, v, g (case-insensitive).
    - Separators (space, comma, dot, underscore, dash) are ignored.
    - If strict=False (default), non-allowed letters are ignored.
      If strict=True, any non-allowed alphabetic character raises ValueError.

    Examples:
      'cmc'       -> ['c','m','c']
      'C-M-Y'     -> ['c','m','y']
      'C+K'       -> ['c','k']      (lenient: '+' ignored)
      'W_cmy'     -> ['w','c','m','y']
      'O-V-G'     -> ['o','v','g']
      'part_001'  -> []            (lenient; no error)
      'c1m'       -> ['c','m'] (lenient) / ValueError (strict)
    """
    if name is None:
        return []

    s = name.strip().lower()
    if not s:
        return []

    pieces = [p for p in _SEP_PATTERN.split(s) if p != ""] if _SEP_PATTERN.search(s) else [s]

    out: List[str] = []
    for piece in pieces:
        for ch in piece:
            if ch.isalpha():
                if ch in _ALLOWED:
                    out.append(ch)
                else:
                    if strict:
                        raise ValueError(f"unknown token '{ch}' in name '{name}'")
                    # lenient: ignore
            else:
                if strict:
                    raise ValueError(f"invalid character '{ch}' in name '{name}'")
                # lenient: ignore
    return out
