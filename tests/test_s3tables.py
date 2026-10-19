"""Connection setup for the S3 Tables stack."""

from __future__ import annotations

import pytest

from demo_stacks import cli, s3tables

ARN = "arn:aws:s3tables:eu-west-2:123456789012:bucket/s-layer-demo-tpch"


def test_each_scale_factor_has_its_own_namespace() -> None:
    assert s3tables.schema(10) == "s3t.tpch_sf10"
    assert s3tables.namespace(100.0) == "tpch_sf100"


@pytest.mark.parametrize("sf", [0, -1, 0.5, 1.5])
def test_fractional_or_non_positive_scale_factors_are_rejected(sf: float) -> None:
    with pytest.raises(ValueError, match="whole scale factor, got"):
        s3tables.namespace(sf)


def test_setup_attaches_the_bucket_with_the_credential_chain() -> None:
    stmts = s3tables.setup_sql(ARN, region="eu-west-2")
    assert stmts[-2] == (
        "CREATE OR REPLACE SECRET s3tables (TYPE s3, PROVIDER credential_chain, REGION 'eu-west-2')"
    )
    assert stmts[-1] == (
        f"ATTACH IF NOT EXISTS '{ARN}' AS s3t (TYPE iceberg, ENDPOINT_TYPE s3_tables)"
    )
    # Extensions load before the secret and the ATTACH that need them.
    assert stmts.index("LOAD iceberg") < stmts.index(stmts[-1])
    assert "REGION" not in s3tables.setup_sql(ARN)[-2]


def test_load_into_s3_tables_requires_a_scale_factor() -> None:
    with pytest.raises(SystemExit, match="needs --scale-factor"):
        cli.main(["load", "--views-only", "--s3-tables-arn", ARN])
