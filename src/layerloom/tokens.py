# colorflow/tokens.py

from __future__ import annotations
from typing import List
import re

# Allow CMY + K (black) + W (white)
_ALLOWED = {"c", "m", "y", "k", "w"}
_SEP_PATTERN = re.compile(r"[ ,._-]+")

def split_tokens(name: str | None, *, strict: bool = False) -> List[str]:
    """
    Extract a sequence of tokens from a part name.

    - Allowed tokens: c, m, y, k, w (case-insensitive).
    - Separators (space, comma, dot, underscore, dash) are ignored.
    - If strict=False (default), non-allowed letters are ignored.
      If strict=True, any non-allowed alphabetic character raises ValueError.

    Examples:
      'cmc'       -> ['c','m','c']
      'C-M-Y'     -> ['c','m','y']
      'C+K'       -> ['c','k']      (lenient: '+' ignored)
      'W_cmy'     -> ['w','c','m','y']
      'part_001'  -> []            (lenient; no error)
      'c1m'       -> [] (lenient) / ValueError (strict)
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

