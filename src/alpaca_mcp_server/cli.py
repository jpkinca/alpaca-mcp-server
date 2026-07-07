"""
CLI entry point for the Alpaca MCP Server.
"""

import hmac
import os
import sys
from pathlib import Path
from typing import Optional

import click
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__


_HEALTH_BODY = b'{"status":"ok"}'
_HEALTH_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", b"15"),
]


class _BearerAuthMiddleware:
    """ASGI middleware that enforces bearer token auth on all HTTP requests.

    The ``/health`` path is exempt so Railway's healthcheck probe can reach
    it without credentials.

    Comparison uses ``hmac.compare_digest`` to prevent timing attacks.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/health":
                await send({"type": "http.response.start", "status": 200, "headers": _HEALTH_HEADERS})
                await send({"type": "http.response.body", "body": _HEALTH_BODY})
                return

            # Pass OAuth discovery probes through unauthenticated so MCP clients
            # receive a consistent 404 (not a 401 that blocks the auth handshake).
            if path.startswith("/.well-known/"):
                await self._app(scope, receive, send)
                return

            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"")
            if not hmac.compare_digest(auth, self._expected):
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"www-authenticate", b'Bearer realm="Alpaca MCP Server"'),
                        (b"content-type", b"application/json"),
                    ],
                })
                await send({"type": "http.response.body", "body": b'{"error":"Unauthorized"}'})
                return

        await self._app(scope, receive, send)


# Older Docker/Helm configs invoked `alpaca-mcp-server serve ...`; the CLI has no subcommands.
if len(sys.argv) > 1 and sys.argv[1] == "serve":
    sys.argv.pop(1)


def _default_port() -> int:
    """HTTP bind port; honors Render/Fly-style ``PORT`` when ``--port`` is omitted."""
    return int(os.environ.get("PORT", "8000"))


@click.command()
@click.version_option(version=__version__, prog_name="alpaca-mcp-server")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "streamable-http", "sse"]),
    default="stdio",
    help="Transport protocol (default: stdio)",
)
@click.option("--host", default="127.0.0.1", help="Host to bind (HTTP transport only)")
@click.option(
    "--port",
    type=int,
    default=_default_port,
    help="Port to bind (HTTP transport only; defaults to $PORT or 8000)",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Load environment variables from this file before starting",
)
def main(transport: str, host: str, port: int, env_file: Optional[Path]):
    """Alpaca MCP Server — Trading API integration for Model Context Protocol."""
    if env_file is not None:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)

    if not os.environ.get("ALPACA_API_KEY") or not os.environ.get("ALPACA_SECRET_KEY"):
        click.echo(
            "Error: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.\n"
            "Set them in your MCP client config's env block or pass --env-file.",
            err=True,
        )
        sys.exit(1)

    from .server import build_server

    server = build_server()

    if transport == "stdio":
        server.run(transport="stdio")
    else:
        mcp_token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if not mcp_token:
            click.echo(
                "Warning: MCP_AUTH_TOKEN is not set. The server is accessible without "
                "authentication. Set MCP_AUTH_TOKEN to secure this endpoint.",
                err=True,
            )
        import uvicorn
        asgi_app = server.http_app(transport=transport)
        if mcp_token:
            asgi_app = _BearerAuthMiddleware(asgi_app, mcp_token)
        uvicorn.run(asgi_app, host=host, port=port)
