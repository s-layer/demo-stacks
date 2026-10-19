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
export TF_VAR_databricks_account_id=<account id>
```

```sh
just dbx-data-apply       # once: bucket, credential, catalog, schemas, loader
just dbx-stage 1          # generate SF1 locally if needed, upload to staging/tpch_sf1/
just dbx-load 1           # CTAS each table on the loader, check counts, create the views
```

`dbx-load` replaces every table from what is staged and then checks the row counts
against the TPC-H spec, so a partial upload fails loudly instead of skewing every
metric. `dbx-stage` syncs with `--delete`, so slices from an earlier run with more
chunks don't linger in staging.

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

Semolina can't target RT yet. `create_engine` accepts the ADBC `DatabricksConfig`
but not `DatabricksPythonConfig`, and RT rejects the ADBC driver's Thrift
connection. The Semolina models in `generated/semolina/` will work here unchanged
once semolina accepts that config.

## Not yet verified

Nothing in this stack has run against a workspace yet:

- the metric view YAML (nested joins, inlined facts, `MEASURE()` in derived
  measures);
- `CLUSTER BY AUTO` on CTAS;
- whether RT serves metric views over these tables as its docs say.

The tests check the SQL this stack sends and the shape of the DDL, not
Databricks' response to it.
