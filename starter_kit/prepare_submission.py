#!/usr/bin/env python3
"""Local, read-only final-submission preflight for LoomQ contestants."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "competition" / "config.json"
TEAM_ID_RE = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")


def git(*arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *arguments], text=True, capture_output=True, check=False
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(arguments)} failed")
    return completed.stdout.strip()


def normalize_remote(remote: str) -> str:
    value = remote.strip()
    ssh = re.fullmatch(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?", value)
    https = re.fullmatch(r"https://github\.com/([^/]+)/(.+?)(?:\.git)?/?", value)
    match = ssh or https
    if not match:
        raise RuntimeError("origin 必须是 github.com 上的 SSH 或 HTTPS 仓库")
    repo = match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"https://github.com/{match.group(1)}/{repo}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a LoomQ final submission")
    parser.add_argument("--team-id", required=True)
    args = parser.parse_args()
    team_id = args.team_id.strip().lower()
    if not TEAM_ID_RE.fullmatch(team_id):
        raise RuntimeError("Team ID 必须是有效的 GitHub 用户名")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if git("status", "--porcelain"):
        raise RuntimeError("工作区不干净；请先提交所有需要参评的文件")
    commit_sha = git("rev-parse", "HEAD").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise RuntimeError("无法获取 40 位 HEAD SHA")
    repository = normalize_remote(git("remote", "get-url", "origin"))
    repository_owner = repository.removeprefix("https://github.com/").split("/", 1)[0].lower()
    if team_id != repository_owner:
        raise RuntimeError("Team ID 必须与 origin fork 的 GitHub 所有者用户名一致")
    remote_heads = subprocess.run(
        ["git", "ls-remote", "--heads", "origin"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if remote_heads.returncode != 0:
        raise RuntimeError(remote_heads.stderr.strip() or "无法读取 origin")
    pushed = {line.split()[0].lower() for line in remote_heads.stdout.splitlines() if line.split()}
    if commit_sha not in pushed:
        raise RuntimeError("当前 HEAD 尚未成为 origin 上某个分支的最新 commit；请先 git push")
    missing = [path for path in config["required_files"] if not (ROOT / path).is_file()]
    if missing:
        raise RuntimeError("缺少必需文件: " + ", ".join(missing))
    issue_url = f"https://github.com/{config['upstream']}/issues/new?template=final-submission.yml"
    print("✅ 本地提交预检通过\n")
    print(f"Team ID: {team_id}")
    print(f"Fork repository: {repository}")
    print(f"Commit SHA: {commit_sha}")
    print(f"Deadline: {config['deadline_display']}")
    print(f"Submission form: {issue_url}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"❌ 预检失败: {exc}", file=sys.stderr)
        sys.exit(1)
