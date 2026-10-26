"""
The chat app for the browser test in chat/e2e: the scripted model, a fresh SF0.01 warehouse.

Run as ``python -m tests.chat_e2e_server PORT``; it serves chat/dist, so build that first.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import duckdb
import uvicorn

from demo_stacks import chat, cli, dataset, semantic
from demo_stacks.render import duckdb as render_duckdb
from tests.conftest import SF, load_semantic_views
from tests.test_chat import scripted


def warehouse(directory: Path) -> Path:
    dataset.generate(SF, directory / "tpch")
    path = directory / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    try:
        dataset.load(con, directory / "tpch", schema="tpch")
        load_semantic_views(con)
        for view in semantic.load().views:
            for stmt in render_duckdb.statements(view, schema="tpch"):
                con.execute(stmt)
    finally:
        con.close()
    return path


def main() -> None:
    port = int(sys.argv[1])
    with tempfile.TemporaryDirectory() as directory:
        app = chat.create_app(warehouse(Path(directory)), model=scripted(), static_dir=cli.CHAT_UI)
        uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
