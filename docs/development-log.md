# Development Log

## 2026-09-18

### MCP Deployment Stabilization

MCP deployment was stabilized and documented.

Completed:

- MCP Streamable HTTP
- API key authentication
- Keycloak OAuth path preservation
- Tailscale Funnel
- External MCP endpoint verification
- MCP Inspector `tools/list` verification
- MCP Inspector `tools/call` verification
- Successful external MCP invocation of `search_context`
- Successful retrieval of actual LIMO project source code

Baseline commit:

```text
449c32d feat: stabilize and document MCP deployment
```

### Current MCP Tools

- `health_check`
- `search_context`

### Planned Next Stage

The next stage was to standardize MCP tool responses.

Planned:

- Improve `structuredContent`
- Validate `health_check`
- Validate `search_context`
- Expand MCP tools

---

## 2026-09-19

### MCP Structured Output

Implemented typed MCP response models using Pydantic.

Added:

- `HealthCheckResponse`
- `SearchContextResponse`

The search layer in `app/core/search.py` was intentionally left unchanged.

The MCP layer now provides a typed validation boundary between the search implementation and externally exposed MCP tools.

### MCP SDK

- MCP Python SDK: `2.2.0`
- Pydantic: `2.13.5`

The MCP dependency was pinned to:

```text
mcp[cli]==2.2.0
```

### Validation

Verified through the external MCP endpoint using MCP Inspector CLI.

Verified:

- `tools/list`
- `health_check` `tools/call`
- `search_context` `tools/call`
- `outputSchema`
- `structuredContent`

Example validation query:

```text
cmd_vel publisher
```

The `search_context` result returned actual LIMO source code, including:

```text
src/limo_ros2/limo_base/src/limo_driver.cpp
```

### Authentication Troubleshooting case

The MCP Inspector Web UI initially encountered an HTTP 403 during Dynamic Client Registration because the Keycloak `Trusted Hosts` policy rejected the registration request.

The API-key authentication path was separated from the OAuth/DCR path and verified directly through Inspector CLI.

OAuth/Dynamic Client Registration remains a separate track for future Claude Custom Connector integration.

### API Key Rotation

An MCP API key was accidentally exposed during development.

The exposed key was treated as compromised and rotated.

A new key was generated using:

```python
secrets.token_hex(32)
```

The following were verified:

- `.env` is not tracked by Git
- `.env` is listed in `.gitignore`
- The new key was injected into the MCP container
- The new key successfully authenticated external `tools/list`
- The new key successfully authenticated external `tools/call`

## 2026-09-20 — Project and Document MCP Tools

### Core Layer Refactoring

The existing Project and Document database lookup logic was separated from the FastAPI routers into reusable core modules.

- `api/app/core/projects.py`
- `api/app/core/documents.py`

The REST routers continue to handle HTTP-specific behavior such as `HTTPException`, while MCP tools reuse the core lookup functions directly.

### MCP Tools

Added two MCP tools:

- `list_projects`
- `get_document`

Both tools use Pydantic response models so that MCP `outputSchema` and `structuredContent` are generated from explicit response types.

### External Verification

The following were verified through MCP Inspector:

- `tools/list` exposes `health_check`, `search_context`, `list_projects`, and `get_document`
- `list_projects` returns `structuredContent` successfully
- `get_document` with `document_id=1` returns `structuredContent` successfully
- `get_document` with a nonexistent document ID returns `isError: true`
- The existing REST `/projects` and `/documents` APIs remained functional after the refactoring

## 2026-09-20 — Backup and Recovery Validation

### Backup Scope

AI-Hub backup covers two persistent data areas:

- PostgreSQL database
- Uploaded files under `/mnt/data/ai-hub/documents`

The PostgreSQL backup uses custom format (`pg_dump -Fc`), while uploaded files are archived as `tar.gz`.

### Backup Script

Added:

```text
scripts/backup.sh
```

The script:

- creates timestamped PostgreSQL backups
- creates timestamped uploaded-document archives
- verifies that backup files are non-empty
- verifies that the PostgreSQL archive can be read by pg_restore --list
- verifies that the document archive can be read by tar -tzf
### Recovery Validation

A separate PostgreSQL database named aihub_restore_test was created and restored from the backup.

The following counts matched between the production database and the restored database:

- projects: 3
- memories: 5
- documents: 55
- document_chunks: 1041

The uploaded-document archive was also extracted into a temporary directory and the file count matched:

- source files: 57
- restored files: 57

The temporary restore database and extraction directory were removed after verification.

### Automated Backup Scheduling

- Extended `scripts/backup.sh` with 30-day retention cleanup.
- Added systemd service and timer for automated backup execution.
- Configured daily execution at 18:00 UTC (03:00 KST).
- Verified manual systemd service execution with exit status `0/SUCCESS`.
- Verified timer activation and next scheduled execution with `systemctl list-timers`.
- Backup logs are available through `journalctl -u ai-hub-backup.service`.

## Documentation Rule

When a significant feature, deployment change, bug, or troubleshooting case is completed, record it in the appropriate documentation before moving to the next major stage.
