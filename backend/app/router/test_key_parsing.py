"""API key lists pasted into a dashboard come in many shapes. All of them must parse to the
same keys, because a mis-parsed list makes every call that uses the bad 'key' fail."""
import pytest

from app.core.config import Settings, _parse_key_list


@pytest.mark.parametrize("raw", [
    "k1,k2,k3",
    "k1, k2, k3",
    "k1,k2,k3,",
    "k1\nk2\nk3",
    "k1\r\nk2\r\nk3\r\n",
    "k1 k2 k3",
    "k1;k2;k3",
    '"k1,k2,k3"',
    "'k1', 'k2', 'k3'",
    "[k1, k2, k3]",
    " k1 ,\n k2 ,\n k3 ",
])
def test_every_common_format_gives_the_same_three_keys(raw):
    assert _parse_key_list(raw) == ["k1", "k2", "k3"]


def test_duplicates_are_dropped_keeping_order():
    assert _parse_key_list("k1,k2,k1,k3,k2") == ["k1", "k2", "k3"]


def test_empty_and_blank_give_no_keys():
    assert _parse_key_list("") == [] and _parse_key_list(" \n , ; ") == []


def test_realistic_keys_are_not_split_or_mangled():
    groq_key = "gsk_AbC123xyzDEF_-9876"
    gemini_key = "AIzaSyA-b_c1234567890"
    assert _parse_key_list(f"{groq_key}\n{gemini_key}") == [groq_key, gemini_key]


def _settings(**kw):
    base = dict(GROQ_API_KEYS="", GROQ_API_KEY="", GEMINI_API_KEYS="", GEMINI_API_KEY="", JINA_API_KEY="j")
    base.update(kw)
    return Settings(**base)


def test_legacy_single_key_var_is_cleaned_too():
    s = _settings(GROQ_API_KEY='"gsk_x"\n', GEMINI_API_KEY="g")
    assert s.groq_keys() == ["gsk_x"]


def test_list_var_still_wins_over_single_key_var():
    s = _settings(GROQ_API_KEYS="a\nb", GROQ_API_KEY="legacy", GEMINI_API_KEY="g")
    assert s.groq_keys() == ["a", "b"]


def test_no_key_at_all_still_fails_loudly():
    with pytest.raises(ValueError):
        _settings(GEMINI_API_KEY="g").groq_keys()