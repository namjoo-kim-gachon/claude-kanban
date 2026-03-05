# claude-kanban webhook server

[![CI](https://github.com/namjoo-kim-gachon/claude-kanban/actions/workflows/ci.yml/badge.svg)](https://github.com/namjoo-kim-gachon/claude-kanban/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

GitHub `issue_comment` 웹훅을 받아 `@claude` 댓글만 필터링하고, tmux 세션에 순차 실행하는 FastAPI 서버입니다.

## 주요 기능
- `X-Hub-Signature-256` 기반 HMAC 검증
- `issue_comment` + `action=created` + repo/권한/멘션 필터
- `X-GitHub-Delivery` 기반 SQLite idempotency
- 전역 FIFO 큐 + 단일 worker 순차 처리
- reaction 피드백: 수신 직후 `eyes` 1회
- tmux 안전 실행: `shell=False`, `send-keys -l`, `Enter`

## 요구사항
- Python 3.11+
- tmux

## GitHub PAT 권한 가이드
- 최소 권한: Issues (read/write)
- GitHub Projects v2 자동 상태 이동(`In Progress`)을 사용하려면 Projects 관련 추가 권한이 필요합니다.
- Projects 권한이 없거나 이슈가 프로젝트 아이템에 연결되어 있지 않으면, worker는 실패로 중단하지 않고 본 처리(tmux 전달)를 계속 진행합니다.
- 이 경우 tmux payload의 `project_transition.in_progress.reason`에 실패 원인이 기록됩니다.
- tmux payload에는 `issue_author_login`이 포함되어 있어, 후속 처리에서 `@<issue_author_login>` 형태로 멘션 답글을 작성할 수 있습니다.

## 빠른 시작
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install fastapi==0.116.1 uvicorn==0.35.0 httpx==0.28.1 pytest==8.4.1
```

`.env` 파일을 채웁니다.

```dotenv
GITHUB_WEBHOOK_SECRET=
GITHUB_PAT=
ALLOWED_REPO=github_org/repo_name
TMUX_TARGET=session_name:0.0
MENTION_KEYWORD=@claude
SQLITE_PATH=./state/webhook.db
LOG_LEVEL=INFO
```

서버 실행 (`.env` 자동 로드):
```bash
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --env-file .env --app-dir .
```

서버 중지:
```bash
# 실행 터미널에서
Ctrl+C
```

## 테스트
```bash
source .venv/bin/activate
pytest tests
```

## 엔드포인트
- `GET /healthz`
- `POST /webhook/github`

## 웹훅 설정 (GitHub)
- Payload URL: `https://<your-domain>/webhook/github`
- Content type: `application/json`
- Secret: `GITHUB_WEBHOOK_SECRET`와 동일 값
- Events: **Issue comments**
