"""
The chat app for the browser test in chat/e2e: the scripted model, a fresh SF0.01 warehouse.

Run as ``python -m tests.chat_e2e_server PORT``; it serves chat/dist, so build that first.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import uvicorn

from demo_stacks import chat, cli, dataset, semantic
from tests.conftest import SF, build_warehouse
from tests.test_chat import scripted


def warehouse(directory: Path) -> Path:
    dataset.generate(SF, directory / "tpch")
    return build_warehouse(directory / "warehouse.duckdb", directory / "tpch", semantic.load())


def main() -> None:
    port = int(sys.argv[1])
    with tempfile.TemporaryDirectory() as directory:
        app = chat.create_app(warehouse(Path(directory)), model=scripted(), static_dir=cli.CHAT_UI)
        uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
