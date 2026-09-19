# Troubleshooting

AI-Hub 개발 및 MCP 외부 연동 과정에서 발생한 주요 문제와 해결 방법을 기록한다.

---

## 1. Windows에서 `npx` 실행 문제

### 문제

Windows 환경에서 MCP Inspector를 실행하기 위해 `npx`를 사용하려 했지만 명령이 정상적으로 인식되지 않았다.

### 원인

Node.js가 설치되어 있지 않거나, 설치 이후 Node.js 실행 경로가 Windows `PATH`에 제대로 반영되지 않은 상태였다.

### 해결

Node.js를 설치하고 Node.js 실행 경로가 `PATH`에 포함되도록 설정했다.

이후 PowerShell에서 Node.js와 `npx`가 정상적으로 인식되는 것을 확인했다.

### 확인

```powershell
node --version
npx --version

2. PowerShell에서 npm.ps1 / npx.ps1 실행 차단
문제

npx 실행 과정에서 PowerShell의 Execution Policy에 의해 npm.ps1 또는 npx.ps1 실행이 차단되었다.

원인

PowerShell이 스크립트 실행 정책에 따라 .ps1 파일 실행을 제한하고 있었다.

해결

Execution Policy 자체를 변경하는 대신 Windows에서 제공하는 .cmd 실행 파일을 직접 사용했다.

npm.cmd
npx.cmd

MCP Inspector 실행에도 npx.cmd를 사용했다.

선택 이유

프로젝트 환경을 위해 시스템의 PowerShell 보안 정책을 변경하기보다, 필요한 명령만 .cmd 방식으로 실행하는 방법을 선택했다.

3. MCP Inspector Web의 HTTP 403
문제

MCP Inspector Web에서 AI-Hub MCP 서버에 연결했을 때 HTTP 403 오류가 발생했다.

### 확인된 원인

Inspector Web의 OAuth 인증 과정에서 Keycloak의 Dynamic Client Registration이 수행되었고,
Keycloak의 `Trusted Hosts` 정책에 의해 client registration 요청이 거부되었다.

확인된 응답은 다음과 같다.

- HTTP 403
- Dynamic Client Registration 요청 거부
- `Trusted Hosts` 정책의 `Host not trusted` 오류
- 응답의 OAuth error code로 `insufficient_scope`가 포함됨

여기서 `insufficient_scope`를 별도의 원인으로 해석하기보다는,
Keycloak이 `Trusted Hosts` 정책 거부를 해당 OAuth 오류 코드로 반환한 것으로 기록한다.
해결 방향

Inspector Web의 OAuth/Dynamic Client Registration 문제와 MCP API Key 인증 테스트를 분리했다.

API Key 방식으로 MCP endpoint에 직접 연결하여 실제 MCP 기능을 별도로 검증했다.

검증 결과

MCP Inspector CLI를 사용하여 다음 기능을 외부 endpoint에서 정상적으로 호출했다.

tools/list
tools/call health_check
tools/call search_context

따라서 MCP 서버 자체의 Streamable HTTP 통신과 API Key 인증 경로는 OAuth Dynamic Client Registration 문제와 별개로 검증할 수 있었다.

OAuth 기반 Claude Custom Connector 연동은 별도 작업으로 유지한다.

4. MCP_ACCESS_TOKEN 노출 및 교체
문제

개발 과정에서 MCP API 인증에 사용되는 MCP_ACCESS_TOKEN이 노출되는 상황이 발생했다.

대응

기존 인증 키를 폐기하고 새로운 무작위 토큰을 생성했다.

새 토큰은 Python의 secrets 모듈을 사용하여 생성했다.

import secrets

secrets.token_hex(32)
추가 확인

새로운 인증 키 적용 후 다음 사항을 확인했다.

.env가 Git에서 추적되지 않음
.env가 .gitignore에 포함되어 있음
새로운 키가 MCP 컨테이너에 주입됨
외부 tools/list 인증 성공
외부 tools/call 인증 성공
보안 원칙

다음 정보는 Git 저장소에 포함하지 않는다.

MCP_ACCESS_TOKEN
.env
데이터베이스 비밀번호
Keycloak 관리자 비밀번호
Tailscale 인증 정보
5. 문제 해결 과정에서 얻은 운영 원칙

이번 배포 과정에서는 다음과 같은 원칙을 적용했다.

인증 문제와 서버 기능 문제를 분리

OAuth 또는 Dynamic Client Registration에서 문제가 발생하더라도 MCP 서버의 API Key 인증 경로를 별도로 검증하여 서버 기능 자체의 정상 여부를 확인한다.

시스템 설정 변경보다 범위가 작은 해결 방법 우선

PowerShell Execution Policy를 변경하는 대신 npm.cmd와 npx.cmd를 사용하여 시스템 전체 설정 변경을 피했다.

민감한 인증 정보는 즉시 교체

인증 토큰이 노출된 경우 기존 키를 계속 사용하는 대신 새 키를 생성하고 컨테이너와 외부 클라이언트의 인증을 다시 검증한다.

해결 과정을 문서화

단순히 오류를 해결하는 것에서 끝내지 않고 문제의 원인과 해결 방법을 docs/troubleshooting.md에 기록하여 이후 동일한 문제가 발생했을 때 재현 가능한 해결 절차로 활용한다.