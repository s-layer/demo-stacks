---
name: verify
description: Prove a change to demo-stacks works end to end. Use before saying a change is done, before committing, and before opening or updating a pull request.
---

# Verify a change

Run from the repository root:

```bash
uv sync --locked --dev
CI=true just check > /tmp/check.log 2>&1; echo "check rc=$?"
(cd chat && pnpm install --frozen-lockfile && pnpm exec playwright install chromium && pnpm build && pnpm test:browser --forbid-only) > /tmp/chat.log 2>&1; echo "chat rc=$?"
```

It passes when every command exits 0 and every printed `rc=` is 0. `just check` runs
lint, basedpyright, the render check, pytest, `terraform validate` and `terraform test`
(offline, with every provider mocked; it needs Terraform 1.7 or later). The second
command is `just chat-test` with CI's frozen lockfile and the Chromium install it needs,
plus `--forbid-only` so a leftover `test.only` fails as in CI: it builds the chat page
and runs the Playwright test. The tests install DuckDB's tpch and semantic_views
extensions on first use.

`CI=true` turns on `tests/ci_skips.py`, which fails the run on any skip not allowed
there, as CI does. Without it a skipped test only shows in the summary, and a run that
skipped what you changed is not a pass. It is set on each test line rather than
exported, so it still holds when each line runs in a fresh shell, and other tools that
read `CI` (Playwright, for one) keep their local behaviour.

Take each command's exit status from the command itself. In a pipeline `$?` is the last
command's status, so `cmd | tail` hides a failure. Redirect long output to a file, then
read the file.

## What this covers

- the shared layer, generator and local DuckDB stack
- the chat page drawing a real chart in a browser

## What it does not cover

- the AWS and Databricks deployments: `terraform validate` and the mocked `terraform test`
  only check the configuration and what it asks for; nothing is applied
- Python versions other than the one you ran: CI runs a version matrix

## When it fails

- Read the first failure and fix its cause. Do not rerun hoping for a green, and do not
  skip, loosen or delete the failing test to get one.
- If the failure is real but outside the change, say so in the PR and in your report
  rather than working around it.
