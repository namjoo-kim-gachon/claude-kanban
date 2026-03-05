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
