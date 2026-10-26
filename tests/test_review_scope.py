"""Task guidance must filter saved reviews before decompressing large unrelated results."""

import json
from dataclasses import replace

from test_storage import graph

from anaxigraph.storage import Store


def test_scope_and_limit_are_applied_before_review_decompression(tmp_path, monkeypatch):
    import anaxigraph.storage.store as storage

    store = Store(tmp_path / "knowledge.sqlite")
    current = graph(tmp_path)
    store.save_graph(current)
    for index in range(25):
        store.save_review(
            str(tmp_path),
            "one",
            f"unrelated-{index}",
            "patterns",
            {
                "scope": [f"module:unrelated-{index}.py"],
                "result": {"detail": "x" * 100_000},
            },
        )
    for identity in ("first", "second"):
        store.save_review(
            str(tmp_path),
            "one",
            identity,
            "patterns",
            {
                "scope": ["module:a.py"],
                "request_options": {"level": identity},
                "result": {},
            },
        )
    decoded = []
    original = storage._review_text

    def require_selected(body, maximum):
        text = original(body, maximum)
        assert "module:a.py" in json.loads(text)["scope"]
        decoded.append(text)
        return text

    monkeypatch.setattr(storage, "_review_text", require_selected)
    rows = store.reviews(
        str(tmp_path), include_requests=False, module_ids=("module:a.py",), limit=1
    )
    assert [row["id"] for row in rows] == ["second"]
    assert len(decoded) == 1
    assert store.reviews(str(tmp_path), module_ids=()) == []
    newer = replace(current, snapshot=replace(current.snapshot, id="two"))
    store.save_graph(newer)
    assert store.reviews(str(tmp_path), module_ids=("module:a.py",), limit=6) == []
