from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from app.core.search import search_context as unified_search_context


mcp = MCPServer("AI-Hub")


@mcp.tool()
def health_check() -> dict:
    """Check whether the AI-Hub MCP server is alive."""
    return {
        "status": "ok",
        "service": "ai-hub-mcp",
    }


@mcp.tool()
def search_context(
    query: str,
    limit: int = 5,
    project_id: int | None = None,
) -> dict:
    """
    Search AI-Hub memories and indexed documents together.

    Use this when the user asks about project knowledge,
    source code, lecture materials, notes, or previously stored context.
    """
    if not query.strip():
        raise ValueError("query must not be empty")

    limit = max(1, min(limit, 20))

    return unified_search_context(
        query=query,
        limit=limit,
        project_id=project_id,
    )


security = TransportSecuritySettings(
    allowed_hosts=[
        "mcp:*",
        "localhost:*",
        "127.0.0.1:*",
    ],
)


app = mcp.streamable_http_app(
    transport_security=security,
)