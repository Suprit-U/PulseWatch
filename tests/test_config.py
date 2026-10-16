from __future__ import annotations

import pytest

from pulsewatch.config import Retry, Target, load_targets


def test_retry_backoff_is_exponential_and_capped():
    retry = Retry(attempts=5, base_delay=1.0, max_delay=8.0)
    assert [retry.delay_for(i) for i in range(1, 5)] == [1.0, 2.0, 4.0, 8.0]
    assert retry.delay_for(9) == 8.0


def test_retry_rejects_zero_attempts():
    with pytest.raises(ValueError):
        Retry(attempts=0)


def test_target_requires_http_url():
    with pytest.raises(ValueError):
        Target(name="x", url="ftp://example.com")


def test_target_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        Target(name="x", url="https://example.com", interval=0)


def test_target_rejects_negative_cooldown():
    with pytest.raises(ValueError):
        Target(name="x", url="https://example.com", cooldown=-1)


def test_target_from_dict_defaults_and_flags():
    target = Target.from_dict({"name": "gpu", "url": "https://shop.example/gpu", "as_html": True,
                               "tags": ["price"]})
    assert target.interval == 300.0
    assert target.cooldown == 900.0
    assert target.as_html is True
    assert target.tags == ("price",)


def test_target_from_dict_reads_selector():
    target = Target.from_dict({"name": "a", "url": "https://x.example",
                               "selector": {"contains": "Available"}})
    assert target.selector is not None and target.selector.contains == "Available"


def test_load_targets_accepts_bare_list(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text('[{"name": "a", "url": "https://a.example"}]', encoding="utf-8")
    assert [t.name for t in load_targets(path)] == ["a"]


def test_load_targets_accepts_wrapped_object(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text('{"targets": [{"name": "a", "url": "https://a.example"}]}', encoding="utf-8")
    assert len(load_targets(path)) == 1


def test_load_targets_rejects_non_list(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text('{"targets": 7}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_targets(path)