"""Optional GitHub issue ingest. No token, no network call."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_REPOS = (
    "cybrid-systems/aura",
    "cybrid-systems/aura-perf",
    "cybrid-systems/aura-runner",
    "cybrid-systems/aura-harness",
    "cybrid-systems/aura-memory",
)


class GitHubError(RuntimeError):
    pass


def token_from_env() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def issues_to_signals(repo: str, issues: list) -> list[dict]:
    """Map GitHub issue objects to signal records. Pull requests are skipped."""
    out = []
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        number = issue.get("number")
        reactions = issue.get("reactions") or {}
        labels = []
        for lab in issue.get("labels") or []:
            if isinstance(lab, dict) and lab.get("name"):
                labels.append(str(lab["name"]))
            elif isinstance(lab, str):
                labels.append(lab)
        body = issue.get("body") or ""
        out.append(
            {
                "id": f"gh-{repo.replace('/', '-')}-{number}",
                "source": "github",
                "repo": repo,
                "time": issue.get("created_at") or "",
                "title": issue.get("title") or "",
                "body": body[:4000],
                "labels": labels,
                "comments": [],
                "comment_count": int(issue.get("comments") or 0),
                "reactions": int((reactions or {}).get("total_count") or 0),
            }
        )
    return out


def fetch_repo_issues(token: str, repo: str, limit: int = 20) -> list[dict]:
    if not token:
        raise GitHubError("没有 GITHUB_TOKEN，跳过在线拉取。示例数据不需要 token。")
    url = f"https://api.github.com/repos/{repo}/issues?state=open&per_page={int(limit)}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "aura-insight",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise GitHubError(f"GitHub {repo} 返回 {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"无法连接 GitHub：{exc.reason}") from exc
    if not isinstance(payload, list):
        raise GitHubError(f"GitHub {repo} 的响应不是 issue 列表")
    return issues_to_signals(repo, payload)


def ingest(token: str, repos: list[str] | None = None, limit: int = 20) -> list[dict]:
    chosen = repos or list(DEFAULT_REPOS)
    signals: list[dict] = []
    for repo in chosen:
        signals.extend(fetch_repo_issues(token, repo.strip(), limit=limit))
    return signals
