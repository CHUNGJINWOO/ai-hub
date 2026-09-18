# AI-Hub MCP 배포 및 트러블슈팅 기록

## 1. 목적

AI-Hub의 PostgreSQL/pgvector 기반 프로젝트 지식 검색 기능을 MCP
(Model Context Protocol) 서버로 노출하고 외부 MCP 클라이언트에서 사용할 수
있도록 구성한다.

주요 사용 사례:

- ROS2 / LIMO 프로젝트 문서 검색
- 프로젝트 memory + indexed document hybrid search
- MCP client를 통한 `search_context` 호출

## 2. 최종 검증된 구조

```text
External MCP Client
        |
        | HTTPS :10000
        v
Tailscale Funnel
        |
        v
AI-Hub MCP :8001
        |
        +--> TokenVerifier
        |      |
        |      +--> API Key
        |      |
        |      +--> Keycloak JWT
        |
        v
search_context
        |
        v
PostgreSQL + pgvector
        |
        v
Project / Document / Chunk

현재 공개 endpoint:

https://ros2-server.tail49948f.ts.net:10000/mcp
3. MCP 기능

현재 제공하는 도구:

health_check

MCP 서버가 정상적으로 실행 중인지 확인한다.

search_context

AI-Hub의 memory와 indexed document를 함께 검색한다.

인자:

query: string
limit: integer = 5
project_id: integer | null
4. 인증 구조

현재 MCP 서버는 두 가지 인증 경로를 지원한다.

API Key

개인용/개발용 연결에서 사용할 수 있도록 MCP_ACCESS_TOKEN을
정적 API key로 검증한다.

외부 테스트에서는:

/mcp?key=<API_KEY>

를 사용하고 ASGI middleware가 이를 내부적으로:

Authorization: Bearer <API_KEY>

형태로 변환한다.

중요:

API key는 URL에 포함되므로 URL 로그/히스토리 등에 노출될 수 있다.
개인용 테스트 용도로만 사용한다.
키가 노출되면 .env의 MCP_ACCESS_TOKEN을 교체한다.
실제 채팅/문서/Git에 secret 값을 기록하지 않는다.
Keycloak JWT

기존 OAuth 경로도 유지한다.

JWT 검증 조건:

RS256 signature
issuer
audience
exp
iat
iss
sub
aihub:read scope

이를 통해 향후 표준 OAuth/MCP 인증으로 전환할 수 있다.

5. Streamable HTTP 주의사항

MCP 서버는 Streamable HTTP를 사용한다.

Starlette BaseHTTPMiddleware 대신 순수 ASGI middleware를 사용한다.

이유:

Streamable HTTP의 streaming 동작 보존
request scope/header 조작을 낮은 계층에서 수행
streaming response를 middleware가 임의로 감싸는 문제 회피

현재 middleware:

QueryKeyToBearerMiddleware
6. 발생했던 문제와 해결
문제 1. Docker 저장공간 부족

Oracle VM의 루트 디스크:

45GB

AI-Hub 데이터 디스크:

147GB

Docker/containerd가 루트 디스크에 저장되어 MCP image build 중:

no space left on device

오류 발생.

특히 Triton/Python 관련 layer unpack 과정에서 공간 부족이 발생했다.

해결

Docker 저장소를 /mnt/data로 이전했다.

Docker data-root
/var/lib/docker
        ->
/mnt/data/docker

containerd root
/var/lib/containerd
        ->
/mnt/data/containerd

Docker:

{
  "data-root": "/mnt/data/docker"
}

containerd:

root = "/mnt/data/containerd"

이후 루트 디스크 사용량이 약 90%에서 약 35% 수준으로 감소했고
MCP image build가 정상적으로 완료되었다.

문제 2. MCP의 외부 Host 거부

외부 Funnel을 통해 접근할 때:

HTTP 421
Invalid Host header

발생.

해결

MCP transport security의 allowed host에 실제 공개 hostname을 추가했다.

ros2-server.tail49948f.ts.net
ros2-server.tail49948f.ts.net:*

이후 외부 Streamable HTTP 요청이 정상적으로 처리되었다.

문제 3. 인증 없는 MCP 접근

인증 없는 외부 요청:

HTTP 401 Unauthorized

로 차단됨.

이 응답에는 protected resource metadata 위치가 포함된다.

문제 4. Claude Custom Connector OAuth

Keycloak + OAuth + CIMD를 구성했으나 Claude Custom Connector에서는:

ofid_...

오류가 발생했다.

연결 시점에 MCP와 Keycloak 컨테이너 로그가 생성되지 않아
서버가 요청을 받기 전에 Claude 연결 단계에서 실패하는 것으로 판단했다.

따라서 OAuth 구성을 유지하되, 실제 개발/검증은 API key + Bearer header
경로를 사용한다.

7. 외부 MCP 검증

MCP Inspector로 다음을 검증했다.

External URL
    |
    v
Tailscale Funnel
    |
    v
MCP Streamable HTTP
    |
    v
Authorization: Bearer <API_KEY>
    |
    v
tools/list

tools/list 결과:

health_check
search_context

그리고 실제 tools/call search_context를 실행해:

project_id = 2
query = /cmd_vel을 받는 코드는 어디에 있는가?

검색 성공을 확인했다.

검색 결과에서 다음 LIMO 프로젝트 파일이 반환되었다.

scripts/remote_teleop_node.py
src/limo_ros2/limo_base/src/limo_driver.cpp
src/limo_ros2/limo_description/launch/gazebo_models_diff.launch.py
8. 보안 원칙

절대 Git에 포함하지 않는다:

.env
API keys
database passwords
Keycloak admin passwords
Tailscale credentials

.env는 .gitignore로 제외한다.

API key가 URL에 포함되는 방식은 현재 개인용 fallback이며,
장기적으로는 표준 OAuth/Bearer 방식으로 전환하는 것을 목표로 한다.

9. 현재 상태
AI-Hub API                 OK
PostgreSQL                 OK
pgvector                   OK
Hybrid Search              OK
MCP Streamable HTTP        OK
API Key authentication     OK
Bearer header auth         OK
MCP Inspector              OK
search_context             OK
LIMO project search        OK
Claude Custom Connector    OAuth integration pending