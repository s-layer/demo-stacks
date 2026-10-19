"""
Databricks metric view DDL (``CREATE VIEW ... WITH METRICS LANGUAGE YAML``).

A metric view has one ``source`` table and a tree of named ``joins``, which is why
the canonical format requires every view's tables to form a tree from its root.
The renderer maps the canonical shape onto that:

- the root table becomes ``source``, and each other table becomes a join named by
  its alias, nested under the join it is reached from;
- ``alias.column`` becomes ``source.column`` for the root and
  ``<path>.column`` for a joined table, where ``<path>`` is the chain of join names
  from the source (``o.c.c_mktsegment``);
- a fact reference (``ps.supply_cost``) is replaced by the fact's expression, since
  metric views have no facts;
- a derived metric's bare metric names become ``MEASURE(name)``.

This output has not yet been run against a live workspace; the Databricks stack is
where it gets exercised.
"""

from __future__ import annotations

from typing import Any

import yaml

from demo_stacks.render import qualify
from demo_stacks.semantic import SemanticView, rewrite

METRIC_VIEW_VERSION = 1.1  # nested joins need 1.1; Databricks writes it unquoted


def render(view: SemanticView, *, schema: str | None = None, replace: bool = True) -> str:
    """
    Metric view DDL for ``view``.

    Args:
        view: The canonical view.
        schema: ``catalog.schema`` holding the physical tables; the metric view is
            created there too.
        replace: Emit ``CREATE OR REPLACE``.
    """
    body = yaml.safe_dump(definition(view, schema=schema), sort_keys=False, width=1000)
    verb = "CREATE OR REPLACE VIEW" if replace else "CREATE VIEW"
    return f"{verb} {qualify(view.name, schema)}\nWITH METRICS\nLANGUAGE YAML\nAS $$\n{body}$$;\n"


def definition(view: SemanticView, *, schema: str | None = None) -> dict[str, Any]:
    """The metric view's YAML body as a dict."""
    root = view.root.alias

    def ref(alias: str) -> str:
        return "source" if alias == root else ".".join(view.path(alias))

    def expr(text: str, expanding: frozenset[tuple[str, str]] = frozenset()) -> str:
        def qualified(alias: str, column: str) -> str:
            fact = view.fact(alias, column)
            if fact is None or (alias, column) in expanding:
                return f"{ref(alias)}.{column}"
            return f"({expr(fact.expr, expanding | {(alias, column)})})"

        return rewrite(text, qualified=qualified)

    def join(alias: str) -> dict[str, Any]:
        rel = view.inbound(alias)
        assert rel is not None
        # Inside a join's `on`, the parent is named by its own join name (or `source`)
        # and the join being declared by its name.
        parent = "source" if rel.from_ == root else rel.from_
        pk = view.table(alias).primary_key
        on = " AND ".join(
            f"{parent}.{fc} = {alias}.{pc}" for fc, pc in zip(rel.from_columns, pk, strict=True)
        )
        out: dict[str, Any] = {
            "name": alias,
            "source": qualify(view.table(alias).table, schema),
            "on": on,
        }
        nested = [join(r.to) for r in view.children(alias)]
        if nested:
            out["joins"] = nested
        return out

    out: dict[str, Any] = {"version": METRIC_VIEW_VERSION}
    if view.description:
        out["comment"] = " ".join(view.description.split())
    out["source"] = qualify(view.root.table, schema)
    joins = [join(r.to) for r in view.children(root)]
    if joins:
        out["joins"] = joins
    out["dimensions"] = [_item(d.name, expr(d.expr), d.description) for d in view.dimensions]

    metric_names = {m.name for m in view.metrics}
    measures = []
    for m in view.metrics:
        if m.derived:
            text = rewrite(m.expr, bare=lambda n: f"MEASURE({n})" if n in metric_names else n)
        else:
            text = expr(m.expr)
        measures.append(_item(m.name, text, m.description))
    out["measures"] = measures
    return out


def _item(name: str, expr: str, description: str | None) -> dict[str, str]:
    item = {"name": name, "expr": expr}
    if description:
        item["comment"] = " ".join(description.split())
    return item
