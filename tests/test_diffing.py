from __future__ import annotations

from pulsewatch.diffing import (
    changed_tokens,
    digest,
    diff_lines,
    fingerprint,
    get_json_path,
    normalize,
    should_alert,
    strip_html,
    tokenize,
)


def test_strip_html_removes_script_and_style():
    html = "<html><style>p{color:red}</style><script>alert(1)</script><p>Seats available</p></html>"
    text = strip_html(html)
    assert "Seats available" in text
    assert "alert(1)" not in text and "color:red" not in text


def test_normalize_html_collapses_whitespace():
    lines = normalize("<p>Library   closes</p>\n\n<p>at  6pm</p>", as_html=True)
    assert lines == ["Library closes", "at 6pm"]


def test_normalize_json_path_extracts_and_sorts():
    raw = '{"data": {"seats": [3, 1, 2]}}'
    lines = normalize(raw, json_path="data.seats")
    assert lines == ["[", "3,", "1,", "2", "]"]


def test_normalize_json_path_tolerates_bad_payload():
    assert normalize("not json", json_path="a.b") == ["not json"]


def test_get_json_path_supports_list_indexes():
    assert get_json_path({"a": [{"b": 7}]}, "a[0].b") == 7


def test_tokenize_drops_stopwords_and_lowercases():
    assert tokenize("The Library IS open on Friday") == ["library", "open", "friday"]


def test_digest_is_stable_and_order_sensitive():
    assert digest(["a", "b"]) == digest(["a", "b"])
    assert digest(["a", "b"]) != digest(["b", "a"])


def test_diff_lines_reports_added_and_removed():
    diff = diff_lines(["seats: 0", "note: closed"], ["seats: 12", "note: closed"])
    assert diff.added == ("seats: 12",)
    assert diff.removed == ("seats: 0",)
    assert diff.changed is True


def test_diff_lines_identical_input_is_not_changed():
    diff = diff_lines(["same"], ["same"])
    assert diff.changed is False


def test_diff_lines_min_ratio_suppresses_reformatting():
    before = ["alpha", "beta", "gamma"]
    after = ["alpha", "beta", "gamma", "delta"]
    assert diff_lines(before, after, min_ratio=0.5).changed is False


def test_fingerprint_ignores_line_order():
    a = fingerprint("t", ("x", "y"), ("z",))
    b = fingerprint("t", ("y", "x"), ("z",))
    assert a == b


def test_should_alert_blocks_small_changes():
    """One token differs even though the whole line was rewritten."""
    diff = diff_lines(["a b c d e"], ["a b c d f"])
    assert diff.tokens_changed == 2
    send, reason = should_alert(diff, min_tokens=5, last_alert_at=None, cooldown=60, now=0.0)
    assert send is False and "noise floor" in reason


def test_changed_tokens_counts_only_what_differs():
    assert changed_tokens(["seats available 0"], ["seats available 12"]) == 2
    assert changed_tokens(["same line"], ["same line"]) == 0


def test_should_alert_blocks_inside_cooldown():
    diff = diff_lines(["alpha beta"], ["alpha beta gamma delta"])
    send, reason = should_alert(diff, min_tokens=1, last_alert_at=0.0, cooldown=60, now=30.0)
    assert send is False and "cooldown" in reason


def test_should_alert_allows_significant_change_after_cooldown():
    diff = diff_lines(["alpha beta"], ["alpha beta gamma delta"])
    send, reason = should_alert(diff, min_tokens=1, last_alert_at=0.0, cooldown=60, now=120.0)
    assert send is True and "significant" in reason