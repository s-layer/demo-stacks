---
name: verify
description: Prove a change to demo-stacks works end to end. Use before saying a change is done, before committing, and before opening or updating a pull request.
---

# Verify a change

Run from the repository root:

```bash
uv sync --locked --dev
just check > /tmp/check.log 2>&1; echo "check rc=$?"
just chat-test > /tmp/chat.log 2>&1; echo "chat rc=$?"
```

It passes when both exit 0. `just check` runs lint, basedpyright, the render check,
pytest and `terraform validate`. `just chat-test` builds the chat page and runs the
Playwright test in Chromium. The tests install DuckDB's tpch and semantic_views
extensions on first use.

Take each command's exit status from the command itself. In a pipeline `$?` is the last
command's status, so `cmd | tail` hides a failure. Redirect long output to a file, then
read the file.

## What this covers

- the shared layer, generator and local DuckDB stack
- the chat page drawing a real chart in a browser

## What it does not cover

- the AWS and Databricks deployments: `terraform validate` only checks the configuration,
  nothing is applied

## When it fails

- Read the first failure and fix its cause. Do not rerun hoping for a green, and do not
  skip, loosen or delete the failing test to get one.
- If the failure is real but outside the change, say so in the PR and in your report
  rather than working around it.
