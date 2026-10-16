"""GitHub workflows built on ``git`` (local operations) and the REST API (remote ones).

Credential handling rules, because this is where secrets leak in most tools:

* the token is read from an environment variable (name configurable) or from an existing
  ``gh`` CLI login — never from a config file, and never written to disk by CodeMind;
* pushes authenticate through a temporary askpass helper that reads the token from the
  *environment*, so the token never appears in a process argument list;
* everything that is logged or persisted passes through the global redactor;
* if ``gh`` is authenticated, CodeMind prefers it for pull requests, otherwise it uses the
  REST API directly with the same token.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database
from codemind.utils.process import CommandResult, build_env, run_command
from codemind.utils.redaction import GLOBAL_REDACTOR, register_secret
from codemind.utils.textutil import truncate_middle

log = get_logger("github.client")

API_ROOT = "https://api.github.com"


class GithubError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, hint: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.hint = hint


@dataclass
class TokenStatus:
    configured: bool
    source: str
    login: str = ""
    scopes: list[str] = field(default_factory=list)
    detail: str = ""
    checked_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "source": self.source,
            "login": self.login,
            "scopes": self.scopes,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


class GithubClient:
    """Repository and pull-request workflows with strict secret hygiene."""

    def __init__(self, config: Config, db: Database, *, data_root: Path | None = None) -> None:
        self.config = config
        self.db = db
        self.data_root = Path(data_root or tempfile.gettempdir())
        self._token: str = ""
        self._token_source: str = "unset"
        self._login: str = ""
        self._scopes: list[str] = []
        self._last_check: float = 0.0
        self._askpass_path: Path | None = None
        self.refresh_token()

    # -- credentials ------------------------------------------------------
    def update_config(self, config: Config) -> None:
        self.config = config
        self.refresh_token()

    def refresh_token(self) -> TokenStatus:
        env_name = self.config.github.token_env or "GITHUB_TOKEN"
        token = os.environ.get(env_name, "").strip()
        source = f"env:{env_name}"
        if not token:
            gh_token = self._gh_auth_token()
            if gh_token:
                token = gh_token
                source = "gh-cli"
        if token:
            register_secret(token)
        self._token = token
        self._token_source = source if token else "unset"
        if not token:
            self._login = ""
            self._scopes = []
        return self.token_status()

    def _gh_auth_token(self) -> str:
        if not shutil.which("gh"):
            return ""
        result = run_command(["gh", "auth", "token"], timeout=15)
        return result.stdout.strip() if result.ok else ""

    @property
    def token(self) -> str:
        return self._token

    def token_status(self, force: bool = False) -> TokenStatus:
        if not self._token:
            return TokenStatus(
                configured=False,
                source=self._token_source,
                detail=f"Set the {self.config.github.token_env} environment variable (a fine-grained token with repo scope) "
                       "or run `gh auth login`, then reload in Settings → GitHub.",
            )
        if not force and time.time() - self._last_check < 120 and self._login:
            return TokenStatus(True, self._token_source, self._login, self._scopes, f"Authenticated as {self._login}")
        try:
            payload, headers = self._api("/user")
            self._login = str(payload.get("login", ""))
            scopes = [s.strip() for s in str(headers.get("x-oauth-scopes", "")).split(",") if s.strip()]
            self._scopes = scopes
            self._last_check = time.time()
            return TokenStatus(True, self._token_source, self._login, scopes, f"Authenticated as {self._login}")
        except GithubError as exc:
            return TokenStatus(True, self._token_source, detail=f"Token present but the API check failed: {exc}")

    # -- http -------------------------------------------------------------
    def _api(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, accept: str = "application/vnd.github+json") -> tuple[Any, Mapping[str, str]]:
        if not self._token:
            raise GithubError("no GitHub token configured", status=401, hint="Configure a token in Settings → GitHub.")
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", accept)
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "CodeMind")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body) if body.strip() else {}
                return parsed, dict(response.headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            hint = ""
            if exc.code == 401:
                hint = "The token is invalid or expired."
            elif exc.code == 403:
                hint = "The token lacks the required scope, or the rate limit is exhausted."
            elif exc.code == 404:
                hint = "The repository or resource was not found (check the owner/name and token access)."
            raise GithubError(f"GitHub API {exc.code} for {path}: {detail}", status=exc.code, hint=hint) from exc
        except urllib.error.URLError as exc:
            raise GithubError(f"cannot reach GitHub: {exc.reason}") from exc

    # -- repositories -----------------------------------------------------
    def list_repositories(self, limit: int = 50) -> list[dict[str, Any]]:
        configured = [repo for repo in self.config.github.repositories if repo]
        if not self._token:
            return [{"full_name": repo, "configured": True, "state": "token required"} for repo in configured]
        try:
            payload, _ = self._api(f"/user/repos?per_page={min(100, limit)}&sort=updated")
            repos = [
                {
                    "full_name": item.get("full_name", ""),
                    "private": item.get("private", False),
                    "default_branch": item.get("default_branch", "main"),
                    "language": item.get("language"),
                    "updated_at": item.get("updated_at"),
                    "clone_url": item.get("clone_url"),
                    "description": item.get("description") or "",
                    "configured": item.get("full_name") in configured,
                }
                for item in payload
            ]
        except GithubError as exc:
            repos = [{"full_name": repo, "configured": True, "state": str(exc)} for repo in configured]
        for name in configured:
            if not any(repo["full_name"] == name for repo in repos):
                repos.insert(0, {"full_name": name, "configured": True, "state": "not listed by the API"})
        return repos[:limit]

    def clone(self, repo: str, target_dir: Path, *, branch: str = "", depth: int = 0) -> CommandResult:
        """Clone with credentials passed through the askpass helper (never in argv)."""
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        url = self._authenticated_url(repo)
        argv = ["git", "clone", "--quiet"]
        if depth:
            argv += ["--depth", str(depth)]
        if branch:
            argv += ["--branch", branch]
        argv += [url, str(target_dir)]
        result = run_command(argv, timeout=1200, env=self._git_env())
        self._record("clone", repo, "ok" if result.ok else "failed", {"branch": branch}, result.summary(300))
        if not result.ok:
            raise GithubError(f"clone failed: {result.summary(300)}")
        return result

    def _authenticated_url(self, repo: str) -> str:
        if repo.startswith("http") or repo.startswith("git@") or repo.startswith("/") or repo.startswith("."):
            return repo
        return f"https://github.com/{repo}.git"

    def _git_env(self) -> dict[str, str]:
        """Environment for git commands, with an askpass helper when a token is available."""
        env = build_env(drop_secrets=True)
        env["GIT_TERMINAL_PROMPT"] = "0"
        if self.config.github.author_name:
            env["GIT_AUTHOR_NAME"] = self.config.github.author_name
            env["GIT_COMMITTER_NAME"] = self.config.github.author_name
        if self.config.github.author_email:
            env["GIT_AUTHOR_EMAIL"] = self.config.github.author_email
            env["GIT_COMMITTER_EMAIL"] = self.config.github.author_email
        if self._token:
            askpass = self._askpass_file()
            env["GIT_ASKPASS"] = str(askpass)
            env["CODEMIND_GIT_TOKEN"] = self._token
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "credential.helper"
            env["GIT_CONFIG_VALUE_0"] = ""
        return env

    def _askpass_file(self) -> Path:
        if self._askpass_path and self._askpass_path.exists():
            return self._askpass_path
        path = self.data_root / "git-askpass.sh"
        path.write_text(
            "#!/bin/sh\n"
            "# CodeMind askpass helper: reads the token from the environment so it never\n"
            "# appears in a process argument list.\n"
            "case \"$1\" in\n"
            "  *[Uu]sername*) echo \"x-access-token\" ;;\n"
            "  *) echo \"$CODEMIND_GIT_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o700)
        self._askpass_path = path
        return path

    # -- local repository operations --------------------------------------
    def repo_status(self, repo_dir: Path) -> dict[str, Any]:
        result = run_command(["git", "status", "--porcelain=v1", "--branch"], cwd=repo_dir, timeout=30, env=self._git_env())
        if not result.ok:
            return {"ok": False, "error": result.summary(200), "is_repo": False}
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        branch = lines[0].replace("##", "").strip() if lines and lines[0].startswith("##") else ""
        changes = [line for line in lines if not line.startswith("##")]
        ahead_behind = {"ahead": 0, "behind": 0}
        if "[" in branch:
            import re

            match = re.search(r"ahead (\d+)", branch)
            if match:
                ahead_behind["ahead"] = int(match.group(1))
            match = re.search(r"behind (\d+)", branch)
            if match:
                ahead_behind["behind"] = int(match.group(1))
        return {
            "ok": True,
            "is_repo": True,
            "branch": branch.split("...")[0].strip(),
            "tracking": branch.split("...")[1].split(" ")[0].strip() if "..." in branch else "",
            "changes": changes,
            "dirty": bool(changes),
            **ahead_behind,
        }

    def diff(self, repo_dir: Path, *, staged: bool = False, path: str = "", max_chars: int = 20000) -> str:
        argv = ["git", "diff"]
        if staged:
            argv.append("--cached")
        if path:
            argv += ["--", path]
        result = run_command(argv, cwd=repo_dir, timeout=60, env=self._git_env())
        return truncate_middle(GLOBAL_REDACTOR.redact(result.stdout), max_chars) if result.ok else ""

    def log(self, repo_dir: Path, limit: int = 20) -> list[dict[str, str]]:
        result = run_command(
            ["git", "log", f"-{limit}", "--pretty=format:%H\x1f%an\x1f%ar\x1f%s"],
            cwd=repo_dir, timeout=30, env=self._git_env(),
        )
        if not result.ok:
            return []
        commits: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append({"sha": parts[0], "author": parts[1], "when": parts[2], "subject": GLOBAL_REDACTOR.redact(parts[3])})
        return commits

    def branches(self, repo_dir: Path) -> list[dict[str, Any]]:
        result = run_command(
            ["git", "for-each-ref", "--format=%(refname:short)\x1f%(upstream:short)\x1f%(committerdate:relative)", "refs/heads"],
            cwd=repo_dir, timeout=30, env=self._git_env(),
        )
        if not result.ok:
            return []
        out = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f")
            if parts and parts[0]:
                out.append({"name": parts[0], "upstream": parts[1] if len(parts) > 1 else "", "when": parts[2] if len(parts) > 2 else ""})
        return out

    def create_branch(self, repo_dir: Path, name: str, *, base: str = "") -> dict[str, Any]:
        argv = ["git", "checkout", "-q", "-b", name]
        if base:
            argv.append(base)
        result = run_command(argv, cwd=repo_dir, timeout=60, env=self._git_env())
        self._record("branch", str(repo_dir), "ok" if result.ok else "failed", {"branch": name, "base": base}, result.summary(200))
        if not result.ok:
            raise GithubError(f"could not create branch {name}: {result.summary(200)}")
        return {"branch": name, "base": base or "HEAD"}

    def commit(self, repo_dir: Path, message: str, *, paths: Sequence[str] = (), allow_empty: bool = False) -> dict[str, Any]:
        if paths:
            add = run_command(["git", "add", "--", *paths], cwd=repo_dir, timeout=60, env=self._git_env())
        else:
            add = run_command(["git", "add", "-A"], cwd=repo_dir, timeout=60, env=self._git_env())
        if not add.ok:
            raise GithubError(f"git add failed: {add.summary(200)}")
        argv = ["git", "commit", "-m", message]
        if allow_empty:
            argv.append("--allow-empty")
        result = run_command(argv, cwd=repo_dir, timeout=120, env=self._git_env())
        if not result.ok:
            if "nothing to commit" in result.combined:
                return {"committed": False, "reason": "nothing to commit"}
            raise GithubError(f"commit failed: {result.summary(300)}")
        sha = run_command(["git", "rev-parse", "HEAD"], cwd=repo_dir, timeout=20, env=self._git_env())
        record = {"committed": True, "sha": sha.stdout.strip() if sha.ok else "", "message": message, "files": list(paths)}
        self._record("commit", str(repo_dir), "ok", record, record["sha"])
        return record

    def push(self, repo_dir: Path, *, branch: str = "", remote: str = "origin", set_upstream: bool = True) -> dict[str, Any]:
        argv = ["git", "push"]
        if set_upstream:
            argv.append("--set-upstream")
        argv += [remote]
        if branch:
            argv.append(branch)
        result = run_command(argv, cwd=repo_dir, timeout=900, env=self._git_env())
        ok = result.ok
        self._record("push", str(repo_dir), "ok" if ok else "failed", {"branch": branch, "remote": remote}, result.summary(300))
        if not ok:
            raise GithubError(f"push failed: {result.summary(300)}")
        return {"pushed": True, "branch": branch or "current", "remote": remote}

    # -- pull requests ----------------------------------------------------
    def create_pull_request(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str = "",
        draft: bool | None = None,
        repo_dir: Path | None = None,
    ) -> dict[str, Any]:
        if not base:
            base = self._default_branch(repo)
        draft = self.config.github.draft_prs if draft is None else draft
        use_gh = shutil.which("gh") is not None and self._token_source == "gh-cli" and repo_dir is not None
        if use_gh:
            argv = ["gh", "pr", "create", "--repo", repo, "--title", title, "--body", body, "--head", head, "--base", base]
            if draft:
                argv.append("--draft")
            result = run_command(argv, cwd=repo_dir, timeout=180, env=self._git_env())
            if result.ok:
                url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
                record = {"url": url, "number": None, "method": "gh", "draft": draft}
                self._record("pull_request", repo, "ok", record, url)
                return record
            log.warning("gh pr create failed, falling back to the API", cm={"error": result.summary(200)})
        payload = {"title": title, "body": body, "head": head, "base": base, "draft": draft}
        data, _ = self._api(f"/repos/{repo}/pulls", method="POST", payload=payload)
        record = {
            "url": data.get("html_url", ""),
            "number": data.get("number"),
            "method": "api",
            "draft": draft,
            "state": data.get("state", "open"),
        }
        self._record("pull_request", repo, "ok", record, record["url"])
        return record

    def list_pull_requests(self, repo: str, state: str = "open", limit: int = 20) -> list[dict[str, Any]]:
        data, _ = self._api(f"/repos/{repo}/pulls?state={state}&per_page={limit}")
        return [
            {
                "number": item.get("number"),
                "title": item.get("title", ""),
                "state": item.get("state"),
                "draft": item.get("draft", False),
                "user": (item.get("user") or {}).get("login", ""),
                "head": (item.get("head") or {}).get("ref", ""),
                "base": (item.get("base") or {}).get("ref", ""),
                "url": item.get("html_url", ""),
                "created_at": item.get("created_at"),
                "mergeable": item.get("mergeable"),
            }
            for item in data
        ]

    def checks(self, repo: str, ref: str) -> dict[str, Any]:
        try:
            runs, _ = self._api(f"/repos/{repo}/commits/{ref}/check-runs", accept="application/vnd.github+json")
        except GithubError as exc:
            return {"status": "unknown", "detail": str(exc), "runs": []}
        runs_list = runs.get("check_runs", []) if isinstance(runs, dict) else []
        conclusions = [run.get("conclusion") for run in runs_list]
        if not runs_list:
            status = "none"
        elif any(c in (None, "pending") for c in conclusions):
            status = "pending"
        elif any(c in ("failure", "timed_out", "cancelled") for c in conclusions):
            status = "failed"
        else:
            status = "passed"
        return {
            "status": status,
            "runs": [
                {"name": run.get("name"), "status": run.get("status"), "conclusion": run.get("conclusion"), "url": run.get("html_url")}
                for run in runs_list[:20]
            ],
        }

    def _default_branch(self, repo: str) -> str:
        try:
            data, _ = self._api(f"/repos/{repo}")
            return str(data.get("default_branch") or "main")
        except GithubError:
            return "main"

    def repository_info(self, repo: str) -> dict[str, Any]:
        data, _ = self._api(f"/repos/{repo}")
        return {
            "full_name": data.get("full_name", repo),
            "private": data.get("private", False),
            "default_branch": data.get("default_branch", "main"),
            "language": data.get("language"),
            "stars": data.get("stargazers_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "url": data.get("html_url", ""),
            "description": data.get("description") or "",
            "updated_at": data.get("updated_at"),
        }

    # -- audit ------------------------------------------------------------
    def _record(self, kind: str, repo: str, status: str, payload: dict[str, Any], result: str = "") -> None:
        try:
            self.db.insert(
                "github_actions",
                {
                    "ts": time.time(),
                    "repo": repo,
                    "kind": kind,
                    "status": status,
                    "payload": json.dumps(payload, default=str),
                    "result": GLOBAL_REDACTOR.redact(result)[:2000],
                },
            )
        except Exception as exc:  # pragma: no cover
            log.debug("could not record github action", cm={"error": str(exc)})
        BUS.publish(Topics.GITHUB_ACTION, kind=kind, repo=repo, status=status, payload=payload)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM github_actions ORDER BY ts DESC LIMIT ?", (limit,))
        return [
            {
                "id": row["id"],
                "ts": row["ts"],
                "repo": row["repo"],
                "kind": row["kind"],
                "status": row["status"],
                "payload": json.loads(row["payload"] or "{}"),
                "result": row["result"],
            }
            for row in rows
        ]

    def status(self, repo_dir: Path | None = None) -> dict[str, Any]:
        """Answer both "what does this checkout look like" and "is GitHub wired up".

        With a path this is the local git status (branch, tracking, changed files); without one it
        is the integration status the GitHub view shows. Two methods used to share this name, and
        the second definition silently replaced the first — which broke the ``git_status`` tool.
        """
        if repo_dir is not None:
            return self.repo_status(Path(repo_dir))
        token = self.token_status()
        return {
            "enabled": self.config.github.enabled,
            "token": token.as_dict(),
            "repositories": self.config.github.repositories,
            "draft_prs": self.config.github.draft_prs,
            "login": self._login,
        }
