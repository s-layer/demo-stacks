"""
Connect DuckDB to an Amazon S3 Tables bucket through the ``iceberg`` extension.

A DuckDB process that queries the demo's semantic views needs these statements run
once first: the semantic views name their tables through the attached catalog
(``s3t.tpch_sf10.lineitem``), and ``ATTACH`` is not saved in the database file. The
attachment belongs to the database instance, so every connection to it (including
every connection in a semolina pool) sees it.

Each scale factor lives in its own namespace, ``tpch_sf<N>``, so loading SF100 leaves
SF10 in place and benchmarks can compare scales.
"""

from __future__ import annotations

from demo_stacks import dataset
from demo_stacks.render import sql_string

CATALOG = "s3t"


def namespace(scale_factor: float) -> str:
    """The namespace holding TPC-H at ``scale_factor``; see :func:`dataset.schema_name`."""
    return dataset.schema_name(scale_factor)


def schema(scale_factor: float) -> str:
    """``catalog.namespace`` for ``scale_factor``, as DuckDB names it once attached."""
    return f"{CATALOG}.{namespace(scale_factor)}"


def setup_sql(table_bucket_arn: str, *, region: str | None = None) -> list[str]:
    """
    Statements that attach the table bucket ``table_bucket_arn`` as :data:`CATALOG`.

    Credentials come from the standard AWS chain (instance profile, Lambda role,
    environment or ``~/.aws``), so no secret material is written anywhere.
    """
    secret = "CREATE OR REPLACE SECRET s3tables (TYPE s3, PROVIDER credential_chain"
    secret += f", REGION {sql_string(region)})" if region else ")"
    return [
        "INSTALL aws",
        "INSTALL httpfs",
        "INSTALL iceberg",
        "LOAD aws",
        "LOAD httpfs",
        "LOAD iceberg",
        secret,
        f"ATTACH IF NOT EXISTS {sql_string(table_bucket_arn)} AS {CATALOG} "
        "(TYPE iceberg, ENDPOINT_TYPE s3_tables)",
    ]
