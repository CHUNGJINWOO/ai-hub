AI Knowledge Hub — Oracle Cloud 기반 개인 Memory/RAG 시스템 구축기

ChatGPT, Claude, Gemini 등 여러 AI를 쓰면서도 공통된 지식·기억 기반을 갖기 위한 개인용 벡터 검색 백엔드를, 이미 운영 중이던 Oracle Cloud ARM 서버 위에 구축한 기록

0. 폴더 구조
.
├── README.md                  (이 문서)
└── api/
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        └── main.py             # FastAPI 앱 전체 (Memory 저장/검색 API)
1. 왜 만들었는가

ChatGPT, Claude, Gemini, Perplexity를 상황에 따라 나눠 쓰다 보니, 각 서비스가 "나"에 대해 알고 있는 맥락이 전부 따로 놀았다. 예를 들어 한 번은 Claude에게 설명한 프로젝트 배경을 다른 날 ChatGPT에게 처음부터 다시 설명해야 하는 식이었다.

목표: 어떤 AI를 쓰든 참조할 수 있는 공통 기억 저장소를 직접 만들고, 그 위에 검색(RAG) 기능을 얹어서 "예전에 내가 뭘 결정했는지, 뭘 겪었는지"를 AI가 찾아 답할 수 있게 만드는 것.

이미 LIMO 캡스톤 프로젝트용으로 Oracle Cloud Always Free 인스턴스를 운영하고 있었기 때문에, 새 계정을 만드는 대신(오라클 정책상 1인 1계정만 허용) 같은 서버를 공유하는 방향으로 설계했다.

2. 최종 아키텍처
              AI의 두뇌
       ┌────────┼────────┐
       ↓        ↓        ↓
     ChatGPT  Claude   Gemini / Perplexity
       │        │        │
       └────────┼────────┘
                ↓
         (MCP 연동 예정)
                ↓
       ┌─────────────────────────┐
       │ Oracle Cloud ARM64        │
       │  ros2-server               │
       │                           │
       │  FastAPI (127.0.0.1:8000) │
       │       │                   │
       │  multilingual-e5-small    │
       │  (384차원 임베딩)          │
       │       │                   │
       │  PostgreSQL 17 + pgvector │
       │  (Docker 내부 네트워크)    │
       └─────────────────────────┘

핵심 설계 원칙: Oracle 서버는 "AI의 두뇌"가 아니라 기억장치 + 검색 엔진이다. 실제 추론은 각 AI 서비스가 하고, 이 서버는 관련된 맥락(memory)을 찾아 전달하는 역할만 한다.

네트워크 원칙: PostgreSQL은 docker-compose.yml에 ports:를 아예 지정하지 않아, 호스트에도 인터넷에도 노출되지 않는다. 같은 Docker 네트워크 안에서만 postgres:5432라는 서비스 이름으로 접근 가능하다. FastAPI도 127.0.0.1:8000으로만 열려 있어, 현재는 오라클 서버 내부에서만 접근할 수 있다 (외부 접근은 추후 Tailscale + 인증을 붙여서 별도로 열 예정 — 이미 이 서버에서 사고를 겪었던 code-server Funnel 노출 건과는 분리해서 관리한다).

3. 왜 이 구성 요소들을 골랐는가
구성 요소	선택 이유
PostgreSQL + pgvector	별도의 벡터 전용 DB(Pinecone, Milvus 등)를 새로 운영하지 않고, 이미 잘 아는 관계형 DB 안에서 벡터 검색까지 처리. 일반 메타데이터(카테고리, 중요도, 시각)와 벡터를 한 테이블에서 같이 다룰 수 있다.
Docker	ARM64(aarch64) 환경에서 의존성 충돌 없이 재현 가능한 환경을 보장. 서버를 재설정해야 할 때도 docker compose up만으로 복구 가능.
intfloat/multilingual-e5-small	94개 언어를 지원하는 다국어 임베딩 모델 중, 파라미터 수가 작아(약 0.1B) 2코어/12GB인 이 서버에서 가볍게 돌아간다. 출력 차원도 384로 작아 DB 저장 공간과 검색 연산 비용을 줄여준다. bge-m3(1024차원) 같은 더 큰 모델은 나중에 실제로 부족함을 느낄 때 고려하기로 했다.
FastAPI	Pydantic 기반 자동 요청 검증과 Swagger UI(/docs)를 기본 제공해, API를 빠르게 만들고 테스트할 수 있다.
4. 구현 및 코드 설명
4.1 PostgreSQL + pgvector 컨테이너

Docker 컨테이너로 pgvector/pgvector:pg17-bookworm 이미지를 사용해 PostgreSQL 17과 pgvector 확장을 함께 띄웠다. DB 생성 후 확장 활성화:

sql
CREATE EXTENSION vector;

이 명령은 접속한 데이터베이스 단위로 적용된다 — 서버 전체가 아니라 aihub 데이터베이스에서만 pgvector 함수/연산자를 쓸 수 있게 된다.

4.2 DB 연결 — pgvector 타입을 Python에 등록
python
def get_db_connection():
    conn = psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )
    register_vector(conn)   # 이 커넥션에서 Vector 객체 ↔ PostgreSQL vector 타입 매핑
    return conn

설명: psycopg는 기본적으로 Python의 list[float]를 PostgreSQL의 double precision[](일반 실수 배열)로 취급한다. 그런데 pgvector의 <=>(코사인 거리) 연산자는 vector 타입끼리만 계산할 수 있어서, 타입을 명시적으로 등록해줘야 한다. register_vector(conn)이 이 커넥션에서 pgvector.Vector 객체를 PostgreSQL의 vector 타입으로 변환하도록 만들어준다. (커넥션마다 개별적으로 등록해야 하는 점이 포인트 — 전역 설정이 아니다.)

4.3 데이터 모델 및 테이블 생성
python
class MemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    memory_type: str = "fact"
    category: str | None = None
    importance: int = Field(default=3, ge=1, le=5)
    source: str | None = "api"

Pydantic 모델로 요청 body를 선언하면 FastAPI가 자동으로 JSON 검증과 Swagger 문서화를 처리해준다. importance에 ge=1, le=5 제약을 걸어 잘못된 값(예: 10점)이 아예 DB까지 도달하지 못하게 막는다.

python
@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_memories_table()
    yield

app = FastAPI(title="AI Knowledge Hub", lifespan=lifespan)

앱 시작 시 필요한 초기화(테이블 존재 확인)를 lifespan 컨텍스트 매니저로 처리한다. FastAPI는 예전 방식인 @app.on_event("startup")을 지원 중단(deprecated) 표시했고, 현재는 이 lifespan 패턴을 공식적으로 권장한다.

4.4 임베딩 생성 및 저장 — query/passage 구분
python
embedding = Vector(
    model.encode(
        "passage: " + memory.content,
        normalize_embeddings=True,
    ).tolist()
)

왜 "passage: "를 붙이는가: E5 계열 임베딩 모델은 "검색되는 대상(저장되는 문서)"과 "검색하는 질문"을 서로 다른 접두사(passage: / query:)를 붙여 학습되었다. 이 구분을 지키지 않으면 임베딩 자체는 생성되지만 검색 정확도가 떨어진다.

4.5 의미 기반 검색
python
query_embedding = Vector(
    model.encode("query: " + q, normalize_embeddings=True).tolist()
)

rows = conn.execute(
    """
    SELECT id, content, memory_type, category, importance, source,
           embedding <=> %s AS distance
    FROM memories
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> %s
    LIMIT %s;
    """,
    (query_embedding, query_embedding, limit),
).fetchall()

<=>는 pgvector의 코사인 거리 연산자로, 값이 작을수록 의미적으로 더 유사하다. ORDER BY에 같은 연산을 사용해 거리순으로 정렬한다.

실제 검증 결과: "나는 ROS2로 자율주행 로봇 시뮬레이션을 해보고 싶어"라고 질문했을 때, 저장된 문장에 "ROS2"라는 단어가 없는 "자율주행, 로봇공학에 관심" 메모리도 거리 0.15 수준으로 상위에 검색됨 — 단순 키워드 매칭이 아니라 의미 기반 검색이 실제로 작동함을 확인했다.

5. 트러블슈팅 기록
5.1 Connection reset by peer — API 컨테이너 재빌드 직후

증상: docker compose up -d --build api 직후 바로 curl을 날리면 연결이 끊김. 원인: 컨테이너가 뜨는 것과 그 안의 FastAPI/임베딩 모델이 완전히 로드되어 요청을 받을 준비가 되는 것 사이에는 시간차가 있다. 모델(약 470MB)을 처음 로드하는 데 몇 초가 걸리는데, 그 창이 끝나기 전에 요청을 보내면 연결이 거부된다. 해결: docker compose logs --tail=50 api로 Uvicorn running on http://0.0.0.0:8000 로그가 실제로 찍힌 뒤에 요청을 보내도록 순서를 바꿈. 재현 가능한 패턴이라 스크립트에는 헬스체크 재시도 로직을 넣는 것도 고려 중.

5.2 operator does not exist: vector <=> double precision[]

증상: POST /memories는 성공했는데 GET /memories/search만 500 에러. 원인: 4.2에서 설명한 타입 문제. Python list[float]를 그냥 넘기면 PostgreSQL이 double precision[]로 인식해 vector와 연산이 안 됨. INSERT는 타입 추론이 관대해 우연히 통과했지만, <=> 연산은 엄격하게 타입을 요구해서 여기서만 에러가 드러났다. 해결: pgvector-python 패키지의 Vector 클래스와 register_vector(conn)을 도입해 타입을 명시.

5.3 cannot adapt type 'Vector' using placeholder '%s'

증상: Vector 객체를 도입한 직후에도 여전히 에러. 원인: Vector(...)로 값은 감쌌지만, 그 값을 사용하는 커넥션에 register_vector(conn)이 호출되지 않은 상태였다. Vector 객체를 만드는 것과 그 타입을 psycopg가 이해하도록 등록하는 것은 별개의 단계다. 해결: get_db_connection() 내부에서 커넥션 생성 직후 항상 register_vector(conn)을 호출하도록 통일. (헷갈리기 쉬운 부분이라, 이 프로젝트에서 새 DB 커넥션을 만드는 함수는 반드시 이 한 줄을 포함하도록 컨벤션으로 정함.)

5.4 같은 서버에서 이미 code-server 보안 사고를 겪은 뒤였다

이 프로젝트를 시작하기 직전, 같은 오라클 서버에서 운영 중이던 code-server(브라우저 기반 VS Code)가 약한 비밀번호로 공인 인터넷에 노출되어 있던 것을 발견하고 fail2ban + Gmail 알림으로 대응한 사건이 있었다 (별도 문서 참고). 그 경험 때문에 이번 AI Hub는 처음부터 PostgreSQL에 ports:를 아예 안 쓰는 방식으로 설계했다 — "일단 만들고 나중에 막기"가 아니라 "애초에 열 필요가 없는 구조"를 우선한 것.

6. 현재까지 완료된 것 / 다음 단계
 Docker + PostgreSQL 17 + pgvector 0.8.6 (ARM64에서 정상 동작 확인)
 FastAPI: POST /memories, GET /memories/search
 multilingual-e5-small 임베딩 + 코사인 유사도 검색 실측 검증
 DB 비밀번호를 무작위 값으로 로테이션, .env는 .gitignore 처리
 Memory CRUD 완성 (GET /memories, GET/{id}, PATCH/{id}, DELETE/{id})
 projects 테이블 — Memory를 프로젝트 단위로 묶기
 documents / document_chunks — PDF/문서 기반 RAG
 MCP 연동 — ChatGPT/Claude/Gemini가 실제로 이 API를 참조하게 만들기
 외부 접근을 위한 인증 계층 추가 (Tailscale 경유, code-server Funnel과는 분리)
7. 이 프로젝트로 얻은 것
항목	내용
벡터 검색 실전 경험	pgvector를 이용해 임베딩 저장부터 코사인 유사도 검색까지 엔드투엔드로 직접 구현하고 검증
타입 시스템 디버깅	Python↔PostgreSQL 간 타입 불일치(double precision[] vs vector) 문제를 원인까지 추적해 해결
인프라 재사용 설계	새 클라우드 계정을 만드는 대신, 기존 서버에 역할을 안전하게 추가하는 멀티 테넌트적 사고 (Docker 내부망 분리로 기존 워크로드와 충돌 없이 공존)
보안을 설계 단계에 반영	이전 프로젝트(code-server)에서 겪은 사고를 교훈 삼아, 이번엔 "포트를 열고 나중에 막기"가 아니라 처음부터 노출 최소화 구조로 설계
모델 선택 기준 수립	무조건 최신/고성능 모델을 고르지 않고, 실제 서버 스펙(2코어/12GB, ARM64)과 언어 요구사항(한국어 포함)을 기준으로 실용적인 선택


## 8. Documents / RAG — 파일 업로드 및 자동 Chunk/Embedding 파이프라인

### 8.1 왜 필요했는가

지금까지의 Memory API는 사람이 문장을 직접 타이핑해서 `POST /memories`로 넣는 구조였다.
이건 "짧은 사실이나 결정사항"을 기록하기엔 좋지만, 실제로 쌓여있는 지식(강의 PDF,
매뉴얼, 프로젝트 문서)을 그대로 검색 가능하게 만들 수는 없었다. Documents/RAG는
**원본 파일을 업로드하면 자동으로 검색 가능한 지식으로 변환**하는 파이프라인이다.

```
파일 업로드 (PDF/TXT/Markdown)
        ↓
   텍스트 추출
        ↓
   Chunk 분할 (1200자 단위, 200자 오버랩)
        ↓
   각 chunk를 multilingual-e5-small로 임베딩
        ↓
   PostgreSQL(documents, document_chunks) + pgvector 저장
        ↓
   /documents/search, /context/search로 의미 기반 검색
```

### 8.2 DB 스키마

```sql
CREATE TABLE documents (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    filename TEXT,
    mime_type TEXT,
    source TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    file_path TEXT,
    file_size BIGINT,
    sha256 TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE document_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    page_number INTEGER,
    embedding VECTOR(384),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);
```

**설계 포인트**:
- `document_chunks.document_id`에 `ON DELETE CASCADE` — 문서를 지우면 그 안의 chunk도
  자동으로 같이 삭제된다. (`memories.project_id`가 `SET NULL`이었던 것과 대조적으로,
  여기서는 chunk가 문서 없이 홀로 존재할 이유가 없기 때문에 CASCADE가 맞는 선택이다.)
- `sha256` 컬럼과 그 인덱스는 8.4에서 설명하는 중복 업로드 방지용이다.

### 8.3 텍스트 추출 및 Chunk 분할 (`core/document_ingest.py`)

```python
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

def chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(0, end - CHUNK_OVERLAP)
    return chunks
```

**왜 오버랩(`CHUNK_OVERLAP=200`)을 두는가**: chunk를 겹치지 않게 딱 잘라버리면, 문장이
chunk 경계에서 반으로 잘려 앞뒤 맥락이 끊길 수 있다. 200자를 겹치게 하면 경계 부근의
내용이 두 chunk 모두에 포함돼, 검색 시 맥락이 끊긴 chunk만 걸리는 상황을 줄여준다.

파일 형식에 따라 추출 방식을 분기한다:
```python
def extract_text(path: Path, mime_type: str | None) -> list[tuple[int | None, str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf" or mime_type == "application/pdf":
        return extract_text_from_pdf(path)   # 페이지 번호 보존
    if suffix in {".txt", ".md", ".markdown"}:
        return extract_text_from_plain_file(path)  # 페이지 개념 없음 → None
    raise ValueError("Unsupported document type...")
```
PDF는 페이지 단위로 텍스트를 뽑아 `page_number`를 함께 보존하지만(`pypdf`의
`reader.pages` 순회), TXT/Markdown은 페이지 개념이 없어 `None`으로 남긴다. 이렇게 하면
나중에 검색 결과에 "몇 페이지에서 찾았는지"를 PDF에 한해 보여줄 수 있다.

### 8.4 업로드 API — SHA-256 기반 저장

```python
raw_bytes = await file.read()
sha256 = hashlib.sha256(raw_bytes).hexdigest()
target_name = f"{sha256}_{safe_name}"
target_path = upload_root / target_name
target_path.write_bytes(raw_bytes)
```

파일을 원본 이름 그대로 저장하지 않고, **내용의 SHA-256 해시를 파일명 앞에 붙여서**
저장한다. 두 가지 이점이 있다:
1. 같은 이름의 파일을 여러 번 올려도 내용이 다르면 파일명이 겹치지 않는다.
2. 나중에 "이 파일이 이미 업로드된 적 있는지"를 해시값으로 빠르게 조회할 수 있다
   (`idx_documents_sha256` 인덱스가 이를 위한 준비).

### 8.5 트러블슈팅

**`/documents/search`가 `/documents/{document_id}`에 가로채짐 (재발)**
Memory API에서 겪었던 것과 정확히 같은 원인의 문제가 Document Router에도 다시 발생했다.
`/documents/search`를 고정 경로 취급하지 않고 `document_id`로 파싱하려다 실패. 해결도
동일 — `/documents/search`를 `/documents/{document_id}`보다 먼저 선언. **이 패턴이
Memory에 이어 두 번째로 반복됐다는 점에서, 새 하위 리소스(`{id}`)를 추가할 때마다
"고정 경로가 위에 있는지" 체크리스트로 만들어 둘 필요가 있다.**

**Router 경로 이중 접두사 버그**: `APIRouter(prefix="/memories")`로 이미 prefix를
지정해놓고 각 엔드포인트 데코레이터에 또 `@router.get("/memories")`처럼 전체 경로를
써서, 실제 경로가 `/memories/memories`가 되어버린 문제. Router를 여러 개로 쪼개는
리팩터링 과정에서 기존 `@app.get("/memories")` 스타일 경로를 그대로 옮기다 생긴
실수였다. `prefix`를 쓰는 라우터의 엔드포인트는 prefix를 뺀 나머지 경로만 적어야
한다는 점을 이후 모든 라우터 분리에 동일하게 적용해 재발을 막았다.

**업로드 폼 필드가 반영되지 않는 문제 (title)**: FastAPI에서 `UploadFile`과 일반
스칼라 매개변수를 같은 함수에서 받을 때, 스칼라 매개변수를 `Form(...)`으로 명시하지
않으면 멀티파트 폼 필드가 아니라 **쿼리 파라미터**로 취급된다. `-F "title=..."`로
보낸 값이 무시되고 파일명에서 자동 생성한 제목으로 대체된 원인이 이것이었다.

### 8.6 현재까지 검증된 것

- TXT/Markdown 업로드 → chunk 생성 → embedding → `/context/search`에서 Memory와
  Document가 함께 검색되는 것까지 실제 데이터로 확인
- Hugging Face 모델 캐시를 Docker volume(`huggingface-cache/`)에 영속화해, 컨테이너를
  재생성할 때마다 471MB 모델을 다시 받지 않도록 개선
- main.py를 `core/`(DB, 임베딩 공통 로직)와 `routers/`(projects, memories, documents)로
  분리해 단일 파일이 1000줄 넘게 비대해지는 것을 방지

### 8.7 다음 단계 (TODO)

- [v] 실제 다페이지 PDF로 `page_number` 보존 및 다중 chunk 생성 검증
- [v] 업로드 `title` 폼 필드 반영 여부 재확인 (`Form()` 어노테이션 적용)
- [v] `context.py`, `documents.py` 라우터까지 분리 완료 여부 정리
- [v] MCP 서버 — ChatGPT/Claude/Gemini가 `/context/search`를 도구로 호출하게 연결