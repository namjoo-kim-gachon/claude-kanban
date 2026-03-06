# claude-kanban

[![CI](https://github.com/namjoo-kim-gachon/claude-kanban/actions/workflows/ci.yml/badge.svg)](https://github.com/namjoo-kim-gachon/claude-kanban/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

`claude-kanban`은 GitHub 이슈 댓글의 멘션 요청을 받아, **이슈 내용(제목/본문)과 함께** `MENTION_TO_TMUX` 매핑으로 지정한 tmux pane으로 전달해 Claude Code가 바로 작업을 시작할 수 있게 해주는 webhook 서버입니다. 클로드코드는 작업을 마치고 결과를 요약해 이슈 댓글로 남기고, 이슈 작성자를 멘션해 알려줍니다. 또한 해당 이슈가 GitHub Projects에 등록되어 있으면 작업 시작 시 자동으로 `In Progress`로 옮기고, 작업 완료 후 `Review`로 옮겨줍니다.

---

## 왜 만들었나요?

핵심 목적은 **GitHub Issue를 통해 Claude Code를 원격 제어**하는 것입니다.

Claude Code가 점점 더 긴 시간 동안 스스로 작업할 수 있게 되면서, 실시간 대화형으로만 붙어서 작업하기 어려워졌습니다. 그래서 개발자에게 가장 익숙한 GitHub Issue를 비동기 작업 인터페이스로 사용해, 요청 전달(`@claude`) → 실행 → 결과 보고 → Projects 상태 전환(`In Progress` → `Review`)까지 한 흐름으로 운영하려고 만들었습니다.

즉, `claude-kanban`은 단순 webhook 서버가 아니라 **GitHub Issue 기반 Claude Code 원격/비동기 작업 오케스트레이션 레이어**입니다.

---

## 어떤 사용자에게 유용한가요?

이 프로젝트는 아래처럼 **Claude Code를 비동기로 운영**하려는 팀에 맞습니다.

- GitHub Issue를 작업 지시/진행 공유의 기준으로 쓰는 팀
- 실시간 채팅 대신, 댓글 기반으로 원격 작업을 돌리고 싶은 팀
- 작업 결과 보고와 Projects 상태 반영까지 한 흐름으로 자동화하고 싶은 팀

---

## 동작 방식 (원격/비동기 기준)

1. 이슈 댓글에 `@cc` 요청을 남김
2. 서버가 요청 문장 + 이슈 제목/본문을 함께 수집
3. 요청을 `MENTION_TO_TMUX`에 매핑된 tmux pane(예: `@cc=0:0.0`)으로 전달
4. Claude Code가 작업 수행
5. 작업 결과를 이슈 댓글로 요약 보고하고, 작성자를 멘션해 알림
6. Projects에 등록된 이슈라면 상태를 `In Progress` → `Review` 흐름으로 반영

---

## 빠른 시작

### 1) 준비물

- Python 3.11+
- tmux
- GitHub Personal Access Token (PAT)

### 2) 프로젝트 받기 (clone)

```bash
git clone https://github.com/namjoo-kim-gachon/claude-kanban.git
cd claude-kanban
```

### 3) 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4) `.env` 설정

프로젝트 루트에 `.env` 파일을 만들고 아래 값을 채우세요.

```dotenv
GITHUB_WEBHOOK_SECRET=
GITHUB_PAT=
MENTION_TO_TMUX=@cc=0:0.0,@cc1=0:1.0,@cc2=0:2.0,@cc3=0:3.0,@cc4=0:4.0
SQLITE_PATH=./state/webhook.db
LOG_LEVEL=INFO
```

필수로 보면 되는 값:
- `MENTION_TO_TMUX`: 멘션→tmux pane 매핑 (예: `@cc=0:0.0,@cc1=0:1.0`) **필수**
- `GITHUB_PAT`: 댓글/Projects API 처리용 토큰

---

## 처음 설정할 때 많이 막히는 지점

### 1) PAT 권한

개인 계정일 경우 Fine grained token 으로는 Projects 권한 설정할 수 없음.

최소 권한(Fine grainded token 일 경우):
- Issues (read/write)
- 이 경우 깃헙 프로젝트에서 카드를 자동으로 옮겨주는 기능은 사용할 수 없습니다.

Projects 상태 전환까지 쓰려면 추가:
- classic 토큰에서 아래의 두가지 권한을 주어야 함.
- `repo`
- `project`


### 2) GitHub Webhook 설정

- Payload URL: `https://<your-domain>/webhook/github`
- Content type: `application/json`
- Secret: `.env`의 `GITHUB_WEBHOOK_SECRET`와 동일
- Events: **Issue comments**

---

## 서버 실행

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --env-file .env --app-dir .
```

중지:
```bash
Ctrl+C
```

---

## 엔드포인트

- `GET /healthz`
- `POST /webhook/github`

---

## Claude Code 스킬 설치 (`claude-kanban`)

이 레포에는 Claude Code에서 바로 사용할 수 있는 스킬 파일이 포함되어 있습니다.

- 스킬 파일 위치: `skills/claude-kanban/SKILL.md`

아래 명령으로 사용자 로컬 Claude Code 스킬 디렉터리에 설치할 수 있습니다.

```bash
mkdir -p ~/.claude/skills/claude-kanban
cp skills/claude-kanban/SKILL.md ~/.claude/skills/claude-kanban/SKILL.md
```

설치 후 Claude Code에서 아래처럼 사용할 수 있습니다.

```text
/claude-kanban
```

## 테스트

```bash
source .venv/bin/activate
pytest tests
```

