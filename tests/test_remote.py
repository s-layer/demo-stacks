"""The remote benchmark flow, with a fake shell standing in for just, Terraform and AWS."""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from demo_stacks import cli, remote

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SUMMARY: dict[str, object] = {
    "query": "region_year",
    "path": "sql",
    "rows": 35,
    "runs": 1,
    "median_ms": 12.5,
    "p95_ms": 14.0,
    "min_ms": 11.0,
    "stages_median_ms": {},
}


class FakeShell:
    """Plays a runner that logs in three steps, then uploads and terminates."""

    def __init__(
        self,
        runner_root: Path,
        *,
        states: Sequence[str] = ("pending", "running", "running", "terminated"),
        logs: Sequence[str] = ("", "booting\n", "booting\nbenching\n", "booting\nbenching\ndone\n"),
        exit_status: str | None = "0\n",
        fail_apply: bool = False,
        uploads_bench: bool = True,
    ) -> None:
        self.root = runner_root
        self.states = list(states)
        self.logs = list(logs)
        self.exit_status = exit_status
        self.fail_apply = fail_apply
        self.uploads_bench = uploads_bench
        self.streamed: list[tuple[list[str], dict[str, str]]] = []
        self.ran: list[list[str]] = []

    def stream(self, args: Sequence[str], *, env: Mapping[str, str] | None = None) -> None:
        self.streamed.append((list(args), dict(env or {})))
        tfvars = self.root / "run.auto.tfvars.json"
        if args[:2] == ["just", "_s3-run"]:
            tfvars.write_text(
                json.dumps({"run_id": args[2], "region": "eu-west-2", "results_bucket": "res"})
            )
            if self.fail_apply:
                raise subprocess.CalledProcessError(1, list(args))
        elif list(args) == ["just", "s3-runner-destroy"]:
            tfvars.unlink(missing_ok=True)

    def run(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        a = list(args)
        self.ran.append(a)
        if a[0] == "terraform":
            return self._ok(a, "i-0abc\n")
        if a[:3] == ["aws", "ec2", "describe-instances"]:
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return self._ok(a, f"{state}\n")
        if a[:3] == ["aws", "s3", "cp"] and a[-1] == "-":
            if a[-2].endswith("runner.log"):
                log = self.logs.pop(0) if len(self.logs) > 1 else self.logs[0]
                return self._ok(a, log) if log else self._missing(a)
            if a[-2].endswith("exit_status"):
                return self._ok(a, self.exit_status) if self.exit_status else self._missing(a)
        if a[:4] == ["aws", "s3", "cp", "--recursive"]:
            target = Path(a[-1])
            target.mkdir(parents=True)
            if self.uploads_bench:
                (target / "bench.json").write_text(json.dumps([SUMMARY]))
            return self._ok(a, "")
        raise AssertionError(f"unexpected command {a}")

    @staticmethod
    def _ok(args: list[str], out: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, out, "")

    @staticmethod
    def _missing(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "does not exist")


@pytest.fixture
def runner_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runner"
    root.mkdir()
    monkeypatch.setattr(remote, "RUNNER_ROOT", root)
    return root


def _bench(shell: FakeShell, tmp_path: Path, lines: list[str]) -> int:
    return remote.s3_bench(
        10,
        repeats=3,
        shell=shell,
        dest=tmp_path / "results",
        poll=0,
        echo=lines.append,
        sleep=lambda _: None,
    )


def test_run_id_fits_the_runner_rule() -> None:
    now = datetime.datetime(2026, 10, 26, 9, 5, 3, tzinfo=datetime.UTC)
    assert remote.run_id(10, now) == "bench-sf10-20261026-090503"
    assert remote.run_id(0.01, now) == "bench-sf0-01-20261026-090503"


def test_s3_bench_end_to_end(runner_root: Path, tmp_path: Path) -> None:
    shell = FakeShell(runner_root)
    lines: list[str] = []
    assert _bench(shell, tmp_path, lines) == 0

    (launch_args, launch_env), destroy_call = shell.streamed
    assert launch_args[:2] == ["just", "_s3-run"]
    assert json.loads(launch_args[3]) == {
        "job": "bench",
        "scale_factor": 10,
        "bench_repeats": 3,
        "instance_type": "r7i.2xlarge",
    }
    assert launch_env == {"TF_CLI_ARGS_apply": "-auto-approve -input=false"}
    assert destroy_call == (["just", "s3-runner-destroy"], {})
    assert not (runner_root / "run.auto.tfvars.json").exists()

    log = [ln for ln in lines if ln.startswith("  | ")]
    assert log == ["  | booting", "  | benching", "  | done"], "each line printed once"
    assert "runner i-0abc is running" in lines
    assert "runner i-0abc is terminated" in lines
    run = launch_args[2]
    assert (tmp_path / "results" / run / "bench.json").exists()
    assert any(ln.startswith("region_year   sql") for ln in lines), "the table is printed"


def test_s3_bench_reports_a_failed_job(runner_root: Path, tmp_path: Path) -> None:
    shell = FakeShell(runner_root, exit_status="3\n")
    lines: list[str] = []
    assert _bench(shell, tmp_path, lines) == 3
    assert any("exited with status 3" in ln for ln in lines)
    assert not any(ln.startswith("region_year") for ln in lines)
    assert shell.streamed[-1][0] == ["just", "s3-runner-destroy"]


def test_s3_bench_says_when_a_job_uploads_no_results(runner_root: Path, tmp_path: Path) -> None:
    lines: list[str] = []
    assert _bench(FakeShell(runner_root, uploads_bench=False), tmp_path, lines) == 0
    assert any("uploaded no bench.json" in ln for ln in lines)


def test_s3_bench_fails_when_the_runner_dies_without_a_status(
    runner_root: Path, tmp_path: Path
) -> None:
    shell = FakeShell(runner_root, exit_status=None)
    with pytest.raises(RuntimeError, match="uploaded no exit_status"):
        _bench(shell, tmp_path, [])
    assert shell.streamed[-1][0] == ["just", "s3-runner-destroy"]


def test_s3_bench_cleans_up_after_a_failed_apply(runner_root: Path, tmp_path: Path) -> None:
    shell = FakeShell(runner_root, fail_apply=True)
    with pytest.raises(subprocess.CalledProcessError):
        _bench(shell, tmp_path, [])
    assert shell.streamed[-1][0] == ["just", "s3-runner-destroy"]
    assert not (runner_root / "run.auto.tfvars.json").exists()


def test_s3_bench_leaves_an_earlier_runner_alone(runner_root: Path, tmp_path: Path) -> None:
    tfvars = runner_root / "run.auto.tfvars.json"
    tfvars.write_text(json.dumps({"run_id": "load-sf10-earlier"}))
    shell = FakeShell(runner_root)
    with pytest.raises(SystemExit, match="runner load-sf10-earlier is still recorded"):
        _bench(shell, tmp_path, [])
    assert shell.streamed == []
    assert tfvars.exists()


def test_cli_s3_bench_passes_its_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[float, int, str]] = []

    def s3_bench(sf: float, *, repeats: int, instance_type: str) -> int:
        seen.append((sf, repeats, instance_type))
        return 0

    monkeypatch.setattr(remote, "s3_bench", s3_bench)
    argv = ["s3-bench", "--sf", "100", "--repeats", "5", "--instance-type", "c7i.4xlarge"]
    assert cli.main(argv) == 0
    assert seen == [(100.0, 5, "c7i.4xlarge")]


def test_cli_s3_bench_rejects_no_repeats() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["s3-bench", "--sf", "10", "--repeats", "0"])
    assert exc.value.code == 2
