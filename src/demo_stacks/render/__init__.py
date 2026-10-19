"""Render canonical semantic views into each warehouse's DDL."""

from __future__ import annotations


def sql_string(value: str) -> str:
    """A single-quoted SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def qualify(table: str, schema: str | None) -> str:
    """``schema.table``, or the bare table name when ``schema`` is empty."""
    return f"{schema}.{table}" if schema else table
