# Recipes for the shared layer and the stacks. `just` lists them.

set shell := ["bash", "-euo", "pipefail", "-c"]

s3_stack := "stacks/duckdb-s3tables"
dbx_stack := "stacks/databricks-rt"
tf_roots := "stacks/duckdb-s3tables/data stacks/duckdb-s3tables/runner stacks/databricks-rt/data"

# List recipes
default:
    @just --list --unsorted

# --- development -----------------------------------------------------------------

# Install the package and dev tools, and the prek git hooks
setup:
    uv sync
    command -v prek >/dev/null && prek install || echo "prek not found; skipping git hooks (uv tool install prek)"

# Run every pre-commit hook on every file
prek:
    prek run --all-files

# Format Python and Terraform
fmt:
    uv run ruff format .
    uv run ruff check --fix .
    terraform fmt -recursive stacks

# Lint Python and Terraform without changing anything
lint:
    uv run ruff format --check .
    uv run ruff check .
    terraform fmt -check -recursive stacks

# Type-check with basedpyright (strict)
typecheck:
    uv run basedpyright

# Run the test suite (pass pytest arguments through, e.g. `just test -k render`)
test *args:
    uv run pytest {{ args }}

# Rewrite generated/ from semantic/
render:
    uv run demo-stacks render

# Fail if generated/ is out of date with semantic/
render-check:
    uv run demo-stacks render --check

# terraform init (no backend) and validate every root
tf-validate:
    for root in {{ tf_roots }}; do \
        terraform -chdir="$root" init -backend=false -input=false >/dev/null; \
        terraform -chdir="$root" validate; \
    done

# Everything CI runs
check: lint typecheck render-check test tf-validate

# --- shared layer ----------------------------------------------------------------

# Print one target's view DDL for tables in `schema` (target: duckdb, snowflake, databricks)
ddl target schema="":
    uv run demo-stacks ddl {{ target }} {{ if schema == "" { "" } else { "--schema " + schema } }}

# Generate TPC-H Parquet at scale factor `sf` into data/tpch_sf<sf>
generate sf="1" chunks="1":
    uv run demo-stacks generate --sf {{ sf }} --chunks {{ chunks }} --out data/tpch_sf{{ sf }}

# Load data/tpch_sf<sf> into a local DuckDB file with the semantic views (generates first if missing)
local sf="1" chunks="1":
    [ -d data/tpch_sf{{ sf }} ] || just generate {{ sf }} {{ chunks }}
    uv run demo-stacks load --parquet data/tpch_sf{{ sf }} --database data/tpch_sf{{ sf }}.duckdb

# Run a sample semantic view query against the local DuckDB file for scale factor `sf`
local-query sf="1":
    @uv run python -c "import duckdb, sys; con = duckdb.connect('data/tpch_sf{{ sf }}.duckdb'); con.execute('LOAD semantic_views'); print(con.sql(sys.argv[1]))" \
        "FROM semantic_view('tpch_sales', dimensions := ['customer_region', 'order_year'], metrics := ['net_revenue', 'profit']) ORDER BY ALL"

# Time direct SQL, semolina Arrow and semolina JSON on data/tpch_sf<sf>.duckdb (loads it first if missing)
bench-local sf="1" repeats="10":
    [ -f data/tpch_sf{{ sf }}.duckdb ] || just local {{ sf }}
    uv run demo-stacks bench --database data/tpch_sf{{ sf }}.duckdb --repeats {{ repeats }} \
        --out results/bench/duckdb-local-sf{{ sf }}.json

# --- stack 1: duckdb-semantic-views on S3 Tables --------------------------------
# These create real AWS resources and use your current AWS credentials.

# Plan the long-lived data root (table bucket, namespaces, results bucket)
s3-data-plan:
    terraform -chdir={{ s3_stack }}/data init -input=false >/dev/null
    terraform -chdir={{ s3_stack }}/data plan

# Create or update the long-lived data root
s3-data-apply:
    terraform -chdir={{ s3_stack }}/data init -input=false >/dev/null
    terraform -chdir={{ s3_stack }}/data apply

# Destroy the data root, including all loaded tables and results
s3-data-destroy:
    #!/usr/bin/env bash
    set -euo pipefail
    # DuckDB creates the Iceberg tables outside Terraform, and S3 Tables will not
    # delete a namespace that still holds tables, so drop them first.
    data() { terraform -chdir={{ s3_stack }}/data output -raw "$1"; }
    arn=$(data table_bucket_arn); region=$(data region)
    read -r -p "Delete every table in $arn, then destroy the data root? Type yes: " answer
    [ "$answer" = yes ] || { echo "aborted"; exit 1; }
    s3t() { aws s3tables --region "$region" "$@"; }
    for ns in $(s3t list-namespaces --table-bucket-arn "$arn" --query 'namespaces[].namespace[0]' --output text); do
        for t in $(s3t list-tables --table-bucket-arn "$arn" --namespace "$ns" --query 'tables[].name' --output text); do
            echo "deleting $ns.$t"
            s3t delete-table --table-bucket-arn "$arn" --namespace "$ns" --name "$t"
        done
        # Covers namespaces `load` created outside var.scale_factors, which Terraform
        # doesn't know about; refresh drops the managed ones that are already gone.
        echo "deleting namespace $ns"
        s3t delete-namespace --table-bucket-arn "$arn" --namespace "$ns"
    done
    terraform -chdir={{ s3_stack }}/data destroy -auto-approve

# Launch a runner that generates TPC-H at `sf` and loads it into the table bucket
s3-load sf="10" chunks="1" instance_type="r7i.2xlarge":
    just _s3-run "load-sf{{ sf }}-$(date -u +%Y%m%d-%H%M%S)" \
        "$(jq -cn --argjson sf {{ quote(sf) }} --argjson chunks {{ quote(chunks) }} --arg t {{ quote(instance_type) }} \
            '{job: "load", scale_factor: $sf, chunks: $chunks, instance_type: $t}')"

# Launch a runner that runs `command` in the repo checkout (results go in $RESULTS_DIR)
s3-run command instance_type="r7i.2xlarge":
    just _s3-run "run-$(date -u +%Y%m%d-%H%M%S)" \
        "$(jq -cn --arg c {{ quote(command) }} --arg t {{ quote(instance_type) }} '{job: "shell", command: $c, instance_type: $t}')"

# Benchmark tpch_sf<sf> on a runner: launch, follow its log, fetch results to results/remote/, clean up
s3-bench sf="10" repeats="10" instance_type="r7i.2xlarge":
    uv run demo-stacks s3-bench --sf {{ sf }} --repeats {{ repeats }} --instance-type {{ quote(instance_type) }}

# Show the current runner's state: instance status, then its log once uploaded
s3-runner-status:
    #!/usr/bin/env bash
    set -euo pipefail
    vars={{ s3_stack }}/runner/run.auto.tfvars.json
    [ -f "$vars" ] || { echo "no runner launched"; exit 1; }
    run_id=$(jq -r .run_id "$vars"); bucket=$(jq -r .results_bucket "$vars")
    id=$(terraform -chdir={{ s3_stack }}/runner output -raw instance_id)
    echo "run $run_id: instance $id is $(aws ec2 describe-instances --instance-ids "$id" \
        --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo gone)"
    aws s3 cp "s3://$bucket/runs/$run_id/runner.log" - 2>/dev/null || echo "(no log uploaded yet)"

# Destroy the current runner's leftovers (IAM role, security group; the instance self-terminates)
s3-runner-destroy:
    terraform -chdir={{ s3_stack }}/runner destroy -auto-approve
    rm -f {{ s3_stack }}/runner/run.auto.tfvars.json

# Write the runner's variables (data root outputs + `job_json`), then apply
_s3-run run_id job_json:
    #!/usr/bin/env bash
    set -euo pipefail
    vars={{ s3_stack }}/runner/run.auto.tfvars.json
    if [ -f "$vars" ]; then
        echo "runner $(jq -r .run_id "$vars") is still recorded; run 'just s3-runner-destroy' first" >&2
        exit 1
    fi
    trap 'rm -f "$vars.tmp"' EXIT
    data() { terraform -chdir={{ s3_stack }}/data output -raw "$1"; }
    ref=$(git rev-parse HEAD)
    if ! git branch -r --contains "$ref" | grep -q .; then
        echo "commit $ref is not pushed; the runner clones from GitHub" >&2
        exit 1
    fi
    jq -n \
        --arg region "$(data region)" \
        --arg arn "$(data table_bucket_arn)" \
        --arg results "$(data results_bucket)" \
        --arg run_id {{ quote(run_id) }} \
        --arg ref "$ref" \
        --argjson job {{ quote(job_json) }} \
        '{region: $region, table_bucket_arn: $arn, results_bucket: $results,
          run_id: $run_id, repo_ref: $ref} + $job' > "$vars.tmp"
    mv "$vars.tmp" "$vars"
    terraform -chdir={{ s3_stack }}/runner init -input=false >/dev/null
    terraform -chdir={{ s3_stack }}/runner apply
    echo "launched {{ run_id }}; follow it with 'just s3-runner-status'"

# --- stack 2: Databricks Lakehouse//RT -------------------------------------------
# These create real AWS and Databricks resources. They use your current AWS
# credentials and DATABRICKS_HOST plus DATABRICKS_TOKEN (or browser OAuth for queries).

# Plan the long-lived data root (bucket, storage credential, catalog, schemas, loader warehouse)
dbx-data-plan:
    terraform -chdir={{ dbx_stack }}/data init -input=false >/dev/null
    terraform -chdir={{ dbx_stack }}/data plan

# Create or update the data root
dbx-data-apply:
    terraform -chdir={{ dbx_stack }}/data init -input=false >/dev/null
    terraform -chdir={{ dbx_stack }}/data apply

# Destroy the data root, including the catalog's tables and the bucket
dbx-data-destroy:
    terraform -chdir={{ dbx_stack }}/data destroy

# Upload data/tpch_sf<sf> to the staging prefix (generates first if missing)
dbx-stage sf="1" chunks="1":
    [ -d data/tpch_sf{{ sf }} ] || just generate {{ sf }} {{ chunks }}
    aws s3 sync --delete data/tpch_sf{{ sf }} \
        "$(terraform -chdir={{ dbx_stack }}/data output -raw staging_uri)/tpch_sf{{ sf }}/"

# Load staged scale factor `sf` into the catalog and create its metric views, on the loader warehouse
dbx-load sf="1":
    uv run demo-stacks dbx-load --sf {{ sf }} \
        --staging "$(terraform -chdir={{ dbx_stack }}/data output -raw staging_uri)" \
        --catalog "$(terraform -chdir={{ dbx_stack }}/data output -raw catalog)" \
        --http-path "$(terraform -chdir={{ dbx_stack }}/data output -raw loader_http_path)"

# Run the sample metric view query (or `sql`) on the warehouse at `http_path`, e.g. the RT one
dbx-query http_path sf="1" sql="":
    uv run demo-stacks dbx-query --sf {{ sf }} --http-path {{ quote(http_path) }} \
        --catalog "$(terraform -chdir={{ dbx_stack }}/data output -raw catalog)" \
        {{ if sql == "" { "" } else { "--sql " + quote(sql) } }}

# Run the sample query through the semolina models on the warehouse at `http_path`
dbx-semolina http_path sf="1":
    uv run demo-stacks dbx-query --semolina --sf {{ sf }} --http-path {{ quote(http_path) }} \
        --catalog "$(terraform -chdir={{ dbx_stack }}/data output -raw catalog)"

# Time direct SQL, semolina Arrow and semolina JSON on the warehouse at `http_path`, result cache off
dbx-bench http_path sf="1" repeats="10":
    uv run demo-stacks bench --sf {{ sf }} --http-path {{ quote(http_path) }} --repeats {{ repeats }} \
        --catalog "$(terraform -chdir={{ dbx_stack }}/data output -raw catalog)" \
        --out results/bench/databricks-sf{{ sf }}.json
