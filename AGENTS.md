# AI-Hub Repository Guidance

## Purpose and current state

AI-Hub is a personal AI engineering project for storing project knowledge and
documents, ingesting source code and supported document formats, and retrieving
relevant context through semantic and hybrid search. Its primary documented use
case is searching ROS2/LIMO project material. The repository also provides
FastAPI endpoints and a Streamable HTTP MCP service.

Treat the repository and its documentation as the source of truth for what is
implemented. The current documented MCP tools are `health_check`,
`search_context`, `list_projects`, and `get_document`. The REST API includes
`/context/search` and the additive `/context/assemble` endpoint. Context
assembly normalizes retrieved search results; it does not call an LLM or
generate answers.

Do not present planned work as existing functionality. In particular, generated
answers, conversation context, Conversation/Provider/Agent/Multi-Agent
capabilities, OCR, HWP/HWPX ingestion, and a web UI are not documented as
implemented. Check the current status documentation before describing or
changing feature state.

## Repository boundaries

- Work only within this `ai-hub` repository unless the user explicitly
  authorizes work elsewhere.
- Treat separately cloned or indexed project repositories, including ROS2/LIMO
  source repositories, as external inputs. Do not modify them as part of an
  AI-Hub change unless explicitly requested.
- Keep changes scoped to the request. Do not modify deployed infrastructure,
  databases, external services, or generated/indexed user data unless that is
  explicitly in scope.
- Do not change FastAPI, database, or MCP implementation when a task asks only
  for documentation.
- Preserve existing behavior and API/MCP response contracts unless a behavior
  change is explicitly requested and documented.

## Development principles

- Read relevant source and documentation before changing behavior. Reuse
  established project patterns and shared code rather than creating parallel
  implementations.
- Make small, focused, typed changes with clear error handling. Avoid silent
  fallbacks and broad exception handling that hides failures.
- Keep current implementation, historical verification, and future design
  distinct. A historical deployment or test record is not proof of current
  external availability.
- Update directly relevant documentation when implementation, deployment, or
  troubleshooting behavior changes. Do not rewrite unrelated documents.
- Avoid adding dependencies unless the change requires them.

## Testing and verification

- Select validation that covers the actual change: focused tests first, then
  the relevant build, type check, or broader test suite when warranted.
- For behavior changes, verify both the changed path and important existing
  behavior that must remain unchanged. For API or MCP changes, consider the
  corresponding REST/MCP contract and typed response shape.
- Run documentation checks such as `git diff --check` for documentation-only
  changes. Do not claim tests or external services were verified unless they
  were actually run.
- Report validation failures and limitations explicitly; do not imply success
  from an incomplete check.

## Git principles

- Before editing, confirm the repository root, current branch, and worktree
  state. Preserve unrelated user changes.
- Keep changes limited to the requested files and inspect the complete diff
  before finishing.
- Never commit or push unless the user explicitly asks for it. Do not amend
  commits or rewrite history without explicit authorization.
- Do not stage or include unrelated files. Do not commit secrets, credentials,
  local environment files, or generated data.

## Security principles

- Never place secrets, access tokens, passwords, private keys, or credentials
  in source code, documentation examples, logs, tests, commit messages, or
  Git history.
- Keep `.env` and other local secret-bearing configuration out of version
  control. Use clearly fake placeholders when an example requires a value.
- Treat tokens embedded in URLs as exposed to URL logs and history; prefer
  authorization headers where supported. Rotate credentials if they are
  exposed.
- Validate and constrain untrusted input at system boundaries, and avoid
  exposing sensitive database, filesystem, or deployment details in errors.
- Separate authentication/authorization failures from service functionality
  during diagnosis, and do not weaken access controls as a troubleshooting
  shortcut.

## Useful project references

- [Project status](docs/project-status.md) describes documented implemented
  features and deferred work.
- [Database schema](docs/DATABASE_SCHEMA.md) records known repository and
  production schema facts and limitations.
- [Development log](docs/development-log.md) records dated implementation and
  verification history.
- [Troubleshooting](docs/troubleshooting.md) records known operational issues.
- [MCP deployment](docs/MCP_DEPLOYMENT.md) describes repository configuration
  and the limits of external verification records.
