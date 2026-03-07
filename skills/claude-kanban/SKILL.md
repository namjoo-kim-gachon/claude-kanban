---
name: claude-kanban
description: Handle claude-kanban webhook payloads where tmux input is formatted as instruction text followed by JSON metadata. Use when you need to execute the instruction, summarize results, mention the issue author in a GitHub issue comment via gh CLI, and move the linked GitHub Projects v2 item to Review using payload metadata.
---

# Claude Kanban

Process incoming tmux content in this format:
1) instruction text (top)
2) blank line
3) JSON payload (bottom)

Execute the instruction first, then always attempt to move project status to Review, then always report back to the issue with a mention.

## Workflow

1. Parse tmux input into `instruction` and `payload_json`.
2. Validate required payload fields needed for issue comment (`repo_full_name`, `issue_number`, `issue_author_login`).
3. Execute the instruction task.
4. **Always attempt** Review transition via `gh api graphql` when transition IDs exist.
5. **Always attempt** issue comment via `gh issue comment` with `@issue_author_login` mention and concise summary.
6. Return a final status summary including explicit evidence fields.

## Non-negotiable post-actions

After instruction execution (success/failure/blocked), the agent must execute post-actions in this order:
1) review transition attempt (or explicit skipped with reason)
2) issue comment attempt

**Strict timing rule:** Run these two post-actions immediately after instruction handling reaches a terminal state (`done`, `failed`, `blocked_on_clarification`).
- Do not postpone post-actions for extra cleanup tasks (e.g. teammate shutdown retries, local housekeeping, waiting loops).
- If cleanup remains after post-actions, report it as follow-up state in the issue comment body.

This rule also applies when:
- instruction execution fails
- user clarification is required
- user asks to stop/exit mid-task

If clarification is needed, attempt Review transition first when IDs are available, then post clarification comment, mark `blocked_on_clarification`, and set:
- `review_transition_result: done|failed` (based on transition attempt)
- `review_transition_result: skipped (waiting_for_user_clarification)` only when transition IDs are missing or transition cannot be attempted before clarification for explicit reasons

**Failure handling rule:** Even when instruction execution fails (for example push/auth failure), you must still (a) attempt Review transition with available payload IDs and (b) post a failure summary comment to the issue.

**Clarification handling rule:** If instruction is ambiguous and requires user choice/confirmation, you must attempt Review transition first when IDs are available, then post a clarification comment via `gh issue comment` immediately before (or right after) asking the CLI question, then mark current state as `blocked_on_clarification`.

**Hard ordering rule:** Never ask the user clarification question before Review transition attempt and clarification comment attempt have been made.

**Exit/interrupt rule:** Even if the instruction asks to finish quickly (e.g. `/exit`) or conversation appears to end, you must still perform post-actions first and include evidence.

## Mandatory completion gate (hard rule)

You must not declare completion until both post-actions have been attempted and evidence is captured.

Terminal state examples (must trigger post-actions immediately):
- instruction goal achieved, but optional cleanup remains
- helper agent/team shutdown pending or flaky
- additional retries are possible but not required for the primary instruction outcome

Required evidence to include in final output:
- `issue_comment_url`: URL returned by `gh issue comment` (or explicit failure reason)
- `clarification_comment_url`: URL returned by clarification `gh issue comment` when clarification was needed (or explicit failure reason + attempted command)
- `review_transition_result`: returned `projectV2Item.id` from GraphQL (or explicit failure reason)

If either step is skipped due to missing IDs or permissions, final output must explicitly state:
- which step was skipped/failed
- exact reason
- command attempted (for failures)

Forbidden:
- saying "완료" without comment URL / transition result evidence
- skipping post-actions because main instruction succeeded

## Parsing rule

Use **last** blank-line split so instruction can contain blank lines.

```python
instruction, payload_json = raw_input.rsplit("\n\n", 1)
payload = json.loads(payload_json)
```

## Required payload fields

- `repo_full_name`
- `issue_number`
- `issue_author_login` (for mention)
- `project_transition.in_progress.project_id`
- `project_transition.in_progress.project_item_id`
- `project_transition.in_progress.status_field_id`
- `project_transition.next_target_option_id`

If any project-transition identifier is missing, still post comment and report project move as skipped.

## Issue comment format

Always include:
- `@{issue_author_login}` mention at top
- what was processed
- result summary (success/failure)
- project move result (done/skipped/failed)

Clarification comment must include:
- `@{issue_author_login}` mention at top
- 1-2 line summary of what is ambiguous
- required choice options or confirmation question
- explicit status: `blocked_on_clarification`

Completion/failure example:

```bash
gh issue comment "$ISSUE_NUMBER" \
  --repo "$REPO" \
  --body "$(cat <<'EOF'
@$ISSUE_AUTHOR_LOGIN 처리 완료했습니다.

## 결과 요약
- 요청 처리: 성공
- 핵심 변경: ...
- 검증: ...
- Projects 상태: Review 이동 완료
EOF
)"
```

Clarification example:

```bash
gh issue comment "$ISSUE_NUMBER" \
  --repo "$REPO" \
  --body "$(cat <<'EOF'
@$ISSUE_AUTHOR_LOGIN 확인이 필요한 사항이 있어 작업을 잠시 멈췄습니다.

## 모호한 지점
- 배포 대상 브랜치가 `main`인지 `release/*`인지 지시가 불명확합니다.

## 필요한 확인
- 아래 중 하나를 선택해주세요:
  1) main 배포
  2) release 브랜치 배포

## 현재 상태
- blocked_on_clarification
EOF
)"
```

Clarification comment also requires evidence capture in final output:
- `clarification_comment_url` on success
- explicit failure reason + attempted `gh issue comment` command on failure

## Move project item to Review

Run only when all IDs exist:
- `PROJECT_ID`
- `PROJECT_ITEM_ID`
- `STATUS_FIELD_ID`
- `REVIEW_OPTION_ID` (= `next_target_option_id`)

**GraphQL 파싱 안정성 규칙 (중요):**
- `mutation($projectId:...)`의 `$...`는 셸 변수 확장을 절대 타면 안 됩니다.
- GraphQL 본문은 반드시 **single-quoted HEREDOC**(`<<'GRAPHQL'`)로 만들고, `--raw-field query="$GRAPHQL_QUERY"`로 전달합니다.
- 금지: `-f query="mutation($projectId...)"` 형태(더블쿼트 사용 시 `$projectId`가 비어 `VAR_SIGN` 파싱 오류 재발 가능).
- `set -u` 환경에서도 동작하도록, 실행 전 필수 변수 존재를 명시적으로 검증합니다.

```bash
# preflight (set -u 안전)
: "${PROJECT_ID:?missing PROJECT_ID}"
: "${PROJECT_ITEM_ID:?missing PROJECT_ITEM_ID}"
: "${STATUS_FIELD_ID:?missing STATUS_FIELD_ID}"
: "${REVIEW_OPTION_ID:?missing REVIEW_OPTION_ID}"

GRAPHQL_QUERY="$(cat <<'GRAPHQL'
mutation($projectId:ID!,$itemId:ID!,$fieldId:ID!,$optionId:String!){
  updateProjectV2ItemFieldValue(input:{
    projectId:$projectId,
    itemId:$itemId,
    fieldId:$fieldId,
    value:{singleSelectOptionId:$optionId}
  }) {
    projectV2Item { id }
  }
}
GRAPHQL
)"

TRANSITION_OUTPUT="$(gh api graphql \
  --raw-field query="$GRAPHQL_QUERY" \
  --raw-field projectId="$PROJECT_ID" \
  --raw-field itemId="$PROJECT_ITEM_ID" \
  --raw-field fieldId="$STATUS_FIELD_ID" \
  --raw-field optionId="$REVIEW_OPTION_ID" \
  2>&1)"
TRANSITION_EXIT_CODE=$?

if [ $TRANSITION_EXIT_CODE -ne 0 ]; then
  # 실패 시에도 후속 액션(이슈 코멘트)은 계속 진행
  # final summary/comment에 아래 3가지를 반드시 포함:
  # 1) 실패 원문($TRANSITION_OUTPUT)
  # 2) 실행 명령(민감정보 제외)
  # 3) query 전달 방식: --raw-field query + single-quoted HEREDOC
  :
fi
```

If this step fails, do not rollback instruction work. Report failure in final summary and in the issue comment.
- `VAR_SIGN` 또는 GraphQL parse error가 발생하면, 최종 요약/이슈 코멘트에 **실행한 명령과 쿼리 전달 방식**을 함께 남깁니다.
- `review_transition_result`에는 성공 시 `projectV2Item.id`, 실패 시 `failed: <핵심 오류>` 형태로 남깁니다.

If instruction execution fails, comment body must include:
- `@issue_author_login` mention
- what was attempted
- exact failure point (e.g. `git push origin main`)
- key error summary
- current local status (e.g. commit created but push failed)

If clarification comment posting fails, final summary must include:
- exact failure reason
- attempted `gh issue comment` command
- current state remains `blocked_on_clarification`
- no automatic retry loop; retry only after user confirmation

## Execution policy

- Prefer `gh` for all GitHub interactions.
- Keep operations non-destructive: do not close issue/PR unless explicitly requested.
- Do not push code unless explicitly requested.
- If instruction is ambiguous, attempt Review transition first when IDs are available, then post clarification comment (before or immediately after the CLI question).
- During clarification wait state, keep the ordering rule: transition attempted first when possible; otherwise report `skipped: waiting_for_user_clarification` with explicit reason.
- Team/agent lifecycle work (spawn/shutdown/delete) must be treated as optional operational cleanup unless explicitly part of the user's instruction outcome. Do not delay mandatory post-actions for this cleanup.

## Pre-post-action self-check (must run mentally before finishing)

- Did instruction reach a terminal state (`done`/`failed`/`blocked_on_clarification`)?
- If yes, did I run **Review transition attempt first** (or explicitly mark skipped with reason)?
- Did I run `gh issue comment` with `@issue_author_login` mention?
- Did I capture `issue_comment_url` and `review_transition_result` evidence values?
- If clarification path, did I also capture `clarification_comment_url` (or failure reason + attempted command)?
- Am I delaying post-actions for optional cleanup (team shutdown/retry loops)? If yes, stop and execute post-actions now.

## Final response checklist

- Instruction executed or clearly reported as blocked
- Issue author mentioned in comment (or reason why skipped)
- Review transition attempted (or reason why skipped)
- `issue_comment_url` included (or explicit failure reason)
- If clarification occurred: `clarification_comment_url` included (or explicit failure reason + attempted command)
- `review_transition_result` included (or explicit failure reason)
- For clarification wait state: `review_transition_result: skipped (waiting_for_user_clarification)` allowed
- Brief final status summary returned
