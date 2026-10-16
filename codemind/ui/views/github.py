"""GitHub: repositories, branches, commits, diffs, checks and pull requests.

Every action here goes through :class:`codemind.github.client.GithubClient`, which never puts a
token on a command line and redacts secrets from anything it returns. When no credentials are
available the view says so plainly instead of showing an empty list that looks broken.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from codemind.events import Topics
from codemind.ui import theme as T
from codemind.ui.views.base import SectionHeader, View
from codemind.ui.widgets.glass import ActionButton, Card, EmptyState, KeyValue, Pill, StatTile, hline
from codemind.ui.widgets.logview import LogView
from codemind.ui.widgets.table import DataTable
from codemind.utils.textutil import shorten


class GithubView(View):
    title = "GitHub"
    subtitle = "Clone, branch, commit, diff and open pull requests with your configured credentials — secrets never reach a log"

    def build(self) -> None:
        # --- credential state ----------------------------------------------
        self.auth_card = Card("Credentials", "Read from the environment or the GitHub CLI; a token typed in Settings is stored in settings.json")
        self.auth_body = QVBoxLayout()
        self.auth_body.setSpacing(5)
        self.auth_card.content.addLayout(self.auth_body)
        self.add(self.auth_card)

        # --- tiles ---------------------------------------------------------
        tiles = QHBoxLayout()
        tiles.setSpacing(T.GAP)
        self.tile_login = StatTile("Signed in as", "—", sub="", accent=T.ACCENT, icon="◆")
        self.tile_repos = StatTile("Repositories", "0", sub="", accent=T.VIOLET, icon="▤")
        self.tile_actions = StatTile("Actions recorded", "0", sub="", accent=T.INFO, icon="⇅")
        self.tile_prs = StatTile("Open pull requests", "0", sub="", accent=T.SUCCESS, icon="◇")
        for tile in (self.tile_login, self.tile_repos, self.tile_actions, self.tile_prs):
            tiles.addWidget(tile, 1)
        holder = QWidget()
        holder.setLayout(tiles)
        holder.setStyleSheet("background: transparent;")
        self.add(holder)

        # --- repositories --------------------------------------------------
        repo_card = Card("Repositories", "Clone into your projects folder, then work on it like any local project")
        repo_controls = QHBoxLayout()
        repo_controls.setSpacing(8)
        self.repo_input = QLineEdit()
        self.repo_input.setPlaceholderText("owner/name or a full https URL")
        self.repo_input.setFont(T.font(11, mono=True))
        self.repo_input.setStyleSheet(self._input_style())
        repo_controls.addWidget(self.repo_input, 1)
        repo_controls.addWidget(ActionButton("Clone", tone="accent", on_click=self.clone_repo))
        repo_controls.addWidget(ActionButton("Refresh list", on_click=lambda: self._refresh_repositories(force=True)))
        repo_card.content.addLayout(repo_controls)
        self.repo_table = DataTable(["Repository", "Default branch", "Language", "Private", "Updated", "State"], stretch_column=0)
        self.repo_table.setMinimumHeight(160)
        self.repo_table.itemSelectionChanged.connect(self._select_repo_row)
        repo_card.content.addWidget(self.repo_table)
        self.add(repo_card)

        # --- repository detail ---------------------------------------------
        detail_card = Card("Selected repository", "Branches, commits and the diff of the working tree")
        picker = QHBoxLayout()
        picker.setSpacing(8)
        self.local_repo = QComboBox()
        self.local_repo.setMinimumWidth(340)
        self.local_repo.setFont(T.font(10, mono=True))
        self.local_repo.setStyleSheet(self._combo_style())
        self.local_repo.currentIndexChanged.connect(lambda _i: self._refresh_detail())
        picker.addWidget(QLabel("Local checkout"))
        picker.addWidget(self.local_repo, 1)
        for label, callback in (
            ("Refresh", self._refresh_detail),
            ("New branch", self.create_branch),
            ("Commit", self.commit),
            ("Push", self.push),
            ("Open pull request", self.create_pr),
            ("Run checks", self.run_checks),
        ):
            picker.addWidget(ActionButton(label, on_click=callback))
        detail_card.content.addLayout(picker)
        self.detail_body = QVBoxLayout()
        self.detail_body.setSpacing(5)
        detail_card.content.addLayout(self.detail_body)
        split = QHBoxLayout()
        split.setSpacing(T.GAP)
        branches_card = Card("Branches", "Local branches and their upstream")
        self.branch_table = DataTable(["Branch", "Upstream", "Last commit"], stretch_column=0)
        self.branch_table.setMinimumHeight(140)
        branches_card.content.addWidget(self.branch_table)
        split.addWidget(branches_card, 1)
        commits_card = Card("Commits", "The most recent history of this checkout")
        self.commit_table = DataTable(["SHA", "Subject", "Author", "When"], stretch_column=1)
        self.commit_table.set_mono_columns([0])
        self.commit_table.setMinimumHeight(140)
        commits_card.content.addWidget(self.commit_table)
        split.addWidget(commits_card, 2)
        detail_holder = QWidget()
        detail_holder.setLayout(split)
        detail_holder.setStyleSheet("background: transparent;")
        detail_card.content.addWidget(detail_holder)
        self.diff_view = LogView(max_lines=2000)
        self.diff_view.setMinimumHeight(160)
        detail_card.content.addWidget(self.diff_view)
        self.add(detail_card)

        # --- pull requests -------------------------------------------------
        pr_card = Card("Pull requests", "Opened through the API with the branch you pushed")
        self.pr_table = DataTable(["Number", "Title", "State", "Draft", "Author", "Branch"], stretch_column=1)
        self.pr_table.setMinimumHeight(140)
        pr_card.content.addWidget(self.pr_table)
        pr_controls = QHBoxLayout()
        pr_controls.addWidget(ActionButton("Refresh open PRs", on_click=self.refresh_prs))
        pr_controls.addWidget(ActionButton("Checks for latest commit", on_click=self.run_checks))
        pr_controls.addStretch(1)
        self.pr_hint = QLabel("")
        self.pr_hint.setFont(T.font(10))
        self.pr_hint.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        pr_controls.addWidget(self.pr_hint)
        pr_card.content.addLayout(pr_controls)
        self.add(pr_card)

        # --- history -------------------------------------------------------
        self.history_card = Card("GitHub activity", "Every API call and git action CodeMind performed, with its outcome")
        self.history_table = DataTable(["When", "Repository", "Action", "Status", "Result"], stretch_column=4)
        self.history_table.setMinimumHeight(160)
        self.history_card.content.addWidget(self.history_table)
        self.add(self.history_card)

        self._repos: list[dict[str, Any]] = []
        self._repo_dir: Path | None = None

    # -- styles -----------------------------------------------------------
    @staticmethod
    def _input_style() -> str:
        return (
            f"QLineEdit {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }} QLineEdit:focus {{ border-color: {T.ACCENT}; }}"
        )

    @staticmethod
    def _combo_style() -> str:
        return (
            f"QComboBox {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {T.SURFACE}; color: {T.TEXT}; }}"
        )

    # -- actions ----------------------------------------------------------
    def clone_repo(self) -> None:
        target = self.repo_input.text().strip()
        if not target:
            self.pr_hint.setText("enter owner/name or a URL")
            return
        projects_root = Path(self.app.layout.projects_dir())
        name = target.rstrip("/").split("/")[-1].replace(".git", "") or "repository"
        destination = projects_root / name
        self.pr_hint.setText(f"cloning {target} …")
        try:
            result = self.app.github.clone(target, destination)
        except Exception as exc:
            self.pr_hint.setText(f"clone failed: {exc}")
            return
        if getattr(result, "ok", False):
            project_id = self.app.knowledge.register_project(destination)
            self.app.knowledge.index_project(project_id)
            self.pr_hint.setText(f"cloned into {destination} and indexed")
            self._refresh_local_checkouts()
        else:
            self.pr_hint.setText(f"clone failed: {result.summary(200) if hasattr(result, 'summary') else result}")
        self.refresh()

    def create_branch(self) -> None:
        if not self._repo_dir:
            self.pr_hint.setText("select a local checkout first")
            return
        name, ok = QInputDialog.getText(self, "Create branch", "Branch name:", text="codemind/work")
        if not ok or not name.strip():
            return
        try:
            result = self.app.github.create_branch(self._repo_dir, name.strip())
        except Exception as exc:
            self.pr_hint.setText(f"branch failed: {exc}")
            return
        self.pr_hint.setText(f"created {result['branch']} from {result['base']}")
        self._refresh_detail()

    def commit(self) -> None:
        if not self._repo_dir:
            return
        message, ok = QInputDialog.getText(self, "Commit", "Commit message:")
        if not ok or not message.strip():
            return
        try:
            result = self.app.github.commit(self._repo_dir, message.strip())
        except Exception as exc:
            self.pr_hint.setText(f"commit failed: {exc}")
            return
        self.pr_hint.setText(
            f"committed {result.get('sha', '')[:10]} ({result.get('message', '')})"
            if result.get("committed")
            else f"nothing to commit: {result.get('reason', '')}"
        )
        self._refresh_detail()

    def push(self) -> None:
        if not self._repo_dir:
            return
        try:
            result = self.app.github.push(self._repo_dir)
        except Exception as exc:
            self.pr_hint.setText(f"push failed: {exc}")
            return
        self.pr_hint.setText(f"pushed {result.get('branch')} to {result.get('remote')}")
        self._refresh_detail()

    def create_pr(self) -> None:
        if not self._repo_dir:
            return
        repo = self._repo_name()
        if not repo:
            self.pr_hint.setText("this checkout has no GitHub remote; configure one in Settings → GitHub")
            return
        title, ok = QInputDialog.getText(self, "Open pull request", "Title:", text="CodeMind changes")
        if not ok or not title.strip():
            return
        body, ok = QInputDialog.getMultiLineText(self, "Pull request description", "Body:", text="Opened by CodeMind after running the project's tests.")
        if not ok:
            return
        head = self._current_branch() or "codemind/work"
        try:
            result = self.app.github.create_pull_request(repo, title=title.strip(), body=body, head=head)
        except Exception as exc:
            self.pr_hint.setText(f"pull request failed: {exc}")
            return
        url = (result or {}).get("url") or ""
        self.pr_hint.setText(
            f"pull request #{result.get('number')} opened ({'draft' if result.get('draft') else 'ready'}): {url}"
            if result.get("number") is not None
            else f"pull request opened via gh: {url}"
        )
        self.refresh_prs()

    def run_checks(self) -> None:
        repo = self._repo_name()
        if not repo:
            self.pr_hint.setText("select a checkout with a GitHub remote")
            return
        ref = self._latest_sha() or self._current_branch()
        if not ref:
            return
        checks = self.app.github.checks(repo, ref)
        self.pr_hint.setText(f"checks for {ref[:8]}: {checks.get('status')} — {shorten(str(checks.get('detail', '')), 120)}")
        self.refresh()

    def refresh_prs(self) -> None:
        repo = self._repo_name()
        if not repo:
            self.pr_hint.setText("select a checkout with a GitHub remote")
            return
        try:
            pulls = self.app.github.list_pull_requests(repo, state="open", limit=25)
        except Exception as exc:
            self.pr_hint.setText(f"could not list pull requests: {exc}")
            return
        self.pr_table.set_rows(
            [[p.get("number"), p.get("title", ""), p.get("state", ""), p.get("draft", False), p.get("user", ""), p.get("head", "")] for p in pulls]
        )
        self.pr_hint.setText(f"{len(pulls)} open pull request(s) in {repo}")

    def _select_repo_row(self) -> None:
        row = self.repo_table.currentRow()
        if row < 0 or row >= len(self._repos):
            return
        entry = self._repos[row]
        path = entry.get("local_path") or ""
        if path and Path(path).exists():
            self._set_local_repo(Path(path))
        elif entry.get("full_name"):
            self.repo_input.setText(entry["full_name"])

    def _set_local_repo(self, path: Path) -> None:
        self._repo_dir = path
        self._refresh_detail()

    def _refresh_local_checkouts(self) -> None:
        paths: list[Path] = []
        projects_root = Path(self.app.layout.projects_dir())
        if projects_root.exists():
            for child in sorted(projects_root.iterdir()):
                if (child / ".git").exists():
                    paths.append(child)
        for project in self.app.knowledge.projects(limit=60):
            candidate = Path(project.get("path", ""))
            if (candidate / ".git").exists() and candidate not in paths:
                paths.append(candidate)
        current = self.local_repo.currentData()
        self.local_repo.blockSignals(True)
        self.local_repo.clear()
        for path in paths:
            self.local_repo.addItem(str(path), str(path))
        self.local_repo.blockSignals(False)
        index = self.local_repo.findData(current) if current else -1
        if index >= 0:
            self.local_repo.setCurrentIndex(index)
        elif paths and self._repo_dir is None:
            self.local_repo.setCurrentIndex(0)
        selected = self.local_repo.currentData()
        if selected:
            self._repo_dir = Path(selected)

    def _repo_name(self) -> str:
        if not self._repo_dir:
            return ""
        import subprocess

        try:
            out = subprocess.run(
                ["git", "remote", "get-url", "origin"], cwd=self._repo_dir, capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except Exception:
            return ""
        if not out:
            return ""
        cleaned = out.removesuffix(".git")
        if cleaned.startswith("git@github.com:"):
            return cleaned.split(":", 1)[1]
        if "github.com/" in cleaned:
            return cleaned.split("github.com/", 1)[1]
        return ""

    def _current_branch(self) -> str:
        if not self._repo_dir:
            return ""
        status = self.app.github.repo_status(self._repo_dir)
        branch = str(status.get("branch") or "").split("...")[0].strip()
        return branch

    def _latest_sha(self) -> str:
        commits = self.app.github.log(self._repo_dir, limit=1) if self._repo_dir else []
        return commits[0]["sha"] if commits else ""

    # -- events -----------------------------------------------------------
    def on_event(self, topic: str, payload: dict[str, Any]) -> None:
        if topic == Topics.GITHUB_ACTION:
            self.pr_hint.setText(f"{payload.get('kind', 'action')}: {payload.get('status', '')}")

    # -- refresh ----------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_auth()
        self._refresh_repositories()
        self._refresh_local_checkouts()
        self._refresh_detail()
        self._refresh_history()

    def _refresh_auth(self) -> None:
        status = self.app.github.status()
        token = status.get("token") or {}
        while self.auth_body.count():
            item = self.auth_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        configured = token.get("configured")
        self.auth_body.addWidget(
            KeyValue(
                "Status",
                shorten(str(token.get("detail", "")), 200),
                value_color=T.SUCCESS if configured else T.WARNING,
            )
        )
        self.auth_body.addWidget(KeyValue("Integration", "enabled" if status.get("enabled") else "disabled in Settings → GitHub"))
        self.auth_body.addWidget(KeyValue("Watched repositories", ", ".join(status.get("repositories") or []) or "none configured"))
        self.auth_body.addWidget(KeyValue("Pull requests", "opened as draft" if status.get("draft_prs") else "opened as ready for review"))
        self.tile_login.set_value(status.get("login") or ("token configured" if configured else "not signed in"), sub="via gh CLI, env or settings", accent=T.SUCCESS if configured else T.WARNING)
        self.tile_actions.set_value(str(len(self.app.github.history(limit=200))), sub="git and API operations recorded")

    def _refresh_repositories(self, *, force: bool = False) -> None:
        if not force and self._repos:
            return
        try:
            repos = self.app.github.list_repositories(limit=50)
        except Exception as exc:
            self.pr_hint.setText(f"could not list repositories: {exc}")
            return
        self._repos = repos
        projects_root = Path(self.app.layout.projects_dir())
        rows = []
        colors = []
        for entry in repos:
            full_name = entry.get("full_name", "")
            local = projects_root / full_name.split("/")[-1]
            entry["local_path"] = str(local) if (local / ".git").exists() else ""
            rows.append(
                [
                    full_name,
                    entry.get("default_branch", "—"),
                    entry.get("language") or "—",
                    entry.get("private", "—"),
                    shorten(str(entry.get("updated_at") or "—"), 19),
                    entry.get("state") or ("cloned" if entry.get("local_path") else "configured"),
                ]
            )
            colors.append([None, None, None, None, None, T.SUCCESS if entry.get("local_path") else T.TEXT_SOFT])
        self.repo_table.set_rows(rows, colors=colors)
        self.tile_repos.set_value(str(len(repos)), sub=f"{sum(1 for entry in repos if entry.get('local_path'))} cloned locally")

    def _refresh_detail(self) -> None:
        while self.detail_body.count():
            item = self.detail_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        if not self._repo_dir or not self._repo_dir.exists():
            self.detail_body.addWidget(EmptyState("No local checkout selected", "Clone a repository above, or index a project that is already on disk."))
            self.branch_table.set_rows([])
            self.commit_table.set_rows([])
            return
        status = self.app.github.repo_status(self._repo_dir)
        repo_name = self._repo_name()
        self.detail_body.addWidget(KeyValue("Path", str(self._repo_dir), mono=True))
        self.detail_body.addWidget(KeyValue("Remote", repo_name or "no GitHub remote on origin"))
        self.detail_body.addWidget(
            KeyValue(
                "Branch",
                f"{status.get('branch', '—')} · {status.get('ahead', 0)} ahead / {status.get('behind', 0)} behind",
                value_color=T.SUCCESS if not status.get("changes") else T.WARNING,
            )
        )
        changes = status.get("changes") or []
        self.detail_body.addWidget(KeyValue("Working tree", f"{len(changes)} changed file(s)", value_color=T.WARNING if changes else T.SUCCESS))
        for change in changes[:8]:
            self.detail_body.addWidget(KeyValue("  ", str(change), mono=True, value_color=T.TEXT_SOFT))
        branches = self.app.github.branches(self._repo_dir)
        self.branch_table.set_rows([[b.get("name", ""), b.get("upstream") or "—", b.get("when") or "—"] for b in branches])
        commits = self.app.github.log(self._repo_dir, limit=25)
        self.commit_table.set_rows([[c["sha"][:10], c["subject"], c["author"], c["when"]] for c in commits])
        self.diff_view.clear_text()
        diff = self.app.github.diff(self._repo_dir, max_chars=40000)
        self.diff_view.append_line(diff or "(no diff in the working tree)")

    def _refresh_history(self) -> None:
        rows = []
        colors = []
        for entry in self.app.github.history(limit=50):
            rows.append(
                [
                    time.strftime("%m-%d %H:%M", time.localtime(entry.get("ts") or time.time())),
                    entry.get("repo") or "—",
                    entry.get("kind", ""),
                    entry.get("status", ""),
                    shorten(str(entry.get("result") or entry.get("payload") or ""), 90),
                ]
            )
            colors.append([None, None, None, T.SUCCESS if entry.get("status") == "ok" else T.WARNING, None])
        self.history_table.set_rows(rows, colors=colors)
