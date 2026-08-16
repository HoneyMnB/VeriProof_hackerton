# VeriProof AI 어시스턴트 채팅 및 저작권 등록 아키텍처 분석 보고서
**— 상용 에이전트 플랫폼(ChatGPT, Claude, Gemini) 비교 분석 및 프로토콜 점검 —**

---

## 1. 개요 및 분석 배경

VeriProof는 창작자가 자연스러운 대화(Conversational UI)를 통해 자신의 저작물(이미지, 음원, 영상, 문서 등)을 솔라나(Solana) 블록체인에 영구 앵커링하고, 스마트 라이선스 조건을 설정하며, AI 에이전트 간 자율 협상(A2A) 및 x402 결제 파이프라인을 관리할 수 있는 **Web3 + AI 저작권 등록·관리 플랫폼**입니다.

본 문서는 현재 루트 URL(`/`)에 구현된 **창작자 작업공간(Workspace) 내 대화형 어시스턴트(Creator Assistant)의 동작 구조와 통신 프로토콜, 파일 첨부 처리 방식, 캔버스 연동 설계**를 면밀히 점검하고, 글로벌 선도 상용 에이전트 플랫폼(**OpenAI ChatGPT, Anthropic Claude, Google Gemini**)의 최신 아키텍처와 비교 분석하여 현재 설계의 적합성 평가 및 향후 고도화 방안을 제시합니다.

---

## 2. 현재 시스템 아키텍처 및 구현 설계 분석

```
+---------------------------------------------------------------------------------------------------+
|                                  Browser Client (workspace.js)                                    |
|  +---------------------------+   +-------------------------------+   +-------------------------+  |
|  |     Chat & Composer       |   |      Composer Drop / File     |   |   Registration Canvas   |  |
|  | (Message Input / History) |   |  (Drag&Drop / Attachment Tray)|   |  (Metadata Form/Draft)  |  |
|  +-------------+-------------+   +---------------+---------------+   +------------+------------+  |
+----------------|---------------------------------|--------------------------------|---------------+
                 | (2) POST /assistant/chat        | (1) POST /assistant/           | (4) POST /assistant/
                 |     {wallet, msg, att_ids, id}  |     attachments (multipart)    |     drafts / confirm
                 v                                 v                                v
+---------------------------------------------------------------------------------------------------+
|                                Django Backend (views_assistant.py)                                |
|  +---------------------------+   +-------------------------------+   +-------------------------+  |
|  |  views_assistant.chat     |   | conversation_attachment       |   |   registration_drafts   |  |
|  +-------------+-------------+   +---------------+---------------+   +------------+------------+  |
|                |                                 |                                |               |
|                v                                 v                                v               |
|  +---------------------------------------------------------------------------------------------+  |
|  |                                  Application Service Layer                                  |  |
|  |  - CreatorAssistantService (Context Assembly, Intent Routing, Plan Execution)                |  |
|  |  - ConversationAttachmentService (MIME/Size Validation, SHA256, Temp Storage)               |  |
|  |  - RegistrationDraftService (Draft Lifecycle, Token Issue, Confirmation)                    |  |
|  |  - CreatorActionService (Grounded Tool Execution: Expense, Terms, etc.)                     |  |
|  +---------------------------------------------------------------------------------------------+  |
+---------------------------------------------------------------------------------------------------+
                 |                                                                  |
                 v                                                                  v
+------------------------------------+                             +--------------------------------+
|        External AI Service         |                             |      Storage & Persistence     |
|   (Google Gemini 3.1 / 3.6 SDK)    |                             |  - PostgreSQL / SQLite (DB)    |
| - plan_creator_action (JSON Schema)|                             |  - Local/Cloud Temp Storage    |
| - assist_with_attachments (Vision) |                             |  - Firestore Mirror (Optional) |
| - suggest_registration_metadata    |                             |  - Solana Devnet (Anchoring)   |
+------------------------------------+                             +--------------------------------+
```

### 2.1. 파일 첨부 및 처리 파이프라인 (Two-Phase Upload)

현재 구현된 파일 첨부 방식은 **"선(先) 업로드 및 식별자 획득 → 후(後) 메시지 전송 시 식별자 참조"**라는 **Two-Phase (Out-of-band) Upload** 아키텍처를 따릅니다.

1. **클라이언트 드롭/선택 시점 (`workspace.js:uploadConversationFile`)**:
   - 사용자가 컴포저에 파일을 드롭하거나 파일 선택기로 파일을 지정하면 즉시 `POST /api/v1/assistant/attachments`로 Multipart Form 데이터를 전송합니다.
   - 로컬 브라우저에서는 `URL.createObjectURL(file)`을 생성하여 즉각적인 썸네일 미리보기 칩(Chip)을 UI에 표시합니다.
2. **백엔드 검증 및 임시 보관 (`conversation_attachment_service.py`)**:
   - `ALLOWED_ATTACHMENT_MIMES`(이미지, PDF, 텍스트, 오디오, 비디오, 아카이브 등) 목록 및 `MAX_UPLOAD_BYTES` 크기를 엄격하게 화이트리스트 방식으로 검증합니다.
   - 고유 `attachment_id`(UUIDv4)를 발급하고 원본 콘텐츠의 SHA256 해시를 계산합니다.
   - 안전한 임시 스토리지(`storage.save_temporary`)에 저장하고 TTL(기본 보존 기간)을 부여합니다.
   - `ConversationAttachment` 모델에 메타데이터를 영속화하고 `{ attachment_id, file_name, content_mime_type, analysis }`를 응답합니다.
3. **지연 분석(Lazy Analysis) 철학**:
   - 파일 업로드 시점에는 불필요하게 고비용의 멀티모달 LLM을 호출하지 않고 안전하게 저장만 수행합니다.
   - 사용자가 실제로 채팅 메시지를 통해 해당 파일에 대한 질문이나 등록 요청을 할 때 비로소 LLM에 컨텍스트 및 원본 바이트를 전달합니다.

### 2.2. 대화 송수신 프로토콜 (`/api/v1/assistant/chat`)

- **요청 프로토콜**: 단일 HTTP `POST` JSON 통신
  ```json
  {
    "creator_wallet": "0x...",
    "message": "이 이미지로 라이선스 등록해줘",
    "attachment_ids": ["550e8400-e29b-41d4-a716-446655440000"],
    "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
  }
  ```
- **백엔드 오케스트레이션 (`CreatorAssistantService.ask`)**:
  1. **감사 추적(Audit Trail)**: 외부 AI 장애와 무관하게 사용자 입력 메시지(`AssistantMessage`, role='user')와 첨부 연결 관계를 DB에 우선 기록.
  2. **컨텍스트 조립(Grounded Context Assembly)**: 창작자의 온체인/오프체인 자산 현황(`overview`), 사용자 정의 지침(`behavior_instructions`), 공개 카탈로그, 첨부 파일 메타데이터를 실시간 질의하여 프롬프트에 주입.
  3. **의도 라우팅 및 LLM 호출**:
     - **명시적 등록 요청(`_license_registration_attachment`)**: 정규식 및 첨부 이력을 파악하여 `gemini.suggest_registration_metadata` 호출 -> 제목, 설명, 검색 태그 생성.
     - **일반 액션 계획(`plan_creator_action`)**: Gemini의 구조화된 출력(JSON Schema: `CREATOR_ACTION_RESPONSE_SCHEMA`)을 통해 도구 실행 계획 수립.
     - **멀티모달 질의(`analyze_attachment`)**: 첨부된 원본 바이너리를 읽어 `gemini.assist_with_attachments`로 전달.
  4. **도구 실행 격리**: AI에게 직접적인 DB/시스템 쓰기 권한을 주지 않고, `CreatorActionService`가 계획을 재검증한 후 안전하게 실행.
- **응답 프로토콜**:
  ```json
  {
    "answer": "해당 이미지에 대한 라이선스 등록 초안을 생성했습니다.",
    "action": null,
    "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "registration_metadata": {
      "attachment_id": "550e8400-e29b-41d4-a716-446655440000",
      "file_name": "artwork.png",
      "content_mime_type": "image/png",
      "reply": "등록 초안이 준비되었습니다.",
      "title": "네온 사이버펑크 시티",
      "description": "미래 도시의 야경을 담은 디지털 아트워크",
      "tags": ["사이버펑크", "디지털아트", "야경", "네온"]
    }
  }
  ```

### 2.3. 캔버스(Canvas) 연동 및 저작권 등록 플로우

현재 UI는 단순한 텍스트 챗봇이 아니라, **"Chat + Interactive Registration Canvas"**의 하이브리드 구조로 구현되어 있습니다.

- 에이전트 응답에 `registration_metadata`가 포함되어 있으면, 클라이언트 JS는 자동으로 오른쪽 **등록 캔버스(`registration-canvas`)**를 열고 AI가 추천한 메타데이터(제목, 설명, 태그)를 폼에 자동 주입합니다.
- 창작자는 캔버스에서 가격(Devnet SOL), 공개 여부, 패키지 구성 등을 검토/수정합니다.
- **2단계 확정 절차**: `saveDraft` -> `confirm` (확정 토큰 발급) -> `uploadConfirmed` (`/api/v1/ip/register`를 통한 최종 온체인 앵커링) 순으로 진행되어 안전한 상태 전이를 보장합니다.

---

## 3. 상용 에이전트 플랫폼(ChatGPT, Claude, Gemini)과의 비교 분석

| 비교 항목 | 현재 VeriProof 구현 | OpenAI ChatGPT (Assistants / Chat) | Anthropic Claude (Messages API / Web) | Google Gemini (Gemini API / Live) |
| :--- | :--- | :--- | :--- | :--- |
| **파일 첨부 방식** | **Two-Phase (Out-of-band)**<br>- `/attachment` 선행 업로드<br>- ID 발급 후 채팅 페이로드 참조 | **Two-Phase (Files API)**<br>- `POST /v1/files` 선행 업로드<br>- `file_ids` 참조 (Assistants/Chat) | **Hybrid (In-line / Files API)**<br>- Base64 인라인 지원<br>- Files API를 통한 사전 업로드 지원 | **Two-Phase (Files API)**<br>- `client.files.upload` 선행 업로드<br>- Resource URI 참조 |
| **채팅 통신 프로토콜** | **단일 HTTP REST (JSON)**<br>- Request-Response 동기 통신 | **SSE (Server-Sent Events)**<br>- 토큰 단위 스트리밍 (`text/event-stream`) | **SSE (Server-Sent Events)**<br>- 이벤트/블록 스트리밍 (`text/event-stream`) | **SSE 및 양방향 WebSocket**<br>- 스트리밍 및 실시간 Live API (Bidi WS) |
| **파일 분석 타이밍** | **지연 분석 (Lazy Analysis)**<br>- 업로드 시 저장만 수행<br>- 질문/등록 트리거 시 멀티모달 분석 | **사전/지연 혼합**<br>- Code Interpreter는 사전 인덱싱<br>- Vision은 턴 질의 시 처리 | **지연 분석**<br>- 메시지 생성 턴에 문서/이미지 파싱 및 컨텍스트 투입 | **지연 분석**<br>- File API 업로드 후 `generate_content` 시점에 처리 |
| **도구/액션 실행 방식** | **Structured Output 기반 계획 수립**<br>- JSON Schema 강제 후 내부 디스패처 검증 실행 | **Native Tool Calling / Function Calling**<br>- Model-native Function Calling + Multi-turn Loop | **Native Tool Use**<br>- `tools` 블록 정의 + Model-driven Tool Call Loop | **Native Function Calling**<br>- `tools=[FunctionDeclaration]` 기반 모델 제어 |
| **UI 상호작용 모델** | **Chat + Side Canvas (폼 연동)**<br>- 채팅 응답에 메타데이터 탑재 시 캔버스 자동 프리필 | **ChatGPT Canvas**<br>- 텍스트/코드 전용 사이드 에디터 패널 연동 | **Claude Artifacts**<br>- 독립된 사이드 뷰어/에디터 (React/SVG/Doc) | **Gemini Extensions / Workspace**<br>- 사이드 패널 및 구글 워크스페이스 도구 연동 |
| **대화 상태 및 메모리** | **DB 영속화 + RAG 컨텍스트 주입**<br>- `AssistantMessage`, 지갑 자산, Directives 주입 | **Thread 기반 서버 세션 또는 클라이언트 주입**<br>- Assistants Thread / Custom Instructions | **Stateless API + 클라이언트 히스토리**<br>- Project Knowledge + System Prompt | **Session / Content History 주입**<br>- System Instruction + Context Cache |

---

## 4. 핵심 영역별 적합성 평가 및 진단

### 4.1. 파일 첨부 방식: `/attachment` 사전 업로드 방식의 적합성
- **판정: 매우 적합 (Industry Standard Best Practice)**
- **근거**:
  1. **네트워크 효율성**: 이미지나 영상 등 수 MB~수십 MB 단위의 바이너리를 대화 메시지 JSON 페이로드에 Base64로 인코딩하여 전송하면 약 33%의 페이로드 오버헤드가 발생합니다. REST 엔드포인트를 분리하여 바이너리를 먼저 전송하고 고유 식별자(`attachment_id`)만 전달하는 것은 네트워크와 메모리 효율 측면에서 가장 이상적입니다.
  2. **에러 핸들링 및 UX**: 파일 크기 초과, 허용되지 않는 확장자 등의 오류를 채팅 전송 버튼을 누르기 전(파일을 드롭하는 즉시) 감지하여 사용자에게 피드백할 수 있습니다.
  3. **재사용성**: 하나의 첨부 파일을 여러 대화 턴이나 등록 캔버스에서 중복 업로드 없이 식별자로 재참조할 수 있습니다.
  4. **비용 최적화**: 업로드 즉시 LLM을 돌리지 않고, 사용자가 "이 파일로 등록해줘"라고 요청할 때만 LLM을 호출하는 Lazy Evaluation 설계는 불필요한 토큰 비용을 방지합니다.

### 4.2. 채팅 프로토콜: 단일 HTTP POST vs 스트리밍(SSE)
- **판정: 기능적으로 타당하나 UX 관점에서 스트리밍 도입 검토 필요 (Good, but needs Streaming Evolution)**
- **근거**:
  1. **현재의 장점**: 구현 복잡도가 낮고, Django의 전통적인 동기 뷰 및 세션/CSRF 처리와 완벽하게 부합하며 디버깅과 테스트가 용이합니다.
  2. **한계점 (Latency 체감)**: 멀티모달 이미지 분석(`suggest_registration_metadata`)이나 복합 프롬프트 추론 시 응답 생성까지 2~5초 이상 소요될 수 있습니다. 상용 플랫폼처럼 첫 토큰을 즉시 노출하는 **TTFT(Time To First Token) 단축**이 되지 않아 사용자가 타이핑 애니메이션(`vp-typing`)만 보고 대기해야 합니다.

### 4.3. 캔버스 연동 및 저작권 등록 인터랙션
- **판정: 최신 에이전트 UX 트렌드와 완벽 부합 (Excellent Alignment with Artifacts/Canvas)**
- **근거**:
  - 단순 챗봇은 모든 등록 정보를 채팅창 텍스트로만 나열하여 실제 수정 및 온체인 서명으로 이어지는 과정에서 심각한 마찰(Friction)이 발생합니다.
  - VeriProof의 구현은 Claude Artifacts나 ChatGPT Canvas와 같이 **"대화로 의도를 파악하고, 구체적인 작업물은 전용 캔버스에서 검토·확정"**하는 최첨단 인터랙션 패턴을 정확하게 따르고 있습니다.

---

## 5. 향후 아키텍처 고도화 로드맵 (개선 권장사항)

```mermaid
flowchart TD
    subgraph Phase1["Phase 1: 실시간 스트리밍 고도화"]
        A[HTTP POST Request] --> B[Django StreamingHttpResponse / SSE]
        B --> C[Token-by-Token Streaming to Client]
        C --> D[Final Chunk: Tool Call & Metadata Payload]
    end

    subgraph Phase2["Phase 2: Function Calling 고도화"]
        E[Gemini Native Function Calling] --> F[Multi-turn Tool Loop]
        F --> G[Dynamic Schema Validation]
    end

    subgraph Phase3["Phase 3: 멀티모달 & 보안 강화"]
        H[Presigned Direct S3/GCS Upload] --> I[Async Virus & Content Scan]
        I --> J[Perceptual Hash Pre-check]
    end
```

### 1) SSE (Server-Sent Events) 기반 토큰 스트리밍 도입
- **방안**: `StreamingHttpResponse` 또는 Django Channels/ASGI를 활용하여 `POST /api/v1/assistant/chat/stream` 엔드포인트 구축.
- **프로토콜**:
  ```http
  event: token
  data: {"delta": "해당 이미지의 "}

  event: token
  data: {"delta": "저작권 등록 정보를 분석 중입니다..."}

  event: canvas_action
  data: {"type": "prefill_registration", "metadata": {...}}

  event: done
  data: {"conversation_id": "..."}
  ```

### 2) 네이티브 Function Calling 루프 표준화
- 현재의 JSON Schema 단일 계획 수립 방식에서 한 단계 나아가, Gemini의 네이티브 `Tool / FunctionDeclaration`을 활용하여 **"질의 → 도구 호출(지갑 조회) → 결과 주입 → 추가 도구 호출(등록 초안 생성) → 최종 답변"**으로 이어지는 다단계 에이전틱 루프(Multi-step Agentic Loop) 지원.

### 3) 대용량 파일용 Presigned Direct Upload 고려
- 현재는 파일이 Django 애플리케이션 서버를 거쳐 임시 스토리지로 저장됩니다. 향후 고해상도 비디오/음원(수백 MB 이상) 등록 확장을 위해, 클라이언트가 스토리지(GCS / S3 / R2)로 직접 업로드하는 Presigned URL 발급 방식으로 확장할 수 있습니다.

---

## 6. 결론 요약

현재 VeriProof 프로젝트의 에이전트 채팅 및 저작권 등록 아키텍처는 **상용 에이전트 플랫폼(ChatGPT, Claude, Gemini)의 설계 원칙과 업계 표준 패턴(Two-Phase File Upload, Grounded RAG, Side-Canvas Interaction, Strict Action Separation)을 충실하고 견고하게 준수**하고 있습니다.

특히 `/attachment`를 통한 선업로드 및 메타데이터 캔버스 프리필 연동은 기능적 안정성과 사용자 경험 측면에서 매우 높은 완성도를 갖추고 있으며, 향후 **SSE 기반 스트리밍 프로토콜** 및 **네이티브 툴 체이닝**을 추가 도입한다면 상용 플랫폼 수준의 실시간 응답 체감과 에이전틱 확장성을 모두 확보할 수 있을 것으로 판단됩니다.
