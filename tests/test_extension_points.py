"""Open-ended families registered by editing shared files are pointed out, never gated."""

from __future__ import annotations

from anaxigraph.analysis.extension import change_findings, check_reach, closed_families
from anaxigraph.application import Application

CONTRACT = "class Provider:\n    name = ''\n    def capabilities(self):\n        return []\n"
MEMBER = "from app.contract import Provider\n\n\nclass {cls}(Provider):\n    name = '{name}'\n"


def _write(root, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def _closed_repo(root) -> Application:
    _write(
        root,
        {
            "app/__init__.py": "",
            "app/contract.py": CONTRACT,
            "app/providers/__init__.py": "",
            "app/providers/weather.py": MEMBER.format(cls="Weather", name="weather"),
            "app/providers/crm.py": MEMBER.format(cls="Crm", name="crm"),
            "app/main.py": (
                "from app.providers.weather import Weather\n"
                "from app.providers.crm import Crm\n\n\ndef providers():\n    return [Weather(), Crm()]\n"
            ),
            "tests/test_main.py": "from app.main import providers\n\n"
            "def test_count():\n    assert providers()\n",
            "tests/test_weather.py": "from app.providers.weather import Weather\n\n"
            "def test_name():\n    assert Weather.name\n",
        },
    )
    app = Application(root)
    app.scan()
    return app


def test_static_registrar_makes_a_closed_family_with_its_check_reach(tmp_path):
    graph = _closed_repo(tmp_path)._current()
    [family] = closed_families(graph)
    assert family["directory"] == "app/providers"
    assert family["contract"] == "app/contract.py" and family["interface"] == "Provider"
    assert family["registrars"] == ["app/main.py"]
    assert family["members"] == 2
    assert check_reach(graph, ["app/main.py"])["test_modules"] == 1
    assert check_reach(graph, ["app/contract.py"])["test_modules"] == 2


def test_discovered_family_has_no_static_registrar(tmp_path):
    _write(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/contract.py": CONTRACT,
            "app/providers/__init__.py": "",
            "app/providers/weather.py": MEMBER.format(cls="Weather", name="weather"),
            "app/providers/crm.py": MEMBER.format(cls="Crm", name="crm"),
            "app/main.py": (
                "import importlib\nimport pkgutil\n\nimport app.providers as package\n\n"
                "PROVIDERS = [importlib.import_module(f'{package.__name__}.{m.name}')"
                " for m in pkgutil.iter_modules(package.__path__)]\n"
            ),
        },
    )
    app = Application(tmp_path)
    app.scan()
    graph = app._current()
    assert closed_families(graph) == []
    assert change_findings(graph, {"app/providers/geo.py"}, {"app/main.py"}) == []


def test_modules_that_only_share_a_helper_are_not_a_family(tmp_path):
    helper = "from app.errors import fail\n\n\ndef {name}():\n    return fail('{name}')\n"
    _write(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/errors.py": "def fail(reason):\n    raise ValueError(reason)\n",
            "app/handlers/__init__.py": "",
            "app/handlers/orders.py": helper.format(name="orders"),
            "app/handlers/refunds.py": helper.format(name="refunds"),
            "app/main.py": (
                "from app.handlers.orders import orders\n"
                "from app.handlers.refunds import refunds\n\n\n"
                "def routes():\n    return [orders, refunds]\n"
            ),
        },
    )
    app = Application(tmp_path)
    graph = app._live()
    assert closed_families(graph) == []
    plan = app.assess_plan("Add a returns handler", ["app/handlers/returns.py", "app/main.py"])
    assert plan["architecture"]["advisory"] == [] and plan["status"] == "advisory"
    assert app.prepare_change("Add a returns handler")["design_brief"]["closed_families"] == []


def test_plan_adding_a_member_through_the_registrar_is_advised_and_never_gated(tmp_path):
    app = _closed_repo(tmp_path)
    brief = app.prepare_change("Add a geo provider")
    assert next(iter(brief)) == "design_brief"
    assert brief["design_brief"]["closed_families"][0]["directory"] == "app/providers"
    assert any("app/providers/" in q for q in brief["design_brief"]["questions"])

    files = ["app/providers/geo.py", "app/main.py", "tests/test_geo.py"]
    plan = app.assess_plan("Add a geo provider", files)
    assert plan["status"] == "advisory"
    assert plan["architecture"]["requirements"] == []
    assert plan["architecture"]["status"] == "no_current_material_proposals"
    [requirement] = plan["architecture"]["advisory"]
    assert requirement["kind"] == "extension-point"
    assert requirement["adding"] == ["app/providers/geo.py"]
    assert requirement["editing_registrars"] == ["app/main.py"]
    assert "pkgutil" in requirement["suggestion"]
    assert not any(item["kind"] == "reuse-opportunity" for item in plan["findings"])

    decided = app.assess_plan(
        "Add a geo provider",
        files,
        [{"proposal_id": requirement["proposal_id"], "decision": "adopt", "reason": "Discover."}],
    )
    assert decided["status"] == "advisory"
    assert decided["architecture"]["status"] == "decisions_recorded"


def test_plan_that_only_adds_files_needs_no_extension_decision(tmp_path):
    app = _closed_repo(tmp_path)
    plan = app.assess_plan("Add a geo provider", ["app/providers/geo.py", "tests/test_geo.py"])
    assert plan["architecture"]["requirements"] == plan["architecture"]["advisory"] == []


def test_validation_reports_closed_family_growth_from_the_diff(tmp_path):
    app = _closed_repo(tmp_path)
    app.prepare_change("Add a geo provider")
    (tmp_path / "app/providers/geo.py").write_text(MEMBER.format(cls="Geo", name="geo"))
    main = tmp_path / "app/main.py"
    main.write_text(
        "from app.providers.weather import Weather\nfrom app.providers.crm import Crm\n"
        "from app.providers.geo import Geo\n\n\ndef providers():\n    return [Weather(), Crm(), Geo()]\n"
    )
    app.scan()
    review = app.validate_change()["extension_points"]
    assert review["status"] == "concern"
    assert review["findings"][0]["adding"] == ["app/providers/geo.py"]
    assert review["change_check_reach"]["test_modules"] == 1


def test_a_deferral_is_recorded_without_conditions_and_costs_are_stated(tmp_path):
    app = _closed_repo(tmp_path)
    files = ["app/providers/geo.py", "app/main.py"]
    [advice] = app.assess_plan("Add a geo provider", files)["architecture"]["advisory"]
    assert "1,000 more members" in advice["cost_of_staying_closed"]
    assert advice["decision"].startswith("Advice, not a gate")
    plain = {"proposal_id": advice["proposal_id"], "decision": "defer", "reason": "Later."}
    deferred = app.assess_plan("Add a geo provider", files, [plain])
    assert deferred["status"] == "advisory"
    assert deferred["architecture"]["status"] == "decisions_recorded"
    assert deferred["architecture"]["decisions"] == [plain]


def test_selector_map_naming_members_counts_as_a_registrar(tmp_path):
    app = _closed_repo(tmp_path)
    _write(
        tmp_path,
        {
            "tools/select_tests.py": "MAP = {\n    'app/providers/weather.py': ['tests/test_weather.py'],\n"
            "    'app/providers/crm.py': [],\n}\n"
        },
    )
    app.scan()
    plan = app.assess_plan("Add a geo provider", ["app/providers/geo.py", "tools/select_tests.py"])
    [requirement] = plan["architecture"]["advisory"]
    assert requirement["editing_registrars"] == ["tools/select_tests.py"]


def test_plumbing_that_uses_few_members_is_not_a_registrar(tmp_path):
    _write(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/contract.py": CONTRACT,
            "app/providers/__init__.py": "",
            **{
                f"app/providers/p{i}.py": MEMBER.format(cls=f"P{i}", name=f"p{i}") for i in range(6)
            },
            "app/main.py": "".join(f"from app.providers.p{i} import P{i}\n" for i in range(6))
            + "\n\ndef providers():\n    return [P0(), P1(), P2(), P3(), P4(), P5()]\n",
            "app/platform.py": "from app.providers.p0 import P0\nfrom app.providers.p1 import P1\n\n"
            "def defaults():\n    return [P0, P1]\n",
        },
    )
    app = Application(tmp_path)
    app.scan()
    [family] = closed_families(app._current())
    assert family["registrars"] == ["app/main.py"]
    plan = app.assess_plan("Add a geo provider", ["app/providers/geo.py", "app/platform.py"])
    assert plan["architecture"]["advisory"] == []


def test_validation_ignores_registrar_edits_that_do_not_name_the_new_member(tmp_path):
    app = _closed_repo(tmp_path)
    app.prepare_change("Add a geo provider")
    (tmp_path / "app/providers/geo.py").write_text(MEMBER.format(cls="Geo", name="geo"))
    main = tmp_path / "app/main.py"
    main.write_text(main.read_text() + "\n\nTIMEOUT = 5\n")
    app.scan()
    assert app.validate_change()["extension_points"]["status"] == "no_closed_family_growth"
