"""Declared CI inputs give a fast path without guessing that unrelated checks passed."""

from __future__ import annotations

import copy

import pytest

from anaxigraph.analysis.ci_policy import plan_ci, validate_policy


def target(identity, inputs, **options):
    return {
        "id": identity,
        "command": f"check {identity}",
        "inputs": inputs,
        "cache": True,
        "estimated_seconds": 16,
        **options,
    }


@pytest.fixture
def policy():
    return {
        "version": 1,
        "targets": [
            target("payments", ["packages/payments/**"]),
            target("payment-contract", ["packages/payments/**", "contracts/payment.json"]),
            target("consumer", ["app/**", "packages/payments/api.py"]),
            target("bathroom", ["packages/bathroom/**"]),
            target("full", ["**"], cadence="scheduled", estimated_seconds=3660),
            target("boundaries", [], kind="check", cache=False, estimated_seconds=1),
            target("production", ["app/**"], cadence="release", kind="deploy", needs=["consumer"]),
        ],
        "always": ["boundaries"],
        "fallback": ["full"],
        "shared_inputs": ["uv.lock"],
        "ignored_inputs": ["README.md", "docs/**"],
    }


@pytest.fixture
def files():
    return {
        "packages/payments/body.py": "a" * 64,
        "packages/payments/api.py": "b" * 64,
        "contracts/payment.json": "c" * 64,
        "packages/bathroom/tiles.py": "d" * 64,
        "app/main.py": "e" * 64,
        "uv.lock": "f" * 64,
        "README.md": "g" * 64,
    }


def selected(result):
    return {row["id"]: row for row in result["selected_targets"]}


def test_payments_implementation_has_a_sixteen_second_local_path(policy, files):
    result = plan_ci(
        policy, ["packages/payments/body.py"], files, runtime_context={"python": "3.12"}
    )
    assert result["status"] == "planned" and result["execution_ready"]
    assert set(selected(result)) == {"payments", "payment-contract", "boundaries"}
    assert "full" in result["retained_periodic"] and "production" in result["retained_release"]
    assert result["critical_path_seconds"] == 16
    assert result["savings_seconds"] is None
    assert selected(result)["payments"]["cache_eligible"]
    assert "cache hit" in result["assurance"]


def test_declared_public_interface_inputs_add_consumer_checks(policy, files):
    result = plan_ci(policy, ["packages/payments/api.py"], files)
    assert set(selected(result)) == {"payments", "payment-contract", "consumer", "boundaries"}
    assert not selected(result)["consumer"]["cache_eligible"]


def test_shared_roof_expands_only_the_requested_cadence_and_always(policy, files):
    result = plan_ci(policy, ["uv.lock"], files)
    assert set(selected(result)) == {
        "payments",
        "payment-contract",
        "consumer",
        "bathroom",
        "boundaries",
    }
    assert result["reevaluation_required"]
    assert any("Shared input" in reason for reason in result["expansion_reasons"])


def test_shared_change_without_any_applicable_check_remains_incomplete():
    policy = {
        "version": 1,
        "targets": [target("full", ["**"], cadence="scheduled")],
        "fallback": ["full"],
        "shared_inputs": ["uv.lock"],
    }
    result = plan_ci(policy, ["uv.lock"], {"uv.lock": "fingerprint"})
    assert result["status"] == "incomplete" and not result["execution_ready"]
    assert result["uncovered_paths"] == ["uv.lock"]
    assert set(selected(result)) == {"full"}


@pytest.mark.parametrize("cadence", ["scheduled", "release"])
def test_explicit_non_change_cadence_retains_all_its_obligations_without_source_edits(cadence):
    policy = {
        "version": 1,
        "targets": [
            target("local", ["src/**"]),
            target("one", ["src/**"], cadence=cadence),
            target("two", ["other/**"], cadence=cadence, needs=["local"]),
        ],
    }
    result = plan_ci(policy, [], {"src/body.py": "source"}, cadence=cadence)
    assert set(selected(result)) == {"one", "two", "local"}
    assert result["layers"] == [["local", "one"], ["two"]]
    assert f"Explicit {cadence} cadence obligation" in selected(result)["two"]["reasons"]


def test_ordering_does_not_transfer_changes_but_consumed_data_does():
    policy = {
        "version": 1,
        "targets": [
            target("producer", ["core/**"]),
            target("ordering-only", ["other/**"], needs=["producer"]),
            target("consumer", ["web/**"], consumes=["producer"]),
            target("lint", [], cache=False),
        ],
        "always": ["lint"],
    }
    files = {"core/a.py": "a", "other/b.py": "b", "web/c.py": "c"}
    changed = plan_ci(policy, ["core/a.py"], files)
    assert set(selected(changed)) == {"producer", "consumer", "lint"}
    assert changed["layers"] == [["lint", "producer"], ["consumer"]]
    ordering = plan_ci(policy, ["other/b.py"], files)
    assert set(selected(ordering)) == {"producer", "ordering-only", "lint"}
    assert "consumer" not in selected(ordering)
    empty = plan_ci(policy, [], files)
    assert set(selected(empty)) == {"lint"}


def test_consumed_output_reaches_change_target_through_periodic_producer():
    policy = {
        "version": 1,
        "targets": [
            target("release-build", ["core/**"], cadence="release"),
            target("contract", ["contract/**"], consumes=["release-build"]),
        ],
    }
    result = plan_ci(policy, ["core/a.py"], {"core/a.py": "a", "contract/test.py": "b"})
    assert result["execution_ready"]
    assert result["layers"] == [["release-build"], ["contract"]]


def test_unknown_ownership_uses_only_explicit_fallback_and_cannot_cache(policy, files):
    result = plan_ci(policy, ["unowned/new.py"], files, runtime_context={"python": "3.12"})
    assert result["status"] == "incomplete" and not result["execution_ready"]
    assert result["uncovered_paths"] == ["unowned/new.py"]
    assert set(selected(result)) == {"full", "boundaries"}
    assert all(not row["cache_eligible"] for row in result["selected_targets"])
    assert any("Declare ownership" in reason for reason in result["reevaluation_reasons"])
    assert "production" not in selected(result)


def test_periodic_ownership_alone_does_not_prove_change_selection_complete(policy, files):
    result = plan_ci(policy, ["misc/config.py"], files)
    assert result["uncovered_paths"] == ["misc/config.py"]


def test_ignored_docs_do_not_override_real_inputs(policy, files):
    policy["targets"] = [item for item in policy["targets"] if item["id"] != "full"]
    policy["fallback"] = []
    docs = plan_ci(policy, ["README.md"], files)
    assert docs["ignored_paths"] == ["README.md"] and docs["execution_ready"]
    policy["targets"].append(target("readme-build", ["README.md"]))
    consumed = plan_ci(policy, ["README.md"], files)
    assert consumed["ignored_paths"] == []
    assert "readme-build" in selected(consumed)


def test_posix_globs_allow_zero_directory_double_star_without_star_crossing_paths():
    policy = {
        "version": 1,
        "targets": [target("python", ["**/*.py"]), target("flat", ["pkg/*.py"])],
    }
    result = plan_ci(
        policy, ["root.py", "pkg/deep/item.py"], {"root.py": "a", "pkg/deep/item.py": "b"}
    )
    assert set(selected(result)) == {"python"}
    recursive = {"version": 1, "targets": [target("nested", ["pkg/**/item.py"])]}
    assert plan_ci(recursive, ["pkg/item.py"], {"pkg/item.py": "a"})["execution_ready"]


def test_cache_fingerprints_follow_data_not_ordering_or_unrelated_sources():
    policy = {
        "version": 1,
        "targets": [
            target("core", ["core/**"]),
            target("ordering", ["ordering/**"], needs=["core"]),
            target("consumer", ["web/**"], consumes=["core"]),
        ],
    }
    files = {"core/a.py": "a", "ordering/b.py": "b", "web/c.py": "c", "unrelated/x.py": "x"}
    context = {"python": "3.12", "runner": "linux"}
    first = selected(plan_ci(policy, ["ordering/b.py", "web/c.py"], files, runtime_context=context))
    unrelated = files | {"unrelated/x.py": "new"}
    second = selected(
        plan_ci(policy, ["ordering/b.py", "web/c.py"], unrelated, runtime_context=context)
    )
    assert first["consumer"]["cache_key"] == second["consumer"]["cache_key"]
    core_changed = files | {"core/a.py": "new"}
    third = selected(
        plan_ci(policy, ["ordering/b.py", "web/c.py"], core_changed, runtime_context=context)
    )
    assert first["ordering"]["cache_key"] == third["ordering"]["cache_key"]
    assert first["consumer"]["cache_key"] != third["consumer"]["cache_key"]
    environment = selected(
        plan_ci(policy, ["web/c.py"], files, runtime_context=context | {"python": "3.13"})
    )
    assert first["consumer"]["cache_key"] != environment["consumer"]["cache_key"]
    command = copy.deepcopy(policy)
    command["targets"][2]["working_directory"] = "web"
    assert (
        first["consumer"]["cache_key"]
        != selected(plan_ci(command, ["web/c.py"], files, runtime_context=context))["consumer"][
            "cache_key"
        ]
    )


def test_deleted_or_unobserved_inputs_and_unknown_runtime_are_not_cache_proof():
    policy = {"version": 1, "targets": [target("test", ["pkg/**", "missing/**"])]}
    files = {"pkg/a.py": "a"}
    missing = selected(plan_ci(policy, ["pkg/a.py"], files, runtime_context={"python": "3.12"}))[
        "test"
    ]
    assert not missing["cache_eligible"] and missing["cache_key"] is None
    deleted = selected(
        plan_ci(
            {"version": 1, "targets": [target("test", ["pkg/**"])]},
            ["pkg/gone.py"],
            files,
            runtime_context={"python": "3.12"},
        )
    )["test"]
    assert deleted["cache_key"] is None
    assert not selected(plan_ci(policy, ["pkg/a.py"], files))["test"]["cache_eligible"]


def test_critical_path_is_not_sum_and_unknown_estimates_remain_unknown():
    policy = {
        "version": 1,
        "targets": [
            target("a", ["src/**"], estimated_seconds=10),
            target("b", ["src/**"], estimated_seconds=20),
            target("c", ["src/**"], needs=["a", "b"], estimated_seconds=5),
        ],
    }
    assert plan_ci(policy, ["src/a"], {"src/a": "x"})["critical_path_seconds"] == 25
    policy["targets"][1]["estimated_seconds"] = None
    assert plan_ci(policy, ["src/a"], {"src/a": "x"})["critical_path_seconds"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "unknown",
        "needs-cycle",
        "consumes-cycle",
        "absolute",
        "traversal",
        "working-directory",
        "empty-input",
        "nan",
        "extra",
    ],
)
def test_invalid_policies_are_rejected(mutation):
    policy = {"version": 1, "targets": [target("a", ["a/**"]), target("b", ["b/**"])]}
    a, b = policy["targets"]
    if mutation == "duplicate":
        b["id"] = "a"
    elif mutation == "unknown":
        a["needs"] = ["missing"]
    elif mutation.endswith("cycle"):
        field = mutation.split("-")[0]
        a[field], b[field] = ["b"], ["a"]
    elif mutation in {"absolute", "traversal"}:
        a["inputs"] = ["/a/**" if mutation == "absolute" else "a/../**"]
    elif mutation == "working-directory":
        a["working_directory"] = "../other"
    elif mutation == "empty-input":
        a["inputs"] = []
    elif mutation == "nan":
        a["estimated_seconds"] = float("nan")
    else:
        a["unknown_option"] = True
    with pytest.raises(ValueError):
        validate_policy(policy)
