from __future__ import annotations

import os
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

WATERMARKS_REMOVER_VERSION = "v0.7.0"
WATERMARKS_REMOVER_COMMIT = "321d93d2efd6a8b26915c5eb5193d9d1701e2c4b"
APOSTROPHES = {"'", "’"}


def _load_upstream_cleaner() -> Callable[..., tuple[str, dict[str, Any]]]:
    default_scripts = (
        Path(__file__).resolve().parents[1]
        / ".vendor"
        / "watermarks-remover"
        / "service"
        / "scripts"
    )
    scripts = Path(os.environ.get("WATERMARKS_REMOVER_SCRIPTS", default_scripts))
    if not (scripts / "text_unicode.py").is_file():
        raise RuntimeError(
            "watermarks-remover source is unavailable; run pnpm server:setup"
        )
    sys.path.insert(0, str(scripts))
    try:
        from text_unicode import clean_text  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(scripts))
    return cast("Callable[..., tuple[str, dict[str, Any]]]", clean_text)


_upstream_clean_text = _load_upstream_cleaner()


def _is_word_character(character: str) -> bool:
    return unicodedata.category(character)[0] in {"L", "M", "N"}


def count_words(text: str) -> int:
    words = 0
    in_word = False
    for index, character in enumerate(text):
        if _is_word_character(character):
            if not in_word:
                words += 1
            in_word = True
            continue
        if (
            character in APOSTROPHES
            and in_word
            and index + 1 < len(text)
            and _is_word_character(text[index + 1])
        ):
            continue
        in_word = False
    return words


@dataclass(frozen=True, slots=True)
class DeterministicResult:
    text: str
    removed: int
    normalized_spaces: int


def clean_deterministically(text: str) -> DeterministicResult:
    cleaned, stats = _upstream_clean_text(
        text,
        nfkc=False,
        aggressive_homoglyphs=False,
        normalize_spaces=True,
        strip_emoji_glue=False,
        strip_bidi=False,
    )
    return DeterministicResult(
        text=cleaned,
        removed=int(stats["removed_count"]),
        normalized_spaces=int(stats["replaced_count"]),
    )
