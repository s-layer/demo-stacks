"""Validation of canonical definitions and the expression rewriter."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from pydantic import ValidationError

from demo_stacks import semantic
from demo_stacks.semantic import bare_identifiers, qualified_refs, rewrite

if TYPE_CHECKING:
    from pathlib import Path

BASE: dict[str, Any] = {
    "name": "v",
    "tables": [
        {"alias": "o", "table": "orders", "primary_key": ["id"]},
        {"alias": "c", "table": "customers", "primary_key": ["id"]},
        {"alias": "n", "table": "nations", "primary_key": ["id"]},
    ],
    "relationships": [
        {"name": "oc", "from": "o", "from_columns": ["customer_id"], "to": "c"},
        {"name": "cn", "from": "c", "from_columns": ["nation_id"], "to": "n"},
    ],
    "facts": [{"name": "nation_weight", "table": "n", "expr": "n.weight"}],
    "dimensions": [{"name": "nation", "table": "n", "expr": "n.name"}],
    "metrics": [
        {"name": "revenue", "table": "o", "expr": "sum(o.amount)"},
        {"name": "orders", "table": "o", "expr": "count(*)"},
        {"name": "per_order", "expr": "revenue / orders"},
    ],
}


def view(**changes: Any) -> semantic.SemanticView:
    data = copy.deepcopy(BASE)
    data.update(changes)
    return semantic.SemanticView.model_validate(data)


def test_base_view_is_valid() -> None:
    v = view()
    assert v.path("n") == ("c", "n")
    assert v.path("o") == ()
    assert v.derived_dependencies(v.metric("per_order")) == ("revenue", "orders")


def test_repository_definitions_load() -> None:
    defs = semantic.load()
    assert [v.name for v in defs.views] == ["tpch_sales", "tpch_orders"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"relationships": [BASE["relationships"][0]]},
            "table 'n' must be reached by exactly one relationship, found 0",
        ),
        (
            {
                "relationships": [
                    *BASE["relationships"],
                    {"name": "on", "from": "o", "from_columns": ["n"], "to": "n"},
                ]
            },
            "table 'n' must be reached by exactly one relationship, found 2",
        ),
        (
            {
                "relationships": [
                    *BASE["relationships"],
                    {"name": "back", "from": "c", "from_columns": ["x"], "to": "o"},
                ]
            },
            "points at the root table",
        ),
        (
            {
                "relationships": [
                    {"name": "oc", "from": "o", "from_columns": ["a", "b"], "to": "c"},
                    BASE["relationships"][1],
                ]
            },
            "has 2 column(s) but 'c''s primary key has 1",
        ),
        (
            {
                "relationships": [
                    {"name": "oc", "from": "o", "from_columns": ["a"], "to": "x"},
                    BASE["relationships"][1],
                ]
            },
            "unknown table 'x'",
        ),
        (
            {"dimensions": [{"name": "nation", "table": "n", "expr": "z.name"}]},
            "unknown table alias 'z'",
        ),
        (
            {"metrics": [{"name": "m", "table": "o", "expr": "sum(c.amount)"}]},
            "references column c.amount; define a fact on 'c'",
        ),
        (
            {"dimensions": [{"name": "nation", "table": "c", "expr": "n.name"}]},
            "references column n.name",
        ),
        (
            {"metrics": [{"name": "per", "expr": "revenue / o.amount"}, BASE["metrics"][0]]},
            "must combine metrics by bare name",
        ),
        (
            {"metrics": [{"name": "per", "expr": "revenue / nope"}, BASE["metrics"][0]]},
            "unknown metric(s) ['nope']",
        ),
        (
            {"metrics": [{"name": "a", "expr": "b + 1"}, {"name": "b", "expr": "a * 2"}]},
            "derived metrics form a cycle: a -> b -> a",
        ),
        (
            {
                "facts": [
                    {"name": "f", "table": "n", "expr": "n.g"},
                    {"name": "g", "table": "n", "expr": "n.f"},
                ]
            },
            "facts form a cycle",
        ),
        (
            {"dimensions": [{"name": "revenue", "table": "o", "expr": "o.x"}]},
            "duplicate dimension/metric name in view 'v' 'revenue'",
        ),
        ({"dimensions": [], "metrics": []}, "neither dimensions nor metrics"),
    ],
)
def test_invalid_views_are_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        view(**changes)
    assert message in str(exc.value)


def test_metric_may_use_another_tables_fact() -> None:
    v = view(metrics=[{"name": "w", "table": "o", "expr": "sum(n.nation_weight * o.qty)"}])
    assert v.metric("w").table == "o"


def test_passthrough_fact_is_not_a_cycle() -> None:
    view(facts=[{"name": "weight", "table": "n", "expr": "n.weight"}])


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        view(colour="red")


def test_duplicate_view_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate view 'v'"):
        semantic.Definitions.model_validate({"dataset": "d", "views": [BASE, BASE]})


def test_load_reads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "defs.yaml"
    path.write_text(yaml.safe_dump({"dataset": "d", "views": [BASE]}))
    assert semantic.load(path).view("v").root.alias == "o"


class TestScanner:
    def test_qualified_refs_skip_strings_and_quoted_identifiers(self) -> None:
        expr = """concat(o.a, 'o.b', "o.c", o.d)"""
        assert qualified_refs(expr) == [("o", "a"), ("o", "d")]

    def test_bare_identifiers_exclude_functions_and_qualified_names(self) -> None:
        assert bare_identifiers("coalesce(revenue, 0) / o.orders + cost") == ["revenue", "cost"]

    def test_rewrite_leaves_quotes_alone(self) -> None:
        out = rewrite(
            "sum(o.x) + o.y || 'o.x' || \"o.x\"",
            qualified=lambda a, c: f"T.{c}",
        )
        assert out == "sum(T.x) + T.y || 'o.x' || \"o.x\""

    def test_rewrite_handles_escaped_quotes_in_strings(self) -> None:
        out = rewrite("'it''s revenue' || revenue", bare=lambda n: n.upper())
        assert out == "'it''s revenue' || REVENUE"
