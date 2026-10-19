# Recipes for the shared layer and the stacks. `just` lists them.

set shell := ["bash", "-euo", "pipefail", "-c"]

s3_stack := "stacks/duckdb-s3tables"
tf_roots := "stacks/duckdb-s3tables/data stacks/duckdb-s3tables/runner"

# List recipes
default:
    @just --list --unsorted

# --- development -----------------------------------------------------------------

# Install the package and dev tools
setup:
    uv sync

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
check: lint render-check test tf-validate

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
    terraform -chdir={{ s3_stack }}/data destroy

# Launch a runner that generates TPC-H at `sf` and loads it into the table bucket
s3-load sf="10" chunks="1" instance_type="r7i.2xlarge":
    just _s3-run "load-sf{{ sf }}-$(date -u +%Y%m%d-%H%M%S)" \
        "$(jq -cn --argjson sf {{ quote(sf) }} --argjson chunks {{ quote(chunks) }} --arg t {{ quote(instance_type) }} \
            '{job: "load", scale_factor: $sf, chunks: $chunks, instance_type: $t}')"

# Launch a runner that runs `command` in the repo checkout (results go in $RESULTS_DIR)
s3-run command instance_type="r7i.2xlarge":
    just _s3-run "run-$(date -u +%Y%m%d-%H%M%S)" \
        "$(jq -cn --arg c {{ quote(command) }} --arg t {{ quote(instance_type) }} '{job: "shell", command: $c, instance_type: $t}')"

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
