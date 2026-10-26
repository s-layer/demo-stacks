"""
Run a benchmark on a stack 1 runner and bring the results home, from one command.

:func:`s3_bench` launches a runner with ``job = "bench"`` (through ``just _s3-run``,
which records the run and refuses an unpushed commit), follows its log as the
runner pushes it to the results bucket, waits for the instance to finish and
terminate, downloads ``runs/<run_id>/`` into ``results/remote/<run_id>/``, prints
the benchmark table, and destroys the runner's leftovers. The destroy also runs on
an error or Ctrl-C, and destroying the instance terminates it if it is still up.

Everything outside Python goes through a :class:`Shell`, so the flow can be checked
without AWS.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from demo_stacks import bench, semantic

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

RUNNER_ROOT = semantic.REPO_ROOT / "stacks" / "duckdb-s3tables" / "runner"
RESULTS_ROOT = semantic.REPO_ROOT / "results" / "remote"

# The instance states after which nothing more will be uploaded.
_GONE = frozenset({"shutting-down", "terminated", "stopping", "stopped"})

# Polls in a row that may fail (a network blip, expired credentials being refreshed)
# before following gives up. Giving up destroys the runner, so this errs long.
MAX_POLL_FAILURES = 6


class Shell(Protocol):
    """Runs a command; :class:`SubprocessShell` is the real one."""

    def run(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and capture its output, whatever its exit status."""
        ...

    def stream(self, args: Sequence[str], *, env: Mapping[str, str] | None = None) -> None:
        """Run ``args`` with its output going straight to the terminal; raise if it fails."""
        ...


class SubprocessShell:
    """A :class:`Shell` that runs commands in the repo root."""

    def run(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args), cwd=semantic.REPO_ROOT, capture_output=True, text=True, check=False
        )

    def stream(self, args: Sequence[str], *, env: Mapping[str, str] | None = None) -> None:
        proc = subprocess.Popen(
            list(args), cwd=semantic.REPO_ROOT, env={**os.environ, **(env or {})}
        )
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            # The child got the same Ctrl-C. Let it stop cleanly (Terraform releases its
            # state lock) before the cleanup runs its own Terraform.
            proc.wait()
            raise
        if code != 0:
            raise subprocess.CalledProcessError(code, list(args))


@dataclass(frozen=True)
class Run:
    """One launched runner: where it uploads, and which instance it is."""

    run_id: str
    region: str
    results_bucket: str
    instance_id: str

    @property
    def prefix(self) -> str:
        return f"s3://{self.results_bucket}/runs/{self.run_id}/"


def run_id(sf: float, now: datetime.datetime | None = None) -> str:
    """``bench-sf<N>-<UTC timestamp>``, within the runner's ``run_id`` rule."""
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime("%Y%m%d-%H%M%S")
    return f"bench-sf{f'{sf:g}'.replace('.', '-')}-{stamp}"


def launch(shell: Shell, run: str, sf: float, *, repeats: int, instance_type: str) -> Run:
    """Apply the runner for a ``bench`` job; returns the launched :class:`Run`."""
    job = {
        "job": "bench",
        "scale_factor": sf,
        "bench_repeats": repeats,
        "instance_type": instance_type,
    }
    # `just _s3-run` writes the run's variables and applies. Running this command is
    # the go-ahead, so the apply doesn't stop to ask again.
    shell.stream(
        ["just", "_s3-run", run, json.dumps(job)],
        env={"TF_CLI_ARGS_apply": "-auto-approve -input=false"},
    )
    tfvars = json.loads((RUNNER_ROOT / "run.auto.tfvars.json").read_text())
    instance_id = _check(
        shell.run(["terraform", f"-chdir={RUNNER_ROOT}", "output", "-raw", "instance_id"])
    )
    return Run(run, tfvars["region"], tfvars["results_bucket"], instance_id.strip())


def follow(
    shell: Shell,
    run: Run,
    *,
    poll: float = 10,
    echo: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """
    Print the runner's log as it grows, until the instance is gone; return the job's status.

    The runner pushes its log every 15 seconds and uploads everything, ``exit_status``
    included, before it powers off, so the run is over once the instance is shutting
    down. A runner that is gone without an ``exit_status`` hit its lifetime limit or
    died; that raises. So do :data:`MAX_POLL_FAILURES` failed state polls in a row;
    fewer are waited out.
    """
    printed = 0
    last_state = ""
    failures = 0
    while True:
        result = _instance_state(shell, run)
        if result.returncode != 0:
            failures += 1
            if failures >= MAX_POLL_FAILURES:
                _check(result)
            echo(f"could not read the runner's state ({result.stderr.strip()}); retrying")
            sleep(poll)
            continue
        failures = 0
        state = result.stdout.strip()
        gone = state in _GONE
        log = _s3_text(shell, run, "runner.log") or ""
        # While the runner is up, its last line may still be half written.
        end = len(log) if gone else log.rfind("\n", printed) + 1
        for line in log[printed:end].splitlines():
            echo(f"  | {line}")
        printed = max(printed, end)
        if state != last_state:
            echo(f"runner {run.instance_id} is {state}")
            last_state = state
        if gone:
            status = _s3_text(shell, run, "exit_status")
            if status is None:
                raise RuntimeError(
                    f"runner {run.instance_id} is {state} but uploaded no exit_status: "
                    f"it hit max_lifetime_minutes or died; see {run.prefix}runner.log"
                )
            return int(status.strip())
        sleep(poll)


def download(shell: Shell, run: Run, dest: Path) -> Path:
    """Copy the run's uploads into ``dest/<run_id>/``."""
    target = dest / run.run_id
    _check(
        shell.run(["aws", "s3", "cp", "--recursive", "--only-show-errors", run.prefix, str(target)])
    )
    return target


def destroy(shell: Shell) -> None:
    """Remove the runner's role and security group, and the instance if still up."""
    # Wait out a lock left by an apply that is still stopping after Ctrl-C.
    shell.stream(["just", "s3-runner-destroy"], env={"TF_CLI_ARGS_destroy": "-lock-timeout=5m"})


def s3_bench(
    sf: float,
    *,
    repeats: int = 10,
    instance_type: str = "r7i.2xlarge",
    shell: Shell | None = None,
    dest: Path = RESULTS_ROOT,
    poll: float = 10,
    echo: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """
    Benchmark stack 1 at ``sf`` on a runner, end to end; returns the job's exit status.

    The namespace ``tpch_sf<N>`` must already be loaded (``just s3-load``).
    """
    sh = shell or SubprocessShell()
    if (earlier := recorded_run()) is not None:
        raise SystemExit(f"runner {earlier} is still recorded; run 'just s3-runner-destroy' first")
    run = run_id(sf)
    echo(f"launching runner {run}: bench SF{sf:g}, {repeats} repeats, on {instance_type}")
    try:
        r = launch(sh, run, sf, repeats=repeats, instance_type=instance_type)
        echo(f"following {r.prefix}runner.log")
        status = follow(sh, r, poll=poll, echo=echo, sleep=sleep)
        local = download(sh, r, dest)
        echo(f"results in {local}")
        summaries = local / "bench.json"
        if status != 0:
            echo(f"the benchmark job exited with status {status}; see {local / 'runner.log'}")
        elif not summaries.exists():
            echo(f"the job succeeded but uploaded no bench.json; see {local / 'runner.log'}")
        else:
            for line in bench.summary_table(json.loads(summaries.read_text())):
                echo(line)
        return status
    finally:
        # `_s3-run` records the run before applying, so this also cleans up after an
        # apply that failed partway. A run recorded by someone else is left alone.
        if recorded_run() == run:
            echo("destroying the runner's leftovers")
            try:
                destroy(sh)
            except Exception as exc:  # noqa: BLE001 - the run's own outcome matters more
                # Raising here would hide the job's status or the error that got us here.
                echo(f"destroy failed ({exc}); run 'just s3-runner-destroy' to clean up")


def recorded_run() -> str | None:
    """The ``run_id`` that ``just _s3-run`` recorded and no destroy has cleared yet."""
    tfvars = RUNNER_ROOT / "run.auto.tfvars.json"
    if not tfvars.exists():
        return None
    return str(json.loads(tfvars.read_text())["run_id"])


def _instance_state(shell: Shell, run: Run) -> subprocess.CompletedProcess[str]:
    return shell.run(
        [
            "aws",
            "ec2",
            "describe-instances",
            "--region",
            run.region,
            "--instance-ids",
            run.instance_id,
            "--query",
            "Reservations[0].Instances[0].State.Name",
            "--output",
            "text",
        ]
    )


def _s3_text(shell: Shell, run: Run, name: str) -> str | None:
    """The object ``name`` under the run's prefix, or None while it doesn't exist."""
    result = shell.run(["aws", "s3", "cp", "--only-show-errors", f"{run.prefix}{name}", "-"])
    return result.stdout if result.returncode == 0 else None


def _check(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(result.args)} failed: {result.stderr.strip()}")
    return result.stdout
