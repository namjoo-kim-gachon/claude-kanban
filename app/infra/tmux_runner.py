from __future__ import annotations

import subprocess
import uuid


class TmuxRunner:
    def run_payload(self, *, target: str, payload: str) -> None:
        try:
            preflight = subprocess.run(
                ["tmux", "display-message", "-p", "-t", target, "#{pane_dead}:#{pane_in_mode}"],
                check=True,
                shell=False,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"tmux preflight failed for target '{target}'") from exc

        pane_state = preflight.stdout.strip()
        if ":" not in pane_state:
            raise RuntimeError(f"tmux preflight returned invalid pane state: {pane_state!r}")

        pane_dead, pane_in_mode = pane_state.split(":", 1)
        if pane_dead == "1":
            raise RuntimeError(f"tmux target pane is dead: {target}")

        if pane_in_mode == "1":
            try:
                subprocess.run(
                    ["tmux", "send-keys", "-t", target, "-X", "cancel"],
                    check=True,
                    shell=False,
                )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"tmux cancel-copy-mode failed for target '{target}'") from exc

        buffer_name = f"claude-kanban-{uuid.uuid4().hex}"
        try:
            subprocess.run(
                ["tmux", "load-buffer", "-b", buffer_name, "-"],
                input=payload,
                text=True,
                check=True,
                shell=False,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("tmux load-buffer failed") from exc

        try:
            subprocess.run(
                ["tmux", "paste-buffer", "-d", "-b", buffer_name, "-t", target],
                check=True,
                shell=False,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"tmux paste-buffer failed for target '{target}'") from exc

        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "C-m"],
                check=True,
                shell=False,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"tmux send-keys C-m failed for target '{target}'") from exc
