"""
Connect DuckDB to an Amazon S3 Tables bucket through the ``iceberg`` extension.

Every DuckDB connection that queries the demo's semantic views needs these
statements run first: the semantic views name their tables through the attached
catalog (``s3t.tpch.lineitem``), and ``ATTACH`` does not persist in a database file.
"""

from __future__ import annotations

from demo_stacks.render import sql_string

CATALOG = "s3t"
NAMESPACE = "tpch"
SCHEMA = f"{CATALOG}.{NAMESPACE}"


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
