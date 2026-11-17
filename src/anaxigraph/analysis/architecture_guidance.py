"""Project current model findings into bounded, explicit plan decisions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from anaxigraph.contracts import Graph

from .fields import fingerprint, references, short
from .patterns import rated_opportunities


def pattern_proposals(
    rows: list[dict], owners: set[str], graph: Graph, limit: int | None = 2
) -> list[dict]:
    items = []
    for item in rated_opportunities(rows):
        evidence = references(item.get("evidence"), graph)
        if (
            item["module_id"] not in owners
            or not isinstance(item.get("proposed_change"), str)
            or not item["proposed_change"].strip()
            or not evidence
        ):
            continue
        items.append(
            {
                "kind": "pattern",
                "proposal_id": f"{item['review_id']}:{fingerprint(item)}",
                "review_id": item["review_id"],
                "title": f"{item['pattern_id']} in {item['module_id']}",
                "status": "proposal-needs-source-verification",
                "module_id": item["module_id"],
                "module_ids": [item["module_id"]],
                "pattern_id": item["pattern_id"],
                "opportunity": item["opportunity"],
                "current_fit": item.get("current_fit", "unknown"),
                "suggestion": short(item["proposed_change"], 360),
                "pros": [short(value, 180) for value in item.get("pros", [])[:2]],
                "cons": [short(value, 180) for value in item.get("cons", [])[:2]],
                "simpler_alternative": short(item.get("alternative")),
                "evidence": evidence,
                "expand": "review:" + item["review_id"],
            }
        )
    items.sort(key=lambda item: (-item["opportunity"], item["proposal_id"]))
    return items[:limit]


def proposals(rows: list[dict[str, Any]], owners: set[str], limit: int | None = 2) -> list[dict]:
    items = []
    for row in rows:
        if row.get("kind") != "architecture" or row.get("status") != "current":
            continue
        result = row["result"]["result"]
        for item in result["findings"]:
            if not owners.intersection(item["module_ids"]):
                continue
            items.append(
                {
                    "kind": "architecture",
                    "proposal_id": f"{row['id']}:{fingerprint(item)}",
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


def brief_proposals(rows: list[dict], owners: set[str]) -> list[dict]:
    """Show the next action without displacing its responsibility owner."""
    fields = {
        "kind",
        "proposal_id",
        "review_id",
        "lens",
        "title",
        "status",
        "opportunity",
        "placement",
        "suggestion",
        "evidence",
        "expand",
    }
    return [
        {key: value for key, value in item.items() if key in fields}
        | {
            "public_contract": item["public_contract"][:180],
            "verification": item["verification"][:1],
            "options": [
                option for option in item["options"] if option["placement"] == item["placement"]
            ],
        }
        for item in proposals(rows, owners)
    ]


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


def decide(
    requirements: list[dict],
    decisions: Any,
    *,
    advisory: Sequence[dict] = (),
    truncated: bool = False,
    previous: Sequence[dict] = (),
) -> tuple[dict, list[dict]]:
    """Require an explicit disposition, never agreement with a model proposal.

    Advisory findings come from structural observation alone: a decision about one is recorded
    when given and never asked for.
    """
    required = {item["proposal_id"] for item in requirements}
    expected = required | {item["proposal_id"] for item in advisory}
    accepted = _accepted(decisions, previous, expected)
    pending = required - accepted.keys()
    ordered = sorted(requirements, key=lambda item: (-item["opportunity"], item["proposal_id"]))
    page = [item for item in ordered if item["proposal_id"] in pending][:12] or ordered[:12]
    shown = {item["proposal_id"] for item in (*page, *advisory[:6])}
    result = {
        "status": "scope_incomplete"
        if truncated
        else "decision_required"
        if pending
        else "decisions_recorded"
        if accepted
        else "no_current_material_proposals",
        "requirements": page,
        "advisory": list(advisory[:6]),
        "decisions": [item for key, item in accepted.items() if key in shown],
        "pending_ids": [item["proposal_id"] for item in page if item["proposal_id"] in pending],
        "total": len(required),
        "remaining": len(pending),
        "resolved": len(required) - len(pending),
        "truncated": truncated or len(page) < len(requirements),
        "expand": {"entity_id": "plan", "full": True},
        "implementation_status": "unverified",
        "coverage": "Bounded current owner-scoped material proposals; no proposal is a mandatory rewrite or proof of design quality.",
    }
    return result, list(accepted.values())


def _accepted(decisions: Any, previous: Sequence[dict], expected: set[str]) -> dict:
    if decisions is not None and (not isinstance(decisions, list) or len(decisions) > 12):
        raise ValueError(
            "Architecture decisions must be a list of at most twelve dispositions per batch"
        )
    accepted = {item["proposal_id"]: item for item in previous if item["proposal_id"] in expected}
    seen: set[str] = set()
    for item in decisions or []:
        item = _decision(item, expected - seen)
        accepted[item["proposal_id"]] = item
        seen.add(item["proposal_id"])
    return accepted


def assess_proposals(rows, owners, graph, prior, decisions, structural) -> dict:
    known = dict(prior.get("known", {}))
    scouted = [
        item
        for item in proposals(rows, owners, None) + pattern_proposals(rows, owners, graph, None)
        if item["opportunity"] >= 2
    ]
    for required, candidates in ((True, scouted), (False, structural or [])):
        for item in candidates:
            known[item["proposal_id"]] = item | {
                "required": required,
                "assessed_snapshot_id": graph.snapshot.id,
            }
    view, choices = decide(
        [item for item in known.values() if item["required"]],
        decisions,
        advisory=[item for item in known.values() if not item["required"]],
        previous=prior.get("choices", []),
    )
    return {"known": known, "choices": choices, "view": view}


def _decision(item: Any, expected: set[str]) -> dict:
    required = {"proposal_id", "decision", "reason"}
    if not isinstance(item, dict) or not required <= set(item) <= required | {"revisit_when"}:
        raise ValueError(
            "Each architecture decision needs proposal_id, decision and reason, "
            "and may add revisit_when"
        )
    revisit = item.get("revisit_when")
    if revisit is not None and (
        not isinstance(revisit, str) or not 1 <= len(revisit.strip()) <= 200
    ):
        raise ValueError("revisit_when must be a nonempty text of at most 200 characters")
    if not isinstance(item["proposal_id"], str) or item["proposal_id"] not in expected:
        raise ValueError(
            "Architecture decision names an unknown or duplicate proposal; "
            f"available proposal IDs (first twelve): {sorted(expected)[:12]}"
        )
    if item["decision"] not in ("adopt", "defer", "reject"):
        raise ValueError("Architecture decision must adopt, defer or reject")
    if not isinstance(item["reason"], str) or not 1 <= len(item["reason"].strip()) <= 500:
        raise ValueError("Architecture decisions need a nonempty reason of at most 500 characters")
    return item | {"reason": item["reason"].strip()}
