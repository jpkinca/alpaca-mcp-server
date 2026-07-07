FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY src/ ./src/
COPY .github/core/ ./.github/core/

RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

# HTTP transport for remote MCP clients (e.g. ChatGPT). Bind all interfaces; use Render's $PORT via cli default.
CMD ["alpaca-mcp-server", "--transport", "streamable-http", "--host", "0.0.0.0"]
