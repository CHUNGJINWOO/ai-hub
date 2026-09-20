import os
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

import jwt
from jwt import PyJWKClient

from pydantic import AnyHttpUrl, BaseModel

from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from app.core.documents import get_document as fetch_document
from app.core.projects import list_projects as fetch_projects
from app.core.search import search_context as unified_search_context

MCP_RESOURCE_URL = os.getenv(
    "MCP_RESOURCE_URL",
    "https://ros2-server.tail49948f.ts.net:10000/mcp",
)

KEYCLOAK_ISSUER = os.getenv(
    "KEYCLOAK_ISSUER",
    "https://ros2-server.tail49948f.ts.net:8443/realms/ai-hub",
)

KEYCLOAK_JWKS_URL = os.getenv(
    "KEYCLOAK_JWKS_URL",
    "http://keycloak:8080/realms/ai-hub/protocol/openid-connect/certs",
)

# Existing secret from .env.
# Never print this value.
MCP_API_KEY = os.getenv("MCP_ACCESS_TOKEN")

REQUIRED_SCOPE = "aihub:read"


if not MCP_API_KEY:
    raise RuntimeError("MCP_ACCESS_TOKEN is required")


class KeycloakJWTVerifier(TokenVerifier):
    """Verify either the private AI-Hub API key or a Keycloak JWT."""

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        api_key: str,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.api_key = api_key
        self.jwks_client = PyJWKClient(jwks_url)

    async def verify_token(self, token: str) -> AccessToken | None:
        # Static API-key path.
        if token == self.api_key:
            return AccessToken(
                token=token,
                client_id="static-api-key",
                scopes=[REQUIRED_SCOPE],
                expires_at=None,
                resource=self.audience,
                subject="static-api-key",
                claims={
                    "auth_method": "static_api_key",
                    "scope": REQUIRED_SCOPE,
                    "aud": self.audience,
                },
            )

        # Existing Keycloak JWT path.
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)

            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require": ["exp", "iat", "iss", "sub"],
                },
            )

            raw_scope = claims.get("scope", "")
            scopes = raw_scope.split() if isinstance(raw_scope, str) else []

            if REQUIRED_SCOPE not in scopes:
                return None

            raw_aud = claims.get("aud")

            if isinstance(raw_aud, str):
                audiences = [raw_aud]
            elif isinstance(raw_aud, list):
                audiences = [str(value) for value in raw_aud]
            else:
                return None

            if self.audience not in audiences:
                return None

            expires_at = claims.get("exp")
            client_id = (
                claims.get("azp")
                or claims.get("client_id")
                or "unknown"
            )

            return AccessToken(
                token=token,
                client_id=str(client_id),
                scopes=scopes,
                expires_at=(
                    int(expires_at)
                    if expires_at is not None
                    else None
                ),
                resource=self.audience,
                subject=str(claims["sub"]),
                claims=claims,
            )

        except Exception:
            return None


class QueryKeyToBearerMiddleware:
    """
    Convert /mcp?key=<API_KEY> into:
        Authorization: Bearer <API_KEY>

    This is pure ASGI middleware so Streamable HTTP streaming
    is not wrapped by Starlette BaseHTTPMiddleware.
    """

    def __init__(self, app: Any, query_parameter: str = "key") -> None:
        self.app = app
        self.query_parameter = query_parameter

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        query_string = scope.get("query_string", b"")
        params = parse_qs(
            query_string.decode("utf-8", errors="ignore"),
            keep_blank_values=False,
        )

        key_values = params.get(self.query_parameter, [])
        api_key = key_values[0] if key_values else None

        if api_key:
            headers = list(scope.get("headers", []))

            has_authorization = any(
                name.lower() == b"authorization"
                for name, _ in headers
            )

            if not has_authorization:
                headers.append(
                    (
                        b"authorization",
                        f"Bearer {api_key}".encode("utf-8"),
                    )
                )

                scope = dict(scope)
                scope["headers"] = headers

        await self.app(scope, receive, send)

class HealthCheckResponse(BaseModel):
    status: str
    service: str


class SearchContextResponse(BaseModel):
    query: str
    project_id: int | None = None
    memory_count: int
    document_count: int
    memories: list[dict[str, Any]]
    documents: list[dict[str, Any]]


class ProjectSummary(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    memory_count: int


class ListProjectsResponse(BaseModel):
    count: int
    projects: list[ProjectSummary]


class GetDocumentResponse(BaseModel):
    id: int
    project_id: int | None = None
    title: str
    filename: str | None = None
    mime_type: str | None = None
    source: str | None = None
    description: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    chunk_count: int

mcp = MCPServer(
    "AI-Hub",
    token_verifier=KeycloakJWTVerifier(
        jwks_url=KEYCLOAK_JWKS_URL,
        issuer=KEYCLOAK_ISSUER,
        audience=MCP_RESOURCE_URL,
        api_key=MCP_API_KEY,
    ),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(KEYCLOAK_ISSUER),
        resource_server_url=AnyHttpUrl(MCP_RESOURCE_URL),
        required_scopes=[REQUIRED_SCOPE],
        validate_token_resource=True,
    ),
)


@mcp.tool()
def health_check() -> HealthCheckResponse:
    """Check whether the AI-Hub MCP server is alive."""
    return HealthCheckResponse(
        status="ok",
        service="ai-hub-mcp",
    )


@mcp.tool()
def search_context(
    query: str,
    limit: int = 5,
    project_id: int | None = None,
) -> SearchContextResponse:
    """Search AI-Hub memories and indexed documents together."""
    if not query.strip():
        raise ValueError("query must not be empty")

    limit = max(1, min(limit, 20))

    result = unified_search_context(
        query=query,
        limit=limit,
        project_id=project_id,
    )

    return SearchContextResponse.model_validate(result)

@mcp.tool()
def list_projects() -> ListProjectsResponse:
    """List AI-Hub projects and their memory counts."""
    result = fetch_projects()
    return ListProjectsResponse.model_validate(result)


@mcp.tool()
def get_document(document_id: int) -> GetDocumentResponse:
    """Get metadata for one AI-Hub document."""
    result = fetch_document(document_id)

    if result is None:
        raise ValueError(f"document not found: {document_id}")

    return GetDocumentResponse.model_validate(result)

security = TransportSecuritySettings(
    allowed_hosts=[
        "mcp:*",
        "localhost:*",
        "127.0.0.1:*",
        "ros2-server.tail49948f.ts.net",
        "ros2-server.tail49948f.ts.net:*",
    ],
    allowed_origins=[
        "http://127.0.0.1:6274",
        "http://localhost:6274",
    ],
)


mcp_app = mcp.streamable_http_app(
    transport_security=security,
)

app = QueryKeyToBearerMiddleware(mcp_app)
