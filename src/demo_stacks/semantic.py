"""
Canonical semantic view definitions, loaded from ``semantic/*.yaml``.

The definitions are warehouse-neutral. The renderers in :mod:`demo_stacks.render`
turn them into each warehouse's DDL. :func:`load` validates the structural rules
every renderer relies on, so a renderer never has to defend against a malformed
definition.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEFINITIONS = REPO_ROOT / "semantic" / "tpch.yaml"

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_IDENT_RE = re.compile(rf"^{_IDENT}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Table(_Model):
    alias: str = Field(pattern=_IDENT_RE.pattern)
    table: str = Field(pattern=_IDENT_RE.pattern)
    primary_key: tuple[str, ...] = Field(min_length=1)
    description: str | None = None


class Relationship(_Model):
    name: str = Field(pattern=_IDENT_RE.pattern)
    from_: str = Field(alias="from")
    from_columns: tuple[str, ...] = Field(min_length=1)
    to: str


class Fact(_Model):
    """A named row-level expression; the only way to use one table's column in another's."""

    name: str = Field(pattern=_IDENT_RE.pattern)
    table: str
    expr: str
    description: str | None = None


class Dimension(_Model):
    name: str = Field(pattern=_IDENT_RE.pattern)
    table: str
    expr: str
    description: str | None = None


class Metric(_Model):
    name: str = Field(pattern=_IDENT_RE.pattern)
    table: str | None = None
    expr: str
    description: str | None = None

    @property
    def derived(self) -> bool:
        """A metric with no owning table combines other metrics by name."""
        return self.table is None


class SemanticView(_Model):
    name: str = Field(pattern=_IDENT_RE.pattern)
    description: str | None = None
    tables: tuple[Table, ...] = Field(min_length=1)
    relationships: tuple[Relationship, ...] = ()
    facts: tuple[Fact, ...] = ()
    dimensions: tuple[Dimension, ...] = ()
    metrics: tuple[Metric, ...] = ()

    @property
    def root(self) -> Table:
        return self.tables[0]

    def table(self, alias: str) -> Table:
        return next(t for t in self.tables if t.alias == alias)

    def inbound(self, alias: str) -> Relationship | None:
        """The one relationship that reaches ``alias`` (``None`` for the root)."""
        return next((r for r in self.relationships if r.to == alias), None)

    def children(self, alias: str) -> tuple[Relationship, ...]:
        return tuple(r for r in self.relationships if r.from_ == alias)

    def path(self, alias: str) -> tuple[str, ...]:
        """Aliases from the root's first child down to ``alias``; empty for the root."""
        out: list[str] = []
        current = alias
        while (rel := self.inbound(current)) is not None:
            out.append(current)
            current = rel.from_
        return tuple(reversed(out))

    def fact(self, alias: str, name: str) -> Fact | None:
        return next((f for f in self.facts if f.table == alias and f.name == name), None)

    def metric(self, name: str) -> Metric:
        return next(m for m in self.metrics if m.name == name)

    def derived_dependencies(self, metric: Metric) -> tuple[str, ...]:
        """Names of the metrics a derived metric's expression refers to, in order."""
        names = {m.name for m in self.metrics}
        return tuple(dict.fromkeys(tok for tok in bare_identifiers(metric.expr) if tok in names))

    @model_validator(mode="after")
    def _check(self) -> SemanticView:
        _validate_view(self)
        return self


class Definitions(_Model):
    dataset: str
    views: tuple[SemanticView, ...] = Field(min_length=1)

    def view(self, name: str) -> SemanticView:
        return next(v for v in self.views if v.name == name)

    @model_validator(mode="after")
    def _unique_views(self) -> Definitions:
        _no_duplicates("view", [v.name for v in self.views])
        return self


def load(path: Path = DEFAULT_DEFINITIONS) -> Definitions:
    """Load and validate a canonical definitions file."""
    with path.open() as f:
        return Definitions.model_validate(yaml.safe_load(f))


# --- expression scanning -------------------------------------------------------------
#
# Renderers rewrite table references (``o.o_orderdate``) and metric references
# (``net_revenue``) inside SQL expressions. The scanner below splits an expression
# into string literals, quoted identifiers and everything else, so a rewrite never
# touches text inside quotes.

_TOKEN_RE = re.compile(
    r"""
    (?P<string>'(?:[^']|'')*')         # 'literal', '' escapes a quote
  | (?P<quoted>"(?:[^"]|"")*")         # "quoted identifier"
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)


def _tokens(expr: str) -> Iterator[tuple[str, str]]:
    for m in _TOKEN_RE.finditer(expr):
        kind = m.lastgroup
        assert kind is not None
        yield kind, m.group()


def qualified_refs(expr: str) -> list[tuple[str, str]]:
    """``(alias, column)`` pairs for each ``alias.column`` reference in ``expr``."""
    refs: list[tuple[str, str]] = []

    def collect(alias: str, column: str) -> str:
        refs.append((alias, column))
        return f"{alias}.{column}"

    rewrite(expr, qualified=collect)
    return refs


def bare_identifiers(expr: str) -> list[str]:
    """Identifiers that are neither qualified (``x.y``) nor function names (``f(``)."""
    toks = list(_tokens(expr))
    out: list[str] = []
    for i, (kind, value) in enumerate(toks):
        if kind != "ident":
            continue
        prev = _neighbour(toks, i, -1)
        nxt = _neighbour(toks, i, +1)
        if prev != "." and nxt not in (".", "("):
            out.append(value)
    return out


def _neighbour(toks: list[tuple[str, str]], i: int, step: int) -> str:
    j = i + step
    while 0 <= j < len(toks) and toks[j][0] == "other" and toks[j][1].isspace():
        j += step
    return toks[j][1] if 0 <= j < len(toks) else ""


def rewrite(
    expr: str,
    *,
    qualified: Callable[[str, str], str] | None = None,
    bare: Callable[[str], str] | None = None,
) -> str:
    """
    Rewrite references in ``expr``, leaving quoted text untouched.

    ``qualified(alias, column)`` replaces each ``alias.column``; ``bare(name)``
    replaces each bare identifier (as :func:`bare_identifiers` defines it).
    Either callback may return its input unchanged.
    """
    toks = list(_tokens(expr))
    out: list[str] = []
    i = 0
    while i < len(toks):
        kind, value = toks[i]
        if kind == "ident" and _neighbour(toks, i, -1) != ".":
            nxt = _neighbour(toks, i, +1)
            if nxt == "." and qualified is not None:
                # Find the column token after the dot (no whitespace inside a reference).
                if i + 2 < len(toks) and toks[i + 1][1] == "." and toks[i + 2][0] == "ident":
                    out.append(qualified(value, toks[i + 2][1]))
                    i += 3
                    continue
            elif nxt not in (".", "(") and bare is not None:
                out.append(bare(value))
                i += 1
                continue
        out.append(value)
        i += 1
    return "".join(out)


# --- validation ------------------------------------------------------------------------


class DefinitionError(ValueError):
    """A canonical definition breaks a rule the renderers depend on."""


def _no_duplicates(kind: str, names: list[str]) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise DefinitionError(f"duplicate {kind} {name!r}")
        seen.add(name)


def _validate_view(view: SemanticView) -> None:
    where = f"view {view.name!r}"
    aliases = [t.alias for t in view.tables]
    _no_duplicates(f"table alias in {where}", aliases)
    _no_duplicates(f"relationship in {where}", [r.name for r in view.relationships])
    # Dimensions and metrics share one namespace: a query names either by itself.
    _no_duplicates(
        f"dimension/metric name in {where}",
        [f.name for f in view.facts]
        + [d.name for d in view.dimensions]
        + [m.name for m in view.metrics],
    )
    if not view.dimensions and not view.metrics:
        raise DefinitionError(f"{where} declares neither dimensions nor metrics")

    known = set(aliases)
    for rel in view.relationships:
        for end in (rel.from_, rel.to):
            if end not in known:
                raise DefinitionError(
                    f"{where}: relationship {rel.name!r} names unknown table {end!r}"
                )
        if rel.to == view.root.alias:
            raise DefinitionError(
                f"{where}: relationship {rel.name!r} points at the root table {rel.to!r}; "
                "every relationship must lead away from the root"
            )
        target_pk = view.table(rel.to).primary_key
        if len(rel.from_columns) != len(target_pk):
            raise DefinitionError(
                f"{where}: relationship {rel.name!r} has {len(rel.from_columns)} column(s) "
                f"but {rel.to!r}'s primary key has {len(target_pk)}"
            )

    # Tree check: every non-root table has exactly one inbound relationship, and walking
    # inbound edges from any table reaches the root without a cycle.
    for t in view.tables[1:]:
        inbound = [r.name for r in view.relationships if r.to == t.alias]
        if len(inbound) != 1:
            raise DefinitionError(
                f"{where}: table {t.alias!r} must be reached by exactly one relationship, "
                f"found {len(inbound)} ({', '.join(inbound) or 'none'})"
            )
    for t in view.tables:
        seen = {t.alias}
        current = t.alias
        while (rel := view.inbound(current)) is not None:
            current = rel.from_
            if current in seen:
                raise DefinitionError(f"{where}: relationships form a cycle through {current!r}")
            seen.add(current)

    def check_refs(kind: str, name: str, owner: str, expr: str) -> None:
        if owner not in known:
            raise DefinitionError(f"{where}: {kind} {name!r} names unknown table {owner!r}")
        for alias, column in qualified_refs(expr):
            if alias not in known:
                raise DefinitionError(
                    f"{where}: {kind} {name!r} references unknown table alias {alias!r}"
                )
            # Snowflake and duckdb-semantic-views only let an expression read its own
            # table's columns; another table's values come in through its facts.
            if alias != owner and view.fact(alias, column) is None:
                raise DefinitionError(
                    f"{where}: {kind} {name!r} on table {owner!r} references column "
                    f"{alias}.{column}; define a fact on {alias!r} and reference that instead"
                )

    for f in view.facts:
        check_refs("fact", f.name, f.table, f.expr)
    _check_fact_cycles(view, where)
    for d in view.dimensions:
        check_refs("dimension", d.name, d.table, d.expr)

    metric_names = {m.name for m in view.metrics}
    for m in view.metrics:
        if m.table is not None:
            check_refs("metric", m.name, m.table, m.expr)
            continue
        if qualified_refs(m.expr):
            raise DefinitionError(
                f"{where}: derived metric {m.name!r} must combine metrics by bare name, "
                "not reference table columns"
            )
        unknown = [
            t for t in bare_identifiers(m.expr) if t not in metric_names and not _is_keyword(t)
        ]
        if unknown:
            raise DefinitionError(
                f"{where}: derived metric {m.name!r} references unknown metric(s) {unknown}"
            )
        if not view.derived_dependencies(m):
            raise DefinitionError(f"{where}: derived metric {m.name!r} references no metrics")

    def visit(name: str, stack: tuple[str, ...]) -> None:
        if name in stack:
            cycle = " -> ".join((*stack[stack.index(name) :], name))
            raise DefinitionError(f"{where}: derived metrics form a cycle: {cycle}")
        metric = view.metric(name)
        if metric.derived:
            for dep in view.derived_dependencies(metric):
                visit(dep, (*stack, name))

    for m in view.metrics:
        visit(m.name, ())


def _check_fact_cycles(view: SemanticView, where: str) -> None:
    def visit(fact: Fact, stack: tuple[str, ...]) -> None:
        for alias, column in qualified_refs(fact.expr):
            dep = view.fact(alias, column)
            if dep is None or dep is fact:
                continue  # a column, or a passthrough fact naming its own column
            dep_key = f"{dep.table}.{dep.name}"
            if dep_key in stack:
                cycle = " -> ".join((*stack[stack.index(dep_key) :], dep_key))
                raise DefinitionError(f"{where}: facts form a cycle: {cycle}")
            visit(dep, (*stack, dep_key))

    for f in view.facts:
        visit(f, (f"{f.table}.{f.name}",))


_KEYWORDS = frozenset(
    {"and", "or", "not", "null", "case", "when", "then", "else", "end", "is", "true", "false"}
)


def _is_keyword(token: str) -> bool:
    return token.lower() in _KEYWORDS
