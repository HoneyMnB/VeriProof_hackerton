# 테스트 계획 (TDD Strategy) — VeriProof AI

> TDD(RED→GREEN→REFACTOR)로 구현하기 위한 전체 테스트 전략, 도구, 픽스처, 커버리지 목표, SPEC↔테스트 매핑.

- 관련: [PRD](./PRD.md) · [아키텍처](./00-architecture-and-data-model.md) · [SPEC](./specs/)

---

## 1. 원칙
- **테스트 우선**: 각 SPEC의 인수조건(AC)마다 실패하는 테스트(RED)를 먼저 작성 → 최소 구현(GREEN) → 정리(REFACTOR).
- **외부 I/O 격리**: Gemini·Solana·GCP(Firestore/BigQuery/PubSub)·pay.sh는 전부 서비스 레이어 뒤에 두고 테스트 전용 fake/stub을 주입한다. 실제 런타임 factory는 Solana mock 어댑터를 선택하지 않는다.
- **결정성**: 시간·랜덤·네트워크 의존 제거(고정 시드/주입 시각/mock).
- **단일 로직 경로**: 정산 후속처리는 Workflows/동기 폴백이 같은 서비스 메서드를 호출 → 테스트는 서비스 메서드 단위로 검증(경로 중복 테스트 불필요).

## 2. 테스트 계층
| 계층 | 대상 | 도구 | 비고 |
|------|------|------|------|
| Unit | services/ 로직, 계산·검증·폴백 | pytest | 외부 mock, 최다 |
| Integration | Django views/API, DB 왕복 | pytest-django, test DB(PostgreSQL) | 외부서비스 mock |
| Contract | 402/협상/정산 JSON 스키마 | pytest + jsonschema | API 계약 고정 |
| Frontend | vanilla JS 동작 | jsdom 또는 Playwright(최소) | fetch/onSnapshot mock |
| E2E | 등록→402→협상→정산→인증서 | pytest e2e + 시뮬레이터 | Devnet(옵션) 또는 SANDBOX_MODE=mock |

## 3. 도구·설정
- `pytest`, `pytest-django`, `pytest-cov`, `pytest-mock`, `factory_boy`(팩토리), `freezegun`(시각 고정), `responses`/커스텀 fake(외부 API).
- 커버리지 게이트: **서비스 레이어 ≥ 85%**, 전체 ≥ 75%. CI에서 `--cov-fail-under`.
- 테스트 DB: PostgreSQL(운영과 동일 엔진). Firestore/BigQuery/PubSub은 fake 어댑터.

## 4. 공용 픽스처 / 팩토리 (`tests/conftest.py`, `factories.py`)
- `creator_factory`, `ip_asset_factory`, `negotiation_session_factory`, `license_factory`, `batch_order_factory`.
- `fake_gemini`(analyze/negotiate/quote 반환·실패 주입), `fake_solana`(verify/anchor/transfer/issue_cert·실패 주입, 테스트 전용), `fake_storage`(in-memory), `fake_firestore`/`fake_bigquery`/`fake_pubsub`(호출 기록).
- `image_bytes_png`(유효 PNG), `oversize_bytes`, `non_image_bytes`.
- `settings_local`(FIRESTORE_ENABLED=false, STORAGE_BACKEND=local, AP2_ENABLED=false).

## 5. 외부 서비스 실패 주입 매트릭스
| 서비스 | 정상 | 실패 모드 | 검증 SPEC |
|--------|------|-----------|-----------|
| Gemini analyze | 스키마 반환 | 3회 실패 → degraded | SPEC-001 R13 |
| Gemini negotiate | ACCEPT/COUNTER | 파싱 실패 → 규칙 폴백 | SPEC-003 R8 |
| Solana anchor | 서명 반환 | 실패 → draft/202 | SPEC-001 R14 |
| Solana verify | 유효 | recipient/mint/amount 불일치 | SPEC-004 R2 |
| Solana issue_cert | 서명 | 실패 → license 유지 | SPEC-004 R16 |
| Solana transfer | 서명 | 부분 실패 → failed 유지 | SPEC-008 R9 |
| pay.sh webhook | 유효 서명 | 서명 불일치 → 401 | SPEC-004 R12 |

## 6. SPEC ↔ 테스트 매핑 (요약)
| SPEC | 핵심 RED 테스트 수(가이드) | 대표 검증 |
|------|--------------------------|-----------|
| SPEC-001 | 7 unit + 11 integration | 등록·해시·앵커링·저장·degrade |
| SPEC-002 | 8 unit + 7 integration | 402/판별/라이선스 검사/수취주소 해석 |
| SPEC-003 | 8 unit + 10 integration | ACCEPT/COUNTER/폴백/불변식/에스크로 라우팅 |
| SPEC-004 | 8 unit + 14 integration | 검증/멱등/인증서/다운로드/webhook/파이프라인 |
| SPEC-005 | 6 backend + 4 frontend | 라이브러리/증명서/타임라인 |
| SPEC-006 | 6 backend + 3 frontend | 샌드박스 흐름/스트림 |
| SPEC-007 | 6 unit + 7 integration | 배치 견적/정산/부분성공 |
| SPEC-008 | 8 unit + 3 integration | 분배 계산/송금/부분실패 |

## 7. E2E 시나리오 (통합 검증)
- **E2E-1 (S1)**: register → GET(402) → negotiate(ACCEPT) → settle(valid tx) → GET(200 download) → 인증서 존재.
- **E2E-2 (S2)**: batch/negotiate(3건) → batch/settle(total) → 각 라이선스·토큰.
- **E2E-3 (S3)**: 2차창작 register(parent, bps=3000) → escrow settle(10) → 3/7 분배 tx 2건.
- 각 E2E는 `SANDBOX_MODE=mock`(CI)와 Devnet(수동 증빙) 2모드로 실행.

## 8. Definition of Done (테스트 관점)
- 모든 SPEC AC에 대응하는 GREEN 테스트 존재.
- 서비스 레이어 커버리지 ≥ 85%.
- E2E-1/2/3 통과(mock 모드 CI 필수, Devnet 증빙 1회 이상).
- 외부서비스 없이 전체 스위트 오프라인 실행 가능.
