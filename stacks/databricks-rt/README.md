# Stack 2: semolina + Databricks metric views on Lakehouse//RT

TPC-H lives as Unity Catalog managed Delta tables, one schema per scale factor
(`s_layer_demo.tpch_sf1`, `s_layer_demo.tpch_sf10`). The metric views from
`generated/databricks/` sit on top of them, and the queries run on a
[Lakehouse//RT](https://docs.databricks.com/aws/en/compute/sql-warehouse/real-time)
warehouse, Databricks' low-latency serving warehouse type.

RT shapes the stack in three ways:

- **It is read-only.** It runs no DDL, CTAS or inserts. Loading and creating the
  metric views happen on a small serverless SQL warehouse (the *loader*), which
  stops after a few idle minutes.
- **It reads only Unity Catalog managed tables, and not ones in the workspace's
  default storage.** So the catalog gets its own managed location in an S3 bucket
  this stack owns.
- **It accepts only the Statement Execution API**, not Thrift. Everything here
  connects through adbc-poolhouse's `DatabricksPythonConfig`
  (`databricks-sql-connector` with `use_kernel=True`), not the ADBC driver.

## What Terraform creates

[`data/`](data/) is the long-lived root, in an existing workspace:

- an S3 bucket with `managed/` (the catalog's storage) and `staging/` (Parquet on
  its way in, expired after `staging_retention_days`);
- the IAM role, storage credential and external location Unity Catalog needs to
  use the bucket;
- the `s_layer_demo` catalog and a `tpch_sf<N>` schema per entry in `scale_factors`;
- the serverless loader warehouse.

Nothing in it bills while idle beyond stored bytes.

The RT warehouse is **not** created here. RT is in beta: your Databricks account
team has to enable it for the workspace, and there is no documented API or
Terraform support for the warehouse type yet. Create it in the UI
(**Compute > SQL Warehouses > Create > Real-Time**), set a short auto stop, and
copy its HTTP path from **Connection details**.

## Load a scale factor

From the repo root, with AWS credentials in your environment and:

```sh
export DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
export DATABRICKS_TOKEN=dapi...                  # or omit it for browser OAuth
```

```sh
just dbx-data-apply       # once: bucket, credential, catalog, schemas, loader
just dbx-stage 1          # generate SF1 locally if needed, upload to staging/tpch_sf1/
just dbx-load 1           # CTAS each table on the loader, check counts, create the views
```

`dbx-load` loads every table under a side name (`<table>__load`) and checks the
row counts against the TPC-H spec, including that lineitem covers every order. Only
then does it swap them in for the live tables the metric views read, so a partial or
expired staging upload fails loudly and leaves the served data as it was. Each swap
is a single `CREATE OR REPLACE ... DEEP CLONE`, so RT never sees a table missing.
`dbx-stage` syncs with `--delete`, so slices from an earlier run with more chunks
don't linger in staging.

Staging uploads from your machine, which is fine for SF1 and SF10. For SF100 and
up, generate and upload from AWS, e.g. with a stack 1 runner job.

## Querying

```sh
just dbx-query /sql/1.0/warehouses/<rt-id> 1            # sample metric view query on RT
just dbx-query /sql/1.0/warehouses/<rt-id> 1 "SELECT 1"
```

The sample query reads `tpch_sales` with `MEASURE()`:

```sql
SELECT customer_region, order_year,
       MEASURE(net_revenue) AS net_revenue, MEASURE(profit) AS profit
FROM s_layer_demo.tpch_sf1.tpch_sales
GROUP BY ALL ORDER BY ALL
```

The same query through the generated semolina models in `generated/semolina/`:

```sh
just dbx-semolina /sql/1.0/warehouses/<rt-id> 1
```

```python
from demo_stacks import cli, databricks as dbx

models = cli.load_models()
with dbx.semolina_engine("/sql/1.0/warehouses/<rt-id>", "s_layer_demo", 1):
    sales = models.TpchSales
    query = sales.query().metrics(sales.net_revenue).dimensions(sales.customer_region)
    with query.execute() as result:
        rows = result.fetchall_rows()
```

`semolina_engine` builds a `DatabricksPythonConfig` whose default catalog and schema
are `s_layer_demo.tpch_sf<N>`, so the models' unqualified view names resolve there.
Semolina supports this config from 0.9.0, the floor in `pyproject.toml`.

## Not yet verified

Nothing in this stack has run against a workspace yet:

- the metric view YAML (nested joins, inlined facts, `MEASURE()` in derived
  measures);
- `CLUSTER BY AUTO` on CTAS, and that `DEEP CLONE` carries it to the live table;
- whether RT serves metric views over these tables as its docs say;
- semolina's queries on RT. Until adbc-poolhouse translates this connector's
  errors, a failure shows up as the raw `databricks-sql-connector` error.

The tests check the SQL this stack sends and the shape of the DDL, not
Databricks' response to it.
