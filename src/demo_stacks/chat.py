"""
A chat app over the TPC-H views: an agent that queries them and draws Flint charts.

The agent is a Pydantic AI agent with s-layer-flint-charts' toolset, served over AG-UI at
``POST /agent``. The browser fetches each chart's rows from ``/flint``, so both routes tell
conversations apart by the same session cookie. The browser half, in ``chat/``, is served from
``/`` once built. Needs the ``chat`` extra.
"""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import semolina
from adbc_poolhouse import DuckDBConfig
from pydantic_ai import Agent, RunContext
from pydantic_ai.ui.ag_ui import AGUIAdapter
from s_layer_flint_charts import ChartStore
from s_layer_flint_charts.pydantic_ai import chart_toolset
from s_layer_flint_charts.server import chart_routes
from semolina import SemanticView
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from demo_stacks import cli, semantic

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from pydantic_ai.models import Model
    from s_layer_flint_charts import ChartValidator
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
"""The model the agent runs on, unless ``DEMO_CHAT_MODEL`` or ``--model`` names another."""

SESSION_COOKIE = "demo_chat_session"

INSTRUCTIONS = """\
You answer questions about TPC-H sales and orders. Query the semantic views with the query
tools, then draw what you found with show_chart. Prefer one clear chart to several; keep the
dimensions to what the chart can place on its channels. Say in a sentence or two what the chart
shows, and don't repeat its numbers back as a table."""


@dataclass
class Conversation:
    """What a run knows about who is asking: the browser session its charts belong to."""

    session_id: str


def session_of(request: Request) -> str:
    """The browser session a request belongs to, as :class:`SessionCookie` set it."""
    return request.scope["state"][SESSION_COOKIE]


def create_app(
    database: Path,
    *,
    definitions: semantic.Definitions | None = None,
    model: Model | str | None = None,
    validator: ChartValidator | None = None,
    static_dir: Path | None = None,
) -> Starlette:
    """
    The chat app over a DuckDB file that ``demo-stacks load`` wrote.

    Args:
        database: The DuckDB file with the tables and semantic views.
        definitions: The definitions whose generated semolina models the agent queries, one
            query tool per view. Default: ``semantic/tpch.yaml``.
        model: The model the agent runs on. Default: ``DEMO_CHAT_MODEL``, else
            :data:`DEFAULT_MODEL`.
        validator: Checks each chart with Flint itself, such as a started ``FlintSidecar``.
        static_dir: The built browser app to serve from ``/``, if any.

    Returns:
        The ASGI app.
    """
    models = cli.load_models(definitions)
    views = [
        value
        for value in vars(models).values()
        if isinstance(value, type) and issubclass(value, SemanticView) and value is not SemanticView
    ]
    store = ChartStore()

    def conversation_id(ctx: RunContext[Conversation]) -> str:
        return ctx.deps.session_id

    agent = Agent(
        model or os.environ.get("DEMO_CHAT_MODEL", DEFAULT_MODEL),
        name="tpch_chat_agent",
        deps_type=Conversation,
        instructions=INSTRUCTIONS,
        toolsets=[
            chart_toolset(
                views,
                store=store,
                session_id=conversation_id,
                validator=validator,
            )
        ],
        defer_model_check=True,
    )

    async def run_agent(request: Request) -> Response:
        return await AGUIAdapter.dispatch_request(
            request, agent=agent, deps=Conversation(session_of(request))
        )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        config = DuckDBConfig(database=str(database), read_only=True)
        async with semolina.create_async_engine(config, register=True):
            yield

    routes: list[Route | Mount] = [
        Route("/agent", run_agent, methods=["POST"]),
        Mount("/flint", routes=chart_routes(store, session_of)),
    ]
    if static_dir is not None:
        routes.append(Mount("/", StaticFiles(directory=static_dir, html=True)))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(SessionCookie)
    return app


class SessionCookie:
    """
    Give every browser a random session id in a cookie, and every request its session.

    The agent keeps a browser's results and charts under this id, and the rows route answers
    only the browser that holds it. It is a plain ASGI middleware so the agent's streamed
    responses pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Lenient on purpose: browsers send every localhost cookie to every port, and other
        # apps' cookies need not be ones http.cookies would accept.
        known = cookie_parser(Headers(scope=scope).get("cookie", "")).get(SESSION_COOKIE) or None
        session_id = known or secrets.token_urlsafe(24)
        scope.setdefault("state", {})[SESSION_COOKIE] = session_id

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start" and known is None:
                cookie = f"{SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Lax"
                message["headers"] = [*message.get("headers", []), (b"set-cookie", cookie.encode())]
            await send(message)

        await self.app(scope, receive, send_with_cookie)
