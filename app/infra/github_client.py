from __future__ import annotations

from typing import Any

import httpx


class GithubClient:
    def __init__(self, *, token: str, base_url: str = "https://api.github.com") -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def list_issue_comments(self, *, repo_full_name: str, issue_number: int) -> list[dict[str, Any]]:
        url = f"{self._base_url}/repos/{repo_full_name}/issues/{issue_number}/comments"
        response = httpx.get(url, headers=self._headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    def add_comment_reaction(self, *, repo_full_name: str, comment_id: int, content: str) -> None:
        url = f"{self._base_url}/repos/{repo_full_name}/issues/comments/{comment_id}/reactions"
        response = httpx.post(
            url,
            headers=self._headers,
            json={"content": content},
            timeout=10.0,
        )
        response.raise_for_status()

    def _graphql(self, *, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self._base_url}/graphql",
            headers=self._headers,
            json={"query": query, "variables": variables},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("invalid_graphql_response")
        errors = data.get("errors")
        if errors:
            raise RuntimeError("graphql_errors")
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise RuntimeError("missing_graphql_data")
        return payload

    def prepare_project_transition(self, *, repo_full_name: str, issue_number: int) -> dict[str, Any]:
        result: dict[str, Any] = {
            "attempted": False,
            "in_progress": {
                "ok": False,
                "reason": "unknown",
                "project_item_id": None,
                "project_id": None,
                "status_field_id": None,
                "in_progress_option_id": None,
            },
            "next_target_status": "Review",
            "next_target_option_id": None,
        }

        try:
            owner, repo = repo_full_name.split("/", 1)
        except ValueError:
            result["in_progress"]["reason"] = "invalid_repo_full_name"
            return result

        issue_query = """
        query($owner: String!, $repo: String!, $issueNumber: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $issueNumber) {
              id
              projectItems(first: 50) {
                nodes {
                  id
                  project {
                    id
                  }
                }
              }
            }
          }
        }
        """

        try:
            issue_data = self._graphql(
                query=issue_query,
                variables={"owner": owner, "repo": repo, "issueNumber": issue_number},
            )
        except Exception as exc:
            result["in_progress"]["reason"] = f"graphql_issue_query_failed:{type(exc).__name__}"
            return result

        repository = issue_data.get("repository")
        if not isinstance(repository, dict):
            result["in_progress"]["reason"] = "repository_not_found"
            return result

        issue = repository.get("issue")
        if not isinstance(issue, dict):
            result["in_progress"]["reason"] = "issue_not_found"
            return result

        project_items = ((issue.get("projectItems") or {}).get("nodes") or [])
        if not isinstance(project_items, list) or not project_items:
            result["in_progress"]["reason"] = "issue_not_in_project"
            return result

        first_item = project_items[0] if isinstance(project_items[0], dict) else {}
        project_item_id = first_item.get("id")
        project_id = ((first_item.get("project") or {}).get("id")) if isinstance(first_item, dict) else None
        if not project_item_id or not project_id:
            result["in_progress"]["reason"] = "project_item_missing_ids"
            return result

        result["in_progress"]["project_item_id"] = project_item_id
        result["in_progress"]["project_id"] = project_id

        fields_query = """
        query($projectId: ID!) {
          node(id: $projectId) {
            ... on ProjectV2 {
              id
              fields(first: 50) {
                nodes {
                  ... on ProjectV2SingleSelectField {
                    id
                    name
                    options {
                      id
                      name
                    }
                  }
                }
              }
            }
          }
        }
        """

        try:
            fields_data = self._graphql(query=fields_query, variables={"projectId": project_id})
        except Exception as exc:
            result["in_progress"]["reason"] = f"graphql_fields_query_failed:{type(exc).__name__}"
            return result

        node = fields_data.get("node")
        if not isinstance(node, dict):
            result["in_progress"]["reason"] = "project_not_found"
            return result

        field_nodes = ((node.get("fields") or {}).get("nodes") or [])
        status_field = None
        for field in field_nodes:
            if not isinstance(field, dict):
                continue
            if str(field.get("name", "")).strip().lower() == "status":
                status_field = field
                break

        if not status_field:
            result["in_progress"]["reason"] = "status_field_not_found"
            return result

        status_field_id = status_field.get("id")
        result["in_progress"]["status_field_id"] = status_field_id

        options = status_field.get("options") or []
        in_progress_option_id = None
        in_review_option_id = None
        for option in options:
            if not isinstance(option, dict):
                continue
            option_name = str(option.get("name", "")).strip().lower()
            if option_name == "in progress":
                in_progress_option_id = option.get("id")
            elif option_name in {"in review", "review"}:
                in_review_option_id = option.get("id")

        result["in_progress"]["in_progress_option_id"] = in_progress_option_id
        result["next_target_option_id"] = in_review_option_id

        if not in_progress_option_id:
            result["in_progress"]["reason"] = "in_progress_option_not_found"
            return result

        if not status_field_id:
            result["in_progress"]["reason"] = "status_field_id_missing"
            return result

        result["in_progress"]["reason"] = "ready"
        return result

    def try_move_issue_to_in_progress(
        self,
        *,
        project_id: str,
        project_item_id: str,
        status_field_id: str,
        in_progress_option_id: str,
    ) -> dict[str, Any]:
        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(
            input: {
              projectId: $projectId
              itemId: $itemId
              fieldId: $fieldId
              value: {singleSelectOptionId: $optionId}
            }
          ) {
            projectV2Item {
              id
            }
          }
        }
        """

        try:
            self._graphql(
                query=mutation,
                variables={
                    "projectId": project_id,
                    "itemId": project_item_id,
                    "fieldId": status_field_id,
                    "optionId": in_progress_option_id,
                },
            )
        except Exception as exc:
            return {"ok": False, "reason": f"graphql_update_failed:{type(exc).__name__}"}

        return {"ok": True, "reason": "updated"}
