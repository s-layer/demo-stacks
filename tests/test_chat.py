"""The chat app: an agent run over AG-UI queries the views and the browser fetches the chart."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from starlette.testclient import TestClient

from demo_stacks import chat, cli

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from pydantic_ai import ModelMessage

QUERY = {"metrics": ["net_revenue"], "dimensions": ["customer_region", "order_year"]}
CHART = {
    "chart_type": "Grouped Bar Chart",
    "encodings": {"x": "order_year", "y": "net_revenue", "color": "customer_region"},
    "title": "Net revenue by region and year",
}


def scripted() -> FunctionModel:
    """A model that queries tpch_sales, charts the result, then says it is done."""

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        parts = [part for message in messages for part in message.parts]
        returns = [part for part in parts if isinstance(part, ToolReturnPart)]
        if not returns:
            yield {0: DeltaToolCall(name="query_tpch_sales", json_args=json.dumps(QUERY))}
        elif returns[-1].tool_name == "query_tpch_sales":
            summary = cast("dict[str, Any]", returns[-1].content)
            args = {"result_id": summary["result_id"], **CHART}
            yield {0: DeltaToolCall(name="show_chart", json_args=json.dumps(args))}
        else:
            yield "Here it is."

    return FunctionModel(stream_function=stream)


@pytest.fixture
def app(warehouse: Path) -> Iterator[TestClient]:
    with TestClient(chat.create_app(warehouse, model=scripted())) as client:
        yield client


def run(client: TestClient, text: str) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "threadId": "t1",
        "runId": "r1",
        "state": {},
        "messages": [{"id": "m1", "role": "user", "content": text}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    response = client.post("/agent", json=body, headers={"Accept": "text/event-stream"})
    assert response.status_code == 200
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def chart_receipt(events: list[dict[str, Any]]) -> dict[str, Any]:
    names = {
        event["toolCallId"]: event["toolCallName"]
        for event in events
        if event["type"] == "TOOL_CALL_START"
    }
    (result,) = [
        event
        for event in events
        if event["type"] == "TOOL_CALL_RESULT" and names[event["toolCallId"]] == "show_chart"
    ]
    return json.loads(result["content"])


def test_a_run_draws_a_chart_the_browser_can_fetch(app: TestClient) -> None:
    receipt = chart_receipt(run(app, "net revenue by region and year"))
    assert receipt["ok"]
    url = receipt["chart"]["data"]["url"]
    assert url == f"/flint/charts/{receipt['chart_id']}/rows"
    rows = app.get(url).json()
    assert len(rows) == receipt["rows_drawn"]
    assert {row["customer_region"] for row in rows} == {
        "AFRICA",
        "AMERICA",
        "ASIA",
        "EUROPE",
        "MIDDLE EAST",
    }


def test_another_browser_cannot_fetch_the_rows(app: TestClient) -> None:
    receipt = chart_receipt(run(app, "net revenue by region and year"))
    url = receipt["chart"]["data"]["url"]
    app.cookies.clear()
    assert app.get(url).status_code == 404


def test_the_session_cookie_is_set_once(app: TestClient) -> None:
    first = app.get("/flint/charts/nope/rows")
    assert first.status_code == 404
    cookie = first.cookies.get(chat.SESSION_COOKIE)
    assert cookie
    assert "HttpOnly" in first.headers["set-cookie"]
    again = app.get("/flint/charts/nope/rows")
    assert "set-cookie" not in again.headers


@pytest.mark.parametrize(
    "stray",
    [
        pytest.param("a@b=1", id="key SimpleCookie rejects"),
        pytest.param('prefs={"a":1}', id="unquoted JSON value"),
        pytest.param("foo", id="bare name"),
    ],
)
def test_another_apps_cookie_keeps_the_session(app: TestClient, stray: str) -> None:
    # Browsers send localhost cookies to every port, so other dev apps' cookies arrive too.
    session = app.get("/flint/charts/nope/rows").cookies[chat.SESSION_COOKIE]
    app.cookies.clear()
    header = f"{stray}; {chat.SESSION_COOKIE}={session}"
    response = app.get("/flint/charts/nope/rows", headers={"Cookie": header})
    assert response.status_code == 404
    assert "set-cookie" not in response.headers


def test_an_empty_session_cookie_is_replaced(app: TestClient) -> None:
    app.cookies.clear()
    response = app.get("/flint/charts/nope/rows", headers={"Cookie": f"{chat.SESSION_COOKIE}="})
    assert response.cookies[chat.SESSION_COOKIE]


def test_the_cli_names_the_same_default_model() -> None:
    assert cli._CHAT_MODEL == chat.DEFAULT_MODEL  # pyright: ignore[reportPrivateUsage]


def test_chat_fails_when_the_app_cannot_start(tmp_path: Path) -> None:
    missing = tmp_path / "missing.duckdb"
    with pytest.raises(SystemExit) as exit_:
        cli.main(["chat", "--database", str(missing), "--port", "0"])
    assert exit_.value.code not in (0, None)
