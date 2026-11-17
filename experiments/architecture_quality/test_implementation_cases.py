"""Runtime oracles check constructed cases without invoking models."""

from .implementation_cases import CASES, pricing_oracle, receipt_oracle


def test_shared_policy_maintains_both_totals_after_a_single_policy_edit():
    duplicated = pricing_oracle(CASES["duplicated-policy"]["source"])
    shared = pricing_oracle(CASES["shared-policy"]["source"])
    assert duplicated["original_totals"] == shared["original_totals"] == [90.0, 90.0]
    assert duplicated["single_edit_totals"] == [80.0, 90.0]
    assert shared["single_edit_totals"] == [80.0, 80.0]


def test_identical_receipt_results_have_different_persistence_effects(tmp_path):
    hidden = receipt_oracle(CASES["hidden-write"]["source"], tmp_path / "hidden")
    pure = receipt_oracle(CASES["pure-formatting"]["source"], tmp_path / "pure")
    assert hidden["text"] == pure["text"] == "Total: 12.50"
    assert hidden["files_written"] == ["last-receipt.txt"]
    assert pure["files_written"] == []
