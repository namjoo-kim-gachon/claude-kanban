# GitHub Webhook → tmux(Claude Code) 자동 실행 서버 기획안

## 1) 목표
GitHub Issue 댓글에 `@claude`가 포함되면, 로컬 tmux 세션 `claude:0.0`에 내용을 자동 주입하고 즉시 실행한다.

---

## 2) 확정 요구사항 (인터뷰 반영)

| 항목 | 확정값 |
|---|---|
| 대상 객체 | Draft Issue가 아닌 **Issue** |
| 트리거 이벤트 | `issue_comment` |
| 액션 필터 | `action == created`만 처리 |
| 트리거 키워드 | 댓글 본문 내 `@claude` 포함 (대소문자 무시) |
| 전달 내용 | **댓글 전체 본문** |
| 추가 컨텍스트 | 해당 이슈에서 `@claude`가 붙은 댓글 기준 첫 실행이면 이슈 제목/본문 포함 |
| 권한 필터 | `OWNER`, `MEMBER`, `COLLABORATOR`만 허용 |
| 허용 저장소 | `namjookim/claude-kanban` 1개 고정 |
| tmux 대상 | `claude:0.0` 고정 |
| 전송 방식 | tmux 입력 후 Enter까지 자동 실행 |
| 중복 처리 | `X-GitHub-Delivery` 기준 idempotent (1회만 처리) |
| 중복 저장소 | SQLite 영속 저장 |
| 동시성 | 전역 FIFO 큐 1개, 순차 처리 |
| 보안 | `X-Hub-Signature-256` 필수 검증 |
| GitHub 인증 | Fine-grained PAT 사용 |
| 실행 피드백 | 댓글 reaction 사용 (`eyes` → `rocket`/`confused`) |
| 서버 프레임워크 | FastAPI |
| 실행 방식 | `uvicorn` 수동 실행 |
| 외부 노출 | cloudflared, 고정 주소 |

---

## 3) 전체 아키텍처

```text
GitHub (issue_comment webhook)
    ↓ HTTPS (cloudflared 고정 주소)
FastAPI /webhook/github
    ├─ 서명 검증(HMAC SHA-256)
    ├─ 이벤트/액션/repo/권한/키워드 필터
    ├─ delivery 중복검사(SQLite)
    ├─ reaction(eyes)
    └─ 전역 FIFO 큐 enqueue

Worker(단일)
    ├─ "첫 @claude 댓글" 판별(GitHub API)
    ├─ tmux payload 생성
    ├─ tmux send-keys + Enter
    └─ reaction(성공 rocket / 실패 confused)
```

---

## 4) 이벤트 처리 플로우 (상세)

1. `POST /webhook/github` 수신
2. 헤더 검증
   - `X-GitHub-Event == issue_comment`
   - `X-Hub-Signature-256` 검증 실패 시 `401`
3. 바디 필터
   - `action == created` 아니면 무시(`202`)
   - `repository.full_name == "namjookim/claude-kanban"` 아니면 무시(`202`)
   - `comment.author_association`이 허용값(OWNER/MEMBER/COLLABORATOR) 아니면 무시(`202`)
   - `comment.body`에 `@claude`(case-insensitive) 없으면 무시(`202`)
4. 중복 방지
   - `X-GitHub-Delivery`가 이미 처리된 ID면 무시(`200` 또는 `202`)
5. 수락 처리
   - 해당 댓글에 `eyes` reaction 추가 시도
   - 작업을 전역 FIFO 큐에 적재
   - `202 Accepted` 반환
6. Worker 순차 처리
   - GitHub API로 해당 이슈 댓글 목록 조회
   - `@claude` 포함 댓글만 필터
   - 현재 댓글이 "`@claude` 댓글 기준 첫 댓글"인지 판별
   - payload 생성
     - 첫 댓글이면: 이슈 제목 + 이슈 본문 + 현재 댓글 전체
     - 아니면: 현재 댓글 전체
   - tmux `claude:0.0`에 literal 입력 후 Enter
   - 성공 시 `rocket`, 실패 시 `confused` reaction 추가

---

## 5) tmux 주입 방식 (안전 기준)

`subprocess.run(..., shell=False)`를 사용하고 `tmux send-keys -l`(literal)로 입력한다.

예시 흐름:
1. `tmux send-keys -t claude:0.0 -l <payload>`
2. `tmux send-keys -t claude:0.0 Enter`

핵심 원칙:
- `shell=True` 금지
- 문자열 결합 명령 실행 금지
- payload는 그대로 literal 전송

---

## 6) SQLite 설계

DB 파일 예시: `./state/webhook.db`

### 테이블: `processed_deliveries`
- `delivery_id TEXT PRIMARY KEY`
- `event TEXT NOT NULL`
- `received_at TEXT NOT NULL` (ISO8601)
- `repo_full_name TEXT NOT NULL`
- `comment_id INTEGER`
- `status TEXT NOT NULL` (`accepted`/`ignored`/`processed`/`failed`)

인덱스(권장):
- `idx_processed_deliveries_received_at(received_at)`

운영 정책:
- delivery 기준 1회 처리 보장
- 주기적 정리(예: N일 지난 레코드 삭제) 배치 가능

---

## 7) GitHub API 사용 범위

### 필요 API
- 이슈 댓글 조회(첫 `@claude` 판별)
- 댓글 reaction 추가
- Projects v2 상태 전환(GraphQL mutation)

### 인증
- Fine-grained PAT
- 대상 repo: `namjookim/claude-kanban`
- 필요한 권한: Issues 읽기 + reaction 추가 가능한 권한
- Projects 전환 시 추가 권한: `project`, `read:project`

### GraphQL 호출 안정성 원칙
- `gh api graphql` 호출 시 mutation은 인라인 문자열(`-f query='mutation(...)'`) 대신 **HEREDOC**으로 전달한다.
- 인라인 방식은 셸/이스케이프 차이로 `Expected VAR_SIGN` 파싱 오류를 유발할 수 있다.

---

## 8) reaction 정책

- 수신/큐잉 성공: `eyes`
- tmux 전송 성공: `rocket`
- tmux 전송 실패: `confused`

원칙:
- reaction 실패가 본 처리(큐잉/실행)를 막지 않도록 분리
- 상태 확인은 댓글 reaction만으로 수행

---

## 9) FastAPI 엔드포인트 설계

### `POST /webhook/github`
- 입력: GitHub webhook payload + headers
- 출력:
  - `401` 서명 불일치
  - `202` 필터 무시 또는 큐 수락
  - `200` 중복 delivery 무시

### `GET /healthz`
- 서버 기동 상태 확인용

(선택) `GET /metrics`
- 큐 길이, 성공/실패 건수 노출

---

## 10) 환경변수/설정값

- `GITHUB_WEBHOOK_SECRET` (필수)
- `GITHUB_PAT` (필수)
- `MENTION_TO_TMUX=@claude=claude:0.0,@ops=claude:0.1`
- `SQLITE_PATH=./state/webhook.db`
- `LOG_LEVEL=INFO`

cloudflared/웹훅 URL은 고정 주소 사용(실제 값은 운영 시 입력).

---

## 11) 운영 절차 (수동 실행 기준)

1. tmux 세션 확인: `claude:0.0` 존재
2. FastAPI 실행 (uvicorn)
3. cloudflared 터널 실행(고정 주소)
4. GitHub Webhook 설정
   - 이벤트: `Issue comments`
   - URL: cloudflared 고정 주소 + `/webhook/github`
   - Secret: `GITHUB_WEBHOOK_SECRET`
5. 테스트 댓글로 E2E 검증 (`@claude ...`)

---

## 12) 테스트 시나리오

1. 서명 틀린 요청 → `401`
2. 다른 이벤트(`issues`) → 무시
3. `issue_comment`지만 `edited` → 무시
4. 다른 repo 이벤트 → 무시
5. 외부 기여자 댓글(`CONTRIBUTOR` 등) → 무시
6. `@claude` 없는 댓글 → 무시
7. 동일 delivery 재전송 → 중복 무시
8. 첫 `@claude` 댓글 → 제목/본문/댓글 함께 주입
9. 두 번째 `@claude` 댓글 → 댓글만 주입
10. tmux 타겟 없음/실패 → `confused` reaction

---

## 13) 에러 처리 원칙

- 웹훅 수신 경로는 빠르게 응답(큐잉 중심)
- 외부 API 실패, tmux 실패는 worker에서 처리하고 로그 + reaction으로 피드백
- 실패 시에도 서버 프로세스는 계속 동작

---

## 14) 구현 단계 (MVP)

1. FastAPI 뼈대 + `/healthz`
2. 서명 검증 + 이벤트 필터
3. SQLite delivery idempotency
4. 전역 FIFO 큐 + 단일 worker
5. GitHub API 연동(댓글 조회/리액션)
6. 첫 `@claude` 판별 로직
7. tmux 주입 + Enter 실행
8. E2E 테스트 및 로그 정리

---

## 15) 미정/실운영 시 입력 필요 값

- cloudflared 고정 웹훅 URL (실제 도메인)
- `GITHUB_WEBHOOK_SECRET` 실제 값
- `GITHUB_PAT` 실제 값
- SQLite 파일 실제 경로(상대/절대)

이 값들은 코드 하드코딩 금지, 환경변수로만 주입한다.
