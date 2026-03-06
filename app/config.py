from __future__ import annotations

from dataclasses import dataclass
import os
import warnings


def _parse_mention_target_map(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not raw.strip():
        return mapping

    invalid_chunks: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        part = chunk.strip()
        if not part:
            continue
        if "=" not in part:
            invalid_chunks.append(part)
            continue

        mention, target = part.split("=", 1)
        mention = mention.strip()
        target = target.strip()
        if mention and target:
            mapping[mention] = target
            continue

        invalid_chunks.append(part)

    if invalid_chunks:
        warnings.warn(
            "Invalid MENTION_TO_TMUX entries ignored: " + ", ".join(invalid_chunks),
            RuntimeWarning,
            stacklevel=2,
        )

    return mapping


@dataclass(frozen=True)
class Settings:
    github_webhook_secret: str = ""
    github_pat: str = ""
    mention_to_tmux: dict[str, str] | None = None
    sqlite_path: str = "./state/webhook.db"
    log_level: str = "INFO"

    @property
    def mention_keywords(self) -> list[str]:
        return [mention.strip() for mention in (self.mention_to_tmux or {}).keys() if mention.strip()]

    def resolve_tmux_target(self, comment_body: str) -> str:
        normalized = comment_body.lower()
        for mention, target in (self.mention_to_tmux or {}).items():
            if mention.lower() in normalized:
                return target
        return ""

    @classmethod
    def from_env(cls) -> "Settings":
        mention_to_tmux = _parse_mention_target_map(os.getenv("MENTION_TO_TMUX", ""))
        return cls(
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            github_pat=os.getenv("GITHUB_PAT", ""),
            mention_to_tmux=mention_to_tmux,
            sqlite_path=os.getenv("SQLITE_PATH", "./state/webhook.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
