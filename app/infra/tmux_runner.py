from __future__ import annotations

import subprocess
import time
import uuid


class TmuxRunner:
    def _assert_target_ready(self, *, target: str) -> None:
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

    def run_payload(self, *, target: str, payload: str) -> None:
        self._assert_target_ready(target=target)

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

    def wait_for_text(self, *, target: str, expected_text: str, timeout_seconds: float = 8.0) -> None:
        deadline = time.monotonic() + timeout_seconds

        while True:
            self._assert_target_ready(target=target)
            try:
                captured = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-t", target, "-S", "-200"],
                    check=True,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"tmux capture-pane failed for target '{target}'") from exc

            if expected_text in captured.stdout:
                return

            if time.monotonic() >= deadline:
                raise RuntimeError(f"expected text not found within timeout: {expected_text}")

            time.sleep(0.1)
