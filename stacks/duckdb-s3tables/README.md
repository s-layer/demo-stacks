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

From the repo root, with AWS credentials in your environment:

```sh
just s3-data-apply            # once: table bucket, namespaces, results bucket
just s3-load 10               # launch a runner that loads SF10 into tpch_sf10
just s3-runner-status         # instance state, then the job log so far
just s3-runner-destroy        # after it finishes: remove the role and security group
```

For SF100 use `just s3-load 100 10` (ten chunks). `just s3-run '<command>'` runs any
command on a runner instead, with `$RESULTS_DIR` uploaded at the end. The runner
clones the commit you have checked out, so push it first; the recipe refuses an
unpushed commit. One runner at a time: its variables live in
`runner/run.auto.tfvars.json` (git-ignored) until `s3-runner-destroy`.

Runner state is local and throwaway. The data root's state is worth keeping, so
give it a backend before sharing it.

## Benchmark

```sh
just s3-bench 10              # benchmark tpch_sf10 (load it first with s3-load)
just s3-bench 100 20 r7i.4xlarge   # SF100, 20 repeats, a bigger runner
```

One command does the whole round trip:

1. launches a runner with `job = "bench"` for the commit you have checked out
   (pushed), without a second confirmation: running the recipe is the go-ahead;
2. prints the runner's log as it goes (the runner pushes it to the results bucket
   every 15 seconds) and its instance state;
3. once the instance has uploaded and terminated, copies `runs/<run_id>/` to
   `results/remote/<run_id>/` (`bench.json`, `runner.log`, `views.duckdb`) and prints
   the benchmark table;
4. destroys the runner's role and security group, which it also does on an error or
   Ctrl-C (destroying a running instance terminates it).

On the runner, the job creates the views in a fresh `views.duckdb` over the
namespace and runs `demo-stacks bench` against them through the attached bucket,
so the timings include S3 Tables reads from inside the region.

## Querying the views

A DuckDB process needs the bucket attached before any semantic view query, because
the views name `s3t.tpch_sf<N>.<table>` and `ATTACH` is not saved in the database
file:

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

With semolina, run the same statements once after building the engine and commit
them. Every pooled connection opens the same DuckDB database, and extensions, secrets
and attached catalogs belong to the database, so one run covers the whole pool:

```python
from adbc_poolhouse import DuckDBConfig
from semolina import create_engine
from demo_stacks import s3tables

engine = create_engine(DuckDBConfig(database="views.duckdb"), register=True)
with engine.connect() as conn:
    with conn.cursor() as cur:
        for stmt in s3tables.setup_sql(table_bucket_arn, region="eu-west-2"):
            cur.execute(stmt)
    conn.commit()  # without this, the pool's rollback undoes the ATTACH
```

Semolina loads `semantic_views` itself. See semolina's backends how-to, "Attach
catalogs and create secrets at startup".

## Not yet verified against AWS

This has passed `terraform validate` and the local DuckDB tests (which stand an
attached DuckDB file in for the Iceberg catalog), but has not been applied. The
first real run will confirm DuckDB's S3 Tables `CREATE TABLE AS` path at scale and
the IAM actions the runner needs.
