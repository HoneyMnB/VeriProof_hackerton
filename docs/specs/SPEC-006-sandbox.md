# SPEC-006 — Multi-Agent 협상 샌드박스 시뮬레이터 (Page 3)

- 상위: [PRD](../PRD.md) · [아키텍처](../00-architecture-and-data-model.md)
- 페이지: Page 3 (심사위원/시연용)
- 관련 SPEC: SPEC-002/003/004 (전 과정 시각화)
- 개발방법: TDD

---

## 1. 목적
외부 구매자 AI가 실제로 x402 접근 → 협상 → 온체인 결제하는 전 과정을 분할 화면으로 실시간 시각화한다. 좌측(판매자 AI 로그), 우측(구매자 AI 터미널), 하단(네트워크 인스펙터: 402→JSON 협상→USDC 송금 라이브 스트림).

## 2. 범위
- IN: 샌드박스 페이지, 구매자 AI 시뮬레이터 실행 트리거, 실시간 이벤트 스트림(Firestore/폴링), 3분할 UI.
- OUT: 실제 결제 검증 로직(SPEC-004 재사용).

---

## 3. EARS 요구사항

### 시뮬레이션 구동
- **R1** WHEN 심사자가 샌드박스에서 "시뮬레이션 시작"을 누르면(대상 `asset_id`, 초기 `offer_usdc` 지정), the system SHALL 구매자 AI 시뮬레이터 실행을 트리거한다(`POST /api/v1/sandbox/run`).
- **R2** WHEN 시뮬레이터가 실행되면, the system SHALL 실제 엔드포인트(`GET /ip/{id}`→402, `POST /negotiate`, `POST /settle`)를 순차 호출하여 실거래 흐름을 재현한다(Devnet 실 트랜잭션).
- **R3** WHEN 각 단계가 발생하면, the system SHALL `EventRecorder`로 이벤트를 기록하고 Firestore `sandbox_feed`에 push한다.

### 실시간 표시
- **R4** WHILE 시뮬레이션이 진행 중이면, the system SHALL 좌측 창에 판매자 AI(Gemini) 추론/응답 로그를 순차 표시한다.
- **R5** WHILE 시뮬레이션이 진행 중이면, the system SHALL 우측 창에 구매자 AI의 offer/counter/accept 액션을 표시한다.
- **R6** WHILE 시뮬레이션이 진행 중이면, the system SHALL 하단 인스펙터에 `HTTP 402` 수신 → JSON 협상 → USDC 송금 tx를 라이브 스트림으로 표시한다.
- **R7** WHERE Firestore 활성이면, the system SHALL `onSnapshot`으로 이벤트를 실시간 반영하고, 아니면 `GET /api/v1/events?since=`를 2초 폴링한다.
- **R8** WHEN 결제 tx가 확정되면, the system SHALL 인스펙터에 Explorer 링크와 소요시간/수수료를 표시한다.

### 견고성
- **R9** IF 시뮬레이터 단계가 실패하면 THEN the system SHALL 실패 이벤트를 스트림에 표시하고 이후 단계를 중단한다(부분 상태 가시화).
- **R10** WHERE 데모 안정성을 위해, the system SHALL `SANDBOX_MODE=mock`일 때 온체인 호출을 모의 tx로 대체하는 옵션을 제공한다(네트워크 장애 대비).

---

## 4. 인수조건
| AC | 조건 | 기대결과 |
|----|------|---------|
| AC-1 | 시뮬레이션 시작 | run 트리거 + 이벤트 스트림 시작 |
| AC-2 | 진행 중 | 좌/우/하단 3영역에 단계별 로그 렌더 |
| AC-3 | 402 단계 | 인스펙터에 402 헤더/본문 표시 |
| AC-4 | 협상 단계 | offer→counter→accept 순차 표시 |
| AC-5 | 결제 단계 | USDC tx + Explorer 링크 |
| AC-6 | Firestore 비활성 | 폴링으로 동일 흐름 표시 |
| AC-7 | 중간 실패 주입 | 실패 이벤트 표시 + 이후 중단 |
| AC-8 | SANDBOX_MODE=mock | 온체인 없이 전 흐름 시연 |

---

## 5. TDD 테스트 명세 (RED 우선 목록)

### 백엔드 — 시뮬레이터/스트림
- `test_sandbox_run_invokes_full_flow_in_order` (mock 클라이언트) — get→negotiate→settle 순.
- `test_sandbox_run_records_events_each_step`.
- `test_sandbox_run_pushes_to_firestore_feed` (flag on, mock).
- `test_events_since_returns_incremental` — 폴링 증분.
- `test_sandbox_step_failure_stops_flow` (R9).
- `test_sandbox_mock_mode_skips_onchain` (R10).

### 프론트 — vanilla JS
- `test_stream_renders_three_panes`.
- `test_inspector_shows_402_then_tx`.
- `test_polling_fallback_when_no_firestore`.

---

## 6. 엣지 케이스 / 가정
- 구매자 시뮬레이터는 `scripts/buyer_agent_sim.py`(x402_a2a 기반) 또는 서버 내부 태스크로 실행.
- 실 Devnet 사용 시 데모 전에 구매자 지갑 USDC 프리펀딩 필요.
- 동시 다중 시뮬레이션 → 세션별 피드 분리.
- 데모 신뢰성: 실거래 우선, 네트워크 장애 시 `mock` 폴백(정직하게 "mock" 라벨 표시).
