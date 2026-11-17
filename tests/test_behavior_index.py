"""Identity matching retains the original lexical policy without repository-sized patterns."""

import random
import re

import pytest

from anaxigraph.analysis.behavior import _compile_matcher


def regex_reference(identities, names):
    """Frozen nonempty-input policy, including code contexts and JSON-label normalization."""
    literal = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, identities)) + r")(?!\w)")
    alternatives = "(?:" + "|".join(map(re.escape, names)) + ")"
    code = re.compile(
        r"`" + alternatives + r"`|(?<!\w)" + alternatives + r"(?=\(|\.[A-Za-z_])"
        r"|(?<!\w)[A-Za-z_]\w*\." + alternatives + r"(?!\w)"
    )
    labels = r"`[a-z]+`(?:(?:\s*,\s*(?:(?i:and)\s+)?|\s+(?i:and)\s+)`[a-z]+`)*"
    data = r"(?i:(?:object|field|key|label|value)s?)"
    normalization = re.compile(rf"\b{data}\s+{labels}|{labels}\s+{data}\b")
    return lambda text: bool(
        literal.search(text)
        or names
        and code.search(normalization.sub(lambda m: m[0].replace("`", ""), text))
    )


INVENTORIES = [
    (
        ("module:service.py", "service.py", "create_invoice", "InvoiceStore"),
        ("status", "record", "Store", "InvoiceStore", "a", "a a", "a-b"),
    ),
    (
        ("module:my folder/a[b].py", "my folder/a[b].py", "foo`bar.py", "δ/κόσμος.py"),
        ("my module", "$parse", "a[b]", "`x`", "δ", "a\u0301", "a\\b"),
    ),
    (
        ("symbol:owner.py#Outer.method", "source+name.py", "tab\tname.py", "line\nname.py"),
        ("method", "kind", "value", "code", "message", ".leading", "trailing."),
    ),
]


@pytest.mark.parametrize("identities,names", INVENTORIES)
def test_identity_index_agrees_with_regex_policy_at_literal_and_code_boundaries(identities, names):
    reference = regex_reference(identities, names)
    matcher = _compile_matcher(identities, names)
    for token in (*identities, *names):
        for prefix in ("", " ", "x", "_", "δ", "owner.", "`", "``", "(", "field `"):
            for suffix in ("", " ", "x", "_", "δ", "()", ".value", ".δ", "`", "``", ")"):
                text = prefix + token + suffix
                assert matcher(text) == reference(text), repr(text)


@pytest.mark.parametrize("identities,names", INVENTORIES)
def test_identity_index_agrees_on_mixed_text_and_overlapping_punctuation(identities, names):
    rng = random.Random(20261117)
    reference = regex_reference(identities, names)
    matcher = _compile_matcher(identities, names)
    alphabet = "abX_δ \n\t`.:/[]()!+-$"
    for _ in range(2500):
        prefix = "".join(rng.choices(alphabet, k=rng.randrange(15)))
        suffix = "".join(rng.choices(alphabet, k=rng.randrange(15)))
        token = rng.choice((*identities, *names, "unknown", "field `kind`, `value`"))
        text = prefix + token + suffix
        assert matcher(text) == reference(text), repr(text)


@pytest.mark.parametrize(
    "text",
    ["", "Ordinary behavior.", "Preserve  two spaces.", "[]() -- `value`", "δ\nβ\tΩ"],
)
def test_no_known_source_identity_cannot_leak_one(text):
    assert _compile_matcher((), ())(text) is False


def test_existing_behavior_text_budget_is_checked_before_searching_untrusted_text():
    matcher = _compile_matcher(("module:service.py",), ("record",))
    assert matcher("x" * 600) is False
    with pytest.raises(ValueError, match="600-character"):
        matcher("x " * 301)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("reports status for each module", False),
        ("search modules by path", False),
        ("a record is exported after each scan", False),
        ("fields `kind`, `value` and `status`", False),
        ("object keys `code`, `message`", False),
        ("Call status() before returning.", True),
        ("Look up owner.status before returning.", True),
        ("Use `record` to persist.", True),
        ("field `record`() handles it.", True),
        ("`unknown`record`", True),
        ("``x``", True),
        ("my module()", True),
    ],
)
def test_ordinary_language_and_data_labels_stay_distinct_from_implementation_names(text, expected):
    matcher = _compile_matcher(
        ("module:service.py",),
        (
            "status",
            "modules",
            "path",
            "record",
            "scan",
            "kind",
            "value",
            "code",
            "message",
            "`x`",
            "my module",
        ),
    )
    assert matcher(text) is expected
