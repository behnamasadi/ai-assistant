"""Git operations for the developer agent: branch, commit, push."""
from __future__ import annotations

import os
from pathlib import Path

from git import Repo


class GitManager:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self.repo = Repo(self.repo_path)
        self._configure_identity()

    def _configure_identity(self) -> None:
        name = os.environ.get("GIT_USER_NAME", "Claude Agent")
        email = os.environ.get("GIT_USER_EMAIL", "claude-agent@localhost")
        with self.repo.config_writer() as cfg:
            cfg.set_value("user", "name", name)
            cfg.set_value("user", "email", email)

    def ensure_feature_branch(self, branch: str, base: str | None = None) -> None:
        """Checkout branch, creating it from base if it doesn't exist yet."""
        base = base or os.environ.get("GIT_DEFAULT_BRANCH", "main")
        # Refresh base from remote so we branch off an up-to-date tip.
        try:
            self.repo.remotes.origin.fetch()
        except Exception:
            pass

        existing = [h.name for h in self.repo.heads]
        if branch in existing:
            self.repo.git.checkout(branch)
        else:
            self.repo.git.checkout(base)
            try:
                self.repo.git.pull("origin", base)
            except Exception:
                pass
            self.repo.git.checkout("-b", branch)

    def has_changes(self) -> bool:
        return self.repo.is_dirty(untracked_files=True)

    def commit_all(self, message: str) -> str | None:
        if not self.has_changes():
            return None
        self.repo.git.add(A=True)
        self.repo.index.commit(message)
        return self.repo.head.commit.hexsha

    def push(self, branch: str) -> None:
        self.repo.git.push("--set-upstream", "origin", branch)

    def merge_to_main(self, branch: str) -> str:
        base = os.environ.get("GIT_DEFAULT_BRANCH", "main")
        self.repo.git.checkout(base)
        try:
            self.repo.git.pull("origin", base)
        except Exception:
            pass
        self.repo.git.merge("--no-ff", branch, "-m", f"Merge {branch}")
        sha = self.repo.head.commit.hexsha
        self.repo.git.push("origin", base)
        return sha
