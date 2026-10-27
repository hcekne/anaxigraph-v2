"""Human and agent surfaces expose the same compact boundary and review evidence."""

from fastapi.testclient import TestClient
from test_workflows import answer

from anaxigraph.application import Application
from anaxigraph.interfaces.http import create_app


def test_boundary_columns_and_selected_card_requests_use_shared_application(tmp_path):
    (tmp_path / "owner.py").write_text(
        '__all__ = ["calculate"]\n'
        "def _internal():\n    return 1\n"
        "def calculate():\n    return _internal()\n"
    )
    (tmp_path / "consumer.py").write_text(
        "from owner import _internal\ndef consume():\n    return _internal()\n"
    )
    application = Application(tmp_path)
    application.scan()
    annotation = application.analysis_request("annotate", ["owner.py", "consumer.py"])
    application.submit_analysis(annotation["request_id"], answer(annotation))
    with TestClient(create_app(application)) as client:
        modules = client.get("/api/modules").json()["items"]
        boundary = {node["path"]: node["data"]["boundary"] for node in modules}
        assert boundary["owner.py"]["public_names"] == 1
        assert boundary["owner.py"]["basis"] == "literal-__all__"
        assert boundary["consumer.py"]["private_accesses"] > 0
        assert boundary["consumer.py"]["cycle_size"] == 0
        screen = client.post(
            "/api/analysis/request", json={"kind": "pattern-screen", "scope": ["consumer.py"]}
        )
        assert screen.status_code == 200, screen.text
        assert len(screen.json()["packet"]["catalogue"]) == 133
        detail = client.post(
            "/api/analysis/request",
            json={"kind": "patterns", "scope": ["consumer.py"], "pattern_ids": ["adapter"]},
        )
        assert detail.status_code == 200, detail.text
        assert [card["id"] for card in detail.json()["packet"]["catalogue"]] == ["adapter"]
