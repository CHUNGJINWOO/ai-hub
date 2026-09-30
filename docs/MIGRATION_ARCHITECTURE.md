# AI-Hub Migration Architecture

## Scope

This document defines the migration contract and the minimum validation
infrastructure for a future versioned schema system. It does not apply SQL,
connect to Production PostgreSQL, create migration metadata, or add
Workspace/User/Membership tables.

## Implementation status

The current repository deliberately separates implemented, planned, and
unimplemented migration concerns:

| Concern | Status | Implementation |
|---|---|---|
| Filename, revision, gap, and duplicate validation | **IMPLEMENTED** | `api/app/core/migration_contract.py` |
| Migration file and applied checksum validation | **IMPLEMENTED** | `api/app/core/migration_contract.py` |
| Canonical JSON normalization and serialization | **IMPLEMENTED** | `api/app/core/schema_representation.py` |
| Semantic fingerprint and repository-name diagnostics | **IMPLEMENTED** | `api/app/core/schema_fingerprint.py` |
| Read-only PostgreSQL 17 catalog query contract | **IMPLEMENTED** | `api/app/core/catalog_queries.py` |
| Catalog snapshot and row conversion boundary | **IMPLEMENTED** | `api/app/core/schema_catalog.py` |
| Pure PostgreSQL/pgvector compatibility policy checks | **IMPLEMENTED** | `api/app/core/compatibility.py` |
| DB-free structured drift diagnostics | **IMPLEMENTED** | `api/app/core/schema_drift.py`; no runtime gate is called |
| Structured compatibility gate result | **IMPLEMENTED** | `api/app/core/compatibility.py`; no runtime gate is called |
| DB-free data-safety checks and aggregate result | **IMPLEMENTED** | `api/app/core/data_safety.py`; no Production observations |
| PostgreSQL read-only data-safety observation adapter | **IMPLEMENTED** | `api/app/core/data_safety_observer.py`; fake and controlled PostgreSQL 17 tested |
| ObservedDataSafety conversion and UNKNOWN propagation | **IMPLEMENTED** | Observer returns facts; policy evaluation remains separate |
| Validation-only aggregate report | **IMPLEMENTED** | `api/app/core/schema_validation.py`; no mutation or runtime integration |
| Controlled PostgreSQL integration harness | **IMPLEMENTED** | `api/tests/test_postgres_integration.py`; skipped without explicit disposable DSN |
| Production catalog introspection execution | **NOT IMPLEMENTED** | Adapter is injectable and not connected to runtime or Production |
| Runtime compatibility gate integration | **NOT IMPLEMENTED** | No application startup or runtime caller |
| Production data-safety observation, backup/restore evidence integration, metadata table, baseline registration, and migration runner | **NOT IMPLEMENTED** | Planned operations layer |

The validation-only aggregate is validated by DB-free unit tests and by the
controlled PostgreSQL 17 integration harness when an explicit disposable DSN
is supplied. Phase N exercised that harness against PostgreSQL 17.11 with
pgvector 0.8.6, including catalog, fingerprint/drift, compatibility,
data-safety, read-only, and mutation-safety checks. No Production validation
has been performed.
The aggregate records an explicit evidence source (`db_free`, `fixture`, or
`controlled_postgresql`) and never synthesizes a Production source.

The representation and fingerprint modules are DB-free. The catalog reader
defines a PostgreSQL 17 read-only adapter contract and can execute only the
catalog SELECT queries supplied through an injected connection factory. It is
not connected to runtime bootstrap or Production. No module registers a
baseline, runs migrations, or repairs a schema.

## Adopted fingerprint policy

The following policy is adopted for the implementation boundary:

- Semantic schema identity and repository canonical object-name identity are
  separate results.
- Constraint, index, and sequence names are retained for repository identity
  diagnostics rather than being the only semantic identity.
- A semantic mismatch is schema drift and blocks baseline registration.
- A semantic match with a name mismatch is repository identity drift. It is
  reported but is not automatically registered; explicit reconciliation or a
  rename revision is required.
- The `vector` extension name/schema and vector dimensions belong to the
  schema contract.
- The installed pgvector version is checked by a separate compatibility gate.
  The observed Production version `0.8.6` is evidence, not an invented version
  range.
- PostgreSQL major is checked by a separate compatibility gate. Production
  PostgreSQL 17 is recorded evidence; other majors are not declared supported
  without verification.

These policies are represented by the DB-free modules above. They are not yet
connected to a Production baseline-registration command.

## Current schema ownership

The repository now has a complete Fresh DB baseline, although runtime
bootstrap remains transitional and is not the versioned source of truth.

- `api/app/core/db.py` creates `projects` and `memories` during API startup.
- The same module adds `memories.project_id` and creates two indexes.
- The repository owns the canonical baseline definitions for `documents` and
  `document_chunks`, adopted from the reviewed Production catalog evidence.
- Routers assume those two tables already exist and insert, update, query, and
  delete rows.
- No Alembic, yoyo, Flyway, Liquibase, Django migration, or custom migration
  runner is present.
- `scripts/backup.sh` creates PostgreSQL backups but is not a schema migration
  source.

The existing bootstrap remains unchanged by this work.

## Bootstrap boundary

`api/app/main.py` calls `ensure_memories_table()` and
`ensure_projects_table()` from its FastAPI lifespan. The two functions use
separate connections and transactions. Bootstrap SQL is idempotent for named
objects, but it does not validate an existing object's definition, detect
schema drift, record a revision, or provide downgrade behavior.

Application startup is not the primary execution point for schema migrations.
Future migrations should run as an explicit deploy/operations step before the
application is started. The existing runtime bootstrap is retained until a
Production-backed migration baseline and a safe transition plan exist.

## Revision contract

Migration files will use:

```text
NNNN_slug.sql
```

Examples:

```text
0001_baseline.sql
0002_workspace_identity.sql
0003_project_workspace.sql
```

Rules:

1. `NNNN` is a positive, four-digit revision number.
2. The slug starts with a lowercase letter and uses lowercase letters,
   numbers, and underscores.
3. Revisions are ordered numerically.
4. A revision is applied at most once.
5. The default policy requires contiguous numbering from `0001`.
6. Gaps may be allowed only when a future branch policy explicitly enables
   them; the validator supports this as an explicit option.
7. Parent/dependency metadata is not required in the first contract. The
   numeric order is the current dependency chain. A future branching need
   must add explicit parent metadata rather than relying on filenames.

The parser and chain validator are implemented in
`api/app/core/migration_contract.py`. They do not open a database.

## Metadata contract

The logical metadata table is planned as:

```text
schema_migrations
  version      positive integer
  applied_at   non-empty timestamp value
  checksum     lowercase SHA-256 hex digest
```

This table is not created by this change. A future implementation should make
`version` unique and should record one row only after that revision succeeds.
An optional description or previous revision can be added later without
changing the meaning of the required fields.

## Checksum and immutability

The migration file bytes are hashed with SHA-256 when the migration is loaded.
When an applied revision is encountered again:

- the revision must exist on disk;
- its checksum must match the recorded checksum;
- a mismatch is an error, not a new application;
- an applied revision is never silently re-executed.

`validate_applied_migrations()` implements these checks without a database.
Changing an applied SQL file requires a new revision or an explicit,
operator-controlled metadata repair process. It must not be handled by
silently updating the stored checksum.

## Lock contract

The future runner must serialize migration execution across API, MCP, and
other deploy processes. PostgreSQL advisory locking is a candidate mechanism.
The intended lifecycle is:

```text
acquire migration lock
  -> read metadata
  -> validate migration chain and checksums
  -> apply pending revision
  -> record version and checksum
  -> commit
  -> release lock
```

The lock is a design contract only; no lock is acquired by the current code.
The preferred operational shape is a single explicit migration command, not
every application instance running migrations at startup.

## Transaction contract

Each transactional revision should use:

```text
BEGIN
  validate expected state
  apply revision SQL
  record schema_migrations row
COMMIT
```

On failure, the revision and its metadata record roll back together. A future
revision must explicitly declare when it cannot run transactionally, such as
an operation with PostgreSQL-specific non-transactional requirements. Such a
revision needs a separate operational procedure and must not pretend to have
the normal atomicity guarantee.

## Baseline contract

The current `db.py` bootstrap is not a complete baseline because:

- it covers only part of `projects` and `memories`;
- the runtime bootstrap covers only part of the canonical baseline;
- the Production catalog evidence is now available for the four core tables;
- the existing database revision and migration metadata state are still
  unknown;
- structured drift diagnostics are available for supplied canonical snapshots,
  but no Production snapshot has been acquired or compared by the repository.

The captured Production schema is the **existing-Production baseline
candidate** and is represented by the reviewed Fresh DB definition in
`migrations/0001_baseline.sql`. It must be registered as an already-present
state, not replayed against the existing database. This work does not execute
baseline registration.

Possible future baseline modes are distinct:

1. **Existing-Production baseline:** snapshot the observed Production schema
   and declare it as the starting revision without recreating existing data.
   Registration must verify the expected catalog fingerprint before recording
   the baseline as applied.
2. **New-database baseline:** restore complete DDL for every application table,
   constraint, index, and required extension.
3. **Separate baselines:** explicitly document different starting procedures
   for an existing Production database and a new installation.

The design direction is to use modes 1 and 2 together: Existing Production is
adopted by mark-as-applied baseline registration, while a fresh database is
created by a complete baseline installation. The baseline definition must
cover `projects`, `memories`, `documents`, `document_chunks`, their sequences,
constraints, indexes, and the `vector` extension dependency.

The baseline DDL is not a normal upgrade for an existing Production database.
Running `CREATE TABLE` against those existing tables is prohibited because it
can fail on conflicts or conceal differences when combined with
`IF NOT EXISTS`. Existing data and sequences must remain untouched during
adoption.

## Pre-baseline schema evidence matrix

The following is a precondition review, not a migration file or an assertion
that the current bootstrap is already a canonical baseline. The complete
catalog details are recorded in
[`PRODUCTION_SCHEMA_RECONCILIATION.md`](PRODUCTION_SCHEMA_RECONCILIATION.md).

| Table | Repository source | Production evidence | Current conclusion |
| --- | --- | --- | --- |
| `projects` | `db.py` `CREATE TABLE`, defaults, status `CHECK`, slug `UNIQUE`, and named indexes | 7 columns in the same ordinal order; sequence-backed `bigint` id, defaults, PK, constraints, and btree indexes match | **confirmed match; repository-owned bootstrap, not versioned** |
| `memories` | `db.py` table DDL plus `project_id` FK and index bootstrap | 10 columns in the same ordinal order; `vector(384)`, defaults, importance `CHECK`, project FK/actions, PK, and index match | **confirmed match; repository-owned bootstrap, not versioned** |
| `documents` | Canonical definition in `migrations/0001_baseline.sql`; application query contract remains usage evidence | 16 columns, defaults/nullability, PK, project FK/action, status `CHECK`, and indexes captured | **repository-owned canonical baseline; adopted from Production evidence** |
| `document_chunks` | Canonical definition in `migrations/0001_baseline.sql`; application query contract remains usage evidence | 7 columns, `vector(384)`, defaults/nullability, PK, composite `UNIQUE`, document FK/action, and indexes captured | **repository-owned canonical baseline; adopted from Production evidence** |

The application contract confirms use of the following `documents` fields:
`id`, `project_id`, `title`, `filename`, `mime_type`, `source`,
`description`, `status`, `created_at`, `updated_at`, `file_path`, `file_size`,
`sha256`, `document_type`, `relative_path`, and `file_hash`. It confirms use
of all seven observed `document_chunks` fields. This proves usage, not the
original authority to define those tables; the reviewed Production definition
is now explicitly adopted as the repository-owned canonical definition.

The baseline object scope should include the `vector` extension dependency,
all four tables, sequence-backed primary-key defaults, primary/unique/check/
foreign-key constraints, foreign-key actions, and indexes. Row data, row
counts, current sequence values, application upload files, Keycloak objects,
and runtime bootstrap behavior are separate from schema identity.

The supplied sequence and index catalog evidence resolves the previous
catalog-semantic blockers. Existing Production still requires fingerprint
verification and explicit mark-as-applied registration; the Fresh DB baseline
must not be replayed there.

### Catalog verification status

| Item | Status | Evidence or remaining decision |
| --- | --- | --- |
| Sequence names and ownership | **CONFIRMED** | Each `id` owns its corresponding public bigint sequence |
| Identity versus serial-style semantics | **CONFIRMED** | `is_identity = NO`; `nextval(..._id_seq::regclass)` defaults |
| Sequence definition | **CONFIRMED** | bigint; start/min 1; increment 1; max `9223372036854775807`; no cycle; cache 1 |
| Index names and exact definitions | **CONFIRMED** | 13 catalog indexes; definitions and key columns supplied |
| Index access method | **CONFIRMED** | All 13 indexes use btree |
| Partial predicates | **CONFIRMED** | None |
| Expression indexes | **CONFIRMED** | None |
| Exclusion indexes | **CONFIRMED** | None |
| `vector` extension and version | **CONFIRMED** | `public.vector`, version `0.8.6`; embeddings are `vector(384)` |
| PostgreSQL major version | **CONFIRMED** | PostgreSQL 17 (`server_version_num = 170011`) |
| Exact vector patch-version enforcement | **DESIGN DECISION** | Keep separate from the observed `0.8.6` evidence |
| PostgreSQL major-version compatibility gate | **DESIGN DECISION** | Enforce as a compatibility gate separate from the schema hash |
| Future revisions to adopted tables | **DESIGN DECISION** | Changes must use versioned revisions |

No schema-semantic item remains `NOT VERIFIED` in the supplied catalog
evidence. The remaining decisions are policy choices, not missing catalog
facts.

### Canonical baseline schema matrix

| Table | Columns (type; nullability; default) | PK / UNIQUE / CHECK | FK and actions | Indexes | Vector |
| --- | --- | --- | --- | --- | --- |
| `public.projects` | `id bigint NOT NULL nextval('projects_id_seq'::regclass)`; `name text NOT NULL`; `slug text NOT NULL`; `description text`; `status text NOT NULL DEFAULT 'active'`; `created_at timestamptz NOT NULL DEFAULT now()`; `updated_at timestamptz NOT NULL DEFAULT now()` | PK `(id)`; UNIQUE `(slug)`; status in `active`, `archived`, `completed` | none | `projects_pkey` PK UNIQUE btree `(id)`; `projects_slug_key` UNIQUE btree `(slug)`; `idx_projects_slug` btree `(slug)` | none |
| `public.memories` | `id bigint NOT NULL nextval('memories_id_seq'::regclass)`; `content text NOT NULL`; `memory_type text NOT NULL DEFAULT 'fact'`; `category text`; `importance integer NOT NULL DEFAULT 3`; `source text`; `embedding vector(384)`; `created_at timestamptz NOT NULL DEFAULT now()`; `updated_at timestamptz NOT NULL DEFAULT now()`; `project_id bigint` | PK `(id)`; importance between 1 and 5 | `project_id -> projects.id`; `ON UPDATE NO ACTION`; `ON DELETE SET NULL` | `memories_pkey` PK UNIQUE btree `(id)`; `idx_memories_project_id` btree `(project_id)` | `embedding vector(384)` |
| `public.documents` | `id bigint NOT NULL nextval('documents_id_seq'::regclass)`; `project_id bigint`; `title text NOT NULL`; `filename text`; `mime_type text`; `source text`; `description text`; `status text NOT NULL DEFAULT 'active'`; `created_at timestamptz NOT NULL DEFAULT now()`; `updated_at timestamptz NOT NULL DEFAULT now()`; `file_path text`; `file_size bigint`; `sha256 text`; `document_type text`; `relative_path text`; `file_hash text` | PK `(id)`; status in `active`, `archived` | `project_id -> projects.id`; `ON UPDATE NO ACTION`; `ON DELETE SET NULL` | `documents_pkey` PK UNIQUE btree `(id)`; `idx_documents_project_id` btree `(project_id)`; `idx_documents_project_relative_path` btree `(project_id, relative_path)`; `idx_documents_sha256` btree `(sha256)`; `idx_documents_status` btree `(status)` | none |
| `public.document_chunks` | `id bigint NOT NULL nextval('document_chunks_id_seq'::regclass)`; `document_id bigint NOT NULL`; `chunk_index integer NOT NULL`; `content text NOT NULL`; `page_number integer`; `embedding vector(384)`; `created_at timestamptz NOT NULL DEFAULT now()` | PK `(id)`; UNIQUE `(document_id, chunk_index)` | `document_id -> documents.id`; `ON UPDATE NO ACTION`; `ON DELETE CASCADE` | `document_chunks_pkey` PK UNIQUE btree `(id)`; `document_chunks_document_id_chunk_index_key` UNIQUE btree `(document_id, chunk_index)`; `idx_document_chunks_document_id` btree `(document_id)` | `embedding vector(384)` |

All four `id` columns use sequence-backed bigint semantics, are not identity
columns, and own their corresponding sequences. Current sequence values are
data-state verification, not part of the schema fingerprint.

The supplied read-only result is now the evidence record for these facts. The
baseline creates `vector` explicitly in the `public` schema and treats the
extension and dimension `384` as compatibility requirements.
Whether the exact `vector` patch version is strict for the fingerprint remains
a policy decision, not an automatic consequence of the installed `0.8.6`
version.

## Adoption and future ownership decisions

The following ownership boundary is the target design:

- baseline and future migration files are the canonical schema source;
- the migration runner is the only component that applies schema revisions;
- Production is the state to verify, not the authoring source;
- application query contracts describe usage and do not define schema;
- runtime bootstrap is transitional compatibility infrastructure only.

For Existing Production, the first adoption operation is a separately
approved baseline registration. It records the baseline identity, catalog
fingerprint, applied timestamp, and checksum without replaying baseline DDL.
The first ordinary upgrade revision is the first revision after that baseline.

For a fresh database, the baseline installation creates the extension and
tables in dependency order, then constraints and indexes, and records the
baseline metadata in the same transaction where PostgreSQL permits it.
`projects` precedes `memories` and `documents`; `documents` precedes
`document_chunks`.

After adoption, all schema changes must be explicit revision files named
`NNNN_slug.sql`. A single explicit deploy/operations command owns execution,
locking, precondition checks, checksums, metadata recording, and failure
reporting. Application startup must not apply pending migrations.

The current runtime bootstrap should first move to a validation-only or
compatibility mode in migration-managed environments, then be removed after
fresh-install and restored-Production rehearsal. It must not continue
creating or altering objects alongside the migration runner indefinitely.

This design is not an implementation or a Production approval to migrate.
The supplied read-only catalog query found no table in the checked candidate
set (`alembic_version`, `schema_migrations`, `migrations`, `migration`, or
`flyway_schema_history`) in non-system schemas. This is evidence that those
candidate tables were not found; it does not prove that no migration metadata
exists under another name or in another form.

## Baseline fingerprint contract (proposed)

The repository currently calculates DB-free semantic fingerprints from
supplied canonical schema data. PostgreSQL catalog introspection, acquisition
of a fingerprint from an actual database, and use of that fingerprint in
Existing-Production baseline registration are not implemented.

### Scope and exclusions

The schema fingerprint input is the canonical `public` application schema
represented by the four baseline tables, their owned sequences, the `vector`
extension dependency, and the related constraints and indexes. It excludes
Keycloak objects, runtime/bootstrap code, application files, row contents, row
counts, and current sequence values.

The canonical representation should contain, in deterministic order, the
following records:

- extensions: schema, name, and an explicit version-policy field;
- tables: schema, name, and relation kind;
- columns: schema, table, ordinal position, name, PostgreSQL type and typmod,
  nullability, and normalized default expression;
- sequences: schema, name, data type, start, minimum, maximum, increment,
  cycle, cache, ownership relation, and the owned column's default relation;
- constraints: schema, table, constraint type, name, ordered local columns,
  ordered referenced columns where applicable, normalized CHECK expression,
  referenced schema/table, and foreign-key actions;
- indexes: schema, table, name, primary/unique/exclusion flags, access
  method, canonical definition, predicate, and expressions.

The representation format is canonical UTF-8 JSON, not SQL text:

1. objects use fixed field names documented by the categories above;
2. arrays are sorted by the ordering rules below;
3. object keys are serialized lexicographically;
4. booleans and numbers use JSON primitives, not display strings;
5. strings use JSON escaping and Unicode code points as returned after
   normalization;
6. serialization is compact, with no insignificant whitespace and no final
   newline;
7. SHA-256 is calculated over the serialized UTF-8 bytes.

This keeps the representation machine-stable while allowing a structured
diff before hashing. Catalog row order, SQL formatting, and JSON pretty
printing must not affect the fingerprint.

### Deterministic ordering and normalization

Arrays are sorted as follows:

- extensions: `schema`, then `name`;
- tables: `schema`, then `name`;
- columns: `schema`, `table`, then ordinal position;
- sequences: `schema`, then `name`;
- constraints: `schema`, `table`, constraint type, then constraint name;
- indexes: `schema`, `table`, then index name;
- column and constraint column lists: ordinal position within the object.

Identifier fields retain PostgreSQL catalog spelling. Type and typmod values
use a canonical catalog representation equivalent to
`format_type(atttypid, atttypmod)`. Default and CHECK expressions are trimmed,
internal runs of whitespace are collapsed to one space, and the normalized
form is lower-cased only for comparison where PostgreSQL expression spelling
is not semantically significant; the original catalog expression remains
available in diagnostics. Index definitions use the same whitespace
normalization for the `pg_get_indexdef()` result, while predicate and
expression fields use the normalized `pg_get_expr()` result. No parser-based
SQL rewriting is implied by this contract.

The following are **proposed policy decisions**, not current implementation:

- Include object names in the strict fingerprint. A rename is then a
  mismatch that requires an explicit revision rather than an unnoticed
  compatibility change.
- Treat column order as schema-significant because it is part of the captured
  baseline, even though most SQL behavior does not depend on it.
- Treat extension version and vector dimension as compatibility requirements.
  A vector dimension mismatch is always a baseline mismatch.
- Gate PostgreSQL major version separately from the schema hash. Do not
  include the PostgreSQL minor version in the schema fingerprint; record it as
  environment evidence and compatibility metadata.
- Exclude row contents, row counts, sequence current values, and application
  data from the schema fingerprint. Validate row counts/integrity and sequence
  state separately during adoption.

The following decisions remain open:

- whether constraint and index names are strict identity fields or are
  replaced by stable semantic identities;
- whether the observed `vector` patch version `0.8.6` is a strict fingerprint
  input or only compatibility metadata;
- whether PostgreSQL major-version compatibility is enforced as a separate
  gate and which major versions are allowed.

Until these decisions are approved, the representation contract is defined
but the production fingerprint policy is not final.

The adoption flow is therefore:

```text
Production catalog
  -> canonical representation
  -> SHA-256 fingerprint
  -> repository baseline fingerprint
  -> match -> mark baseline applied
  -> mismatch -> abort and require manual reconciliation
```

`mark-as-applied` must not run when a table is missing, an unexpected or
missing column exists, type/nullability/default differs, any key/check/FK or
action differs, an index differs, vector dimension differs, the required
extension is missing or incompatible, or the PostgreSQL major-version gate
fails. These are proposed runner preconditions, not implemented behavior.

## Baseline metadata contract (proposed)

The existing `MigrationMetadata` dataclass only validates revision,
timestamp, and file checksum. It does not persist a database row and is not a
runner. A future metadata record should distinguish baseline adoption from
ordinary revisions:

| Field | Status | Purpose |
|---|---|---|
| `revision` / baseline id | required proposal | Identifies the baseline or revision |
| `revision_type` | required proposal | `baseline` versus ordinary migration |
| `description` | optional proposal | Human-readable intent |
| `checksum` | required proposal | Immutable migration/baseline artifact hash |
| `schema_fingerprint` | required for baseline proposal | Matched catalog identity |
| `applied_at` | required proposal | Event timestamp |
| `applied_by` | optional proposal | Operator or runner identity without secrets |
| `runner_version` | optional proposal | Reproducibility/debugging |
| `postgres_major_version` | required compatibility metadata proposal | Environment gate |
| `vector_version` | required compatibility metadata proposal | Extension compatibility |

Creating this metadata table in Existing Production is itself a schema
change. Its DDL and registration must therefore be separately approved,
backed up, rehearsed, and executed by the future runner; this design does not
create or execute it.

## Existing Production versus fresh database

Both paths should converge on the same canonical schema fingerprint after
completion. Existing Production data is not part of that fingerprint. Row
counts and NULL/orphan checks are audit evidence and adoption preconditions,
not baseline identity.

Sequence definitions belong in the schema representation; current sequence
values do not. Current values require a separate data-preservation and
post-migration validation check so that adopting a baseline cannot reset
identifiers.

For Existing Production, the baseline is marked applied only after the
fingerprint and data-safety preconditions pass. For a fresh database, the
baseline DDL creates the extension and dependency-ordered tables, constraints,
and indexes, then records matching metadata. These are different operations
with the same resulting schema identity.

## Migration runner target contract (proposed)

The future runner, invoked by an explicit deploy/operations command, should:

1. discover revision files;
2. validate filename, ordering, gaps, and checksums;
3. acquire an advisory lock;
4. inspect metadata and current schema fingerprint;
5. perform baseline registration or revision preconditions;
6. execute one transactional revision where supported;
7. record metadata only after successful SQL;
8. report and rollback failures without success-shaped fallbacks;
9. run postconditions and drift checks.

Application startup remains separate:

```text
application startup -> schema compatibility/validation check only
deploy/operations  -> migration runner -> versioned revisions
```

This is target architecture, not current behavior. No runner, advisory lock,
fingerprint calculator, metadata table, or startup validation mode is
implemented yet.

## Preconditions before Production migration

Before baseline registration or any post-baseline revision is approved, the
operations procedure must require:

1. an immutable Production catalog snapshot and fingerprint for all four core
   tables, sequences, constraints, indexes, and extensions;
2. confirmation of migration metadata table existence and contents beyond the
   checked candidate set, without assuming that the query proves all possible
   migration metadata is absent;
3. a verified backup and restore rehearsal;
4. a dry-run or restored-Production rehearsal of every precondition and
   postcondition;
5. application compatibility checks for API and MCP startup;
6. an explicit transaction and rollback plan, including any non-transactional
   PostgreSQL operations;
7. an approved procedure for transitioning `db.py` from DDL bootstrap to
   validation-only compatibility behavior.

Until these conditions are met, migration readiness remains **not ready** even
though the four-table Production schema evidence is complete.

## Future migration lifecycle

1. Capture and review the Production schema and data-integrity baseline.
2. Define the baseline revision and complete missing DDL ownership.
3. Add a metadata-aware migration runner and explicit deployment command.
4. Validate the baseline before applying later revisions.
5. Add Workspace/User/Membership schema in a new revision.
6. Backfill and constrain existing resources only after a reviewed data policy.
7. Integrate Principal and authorization behavior separately from schema
   application.
8. Keep migration logs, checksums, and rollback/backup procedures separate
   from API request handling.

Workspace authorization implementation must not be used as a reason to infer
or recreate undocumented `documents` or `document_chunks` schema.

## Alternatives

An established migration framework such as Alembic can be evaluated later.
This contract does not select a framework or add a dependency. The immediate
goal is to make revision ordering, metadata, immutability, locking, and
baseline preconditions explicit before choosing an execution implementation.
