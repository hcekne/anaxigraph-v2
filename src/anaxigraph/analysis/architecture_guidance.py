"""Project source-aware scout findings into bounded, explicit plan decisions."""

from __future__ import annotations

from typing import Any


def proposals(rows: list[dict[str, Any]], owners: set[str], limit: int = 2) -> list[dict]:
    items = []
    for row in rows:
        if row.get("kind") != "architecture" or row.get("status") != "current":
            continue
        result = row["result"]["result"]
        for index, item in enumerate(result["findings"]):
            if not owners.intersection(item["module_ids"]):
                continue
            items.append(
                {
                    "kind": "architecture",
                    "proposal_id": f"{row['id']}:{index}",
                    "review_id": row["id"],
                    "lens": result["lens"],
                    "title": item["title"],
                    "status": "proposal-needs-source-verification",
                    "module_ids": item["module_ids"],
                    "opportunity": item["priority"],
                    "placement": item["placement"],
                    "suggestion": item["next_step"][:300],
                    "public_contract": item["public_contract"][:300],
                    "private_implementation": item["private_implementation"][:180],
                    "verification": [value[:160] for value in item["verification"][:3]],
                    "options": [
                        {
                            "placement": option["placement"],
                            "benefit": option["benefit"][:120],
                            "cost": option["cost"][:120],
                        }
                        for option in item["options"]
                    ],
                    "evidence": item["evidence"][:2],
                    "expand": "review:" + row["id"],
                }
            )
    items.sort(key=lambda item: (-item["opportunity"], item["proposal_id"]))
    return items[:limit]


def coverage(rows: list[dict], owners: set[str]) -> dict:
    pairs = {
        (item["module_id"], row["result"]["result"]["lens"])
        for row in rows
        if row.get("kind") == "architecture" and row.get("status") == "current"
        for item in row["result"]["result"]["assessments"]
        if item["module_id"] in owners
    }
    expected = {
        (owner, lens) for owner in owners for lens in ("boundaries", "packages", "verification")
    }
    return {
        "status": "current"
        if expected and expected <= pairs
        else "partial"
        if pairs
        else "not_reviewed",
        "covered_checks": len(pairs),
        "expected_checks": len(expected),
        "workflow": "architecture",
        "note": "Scoped scout outcomes, not design correctness. Missing or unknown evidence does not establish good architecture.",
    }


def decide(requirements: list[dict], decisions: Any, *, truncated: bool = False) -> dict:
    """Require an explicit disposition, never agreement with a model proposal."""
    expected = {item["proposal_id"] for item in requirements}
    if decisions is not None and (not isinstance(decisions, list) or len(decisions) > 12):
        raise ValueError("Architecture decisions must be a list of at most twelve dispositions")
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in decisions or []:
        item = _decision(item, expected - seen)
        identity = item["proposal_id"]
        accepted.append(item)
        seen.add(identity)
    pending = sorted(expected - seen)
    return {
        "status": "scope_incomplete"
        if truncated
        else "decision_required"
        if pending
        else "decisions_recorded"
        if expected
        else "no_current_material_proposals",
        "requirements": requirements,
        "decisions": accepted,
        "pending_ids": pending,
        "truncated": truncated,
        "implementation_status": "unverified",
        "coverage": "Bounded current owner-scoped material proposals; no proposal is a mandatory rewrite or proof of design quality.",
    }


def _decision(item: Any, expected: set[str]) -> dict:
    if not isinstance(item, dict) or set(item) != {"proposal_id", "decision", "reason"}:
        raise ValueError("Each architecture decision needs proposal_id, decision and reason")
    if not isinstance(item["proposal_id"], str) or item["proposal_id"] not in expected:
        raise ValueError("Architecture decision names an unknown or duplicate proposal")
    if item["decision"] not in ("adopt", "defer", "reject"):
        raise ValueError("Architecture decision must adopt, defer or reject")
    if not isinstance(item["reason"], str) or not 1 <= len(item["reason"].strip()) <= 500:
        raise ValueError("Architecture decisions need a nonempty reason of at most 500 characters")
    return item | {"reason": item["reason"].strip()}
