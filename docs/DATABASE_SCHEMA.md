# AI-Hub Database Schema

## 1. 목적

이 문서는 AI-Hub의 Production PostgreSQL schema audit 결과와 repository에 기록된 schema 정의의 차이를 정리한다. Production 관련 내용은 실제 Production PostgreSQL catalog를 READ-ONLY로 조회해 확인한 결과를 기준으로 한다.

## 2. 현재 Production DB 기준

- `projects`, `memories`, `documents`, `document_chunks` 테이블이 존재한다.
- `projects`와 `memories`의 schema는 repository DDL과 일치한다.
- `documents`와 `document_chunks`는 Production DB에 존재하지만 repository에는 해당 테이블의 `CREATE TABLE` DDL이 없다.
- `memories.embedding`과 `document_chunks.embedding`은 `vector(384)`이다.
- pgvector extension version은 `0.8.6`이다.

이 문서는 Production DB의 현재 schema를 기록하며, 확인되지 않은 컬럼이나 데이터 타입은 기재하지 않는다.

## 3. projects

Repository DDL은 [api/app/core/db.py](../api/app/core/db.py)의 `ensure_projects_table()`에 정의되어 있다. Production audit에서 이 schema가 repository DDL과 일치함을 확인했다.

| Column | Definition |
| --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` |
| `name` | `TEXT NOT NULL` |
| `slug` | `TEXT NOT NULL UNIQUE` |
| `description` | `TEXT` |
| `status` | `TEXT NOT NULL DEFAULT 'active'`; `active`, `archived`, `completed` 중 하나를 허용하는 CHECK constraint |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` |

## 4. memories

Repository DDL은 [api/app/core/db.py](../api/app/core/db.py)의 `ensure_memories_table()`에 정의되어 있다. `project_id` 추가 정의도 같은 파일의 `ensure_projects_table()`에 있으며, Production audit에서 schema가 repository DDL과 일치함을 확인했다.

| Column | Definition |
| --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` |
| `content` | `TEXT NOT NULL` |
| `memory_type` | `TEXT NOT NULL DEFAULT 'fact'` |
| `category` | `TEXT` |
| `importance` | `INTEGER NOT NULL DEFAULT 3`; 1부터 5까지 허용하는 CHECK constraint |
| `source` | `TEXT` |
| `embedding` | `VECTOR(384)` |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` |
| `project_id` | `BIGINT`, `projects(id)` 참조, `ON DELETE SET NULL` |

## 5. documents

Production DB에 테이블이 존재한다. 다만 repository에 `CREATE TABLE` DDL이 없어 Production의 전체 컬럼 정의를 repository로 재현할 수 없다.

애플리케이션 코드([api/app/routers/documents.py](../api/app/routers/documents.py), [api/app/core/documents.py](../api/app/core/documents.py))는 문서 조회/저장에 `id`, `project_id`, `title`, `filename`, `mime_type`, `source`, `description`, `status`, `created_at`, `updated_at` 등을 사용한다. 이는 애플리케이션 쿼리에서 참조하는 필드의 기록이며, Production catalog의 전체 컬럼 목록이나 각 필드의 타입을 대신하지 않는다.

Production에서 확인된 관계는 `documents.project_id`가 `projects.id`를 참조하고 삭제 시 `SET NULL`로 처리된다는 것이다.

## 6. document_chunks

Production DB에 테이블이 존재한다. repository에는 `CREATE TABLE` DDL이 없어 Production의 전체 컬럼 정의를 repository로 재현할 수 없다.

애플리케이션 코드([api/app/routers/documents.py](../api/app/routers/documents.py), [api/app/core/search.py](../api/app/core/search.py))는 chunk 조회와 검색에서 `id`, `document_id`, `chunk_index`, `content`, `page_number`, `created_at`, `embedding` 등을 참조한다. Production audit에서 확인된 embedding 타입은 `vector(384)`이다. 이 코드 참조는 전체 컬럼 정의나 그 밖의 타입을 의미하지 않는다.

Production에서 확인된 제약은 `(document_id, chunk_index)` 조합의 UNIQUE constraint이며, `document_id`는 `documents.id`를 참조하고 문서 삭제 시 chunk가 CASCADE 삭제된다.

## 7. pgvector

- Production의 pgvector version: `0.8.6`
- 확인된 vector column: `memories.embedding`, `document_chunks.embedding`
- 두 column 모두 `vector(384)`
- Repository Compose 설정은 `pgvector/pgvector:pg17-bookworm` 이미지를 사용한다([compose.yml](../compose.yml)).

## 8. Indexes

- `projects.slug`에는 UNIQUE index와 별도의 non-unique index가 함께 존재한다.
- Repository DDL은 `projects.slug`의 `UNIQUE` 정의와 `idx_projects_slug` non-unique index를 생성하도록 되어 있다.
- Repository DDL은 `memories.project_id`에 `idx_memories_project_id` index를 생성하도록 되어 있다.
- Production audit에서 HNSW 및 IVFFlat index는 확인되지 않았다.
- 그 밖의 Production index 목록은 이 문서의 audit 기록 범위에 포함되지 않는다.

## 9. Foreign Keys / Constraints

- `memories.project_id → projects.id`, `ON DELETE SET NULL`
- `documents.project_id → projects.id`, `ON DELETE SET NULL`
- `document_chunks.document_id → documents.id`, `ON DELETE CASCADE`
- `document_chunks(document_id, chunk_index)` UNIQUE
- `projects.slug` UNIQUE
- `projects.status`는 `active`, `archived`, `completed` 값으로 제한
- `memories.importance`는 1에서 5 사이로 제한

Repository DDL의 CHECK 및 UNIQUE 정의는 `projects`와 `memories` schema의 repository 정의에서 확인할 수 있다. `documents`와 `document_chunks` 관련 항목은 Production audit에서 확인한 내용이다.

## 10. Repository와 Production DB의 차이

`projects`와 `memories`는 repository의 DDL로 schema를 설명할 수 있고 Production schema와 일치한다. 반면 `documents`와 `document_chunks`는 API 코드가 테이블을 사용하고 Production DB에도 존재하지만, repository에 생성 DDL이 없다.

따라서 애플리케이션 코드만으로는 해당 두 테이블의 전체 schema 및 제약을 확인하거나 Production 상태를 완전히 재현할 수 없다.

## 11. Schema 재현성 문제

현재 repository만으로는 Production DB의 `documents`와 `document_chunks` schema를 재현할 수 없다. 새 환경을 구성하거나 복구할 때 이 두 테이블의 생성 정의와 확인된 제약을 repository에서 제공할 수 없는 상태다.

이 문서는 audit에서 확인한 사실을 기록한다. 누락된 DDL의 원인이나 두 테이블이 처음 생성된 경로는 확인되지 않았으므로 추정하지 않는다.

## 12. 향후 Migration / Bootstrap 방향

향후 schema 변경과 초기 구성을 repository에서 관리할 수 있도록 migration 또는 명시적인 bootstrap schema를 정하고, `documents` 및 `document_chunks`의 Production 정의를 확인 가능한 버전 관리 파일로 반영하는 것이 필요하다. 실제 migration이나 bootstrap 파일을 추가하기 전에는 Production schema와의 전체 비교 및 기존 데이터에 대한 적용 계획을 마련해야 한다.

이 절은 향후 관리 방향을 제안하며, 현재 migration 또는 bootstrap 방식이 이미 구현되어 있다는 뜻은 아니다.
