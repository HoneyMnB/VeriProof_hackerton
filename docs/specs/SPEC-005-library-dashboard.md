# SPEC-005 — IP 라이브러리 & 온체인 증명서 대시보드 (Page 1·2)

- 상위: [PRD](../PRD.md) · [아키텍처](../00-architecture-and-data-model.md)
- 페이지: Page 1(워크스페이스) + Page 2(라이브러리·증명서)
- 관련 SPEC: SPEC-001(등록), SPEC-004(거래내역)
- 개발방법: TDD (뷰/템플릿 렌더 + 프론트 vanilla)

---

## 1. 목적
창작자가 (Page 1) 대화형으로 이미지를 등록하고, (Page 2) 등록한 IP 목록·온체인 증명서·거래 타임라인을 확인한다. Vanilla HTML/CSS/JS로 구현하며 실시간 상태는 Firestore `onSnapshot`(폴백: 폴링)으로 반영한다.

## 2. 범위
- IN: 워크스페이스 채팅형 등록 UI(드래그&드롭), 자산 프리뷰 카드, 조건 슬라이더, 라이브러리 그리드, Explorer 링크, QR 증명서, 거래 타임라인.
- OUT: 협상/결제 백엔드(SPEC-002~004).

---

## 3. EARS 요구사항

### Page 1 — 워크스페이스
- **R1** WHEN 창작자가 이미지를 드래그&드롭 영역에 놓으면, the system SHALL 클라이언트에서 프리뷰를 표시하고 `POST /api/v1/ip/register`로 업로드한다.
- **R2** WHEN 등록 응답을 받으면, the system SHALL 대화 피드에 분석 결과(태그·카테고리·독창성 점수·추천가)를 카드로 렌더한다.
- **R3** WHEN 창작자가 최소가/목표가 슬라이더를 조정하면, the system SHALL 값을 폼에 반영하여 등록 요청에 포함한다.
- **R4** WHEN 등록이 완료되면, the system SHALL 등록완료 카드에 `tx_hash`(앵커링)와 `x402_endpoint`를 표시한다.

### Page 2 — 라이브러리·증명서
- **R5** WHEN 창작자가 `/library`에 접근하면, the system SHALL 해당 창작자의 `IpAsset` 목록을 그리드 카드로 렌더한다.
- **R6** WHEN 카드의 프리뷰 토글을 누르면, the system SHALL 워터마크/썸네일 프리뷰를 전환한다(원본은 노출하지 않음).
- **R7** WHEN "Solana Explorer 검증" 버튼을 누르면, the system SHALL `anchor_tx_sig`에 대한 Explorer URL(devnet)을 새 탭으로 연다.
- **R8** WHEN "증명서" 버튼을 누르면, the system SHALL 온체인 소유권 정보를 담은 QR 모달을 표시한다(모바일 스캔용).
- **R9** WHEN 자산의 거래 타임라인 탭을 열면, the system SHALL 해당 자산의 `License`·`AgentEvent`를 시간순으로 렌더(구매 에이전트, 금액, 시각, tx).
- **R10** WHERE Firestore가 활성이면, the system SHALL `asset_status`/거래 이벤트를 `onSnapshot`으로 실시간 갱신하고, 아니면 `/api/v1/events` 폴링(2초)으로 갱신한다.

### 데이터 API (프론트 소비)
- **R11** WHEN `/library` 뷰가 렌더되면, the system SHALL 서버가 자산 목록을 컨텍스트로 주입하거나 `GET /api/v1/assets?creator=`를 제공한다.
- **R12** WHEN 거래내역을 요청하면, the system SHALL `GET /api/v1/ip/{asset_id}/transactions`로 라이선스·이벤트를 반환한다.

---

## 4. 인수조건
| AC | 조건 | 기대결과 |
|----|------|---------|
| AC-1 | 이미지 드롭 | 프리뷰 표시 + register 호출 |
| AC-2 | 등록 응답 | 분석 카드 렌더(태그/점수/추천가) |
| AC-3 | 등록완료 | tx_hash + x402_endpoint 표시 |
| AC-4 | /library 접근 | 창작자 자산 그리드 렌더 |
| AC-5 | 프리뷰 토글 | 워터마크↔썸네일, 원본 미노출 |
| AC-6 | Explorer 버튼 | 올바른 devnet Explorer URL |
| AC-7 | 증명서 버튼 | QR 모달 표시 |
| AC-8 | 거래 타임라인 | License/이벤트 시간순 렌더 |
| AC-9 | Firestore 비활성 | 폴링으로 갱신 동작 |

---

## 5. TDD 테스트 명세 (RED 우선 목록)

### 백엔드 — 뷰/데이터
- `test_library_view_renders_only_owner_assets` — 타 창작자 자산 미노출.
- `test_assets_api_filters_by_creator`.
- `test_transactions_api_returns_licenses_and_events` — 시간순.
- `test_explorer_url_builder_devnet` — `anchor_tx_sig`→URL.
- `test_events_polling_endpoint_returns_since` — `since` 파라미터 필터.
- `test_certificate_payload_excludes_original`.

### 프론트 — vanilla JS (jsdom 또는 Playwright 최소)
- `test_dragdrop_triggers_register_fetch` (fetch mock).
- `test_render_analysis_card_from_response`.
- `test_preview_toggle_switches_src`.
- `test_firestore_disabled_uses_polling` (flag mock).

---

## 6. 엣지 케이스 / 가정
- 자산 0개 → 빈 상태 안내.
- 앵커링 보류(status=draft) 자산 → Explorer 버튼 비활성.
- QR은 온체인 링크/증명 데이터만 인코딩(개인정보 없음).
- 접근제어: `/library`는 창작자 지갑 서명 또는 세션 기반 소유 확인(해커톤은 지갑 파라미터 기반 최소구현 허용).
