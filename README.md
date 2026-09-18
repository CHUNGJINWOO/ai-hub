AI Knowledge Hub

Oracle Cloud ARM64 서버에 구축한 개인용 Memory + RAG + Hybrid Search 시스템.

여러 AI 서비스에서 사용하는 개인 지식, 프로젝트 정보, 문서, 코드의 맥락을 하나의 저장소에 모으고, 질문에 맞는 관련 정보를 검색해 AI가 활용할 수 있도록 하는 것이 목표다.

현재 구현 범위는:

Memory
Projects
Documents
PDF / Office / 코드 ingestion
Embedding
pgvector
Semantic Search
Hybrid Search
ROS2 코드 검색

이며, 다음 단계로 검색 결과를 LLM의 답변 생성과 연결하는 것을 목표로 한다.

1. 현재 아키텍처
                    AI / Client
                         │
                         ▼
                 ┌──────────────┐
                 │   FastAPI    │
                 │ 127.0.0.1:8000│
                 └───────┬──────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
          Memory API          Documents API
                                    │
                                    ▼
                         Document Ingestion
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
             PDF                  Office                 Code
                           DOCX/XLSX/PPTX       Python/C/C++/XML/YAML...
              │                     │                     │
              └─────────────────────┼─────────────────────┘
                                    ▼
                                  Chunk
                                    │
                                    ▼
                    multilingual-e5-small
                             384 dimensions
                                    │
                                    ▼
                         PostgreSQL + pgvector
                                    │
                                    ▼
                            Hybrid Search

Oracle 서버는 직접 답변을 생성하는 AI가 아니라 저장소 + 검색 엔진 + context 제공 계층으로 사용한다.

2. 실제 프로젝트 구조
ai-hub/
├── README.md
├── compose.yml
├── .env
├── .gitignore
│
├── api/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       │
│       ├── core/
│       │   ├── db.py
│       │   ├── embedding.py
│       │   ├── document_ingest.py
│       │   └── document_parsers.py
│       │
│       └── routers/
│           ├── projects.py
│           ├── memories.py
│           ├── documents.py
│           └── context.py
│
├── postgres/
├── documents/
└── huggingface-cache/
3. 실행 환경
Oracle Cloud ARM64
Ubuntu 22.04
Docker
Docker Compose
Python 3.10
PostgreSQL 17
pgvector
FastAPI
intfloat/multilingual-e5-small

Embedding 모델은 현재 서버의 CPU/RAM 환경을 고려해 multilingual-e5-small을 사용한다.

4. Memory 시스템

짧은 사실, 결정, 프로젝트 맥락 등을 저장한다.

Memory
  ↓
passage embedding
  ↓
pgvector
  ↓
query embedding
  ↓
semantic search

E5 모델의 검색 규칙에 따라 저장할 때는:

passage: ...

검색할 때는:

query: ...

를 사용한다.

5. Documents / RAG

문서 파일을 직접 업로드하면 자동으로 검색 가능한 지식으로 변환한다.

파일
 ↓
형식 판별
 ↓
텍스트 추출
 ↓
Chunk
 ↓
Embedding
 ↓
PostgreSQL + pgvector

현재 지원 형식:

문서
PDF
TXT
Markdown
DOCX
XLSX
PPTX
코드 / 설정
Python
C
C++
Header
XML
YAML
JSON
Xacro
RViz
CMakeLists.txt
package.xml

현재 chunk 설정:

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
6. Office 문서 처리

document_parsers.py에서:

python-docx
openpyxl
python-pptx

를 사용한다.

PPTX는 slide 번호를 보존한다.

예:

[Slide 17]

& 연산자와 * 연산자

& 연산자: 변수의 주소를 반환한다.
* 연산자: 포인터가 가리키는 곳의 내용을 반환한다.

따라서 검색 결과에서 어느 슬라이드에서 가져온 정보인지 추적할 수 있다.

7. 문서 metadata

문서에는 다음과 같은 metadata를 저장한다.

document_type
relative_path
file_hash
sha256
file_size
mime_type

특히 relative_path가 중요하다.

ROS2 프로젝트처럼:

package_a/CMakeLists.txt
package_b/CMakeLists.txt

같은 이름의 파일이 여러 개 존재하는 경우 파일명만으로는 구별할 수 없기 때문이다.

8. SHA-256

파일 내용의 SHA-256을 계산해서 저장한다.

목적:

파일 식별
중복 확인
변경 감지
metadata 보강
불필요한 재임베딩 방지

실제 LIMO 프로젝트에서는 기존 44개 파일을 다시 embedding하지 않고 SHA-256을 이용해서 DB의 metadata만 보강했다.

9. Hybrid Search

현재 가장 중요한 검색 기능.

단순:

질문 → vector search → 결과

가 아니라:

질문
 ↓
query embedding
 ↓
vector candidate retrieval
 ↓
keyword / filename / title
 ↓
document type / extension
 ↓
검색 의도(code/document)
 ↓
reranking
 ↓
최종 결과

를 사용한다.

검색 결과에는:

distance
hybrid_score

가 함께 반환된다.

10. Code-aware Search

질문이:

코드
구현
함수
노드
토픽
driver
serial
cmd_vel
xacro
cpp
python

등의 성격을 가진다면 코드 파일을 우선적으로 평가한다.

반대로:

설명
개요
문서
README
overview

같은 질문은 문서형 결과를 선호하도록 한다.

그래서 LIMO에서:

/cmd_vel을 받는 코드는 어디에 있는가?

같은 질문을 했을 때 remote_teleop_node.py, limo_driver.cpp가 관련 결과로 올라왔다.

11. 실제 검증

C 언어 강의자료:

제2장 PDF
제3장~제11장 PPTX

총 10개 문서를 실제 서버에 업로드했다.

검증한 질문:

변수와 상수의 차이는 무엇인가?

→ 제4장 변수와자료형의 관련 슬라이드 검색

포인터에서 & 연산자와 * 연산자는 무엇을 하는가?

→ 제11장 포인터의 관련 슬라이드 검색

C 프로그램의 개발 과정에서 설계와 컴파일은 무엇인가?

→ 제2장 PDF의 관련 페이지 검색

즉 PDF와 PPTX 모두:

업로드
→ 추출
→ chunk
→ embedding
→ 검색

전체 파이프라인이 실제 자료로 검증됐다.

12. 자주 사용하는 명령
실행
cd /mnt/data/ai-hub
docker compose up -d
코드 변경 후 재빌드
docker compose up -d --build api
상태
docker compose ps
API 로그
docker compose logs --tail=100 api
API 컨테이너의 실제 코드 확인
docker exec ai-hub-api sh -c 'grep -n "allowed_suffixes" /app/app/routers/documents.py'
API 문서
http://127.0.0.1:8000/docs
13. 중요한 Troubleshooting
Docker 컨테이너에 코드가 반영되지 않는 경우

호스트 파일 수정만으로 실행 중인 이미지가 변경되는 것은 아니다.

docker compose up -d --build api

가 필요하다.

이번 PPTX 지원 과정에서도 이 문제를 확인했다.

pgvector type 오류
operator does not exist:
vector <=> double precision[]

→ Vector + register_vector(conn) 사용.

Router 경로 충돌
/documents/search

같은 고정 경로가:

/documents/{document_id}

보다 뒤에 있으면 "search"가 ID로 해석될 수 있다.

따라서 고정 경로를 dynamic path보다 먼저 선언한다.

14. 보안

GitHub에 올리지 않는 것:

.env
DB password
PostgreSQL data
업로드 문서
Hugging Face cache

현재 네트워크:

PostgreSQL → Docker 내부망
FastAPI → 127.0.0.1:8000

외부 AI와 직접 연결할 때는 인증 계층을 별도로 추가한다.

15. 현재 완료 상태
[x] PostgreSQL + pgvector
[x] FastAPI
[x] Memory CRUD
[x] Project 관리
[x] multilingual-e5-small
[x] PDF ingestion
[x] DOCX ingestion
[x] XLSX ingestion
[x] PPTX ingestion
[x] Python/C/C++/ROS2 code ingestion
[x] Chunk / Embedding
[x] Document metadata
[x] SHA-256
[x] relative_path
[x] Semantic Search
[x] Hybrid Search
[x] Code-aware reranking
[x] ROS2/LIMO indexing
[x] PDF/PPTX 실제 테스트
[x] GitHub 관리
16. 다음 개발 계획
[ ] Search → LLM RAG Answer
[ ] 검색 결과에 출처를 포함한 답변
[ ] Memory + Document 통합 Context
[ ] Conversation Context
[ ] 문서 삭제 시 실제 파일 cleanup
[ ] MIME type 정규화
[ ] 중복 업로드 자동 방지
[ ] 변경 파일 선택적 재임베딩
[ ] OCR
[ ] HWP / HWPX
[x] MCP Streamable HTTP
[x] MCP API key authentication
[x] 외부 MCP endpoint 검증
[ ] 표준 OAuth 기반 외부 AI 인증 안정화
[ ] Web UI
17. Git

현재 안정적인 기준점:

513b3f1
feat: expand document ingestion and hybrid search

개발 후:

git status
git diff --stat

git add ...
git commit -m "..."
git push origin main

최종적으로:

git status --short

가 아무것도 출력되지 않도록 관리한다.

18. 프로젝트에서 얻은 것

이 프로젝트를 통해 직접 경험한 핵심 내용:

pgvector 기반 vector search
Python ↔ PostgreSQL 타입 문제 디버깅
PDF/Office/code ingestion
embedding pipeline
hybrid retrieval / reranking
ROS2 프로젝트 코드 검색
SHA-256 기반 파일 식별
Docker 기반 ARM64 서버 운영
보안을 고려한 서비스 분리
실제 자료를 이용한 RAG 검색 검증

19. MCP 구축 / 인증 / 운영 기록

AI-Hub의 검색 기능을 외부 AI 클라이언트에서 사용할 수 있도록
Model Context Protocol(MCP) 서버를 구축했다.

MCP 서버:

api/app/mcp_server.py

전송 방식:

Streamable HTTP

Endpoint:

https://ros2-server.tail49948f.ts.net:10000/mcp

현재 MCP tool:

health_check
search_context

search_context는 기존 hybrid search를 그대로 재사용한다.

Memory
+
Document
+
pgvector semantic search
+
keyword / filename / title
+
code-aware reranking

구조이므로 MCP 전용 검색 로직을 별도로 만들지 않았다.

20. MCP 인증 구현

초기에는 Keycloak OAuth를 이용한 표준 MCP 인증을 구현했다.

Keycloak:

26.7.4

Realm:

ai-hub

Scope:

aihub:read

JWT 검증:

RS256
issuer
audience
exp
iat
iss
sub
scope

Claude Custom Connector의 실제 연결 과정에서는 ofid_* 오류가
발생했고, 해당 연결 시점에 MCP와 Keycloak 로그에 요청이 생성되지
않는 현상을 확인했다.

반면 curl과 MCP Inspector에서는 동일 MCP endpoint가 정상적으로
인증되고 tool call까지 수행되었다.

따라서 MCP 서버 자체의 인증/네트워크/Streamable HTTP 구현은
독립적으로 검증되었다.

현재 개인용 개발 환경에서는 MCP_ACCESS_TOKEN을 API key로 사용하는
fallback 인증 경로를 유지한다.

21. Query API key → Bearer 변환

Claude와 같은 클라이언트에서 직접 Authorization header를 지정하기
어려운 경우를 위해 다음 구조를 구현했다.

URL:

/mcp?key=<API_KEY>

↓

QueryKeyToBearerMiddleware

↓

Authorization: Bearer <API_KEY>

↓

TokenVerifier

TokenVerifier에서는:

1. API key가 정확히 일치하면 허용
2. 일치하지 않으면 기존 Keycloak JWT 검증 수행

하도록 구현했다.

Streamable HTTP의 streaming 동작을 보존하기 위해
Starlette BaseHTTPMiddleware가 아니라 순수 ASGI middleware를 사용했다.

API key는 URL에 포함될 수 있으므로:

- URL history
- proxy log
- access log

등에 노출될 가능성이 있다.

따라서 현재 방식은 개인용 fallback으로만 사용하며,
키가 노출된 경우 MCP_ACCESS_TOKEN을 즉시 교체한다.

22. MCP 외부 검증

인증 없는 외부 initialize 요청:

HTTP 401 Unauthorized

정상 API key:

HTTP 200
Content-Type: text/event-stream
Mcp-Session-Id 발급

MCP Inspector를 이용한 외부 tools/list:

health_check
search_context

정상 확인.

실제 tools/call:

query:
/cmd_vel을 받는 코드는 어디에 있는가?

project_id:
2

정상 검색 결과:

scripts/remote_teleop_node.py
src/limo_ros2/limo_description/launch/gazebo_models_diff.launch.py
src/limo_ros2/limo_base/src/limo_driver.cpp

즉:

External HTTPS
→ Tailscale Funnel
→ MCP authentication
→ Streamable HTTP
→ MCP tool call
→ Hybrid Search
→ PostgreSQL + pgvector
→ LIMO documents

전체 경로를 실제 데이터로 검증했다.

23. Docker 저장소 이전

MCP 이미지 build 과정에서:

no space left on device

오류가 발생했다.

원인은 Docker/containerd가 45GB root disk를 사용하고 있었기 때문이다.

기존:

/var/lib/docker
/var/lib/containerd

변경:

/mnt/data/docker
/mnt/data/containerd

Docker:

/etc/docker/daemon.json

containerd:

/etc/containerd/config.toml

으로 저장 위치를 변경했다.

이후 root disk:

약 90%
→
약 35%

로 감소했고, MCP image build가 정상적으로 완료되었다.

현재 대용량 Docker/containerd 데이터는 /mnt/data를 사용한다.

24. Claude Custom Connector

Claude Custom Connector에서는:

ofid_63b43f2049c8d003

오류가 발생했다.

연결 직후 MCP/Keycloak server log에 요청이 생성되지 않는 것을
확인했다.

반면:

curl
MCP Inspector

에서는 같은 외부 MCP endpoint가 정상적으로 동작했다.

따라서 현재 Claude 연결 문제와 AI-Hub MCP 서버 구현 문제를
분리하여 관리한다.

Claude 연결이 정상화되기 전까지 서버 쪽 코드를 임의로 변경하지
않는 것을 원칙으로 한다.

25. 현재 안정 상태

AI-Hub API
정상

PostgreSQL + pgvector
정상

Hybrid Search
정상

MCP Streamable HTTP
정상

Bearer authentication
정상

MCP Inspector
정상

search_context
정상

LIMO project search
정상

Claude Custom Connector
연결 pending

26. 향후 개발

우선순위:

Search → LLM RAG Answer
검색 결과 출처 표시
Memory + Document 통합 Context
Conversation Context
MCP tool 확장
structuredContent 개선
중복 업로드 자동 방지
변경 파일 선택적 재임베딩
OCR
HWP / HWPX
Web UI

Claude Custom Connector OAuth는 별도 트랙으로 유지한다.