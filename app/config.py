from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    github_webhook_secret: str = ""
    github_pat: str = ""
    allowed_repo: str = "namjookim/claude-kanban"
    tmux_target: str = "claude:0.0"
    mention_keyword: str = "@claude"
    sqlite_path: str = "./state/webhook.db"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            github_pat=os.getenv("GITHUB_PAT", ""),
            allowed_repo=os.getenv("ALLOWED_REPO", "namjookim/claude-kanban"),
            tmux_target=os.getenv("TMUX_TARGET", "claude:0.0"),
            mention_keyword=os.getenv("MENTION_KEYWORD", "@claude"),
            sqlite_path=os.getenv("SQLITE_PATH", "./state/webhook.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
