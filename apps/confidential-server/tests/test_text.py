import json
from pathlib import Path

from simpleunmark_confidential.text import (
    clean_deterministically,
    count_words,
)

SHARED_FIXTURES = json.loads(
    (Path(__file__).resolve().parents[3] / "fixtures/deterministic-cleaning.json").read_text()
)


def test_matches_the_shared_browser_python_fixtures() -> None:
    for fixture in SHARED_FIXTURES:
        result = clean_deterministically(fixture["input"])
        assert result.text == fixture["text"], fixture["name"]
        assert result.removed == fixture["removed"], fixture["name"]
        assert result.normalized_spaces == fixture["normalizedSpaces"], fixture["name"]
        assert count_words(fixture["input"]) == fixture["words"], fixture["name"]


def test_word_count_matches_the_browser_algorithm() -> None:
    assert count_words("  one\n two   three ") == 3
    assert count_words("can't stop — déjà vu 123") == 5
    assert count_words("Привет, свят!") == 2
    assert count_words("中文測試") == 1


def test_deterministic_cleaning_removes_marks_and_normalizes_spaces() -> None:
    result = clean_deterministically("A\u200bB\u00a0C\U000e0067")
    assert result.text == "AB C"
    assert result.removed == 2
    assert result.normalized_spaces == 1


def test_upstream_cleaner_preserves_load_bearing_joiners() -> None:
    source = "❤️‍🔥 Persian می‌روم and isolated\u200bmark"
    result = clean_deterministically(source)
    assert result.text == "❤️‍🔥 Persian می‌روم and isolatedmark"
    assert result.removed == 1


def test_v070_removes_new_carriers_without_corrupting_layout() -> None:
    source = "A\u2065B\ufff0C\ufdd0D\ue000E"
    result = clean_deterministically(source)
    assert result.text == "ABCDE"
    assert result.removed == 4

    preserved = "\u2066RTL\u2069 \u202aembedded\u202c ©️"
    assert clean_deterministically(preserved).text == preserved


def test_v070_preserves_complete_flag_tags_and_strips_incomplete_tags() -> None:
    scotland = "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"
    assert clean_deterministically(scotland).text == scotland
    assert clean_deterministically("🏴\U000e0067").text == "🏴"
