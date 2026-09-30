# Repository / Production Schema Reconciliation

## 1. 기준과 범위

- 기준 시점: 2026-09-30 UTC
- Repository 기준:
  - [`api/app/core/db.py`](../api/app/core/db.py)
  - [`docs/DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md)
  - [`docs/MIGRATION_ARCHITECTURE.md`](MIGRATION_ARCHITECTURE.md)
  - [`docs/sql/production_db_audit.sql`](sql/production_db_audit.sql)
- Production 기준:
  - 운영자가 Production 서버에서 실행한 read-only audit 결과
  - 별도 read-only FK query 결과
  - `documents` / `document_chunks` catalog query 결과
  - audit transaction의 `ROLLBACK` 확인

이 문서는 Agent 세션에서 Production DB 명령을 재실행하지 않고, 위
evidence만으로 비교한다. 출력이 보존되지 않은 항목은 추측하지 않고
`not verified`로 표시한다.

## 2. 핵심 비교표

| 영역 | Repository 기준 | Production evidence | 상태 | 비고 |
|---|---|---|---|---|
| `projects` | `db.py`에 `BIGSERIAL` PK, 필수 `name`/`slug`/`status`, status CHECK, timestamps, slug UNIQUE 및 관련 index bootstrap이 있음 | 7 columns, PK, UNIQUE slug, status CHECK, PK 및 slug indexes가 catalog에서 확인됨; 3 rows | **confirmed match** | Production catalog가 bootstrap DDL과 일치함 |
| `memories` | `db.py`에 `BIGSERIAL` PK, content/type/importance CHECK, `VECTOR(384)`, timestamps가 있고 `project_id` FK/index를 추가함 | 10 columns, importance CHECK, PK, `project_id` FK, PK 및 project index가 catalog에서 확인됨; 5 rows | **confirmed match** | Production catalog가 bootstrap DDL과 일치함 |
| `documents` | 애플리케이션 query contract만 존재하며 complete `CREATE TABLE` DDL 없음 | 16 columns, 55 rows; `project_id` NULL 0/orphan 0; PK, FK, status CHECK 및 4개 non-PK index 확인 | **confirmed difference** | 실제 Production 정의는 확보했지만 repository DDL ownership은 없음 |
| `document_chunks` | 애플리케이션 query contract만 존재하며 complete `CREATE TABLE` DDL 없음 | 7 columns, 1,041 rows; PK, UNIQUE, FK 및 1개 non-PK index 확인 | **confirmed difference** | 실제 Production 정의는 확보했지만 repository DDL ownership은 없음 |
| FK | `db.py`가 `memories.project_id -> projects.id`를 정의함; documents/chunks DDL 없음 | 3개 FK가 직접 확인됨 | **confirmed partial match** | FK details는 아래 3절 참조 |
| Indexes | `idx_projects_slug`, `idx_memories_project_id`가 runtime bootstrap에 정의됨 | `documents`/`document_chunks` index definitions를 직접 확인; `projects`/`memories` 전체는 미보존 | **confirmed for documents/chunks; otherwise not verified** | 아래 6절 참조 |
| Extensions | Compose는 pgvector PostgreSQL 17 이미지를 사용 | `plpgsql 1.0`, `vector 0.8.6` | **confirmed presence; exact repository parity not verified** | 이미지 설정과 catalog version의 완전한 동일성은 별도 검증 대상 |
| Migration metadata | custom migration contract와 설계 문서만 있음; DB runner/table 생성 없음 | 후보 query 결과 0 rows | **not verified as absence** | 후보 테이블 부재와 빈 테이블을 구분할 전체 출력이 보존되지 않음 |

## 3. Production에서 확인된 FK

별도 query는 `public` schema에서 다음 source table을 대상으로 실행되었다.

- `public.document_chunks.document_id`
  → `public.documents.id`
  - constraint: `document_chunks_document_id_fkey`
  - `ON UPDATE NO ACTION`
  - `ON DELETE CASCADE`
- `public.documents.project_id`
  → `public.projects.id`
  - constraint: `documents_project_id_fkey`
  - `ON UPDATE NO ACTION`
  - `ON DELETE SET NULL`
- `public.memories.project_id`
  → `public.projects.id`
  - constraint: `memories_project_id_fkey`
  - `ON UPDATE NO ACTION`
  - `ON DELETE SET NULL`

따라서 repository의 `memories.project_id` 관계는 Production evidence와
일치한다. `documents`와 `document_chunks` 관계도 Production에는 존재하지만,
해당 테이블의 complete repository DDL이 없어 repository schema ownership은
불완전하다.

## 4. 현재 Production에 이미 존재하는 구조

다음은 supplied audit evidence로 확인된 범위다.

- PostgreSQL 17.11
- `public.projects`, `public.memories`, `public.documents`,
  `public.document_chunks`
- `projects`: 3 rows
- `memories`: 5 rows
- `documents`: 55 rows
- `document_chunks`: 1,041 rows
- `vector` extension 0.8.6 및 `plpgsql` 1.0
- 위 3개 public core FK
- `memories.project_id` NULL 0, orphan 0
- `documents.project_id` NULL 0, orphan 0
- `document_chunks.document_id` orphan 0

Keycloak schema는 AI-Hub public application schema와 별도 infrastructure
영역으로 취급한다. 현재 보존된 compact evidence만으로 Keycloak의 전체
table/column inventory를 재작성하지 않는다.

## 5. Schema ownership 판정

| Table | 판정 | 근거 |
|---|---|---|
| `projects` | **repository-owned (bootstrap-owned)** | [`api/app/core/db.py`](../api/app/core/db.py)의 `ensure_projects_table()`에 `CREATE TABLE IF NOT EXISTS` DDL이 있음. 단, versioned migration source는 아님 |
| `memories` | **repository-owned (bootstrap-owned)** | [`api/app/core/db.py`](../api/app/core/db.py)의 `ensure_memories_table()`와 `project_id`/index bootstrap이 있음. 단, versioned migration source는 아님 |
| `documents` | **application-contract-only; Production schema captured** | router/core/search 코드가 columns를 전제하지만 repository `CREATE TABLE` 또는 migration DDL은 없음 |
| `document_chunks` | **application-contract-only; Production schema captured** | router/search 코드가 columns를 전제하지만 repository `CREATE TABLE` 또는 migration DDL은 없음 |

따라서 네 테이블 모두에 대해 현재 repository가 완전한 versioned schema
source of truth인 것은 아니다. 향후 baseline source of truth는 Production
catalog snapshot과 검토된 repository DDL을 함께 확정한 뒤 별도로 정해야
한다. 현재 `db.py` bootstrap을 migration baseline으로 간주하지 않는다.

## 6. `documents` / `document_chunks` Production schema

### `public.documents`

Production catalog query에서 다음 16개 column을 확인했다.

| Position | Column | Type | Nullable | Default |
|---:|---|---|---|---|
| 1 | `id` | `bigint` | NO | `nextval('documents_id_seq'::regclass)` |
| 2 | `project_id` | `bigint` | YES | — |
| 3 | `title` | `text` | NO | — |
| 4 | `filename` | `text` | YES | — |
| 5 | `mime_type` | `text` | YES | — |
| 6 | `source` | `text` | YES | — |
| 7 | `description` | `text` | YES | — |
| 8 | `status` | `text` | NO | `'active'::text` |
| 9 | `created_at` | `timestamp with time zone` | NO | `now()` |
| 10 | `updated_at` | `timestamp with time zone` | NO | `now()` |
| 11 | `file_path` | `text` | YES | — |
| 12 | `file_size` | `bigint` | YES | — |
| 13 | `sha256` | `text` | YES | — |
| 14 | `document_type` | `text` | YES | — |
| 15 | `relative_path` | `text` | YES | — |
| 16 | `file_hash` | `text` | YES | — |

Constraints:

- `documents_pkey`: `PRIMARY KEY (id)`
- `documents_project_id_fkey`: `FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL`
- `documents_status_check`: `CHECK (status = ANY (ARRAY['active', 'archived']))`

Indexes:

- `documents_pkey`: unique primary btree index on `(id)`
- `idx_documents_project_id`: btree index on `(project_id)`
- `idx_documents_project_relative_path`: btree index on `(project_id, relative_path)`
- `idx_documents_sha256`: btree index on `(sha256)`
- `idx_documents_status`: btree index on `(status)`

### `public.document_chunks`

Production catalog query에서 다음 7개 column을 확인했다.

| Position | Column | Type | Nullable | Default |
|---:|---|---|---|---|
| 1 | `id` | `bigint` | NO | `nextval('document_chunks_id_seq'::regclass)` |
| 2 | `document_id` | `bigint` | NO | — |
| 3 | `chunk_index` | `integer` | NO | — |
| 4 | `content` | `text` | NO | — |
| 5 | `page_number` | `integer` | YES | — |
| 6 | `embedding` | `vector(384)` | YES | — |
| 7 | `created_at` | `timestamp with time zone` | NO | `now()` |

Constraints:

- `document_chunks_pkey`: `PRIMARY KEY (id)`
- `document_chunks_document_id_chunk_index_key`: `UNIQUE (document_id, chunk_index)`
- `document_chunks_document_id_fkey`: `FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE`

Indexes:

- `document_chunks_pkey`: unique primary btree index on `(id)`
- `document_chunks_document_id_chunk_index_key`: unique btree index on `(document_id, chunk_index)`
- `idx_document_chunks_document_id`: btree index on `(document_id)`

The catalog output confirms `vector(384)` for `document_chunks.embedding`.
No additional Production columns, constraints, or indexes should be inferred
from application code beyond this captured output.

## 7. `projects` / `memories` Production schema

### `public.projects`

Production catalog query에서 다음 7개 column을 확인했다.

| Position | Column | Type | Nullable | Default |
|---:|---|---|---|---|
| 1 | `id` | `bigint` | NO | `nextval('projects_id_seq'::regclass)` |
| 2 | `name` | `text` | NO | — |
| 3 | `slug` | `text` | NO | — |
| 4 | `description` | `text` | YES | — |
| 5 | `status` | `text` | NO | `'active'::text` |
| 6 | `created_at` | `timestamp with time zone` | NO | `now()` |
| 7 | `updated_at` | `timestamp with time zone` | NO | `now()` |

Constraints and indexes:

- `projects_pkey`: `PRIMARY KEY (id)`
- `projects_slug_key`: `UNIQUE (slug)`
- `projects_status_check`: status is `active`, `archived`, or `completed`
- `projects_pkey`: unique primary btree index on `(id)`
- `projects_slug_key`: unique btree index on `(slug)`
- `idx_projects_slug`: btree index on `(slug)`

### `public.memories`

Production catalog query에서 다음 10개 column을 확인했다.

| Position | Column | Type | Nullable | Default |
|---:|---|---|---|---|
| 1 | `id` | `bigint` | NO | `nextval('memories_id_seq'::regclass)` |
| 2 | `content` | `text` | NO | — |
| 3 | `memory_type` | `text` | NO | `'fact'::text` |
| 4 | `category` | `text` | YES | — |
| 5 | `importance` | `integer` | NO | `3` |
| 6 | `source` | `text` | YES | — |
| 7 | `embedding` | `vector(384)` | YES | — |
| 8 | `created_at` | `timestamp with time zone` | NO | `now()` |
| 9 | `updated_at` | `timestamp with time zone` | NO | `now()` |
| 10 | `project_id` | `bigint` | YES | — |

Constraints and indexes:

- `memories_pkey`: `PRIMARY KEY (id)`
- `memories_importance_check`: `CHECK (importance >= 1 AND importance <= 5)`
- `memories_project_id_fkey`: `FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL`
- `memories_pkey`: unique primary btree index on `(id)`
- `idx_memories_project_id`: btree index on `(project_id)`

The catalog output confirms `vector(384)` for `memories.embedding`.

## 8. Production baseline evidence 상태

### Complete in the currently supplied evidence

- PostgreSQL version and read-only transaction completion
- `vector`/`plpgsql` extension versions
- `documents` and `document_chunks` columns, defaults, nullability,
  constraints, and indexes
- The three public core foreign keys and their actions
- Core row counts and the supplied NULL/orphan checks

### Partially verified

- Migration metadata candidate query result (`0 rows`)

### Missing or unresolved

- Whether each migration metadata candidate table is absent or present but
  empty
- A versioned repository DDL/baseline that owns all four existing tables
- The complete Keycloak catalog inventory

## 9. Repository에는 있으나 Production과의 일치가 완전히 검증되지 않은 구조

- `idx_projects_slug`
- `idx_memories_project_id`
- Compose image 설정과 Production extension version의 exact parity
- custom migration contract가 요구하는 `schema_migrations` metadata table

이는 Production에 없다는 뜻이 아니다. 현재 확보된 evidence의 보존 범위로
완전한 비교를 할 수 없다는 뜻이다.

## 10. Production에는 있으나 repository 정의가 불완전한 구조

`documents`와 `document_chunks`의 Production catalog는 이제 확보되었다.
그러나 repository에 complete `CREATE TABLE` DDL이 없으므로 다음을
repository schema source만으로 재현하거나 drift 검출할 수 없다.

- 전체 column 및 data type
- nullability와 default
- 전체 PK/UNIQUE/CHECK constraint
- 전체 index 및 vector index definition
- 최초 생성 경로와 현재 schema revision

따라서 이번 단계의 핵심 discrepancy는 Production schema 자체의 미확인이
아니라, 확인된 Production schema에 대한 repository DDL ownership의
부재다.

## 11. Migration mechanism과 metadata 판단

Repository에는 Alembic, yoyo, Flyway, Liquibase, Django migration 또는
실제 custom migration runner가 없다. 대신
[`api/app/core/migration_contract.py`](../api/app/core/migration_contract.py)는
향후 migration filename, ordering, checksum, applied checksum validation을
위한 DB-free contract를 제공한다.

[`docs/MIGRATION_ARCHITECTURE.md`](MIGRATION_ARCHITECTURE.md)는
`schema_migrations`를 계획된 metadata table로 설명하지만 현재 생성하지
않는다. Production audit에서 migration metadata 후보가 `0 rows`였다는
사실은 기록할 수 있으나, 그것만으로 migration system이 전혀 없다고
단정하지 않는다. 후보 table의 부재와 존재하지만 비어 있는 상태를 모두
구분할 수 있는 전체 출력이 이 문서 작성 시점에 보존되어 있지 않다.

## 12. Migration readiness

### 현재 판단

**Workspace/identity migration을 바로 실행할 readiness는 여전히 확보되지
않았다.**

네 핵심 테이블의 Production columns, constraints, indexes가 모두
확인되어 schema uncertainty는 해소되었다. 그러나 기존 Production
schema의 versioned ownership과 revision metadata가 확보되지 않았고,
현재 repository에는 네 테이블 전체를 소유하는 migration DDL/runner가
없다. 현재 evidence에서는 audited FK와 orphan checks가 일관되게
확인되었다.

### Migration 전 반드시 해결할 항목

1. 네 핵심 테이블의 Production catalog snapshot을 baseline evidence로
   보존하고 검토한다. 이를 곧바로 migration SQL로 실행하지 않는다.
2. 기존 Production baseline과 fresh-database baseline을 위한 repository
   source of truth와 도입 절차를 결정한다.
3. migration metadata 후보 table의 existence와 row state를 구분해
   확인한다.
4. Existing Production baseline과 fresh-install baseline을 분리할지
   결정한다.
5. baseline checksum과 Production snapshot의 관계를 결정한다.
6. Workspace backfill 정책과 `project_id`의 NULL/ownership 정책을 먼저
   결정한다.
7. backup, lock, transaction, rollback 및 실패 시 복구 절차를 실제
   운영 절차로 확정한다.

### 위험한 실행 후보

- 현재 `db.py` bootstrap을 complete baseline으로 간주하는 것
- `documents` 또는 `document_chunks`를 모르는 상태에서 `CREATE TABLE`,
  `ALTER TABLE`, constraint/index 추가를 실행하는 것
- Production schema를 새 baseline으로 선언하면서 기존 데이터와 metadata
  처리 방식을 정의하지 않는 것
- migration metadata가 없다고 가정하고 새 metadata table을 즉시 생성하는 것
- Workspace FK와 NOT NULL 제약을 backfill 정책 없이 적용하는 것

## 13. 다음 단계

다음 단계는 migration SQL 작성이 아니라 다음 evidence와 정책을 확정하는
것이어야 한다.

1. 네 테이블의 보존된 Production catalog를 baseline evidence로 승인한다.
2. 네 테이블의 repository schema source of truth와 DDL ownership을 결정한다.
3. Existing-Production baseline과 fresh-database baseline을 각각 설계한다.
4. migration metadata table의 도입 시점과 기존 DB 등록 절차를 결정한다.
5. Workspace/identity 설계에서 기존 project/document/memory data의
   ownership backfill 규칙을 결정한다.
6. 위 결정 이후에만 migration revision 설계와 DB-free validation을
   작성한다.

이 문서와 기존 audit 결과는 migration을 실행하지 않았으며, Production
DB를 변경하지 않았다.
