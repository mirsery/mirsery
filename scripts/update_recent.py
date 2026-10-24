#!/usr/bin/env python3
"""用最近 push 过的公开仓库刷新 README.md 里的「最近更新」列表。"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

USER = "mirsery"
LIMIT = 5
README = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "README.md")
START, END = "<!-- recent:start -->", "<!-- recent:end -->"
SHANGHAI = timezone(timedelta(hours=8))


def fetch_repos():
    # /users/{user}/repos 只返回公开仓库，私有仓库不会出现在列表里
    url = f"https://api.github.com/users/{USER}/repos?type=owner&sort=pushed&per_page=100"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"{USER}-profile"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
        return json.load(resp)


def render(repos):
    lines = []
    for repo in repos:
        if repo["private"] or repo["archived"] or repo["name"] == USER:
            continue
        pushed = datetime.fromisoformat(repo["pushed_at"].replace("Z", "+00:00")).astimezone(SHANGHAI)
        line = f"- `{pushed:%Y-%m-%d}` **[{repo['name']}]({repo['html_url']})**"
        if repo["language"]:
            line += f" · {repo['language']}"
        lines.append(line)
        if len(lines) == LIMIT:
            break
    return "\n".join(lines)


def main():
    with open(README, encoding="utf-8") as f:
        readme = f.read()
    pattern = re.compile(re.escape(START) + ".*?" + re.escape(END), re.S)
    if not pattern.search(readme):
        sys.exit(f"README.md 缺少 {START} … {END} 标记")
    block = f"{START}\n{render(fetch_repos())}\n{END}"
    updated = pattern.sub(lambda _: block, readme, count=1)
    if updated == readme:
        print("没有变化")
        return
    with open(README, "w", encoding="utf-8") as f:
        f.write(updated)
    print("README.md 已更新")


if __name__ == "__main__":
    main()
