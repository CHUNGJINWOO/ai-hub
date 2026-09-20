# AI-Hub Project Status

> Last updated: 2026-09-19

## 1. Project

AI-Hub is a personal AI engineering project that provides document and source-code ingestion, semantic/hybrid search, code-aware reranking, and MCP-based external access.

The current primary use case is searching and retrieving ROS2/LIMO project source code through the AI-Hub search system.

## 2. GitHub Baseline

- Repository: `CHUNGJINWOO/ai-hub`
- Branch: `main`
- Historical baseline: `449c32d`
- Baseline commit: `feat: stabilize and document MCP deployment`

Development continues from this baseline.

## 3. Environment

- Cloud: Oracle Cloud ARM64
- OS: Ubuntu 22.04
- Containerization: Docker Compose
- Python: 3.10
- Database: PostgreSQL 17
- Vector database extension: pgvector
- Backend: FastAPI
- Embedding model: `multilingual-e5-small`
- Primary indexed project: ROS2/LIMO

## 4. Implemented Features

### Data and Project Management

- Memory CRUD
- Project management
- SHA-256 metadata

### Document Ingestion

- PDF
- DOCX
- XLSX
- PPTX

### Source Code Ingestion

- Python
- C
- C++
- ROS2 source code

### Search

- Semantic search
- Hybrid search
- Code-aware reranking
- LIMO project indexing

### MCP

- Streamable HTTP
- API key authentication
- Keycloak OAuth path preserved
- Tailscale Funnel
- External MCP endpoint verification
- MCP Inspector tools/list verification
- MCP Inspector tools/call verification
- Typed MCP response models with Pydantic
- outputSchema verification
- structuredContent verification

## 5. Currently Verified MCP Tools

### health_check

Implemented and externally verified.

Verified:

- tools/list
- outputSchema
- tools/call
- structuredContent

### search_context

Implemented and externally verified.

Verified:

- tools/list
- outputSchema
- tools/call
- structuredContent
- Actual LIMO source-code retrieval

Example validation query:

`cmd_vel publisher`

The result included actual LIMO source code from:

`src/limo_ros2/limo_base/src/limo_driver.cpp`

## 6. Current Development

### Completed in the Current MCP Phase

- MCP structuredContent improvement
- Pydantic response models for MCP tools
- health_check output validation
- search_context output validation
- External API-key authentication revalidation
- MCP API-key rotation after accidental exposure
- .env Git tracking verification

### Current MCP Tools

- health_check
- search_context
- list_projects
- get_document

The four MCP tools above have been externally verified through MCP Inspector.

`list_projects` and `get_document` return typed Pydantic responses and expose `outputSchema` / `structuredContent`.
- Additional retrieval/context tools as required

### Backup and Recovery

- PostgreSQL custom-format backup implemented
- Uploaded document archive implemented
- PostgreSQL restore tested successfully
- Uploaded document archive restore tested successfully
- `scripts/backup.sh` added for repeatable backup execution

Automated retention and scheduled backup execution remain future operational tasks.

## 7. Documentation

Development and troubleshooting information is maintained under docs/.

- docs/project-status.md
- docs/architecture.md
- docs/development-log.md
- docs/troubleshooting.md
- docs/MCP_DEPLOYMENT.md

This file is the current project-state reference.

## 8. Deferred Work

### Claude Custom Connector

Claude Custom Connector integration is currently deferred.

OAuth and Dynamic Client Registration work should be handled as a separate track after the MCP tool interface is further expanded and stabilized.

## 9. Future Operations

After MCP functionality is expanded:

- Backup strategy
- Restore procedure
- Operational monitoring
- Secret and API key management
- Security review
- Deployment documentation
- Production-oriented cleanup

## 10. Final Goal

The final goal is to turn AI-Hub into a clean public GitHub portfolio project.

Before publication:

- Remove secrets and environment-specific credentials
- Clean configuration examples
- Document architecture
- Document deployment
- Document troubleshooting
- Add reproducible setup instructions
- Add MCP usage examples
- Add project screenshots or diagrams where useful
- Review repository structure and commit history
