from __future__ import annotations

import subprocess


class TmuxRunner:
    def run_payload(self, *, target: str, payload: str) -> None:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "-l", payload],
            check=True,
            shell=False,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            check=True,
            shell=False,
        )
