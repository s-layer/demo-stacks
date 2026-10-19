# demo-stacks

End-to-end demos of s-layer stacks, deployed to AWS with Terraform, and a shared
dataset and semantic layer so the stacks can be compared like for like.

| Stack | Query layer | Storage | Status |
|---|---|---|---|
| [`duckdb-s3tables`](stacks/duckdb-s3tables/) | semolina + [duckdb-semantic-views](https://github.com/s-layer/duckdb-semantic-views) | Amazon S3 Tables (Iceberg) | data + runner Terraform |
| [`databricks-rt`](stacks/databricks-rt/) | Databricks metric views on Lakehouse//RT (semolina once it accepts `DatabricksPythonConfig`) | Unity Catalog managed Delta tables | data Terraform + loader, not yet run |

## The shared layer

### Dataset: TPC-H

Every stack uses [TPC-H](https://www.tpc.org/tpch/). It is the example both
vendors already build their semantic layers on (Snowflake's semantic view docs use
`SNOWFLAKE_SAMPLE_DATA.TPCH_SF*`, Databricks ships `samples.tpch`), and DuckDB's
`tpch` extension generates it at any scale factor, so data size is one knob:

| Scale factor | lineitem rows | Parquet (zstd, approx.) |
|---|---|---|
| 1 | ~6M | ~0.3 GB |
| 10 | ~60M | ~3 GB |
| 100 | ~600M | ~30 GB |

```sh
just generate 10          # Parquet in data/tpch_sf10
just local 1              # SF1 into data/tpch_sf1.duckdb with the semantic views
just local-query 1        # a sample semantic view query against it
```

`--chunks` generates in independent slices to bound memory (about one per 10 SF);
the slices together hold exactly the rows of a single-pass run, and generation
checks the row counts the TPC-H spec fixes for each table.

### Semantic views: one canonical definition

[`semantic/tpch.yaml`](semantic/tpch.yaml) defines two views, `tpch_sales`
(line-item grain: revenue, discount, quantity, supplier profit across order,
customer, part, supplier and both geographies) and `tpch_orders` (order grain).
The format is the intersection of what DuckDB, Snowflake and Databricks can all
express, and the comments at the top of the file spell out its rules.

`demo-stacks render` turns it into everything the stacks consume, committed under
[`generated/`](generated/):

| Output | For |
|---|---|
| `generated/duckdb/*.sql` | duckdb-semantic-views `CREATE SEMANTIC VIEW` (tables in `s3t.tpch_sf10`) |
| `generated/snowflake/*.sql` | Snowflake `CREATE SEMANTIC VIEW` over `SNOWFLAKE_SAMPLE_DATA.TPCH_SF10` |
| `generated/databricks/*.sql` | Databricks metric views (`WITH METRICS LANGUAGE YAML`) over `s_layer_demo.tpch_sf10` |
| `generated/semolina/tpch_models.py` | Semolina `SemanticView` models, the same on every stack |

Edit the YAML, run `just render`, commit both; CI fails if they drift.
For tables somewhere else, print the DDL directly:

```sh
just ddl databricks my_catalog.my_schema
```

The DuckDB output is tested end to end: the tests generate SF0.01, create the
views and check their numbers against hand-written TPC-H SQL (Q1, Q5- and Q9-shaped
queries, both nation roles) and through the generated Semolina models. The
Snowflake and Databricks outputs are checked for shape only until their stacks run
them.

## Cost model

Nothing bills while idle except stored bytes. Each stack splits into:

- a **data** root that stays up: storage only (for SF1 + SF10 + SF100 in S3 Tables,
  roughly $1/month);
- **compute** that exists only while a job runs: a one-shot EC2 runner that does
  one job (load data, run a benchmark), uploads its results and terminates itself,
  with a hard time limit as a backstop.
  On Databricks, compute is serverless warehouses that stop after a few idle minutes.

## Development

Everything runs through [`just`](https://just.systems/); `just` lists the recipes.
You need `uv`, `terraform`, `just`, [`prek`](https://github.com/j178/prek) for the
git hooks and, for the AWS recipes, `jq` and the AWS CLI.

```sh
just setup     # uv sync + prek install
just check     # what CI runs: lint, typecheck, render-check, test, tf-validate
just prek      # every pre-commit hook on every file
just fmt       # ruff + terraform fmt
just render    # rewrite generated/ after editing semantic/
```
