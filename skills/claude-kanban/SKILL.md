---
name: claude-kanban
description: Handle claude-kanban webhook payloads where tmux input is formatted as instruction text followed by JSON metadata. Use when you need to execute the instruction, summarize results, mention the issue author in a GitHub issue comment via gh CLI, and move the linked GitHub Projects v2 item to Review using payload metadata.
---

# Claude Kanban

Process incoming tmux content in this format:
1) instruction text (top)
2) blank line
3) JSON payload (bottom)

Execute the instruction first, then report back to the issue with a mention, then move project status to Review.

## Workflow

1. Parse tmux input into `instruction` and `payload_json`.
2. Validate required payload fields.
3. Execute the instruction task.
4. Post issue comment via `gh issue comment` with `@issue_author_login` mention and concise summary.
5. Move project item to Review via `gh api graphql` if transition identifiers exist.
6. Return a final status summary including comment URL (if available) and project transition result.

**Failure handling rule:** Even when instruction execution fails (for example push/auth failure), you must still (a) post a failure summary comment to the issue and (b) attempt Review transition with available payload IDs.

## Mandatory completion gate (hard rule)

You must not declare completion until both post-actions have been attempted and evidence is captured.

Required evidence to include in final output:
- `issue_comment_url`: URL returned by `gh issue comment` (or explicit failure reason)
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

Example:

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

## Move project item to Review

Run only when all IDs exist:
- `PROJECT_ID`
- `PROJECT_ITEM_ID`
- `STATUS_FIELD_ID`
- `REVIEW_OPTION_ID` (= `next_target_option_id`)

```bash
gh api graphql -f query='mutation($projectId:ID!,$itemId:ID!,$fieldId:ID!,$optionId:String!){
  updateProjectV2ItemFieldValue(input:{
    projectId:$projectId,
    itemId:$itemId,
    fieldId:$fieldId,
    value:{singleSelectOptionId:$optionId}
  }) {
    projectV2Item { id }
  }
}' \
-f projectId="$PROJECT_ID" \
-f itemId="$PROJECT_ITEM_ID" \
-f fieldId="$STATUS_FIELD_ID" \
-f optionId="$REVIEW_OPTION_ID"
```

If this step fails, do not rollback instruction work. Report failure in final summary and in the issue comment.

If instruction execution fails, comment body must include:
- `@issue_author_login` mention
- what was attempted
- exact failure point (e.g. `git push origin main`)
- key error summary
- current local status (e.g. commit created but push failed)

## Execution policy

- Prefer `gh` for all GitHub interactions.
- Keep operations non-destructive: do not close issue/PR unless explicitly requested.
- Do not push code unless explicitly requested.
- If instruction is ambiguous, ask a concise clarification question before acting.

## Final response checklist

- Instruction executed or clearly reported as blocked
- Issue author mentioned in comment (or reason why skipped)
- Review transition attempted (or reason why skipped)
- `issue_comment_url` included (or explicit failure reason)
- `review_transition_result` included (or explicit failure reason)
- Brief final status summary returned
