# Stack 1: semolina + duckdb-semantic-views on S3 Tables

TPC-H lives as Iceberg tables in an Amazon S3 Tables bucket, one namespace per
scale factor (`tpch_sf1`, `tpch_sf10`, `tpch_sf100`). DuckDB attaches the bucket
through its `iceberg` extension, the semantic views from `generated/duckdb/` sit
on top, and semolina queries them.

Two Terraform roots:

- [`data/`](data/) stays deployed: the table bucket, its namespaces, and an S3
  bucket for runner logs and results. It costs only storage.
- [`runner/`](runner/) is applied per job: one EC2 instance that clones this repo,
  runs one job, uploads `/data/results` to `s3://<results>/runs/<run_id>/`, and
  terminates itself (and after `max_lifetime_minutes` regardless). It uses the
  default VPC, so there is no NAT gateway, and has no inbound access; use SSM
  Session Manager to look inside while it runs.

## Load a scale factor

```sh
cd stacks/duckdb-s3tables
terraform -chdir=data init && terraform -chdir=data apply

terraform -chdir=runner init
terraform -chdir=runner apply \
  -var table_bucket_arn="$(terraform -chdir=data output -raw table_bucket_arn)" \
  -var results_bucket="$(terraform -chdir=data output -raw results_bucket)" \
  -var run_id=load-sf10 -var scale_factor=10

# The instance terminates when the job ends. Check the log, then tidy up:
aws s3 cp "s3://$(terraform -chdir=data output -raw results_bucket)/runs/load-sf10/runner.log" -
terraform -chdir=runner destroy -var ...   # same -var flags as apply
```

For SF100 use `-var scale_factor=100 -var chunks=10`. Runner state is local and
throwaway; the data root's state is worth keeping, so give it a backend before
sharing it.

## Querying the views

A DuckDB connection needs the bucket attached before any semantic view query,
because the views name `s3t.tpch_sf<N>.<table>` and `ATTACH` does not persist:

```python
import duckdb
from demo_stacks import s3tables

con = duckdb.connect("views.duckdb")
for stmt in s3tables.setup_sql(table_bucket_arn, region="eu-west-2"):
    con.execute(stmt)
con.execute("LOAD semantic_views")
con.sql(
    "FROM semantic_view('tpch_sales', dimensions := ['customer_region'], metrics := ['net_revenue'])"
)
```

`demo-stacks load` creates the views in the `--database` file (the runner keeps it
as `views.duckdb` in its results).

Semolina's DuckDB engine runs only `LOAD semantic_views` on each new pooled
connection, so it cannot attach the bucket yet; running semolina on this stack
needs a per-connection setup hook there first.

## Not yet verified against AWS

This has passed `terraform validate` and the local DuckDB tests (which stand an
attached DuckDB file in for the Iceberg catalog), but has not been applied. The
first real run will confirm DuckDB's S3 Tables `CREATE TABLE AS` path at scale and
the IAM actions the runner needs.
