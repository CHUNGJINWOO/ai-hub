# AI-Hub Agent Expansion Future Architecture / Design Proposal

**문서 성격:** 미래 아키텍처 및 설계 제안
**기준 repository baseline:** `b05a586` (`main`)
**기준일:** 2026-10-02

이 문서는 현재 AI-Hub에 구현된 Knowledge Hub/MCP 기반과 향후 Agent
Expansion 방향을 구분해 기록한다. 아래의 future, planned, proposal 표기는
현재 runtime 기능이나 제품 지원을 의미하지 않는다.

## 1. 현재 baseline과 문서화 원칙

`b05a586` 기준으로 현재 repository에는 다음 기반이 구현되어 있다.

- Project
- Memory
- Document
- DocumentChunk
- 문서 및 source code ingestion
- unified semantic/hybrid search
- canonical Context Assembly와 provenance
- REST `GET /context/search`
- REST `GET /context/assemble`
- request-boundary Authorization Guard
- Minimal Skill Interface
- ROS2 Robotics Skill prototype
- MCP tools: `health_check`, `search_context`, `list_projects`, `get_document`,
  `get_context`, `list_skills`
- FastAPI Context Assembly HTTP integration tests
- disposable PostgreSQL 17 + pgvector Context Assembly E2E validation

현재 구현은 검색, 지식 저장, provenance-aware context 정규화, REST/MCP
접근 경계까지다. LLM 답변 생성, conversation/session runtime, Agent
runner, provider abstraction, OCR, HWP/HWPX ingestion, web UI는 현재
구현된 기능으로 취급하지 않는다.

`get_context`, Authorization Guard, Minimal Skill Interface와 ROS2
Robotics Skill prototype은 `b05a586`까지의 committed capability다.
이후의 local uncommitted 문서는 committed baseline의 구현 완료 목록과
구분한다. `AuthorizationContext`는 project filtering과 분리된
request-boundary capability이며, Skill prototype은 이를 통과한 뒤
`CanonicalContext`와 provenance를 소비한다.

과거 조사 시점의 checkout, commit, remote verification, working tree
상태는 현재 baseline의 사실로 사용하지 않는다. 특히 `docs/architecture.md`
는 현재 repository에 존재하는 tracked 문서이며, 과거에 없었다는 관찰은
현재 상태 설명에 포함하지 않는다.

## 2. 현재 AI-Hub architecture boundary

```text
AI / Agent clients
        │
        ├── REST API
        └── MCP
             │
          AI-Hub Core
             │
    Project / Memory / Document / Code
             │
       Unified Search
             │
      Canonical Context Assembly
             │
   PostgreSQL + pgvector project data
```

### 현재 책임

- **Project:** 여러 지식 공간을 구분하는 project identity와 project-scoped
  filtering을 제공한다. 현재 project filtering은 data filtering이며
  tenant authorization을 의미하지 않는다.
- **Memory:** project와 연결될 수 있는 구조화된 기억/사실 데이터를 저장하고
  조회한다.
- **Document:** 문서와 source code를 document로 저장한다.
- **DocumentChunk:** document의 검색 가능한 chunk와 embedding, provenance
  metadata를 저장한다.
- **Search:** memory와 document chunk를 unified semantic/hybrid 방식으로
  검색하고 `project_id` 범위를 retrieval까지 전달한다.
- **Context Assembly:** 검색 결과를 canonical item/source 구조로 정규화하고
  item-source reference, kind, count invariant를 보장한다. LLM을 호출하거나
  답변을 생성하지 않는다.
- **REST:** 현재 `/context/search`와 `/context/assemble`를 외부 HTTP
  consumer에 제공한다.
- **MCP:** 현재 다섯 개의 Knowledge access tool을 Streamable HTTP 경계로
  제공한다: `health_check`, `search_context`, `list_projects`,
  `get_document`, `get_context`. MCP는 agent runtime이 아니다.
- **AI/Agent:** 현재 AI-Hub core가 소유하는 runtime 계층이 아니다. 외부
  agent host가 REST/MCP를 통해 지식과 context를 소비하는 구조를 전제로 한다.

## 3. Target Agent Expansion architecture

```text
AI-Hub
│
├── Knowledge
├── Context
├── MCP / Tools
├── Agent Support
│   ├── Skill / Workflow / Policy / Hook contracts
│   ├── Harness / Agent Contract
│   └── future discovery support
├── Workflows
├── Memory
└── Model & Routing
```

### Knowledge

Knowledge는 AI-Hub core의 현재 책임이다.

- Projects
- Documents
- Code
- Search

모든 project는 동일한 project-agnostic 계약을 사용한다. LIMO/ROS2는
대표적인 project/use case일 수 있지만 AI-Hub의 platform boundary가 아니다.

### Context

Context 계층은 Knowledge 검색 결과를 agent가 소비할 수 있는 형태로
정규화한다.

- Canonical Context
- provenance와 source reference
- memory/document item normalization
- project scope와 count/invariant 보존

현재 Context Assembly는 이 계층의 구현된 기반이며, 향후 agent-facing
context API의 안정적인 계약으로 확장할 수 있다.

### MCP / Tools

MCP/Tools 계층은 AI-Hub Knowledge MCP와 외부 MCP를 구분한다.

- **AI-Hub Knowledge MCP:** AI-Hub project, memory, document, search,
  context capability에 접근하는 경계
- **External MCP:** GitHub, browser, CI/CD 등 외부 시스템과의 future
  integration

GitHub/browser MCP는 현재 AI-Hub core의 구현이 아니다. MCP는 도구 접근
경계이며 agent planner, session manager, model runtime을 대신하지 않는다.

### Skills

Skills는 재사용 가능한 절차·도메인 지식·검증 규칙을 표현하는
agent-side capability다. 현재는 Minimal Skill Interface와 ROS2 Robotics
Skill prototype이 초기 capability로 구현되어 있지만, Skill Runtime이나
Registry가 구현된 것은 아니다.

- Architecture
- Planning
- Debugging
- Testing
- Documentation
- Security
- Robotics
- ROS2
- LIMO
- Scientific
- Engineering
- 기타 domain-specific skills

Skill은 Project, Document, Memory 같은 core data entity가 아니다. Skill
library가 커지더라도 Task에 필요한 Skill만 선택해야 하며, 모든 Skill을
기본 로드하지 않는다. 선택된 Skill만 context와 execution에 포함하면
token/context 비용과 Skill 간 충돌을 줄일 수 있다. 자동 Skill selection은
future proposal이다. 외부 skill은 검토·버전 고정·권한 검증 후 선택적으로
사용해야 하며 AI-Hub runtime dependency로 자동 편입하지 않는다.

```text
Many Skills
    ↓
Task analysis
    ↓
Required Skill selection
    ↓
Selected Skills only
    ↓
Context / Execution
```

### Planning

Planning은 다음 행동과 작업 순서를 결정하는 agent capability다.

```text
Understand
   ↓
Plan
   ↓
Execute
   ↓
Observe
   ↓
Review
   ↓
Re-plan
```

Planning은 A*, UCS, Greedy 또는 다른 탐색 알고리즘을 AI-Hub에 구현한다는
뜻이 아니다. Planning은 무엇을 어떤 순서로 할지 결정하고, Workflow는
정의된 단계를 어떻게 실행·검증할지 관리하며, Skill은 특정 작업을
어떻게 수행할지 제공한다.

### Harness / Agent Contract

Harness는 External Agent 작업 lifecycle을 관리하는 architectural
concept다. 현재 [Agent Contract](agent/agent-contract.md)는 reference
contract이며 full Harness runtime은 구현되지 않았다.

```text
Task
 ↓
Repository Inspection
 ↓
Context Retrieval
 ↓
Plan
 ↓
Risk / Scope Check
 ↓
Implementation
 ↓
Build / Test
 ↓
Review
 ↓
Documentation / Memory
```

Harness는 inspection, planning, execution sequencing, risk/scope check,
build/test, diff/review, documentation/handoff와 Memory handoff를
조정한다. AI-Hub는 Harness Runtime을 직접 소유하지 않는다.

```text
External Agent / Harness
        ↓
Planning / Workflow / Skill / Policy / Hook
        ↓
AI-Hub
├── Knowledge
├── Search
├── Context Assembly
├── MCP
└── Authorization
```

### Workflows

Workflows는 작업 순서와 승인/정책 경계를 정의하는 future agent-side
계층이다.

- planning
- review
- execution
- approval/policy

Workflow는 Knowledge retrieval 결과를 사용하지만, retrieval 자체와
동일하지 않다. 실행 권한과 변경 승인은 workflow/host policy가 담당한다.

### Memory

Memory는 현재 구현된 project/memory 데이터와 future agent state를
구분해야 한다.

- session memory
- project state
- decision history
- troubleshooting history
- future handoff/state

현재 Memory entity가 session runtime memory나 자동 handoff system을
의미하지는 않는다. Session Memory는 External Agent가 소유하고,
AI-Hub Knowledge Memory로의 promotion은 승인, provenance, project scope를
거친 경우에만 허용하는 future contract다.

```text
Session
   ↓
Summary / Important State
   ↓
Project Memory
   ↓
Next Session
```

Session summary, handoff, decision history와 troubleshooting fact는
자동으로 Knowledge Memory가 되지 않는다. retention, correction, deletion,
provenance 정책을 먼저 정의한 뒤 별도 milestone으로 설계한다.

### Model & Routing

Model과 routing 계층은 특정 vendor/model에 종속되지 않는 future abstraction
이다.

- Router
  - General Router
  - Project-specific Router
- General LLM
- Coding Model
- Vision Model
- Speech Model
- Structured / Classification Model

Router의 핵심 질문은 “이 문제를 누가 처리해야 하는가?”이다. Routing은
AI-Hub Knowledge를 대체하지 않으며, 적절한 model, context capability,
domain skill 또는 workflow로 요청을 전달하는 future coordination layer다.

Provider-specific authentication, pricing, retention, latency, benchmark,
failure behavior는 provider boundary 뒤에 둔다. AI-Hub core가 특정
vendor/model을 기본 요구사항으로 갖지 않도록 한다.

### Plugin

Plugin은 여러 Agent capability를 묶어 배포·공급하는 external ecosystem
단위로 정의한다.

```text
Plugin
├── Skills
├── Custom Agents
├── Hooks
└── MCP
```

Plugin은 AI-Hub core entity가 아니며, plugin runtime, marketplace,
registry를 현재 구현하지 않는다. 외부 plugin은 검토·검증·권한·버전
고정 후 필요한 기능만 선택적으로 사용한다.

### Project Rules and Selectable Capabilities

Agent architecture에는 항상 적용되는 Project Rules와 Task에 따라
선택되는 capabilities라는 두 control plane이 있다.

```text
Project Rules
        │
        ↓
      Agent
        │
Task Analysis / Planning
        │
 ┌──────┼───────┐
 ↓      ↓       ↓
Skill  Agent   MCP
 └──────┼───────┘
        ↓
     Execute
```

`AGENTS.md`, `copilot-instructions.md`, 그리고 이 repository의
`AI_AGENT_WORKFLOW.md` 같은 Project Rules는 repository 조사, 기존 구조
우선, 공식 자료, 근거와 추론 구분, 최소 변경, Build/Test와
Troubleshooting 기록을 지속적으로 안내한다. Skills, Agents, Plugins,
MCP는 Task 분석 결과에 따라 선택되는 capability다. 자동 선택은 현재
구현이 아니다.

### Future Skill and Workflow Discovery

현재는 다음과 같이 AI-Hub MCP가 Knowledge와 Context를 제공한다.

```text
AI
 ↓
AI-Hub MCP
 ↓
Knowledge / Context
```

장기적으로 Agent가 Task에 필요한 Knowledge, Context, Skill, Workflow를
찾고 선택하도록 discovery support를 제공할 수 있다.

```text
AI
 ↓
Task Analysis / Planning
 ↓
AI-Hub
├── Knowledge discovery
├── Context discovery
├── Skill discovery
└── Workflow discovery
 ↓
Agent
```

이는 future infrastructure proposal이며 현재 Skill discovery,
Workflow discovery, marketplace, 자동 Skill selection을 구현된 MCP
capability로 표현하지 않는다.

### M1 Agent Layer contract boundary

M1의 contract는 AI-Hub가 Agent runtime을 구현한다는 뜻이 아니다.
AI-Hub는 Knowledge와 Context를 제공하고, External Agent가 Skill 선택,
workflow 실행, policy enforcement, hook lifecycle을 소유한다.

#### 현재 상태

| Layer | 현재 상태 | 책임 owner |
|---|---|---|
| Skill | runtime 미구현. 재사용 가능한 절차와 도메인 지식의 future artifact로만 정의 | External Agent |
| Workflow | runtime 미구현. 작업 순서와 checkpoint를 정의하는 future contract | External Agent |
| Policy | 기존 MCP 인증, project filtering, compatibility/data-safety 정책은 존재하지만 Agent policy engine은 미구현 | Shared |
| Hook | runtime 미구현. lifecycle callback과 audit 연결은 future contract | External Agent |

`docs/AI_AGENT_WORKFLOW.md`는 repository 변경 지침 문서이며 Skill loader,
Workflow executor, Policy engine, Hook registry 또는 Agent runner가 아니다.

#### Skill Contract

- **Purpose:** 특정 작업에 필요한 재사용 가능한 절차, 도메인 지식, 검증
  규칙을 정의한다.
- **Inputs:** 작업 질문, 명시된 project scope, 필요한 context 요청,
  사용자가 제공한 제약과 승인 상태.
- **AI-Hub capabilities used:** `get_context`, 필요 시 `search_context`,
  `list_projects`, `get_document`, REST context API. Skill은 검색 알고리즘과
  canonical context semantics를 복제하지 않는다.
- **Outputs:** 구조화된 작업 결과, 사용한 source/provenance, 검증 결과,
  미해결 사항과 다음 action.
- **Validation:** 입력 project scope와 권한을 확인하고, context source를
  결과에 연결하며, 필요한 테스트와 정책 검증을 통과해야 한다.

Skill artifact의 저장 형식, discovery 방식, versioning, 실행 host는
아직 정하지 않는다.

#### Workflow Contract

External Agent workflow는 다음 단계를 논리적 순서로 제공한다.

1. **Inspect:** repository, project scope, 기존 구현과 제약을 확인한다.
2. **Context retrieval:** AI-Hub context/search capability에서 필요한
   근거와 provenance를 조회한다.
3. **Planning:** 변경 필요성, 최소 범위, 예상 검증을 계획한다.
4. **Approval:** policy에 따라 read-only인지, write/production 접근이
   필요한지 확인하고 필요한 승인을 받는다.
5. **Execution:** 승인된 범위에서만 외부 repository, tool, model을
   사용한다.
6. **Test/review:** 테스트, diff, provenance, scope와 결과를 검토하고
   handoff 가능한 요약을 만든다.

Workflow state model, retry semantics, cancellation, persistence, 실행
host는 Open Decision이다.

#### Policy Contract

- **Project scope:** 요청과 context retrieval에 project 범위를 명시한다.
  `project_id` filtering은 authorization이 아니라 data filtering이다.
- **Read/write permission:** Knowledge read, repository read, repository
  write, external tool write를 구분한다. 기본 write 허용 여부는 정하지
  않는다.
- **Production restriction:** production DB, production container,
  deployment와 destructive operation은 명시적 scope와 승인 없이는
  허용하지 않는다.
- **Approval requirement:** code write, migration, external side effect,
  secret access와 같은 작업의 승인 기준은 host policy가 적용한다.
- **Secret/data boundary:** credentials, private source, query, context,
  model input의 외부 전송 범위와 보존은 provider/integration별로
  제한한다. 실제 secret을 context나 문서에 기록하지 않는다.

project authorization mapping, approval actor, policy format, policy
precedence와 enforcement location은 Open Decision이다.

#### Hook Contract

Hook은 External Agent workflow lifecycle에 연결되는 future signal이다.

- **Pre-step:** 단계 실행 전 scope, input, policy와 approval을 확인한다.
- **Post-step:** 결과, provenance, output shape와 검증 상태를 기록한다.
- **Failure:** 예외, policy violation, timeout을 표준 failure signal로
  전달하고 partial side effect를 보고한다.
- **Approval gate:** write, production, external side effect 전에 workflow를
  멈추고 승인 결과를 요구한다.
- **Audit signal:** actor, project scope, capability, action, result와
  timestamp를 audit 대상 event로 표현한다.

Hook transport, event schema, ordering, retry와 audit storage owner는
Open Decision이다.

## 4. Generative LLM과 Structured Model의 경계

```text
Agent
        │
   ┌────┴─────┐
   ↓          ↓
Generative   Structured
LLM          Model
   │          │
   ↓          ↓
Natural      Structured
Language     Program Output
   └────┬─────┘
        ↓
    Workflow / Tool
```

### Generative LLM

- 사람에게 설명하는 자연어 response
- 자연어 reasoning과 synthesis
- code/document generation

### Structured Model

- classification
- probability/confidence
- state estimation
- structured decision output
- downstream program input

예를 들어 센서 입력은 다음과 같은 program-oriented 결과로 변환될 수
있다.

```text
LiDAR / Camera
      ↓
Structured Model
      ↓
{
  obstacle: true,
  confidence: 0.97,
  distance: 0.82,
  urgency: 0.91
}
      ↓
Navigation / Avoidance
```

위 숫자는 architecture illustration일 뿐이며 실제 모델 성능, 정확도,
안전성 또는 benchmark 결과가 아니다. 실제 provider를 도입할 때는
검증 데이터셋, 재현 가능한 benchmark, 비용, latency, privacy, failure
mode를 별도로 확인한다.

특정 영상이나 외부 자료에서 주장하는 특정 모델의 가격, “5배 저렴”,
“hallucination zero”, 초당 판단 횟수, 다른 모델보다 높은 정확도 등은
현재 AI-Hub architecture의 사실이나 요구사항으로 기록하지 않는다.

> Structured / Program-oriented Model이라는 개념은 향후 AI-Hub Model
> Provider Layer에서 고려할 수 있다. 특정 모델의 성능, 비용,
> hallucination rate 및 benchmark 결과는 별도의 검증이 필요하며 현재
> AI-Hub 구현의 요구사항이나 사실로 취급하지 않는다.

### Router와 Model 선택 예시

```text
User Request
     ↓
   Router
     │
     ├─ Simple classification → Small Model
     ├─ Document/knowledge task → AI-Hub Context
     ├─ Coding → Coding Model
     ├─ Complex reasoning → Reasoning LLM
     └─ Domain-specific task → Domain Skill
```

Router는 Knowledge source of truth가 아니며, 실제 선택 결과와 품질은
별도 evaluation contract로 검증해야 한다.

### LIMO architecture illustration

```text
LiDAR / Camera
      ↓
Structured Model
      ↓
{
  obstacle: true,
  confidence: 0.97,
  distance: 0.82,
  urgency: 0.91
}
      ↓
Navigation / Avoidance
```

위 숫자는 architecture illustration일 뿐이며 실제 benchmark, 정확도,
안전성 또는 inference rate를 의미하지 않는다.

### Long-term Router learning direction

```text
Request
 ↓
Router
 ↓
Actual result
 ↓
User correction / follow-up
 ↓
Training / Evaluation Data
 ↓
Project-specific Router
```

이 흐름은 Project-specific Router를 장기적으로 평가·개선할 수 있다는
연구 방향만 나타낸다. Online learning, training pipeline, feedback DB는
현재 구현하지 않는다.

## 5. M0–M5 future roadmap

각 milestone은 target architecture다. 상태는 현재 committed repository
구현을 기준으로 표시하며, 제안된 항목을 이미 제공되는 기능으로 해석하지
않는다.

### M0 — Core Infrastructure

- Knowledge
- Search
- Context Assembly
- MCP
- Authorization boundary

**현재 상태:** largely implemented.
Project/Memory/Document/DocumentChunk, unified search, canonical Context
Assembly, REST context routes, 현재 MCP 다섯 개 tool, HTTP integration 및
PostgreSQL 17 + pgvector E2E가 구현·검증되어 있다. Authorization Guard는
project filtering과 분리된 request boundary capability다. 추가적인 MCP
capability는 별도 변경으로 다룬다.

### M1 — Agent Layer

- Planning
- Workflow
- Skill
- Policy
- Harness / Agent Contract
- Hook

**현재 상태:** active development / early implementation.
Committed implementation에는 Authorization boundary, Minimal Skill
Interface, `ContextProvider` contract와 ROS2 Robotics Skill prototype이
포함되어 있다. Agent Contract, Skill/Workflow/Policy/Hook reference
documents는 External Agent 설계를 정의하지만 full Harness runtime,
Skill registry, Workflow runtime, Policy engine, Hook runtime은 구현되지
않았다.

Planning은 abstraction으로만 명시되며 A*, UCS, Greedy 등의 탐색
알고리즘을 AI-Hub에 구현하지 않는다. Skill은 모든 Task에 자동 로드되지
않고 필요한 capability만 선택하는 future design principle을 따른다.

### M2 — Memory

- Session
- Project State
- Decision History
- Troubleshooting

**현재 상태:** architecture/contract defined, implementation future.
현재 Memory entity와 session/handoff/project-state runtime을 혼동하지
않는다. retention과 correction 정책을 포함한 별도 설계가 필요하다.

M2에 들어가기 전에 다음 dependency를 고정해야 한다.

- AI-Hub Knowledge Memory와 Agent Session Memory의 owner와 저장 경계를
  분리한다.
- `get_context`의 canonical item/source와 provenance 의미를 유지한다.
- `project_id` filtering을 authorization으로 승격하지 않으며, 별도
  authorization contract와 요청 경계를 분리한다.
- session summary, project state, troubleshooting과 handoff의 write
  approval, retention, correction, deletion 정책을 정의한다.
- Hook이 session memory를 자동 기록할 수 있는지와 그 승인 기준을
  결정한다.

Session schema, automatic summarization, handoff generation, memory ranking,
multi-agent shared memory와 provider-specific memory behavior는 M2의
별도 설계로 남긴다.

Harness 실행 결과는 향후 명시적인 Memory handoff 후보가 될 수 있다.

```text
Harness
 ↓
Task Execution
 ↓
Result / Failure
 ↓
Review
 ↓
Memory
├── Decision
├── Troubleshooting
└── Project State
```

저장 후보는 중요한 결정, 실패한 접근, 오류 원인, 해결 방법, 변경사항과
프로젝트 상태다. 자동 저장 runtime은 현재 구현하지 않으며, Session
Memory와 AI-Hub Knowledge Memory의 ownership boundary를 유지한다.
Memory promotion에는 approval, provenance, project scope가 필요하다.

### M3 — Semantic / Domain Intelligence

- Ontology
- Entity / Relation
- Knowledge Graph
- Scientific
- Engineering
- Robotics

**현재 상태:** future/planned.
Ontology는 개념과 관계·규칙을 정의하고, Knowledge Graph는 실제 project
entity와 relation을 연결한다. Context Assembly는 현재 Task에 필요한
지식을 정규화하는 경계로 유지되며, 도메인 skill은 project-agnostic
core 위의 선택적 capability다.

```text
Ontology
   ↓
Entity / Relation
   ↓
Project Meaning
   ↓
Context Assembly
   ↓
Agent Planning
```

예시:

```text
Robot
 ├── has_sensor → LiDAR
 ├── uses → Nav2
 ├── has → Local Planner
 └── executes → Patrol
```

Ontology/Knowledge Graph는 현재 구현하지 않는다. Entity resolution,
duplicate identity, ID management, relation validation은 M3의 별도
future design 문제다.

```text
Engineering Question
      ↓
Engineering / Scientific Skill
      ↓
AI-Hub Knowledge / Relevant Sources
      ↓
Calculation / Analysis
      ↓
Result
```

LIMO/로봇 사례:

```text
Robot Joint
   ↓
Mechanics / Dynamics
   ↓
Required Torque
   ↓
Relevant Knowledge
   ↓
Engineering Skill
```

의학 등 다른 분야도 동일한 Domain Skill 구조로 확장할 수 있으나,
실제 Scientific/Medical 활용은 별도 skill, 자료 품질, 안전성 검증이
필요하다. 현재 구현된 기능으로 표현하지 않는다.

### M4 — Model & Routing

- Router
  - General Router
  - Project-specific Router
- General LLM
- Coding Model
- Vision Model
- Speech Model
- Structured / Classification Model

**현재 상태:** future research/experimentation.
Router는 요청을 처리할 capability/model을 선택하지만 AI-Hub Knowledge를
대체하지 않는다. 특정 외부 model을 필수 구성요소로 채택하지 않는다.
provider interface, evaluation set, provenance, 비용·latency·privacy
정책이 정해지기 전에는 특정 provider를 core에 결합하지 않는다.

M1과 연결되는 장기 선택 흐름은 다음과 같다.

```text
User Task
    ↓
Router / Planner
    ↓
Task Classification
    ↓
Skill Selection
    ↓
Workflow Selection
    ↓
Agent / Harness
```

예를 들어 Coding은 Coding Skill, Research는 Scientific Skill, Robotics는
ROS2 / Robotics Skill로 연결될 수 있다. Router와 Planner는 현재
구현되지 않았고, Knowledge source of truth를 대체하지 않는다.

### M5 — Development Automation

- GitHub
- Browser
- Testing
- Documentation
- CI/CD
- Security

**현재 상태:** future/planned.
외부 시스템 connector와 자동화는 최소 권한, 승인, secret, prompt
injection, audit 경계를 갖춘 별도 integration으로 다룬다.

## 6. Future target architecture

장기 target은 AI-Hub Knowledge infrastructure와 External Agent support를
분리한다.

```text
AI-Hub
│
├── Knowledge
│
├── Semantic Layer
│   ├── Ontology
│   ├── Entity / Relation
│   └── Knowledge Graph
│
├── Context Assembly
│
└── Agent Support
    │
    ├── Skill Discovery
    ├── Workflow Discovery
    └── Context Support
         ↓
      Router / Planner
         ↓
      Agent / Harness
         │
    ┌────┼────────┐
    ↓    ↓        ↓
Planning Workflow Review
    │
    ↓
Skills
    │
 ┌──┼─────┐
 ↓  ↓     ↓
Model MCP Tools
    │
    ↓
Project / Code
```

이 diagram은 future target architecture이며 현재 runtime architecture로
해석하지 않는다. 특히 Skill/Workflow discovery, Plugin system, Ontology,
Knowledge Graph, Router, full Harness와 automatic Memory promotion은
현재 구현되지 않았다.

## 7. External reference and candidate classification

다음 항목은 현재 AI-Hub에 통합된 기능이 아니라 외부 reference 또는
future candidate를 분류한 것이다.

| Project / Feature | AI-Hub role | Stage |
|---|---|---|
| ECC / Agent Harness | Harness / workflow reference | M1 |
| Planning / search-planning concepts | Planning reference | M1 |
| Awesome Copilot | External skill library reference | M1 |
| Archify Diagram Skill | M1 candidate | External reference |
| Ponytail Minimal Coding Workflow | M1 | Agent workflow reference |
| Scientific Agent Skills | M3 | Scientific / Engineering skill reference |
| Ontology | M3 | Semantic Layer reference |
| Knowledge Graph | M3 | Semantic Layer reference |
| Claude Map 계열 | M2 | Session Memory reference |
| MCP Official Skills | M1 | MCP development reference |
| GitHub MCP | M5 | GitHub tool reference |
| Chrome DevTools MCP | M5 | Browser / Debug tool reference |
| Copilot `/af` | M1 | Skill / MCP discovery reference |
| Jev 계열 | M4 | Structured / Router model concept |
| Laya | M4 | Local / open-source Router candidate |
| GitHub Actions | M5 | CI / Automation reference |
| Dependabot | M5 | Dependency management reference |
| CodeQL | M5 | Security reference |

Jev 계열과 Laya에 대해 외부 영상·제작자가 제시한 program-oriented
output, classification/decision, probability/confidence, local execution,
custom training, fast inference 또는 privacy-oriented deployment 설명은
architecture 관점의 reference candidate로만 기록한다.

정확한 성능 수치, 특정 모델보다 몇 배 저렴하다는 주장, hallucination zero,
특정 TPS/inference rate, 특정 모델보다 정확하다는 주장은 현재 AI-Hub
사실이나 요구사항으로 채택하지 않는다.

> 외부 영상/제작자가 제시한 모델 성능·가격·hallucination·정확도 수치는
> 별도 검증 대상이며 현재 AI-Hub architecture의 사실이나 요구사항으로
> 채택하지 않는다.

## 8. Agent Expansion principles

1. AI-Hub core remains project-agnostic.
2. LIMO is a project/use case, not the platform boundary.
3. Agent execution remains separate from knowledge infrastructure.
4. Skills are reusable capabilities, not core data entities.
5. MCP is an access/integration boundary, not the agent runtime itself.
6. Model providers and routing policies are replaceable infrastructure.
7. Generative and structured inference can coexist.
8. Structured model output should be machine-consumable.
9. Model/vendor-specific behavior must stay behind provider boundaries.
10. Security/authorization is separate from project filtering.
11. Router selects a handling capability; it does not replace Knowledge.
12. External model claims require independent verification before becoming
    architecture requirements.
13. Production features must be separated from future design proposals.
14. Planning determines intended next actions; Workflow defines execution
    structure.
15. Harness manages the end-to-end agent work lifecycle but does not replace
    Knowledge infrastructure.
16. Skills should be selected for the Task rather than all loaded by default.
17. Project Rules are persistent guidance; Skills, Agents, Plugins and MCP
    are selectable capabilities.
18. AI-Hub may provide future discovery support without becoming the Skill
    runtime.
19. Router and Planner may select capability/model, but Knowledge remains the
    source of truth.
20. Memory captures reusable decisions and troubleshooting only through
    explicit ownership and provenance rules.
21. Semantic Layer improves entity/relation-aware context selection; it does
    not replace Context Assembly.
22. External ecosystem patterns are references until independently validated.
23. Plugin adoption is capability selection, not capability accumulation.
24. Token/context efficiency is an architectural concern when multiple
    capabilities coexist.

## 9. Future architecture examples

### Architecture Skill

```text
Knowledge
   → Context
   → Architecture Skill
   → diagram/design output
```

현재 AI-Hub가 Architecture Skill이나 diagram generator를 runtime으로
제공한다는 뜻이 아니다. Skill은 검색된 근거와 provenance를 사용해
설계 산출물을 만들 수 있는 future host-side capability다.

### Session Memory

```text
Agent Session
   → execution summary
   → project memory
   → next session context
```

이 흐름은 future handoff 설계 예시다. 현재 session memory 저장,
자동 요약, 복원은 구현된 기능이 아니다.

### Scientific Skill

```text
Engineering Question
   → Scientific Skill
   → Relevant Knowledge/Papers
   → Calculation/Analysis
   → Result
```

결과에는 입력, 단위, 가정, 출처, uncertainty, 재현 가능한 계산을
포함해야 한다. 외부 논문/API 접근과 데이터 전송은 별도 정책 대상이다.

### Agent workflow

```text
Plan
   → inspect existing code
   → decide whether change is necessary
   → minimal change
   → test
   → review
```

이는 AI-Hub runtime workflow가 아니라, 향후 agent host가 따를 수 있는
검증 중심 작업 절차의 설계 예시다.

## 10. Scope boundary

### 현재 AI-Hub core가 소유하는 것

- Knowledge
- Search
- Context
- Authorization boundary
- 현재 Knowledge MCP boundary
- REST context API
- PostgreSQL/pgvector 기반 persistence와 retrieval

### Future Agent layer

- Skills
- Workflows
- Planning
- Harness / Agent Contract
- Memory(session/project state/troubleshooting)
- Model & Routing
- Automation

### AI-Hub가 직접 소유하지 않는 외부 concern

- external LLM provider implementation
- external MCP services
- Agent host/runtime
- browser automation
- external CI systems

이 구분은 향후 기능을 추가할 때 core와 host/integration의 책임을
분리하기 위한 것이다. 특히 `project_id` filtering은 authorization이
아니며, 인증·인가 설계는 별도의 security milestone으로 다룬다.

## 11. Agent Stack Audit

장기적으로 다음 Agent ecosystem을 M1 integration review에서 함께
조사하고 다음 상태로 분류할 수 있다.

```text
Plugin
Skill
Custom Agent
Instruction
Hook
MCP
Harness
Memory
Router
```

각 항목은 `ADOPT`, `EXPERIMENT`, `REFERENCE`, `DEFER` 중 하나로 분류하고,
실제 runtime 호환성, 역할 중복, 충돌 가능성, context/token 비용,
유지보수 비용, 보안/권한, portability, AI-Hub ownership boundary를
평가한다. 목표는 기술을 많이 넣는 것이 아니라 다음 흐름으로 검증된
패턴만 shared contract나 guideline으로 승격하는 것이다.

```text
External ecosystem
 ↓
Practical validation
 ↓
Useful pattern
 ↓
AI-Hub shared contract / guideline
```

이 audit은 현재 구현이 아니라 M1 integration review의 다음
research/design 작업이다.

## 12. Design decisions for future work

- 새로운 project는 LIMO 전용 branch나 schema가 아니라 기존 Project,
  Document, Memory, Search, Context 계약을 통해 추가한다.
- 새로운 agent-facing capability는 기존 canonical context와 provenance를
  재사용하고, 검색 알고리즘을 중복 구현하지 않는다.
- MCP tool 추가는 기존 tool compatibility, input validation, structured
  response schema, project filtering, 실제 retrieval evidence를 함께
  검증한 뒤 결정한다.
- Skills와 workflows는 처음부터 AI-Hub core entity로 저장하지 않고,
  host-side portable artifact와 명시적인 policy로 검증한다.
- provider 또는 external automation을 추가할 때 source/query/secret/data
  retention과 권한 범위를 문서화한다.
- Production deployment와 future proposal의 상태를 같은 검증 문장으로
  표현하지 않는다.
- ECC를 AI-Hub Core dependency로 편입하지 않는다. Plan → Execute →
  Review, 작업 전 조사, 최소 변경, 테스트, diff 검토, 실패 기록 등의
  검증된 작업 원칙만 Agent Contract/Workflow 설계에 참고하고 채택한다.
- Plugin, Skill, Agent, Instruction, Hook, MCP, Harness, Memory, Router는
  capability selection과 ownership boundary를 먼저 검토하며, 자동
  accumulation을 기본값으로 삼지 않는다.

## 13. Status vocabulary

문서와 구현 보고에서 다음 용어를 일관되게 사용한다.

- **Implemented:** 현재 committed code와 테스트가 해당 동작을 제공함.
- **Validated:** 명시된 환경과 테스트에서 실제로 검증됨.
- **Architecture/initial capability stage:** 일부 경계나 초기 capability는
  있으나 full runtime layer가 아님.
- **Future/planned:** 설계 제안이며 현재 구현되지 않음.
- **External/unknown:** AI-Hub repository가 소유하거나 검증하지 않는
  외부 시스템 상태.
