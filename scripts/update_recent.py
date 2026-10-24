#!/usr/bin/env python3
"""刷新 README「最近动态」一节：公开仓库最近的新功能，以及博客新文章。

只用标准库。GitHub Actions 每天跑一次（.github/workflows/update-recent.yml），
本地也能直接跑：python3 scripts/update_recent.py，设置 GITHUB_TOKEN 可以避开匿名限流。

规则：
- 仓库：自己的公开、未归档仓库，主页仓库和博客仓库除外；fork 只收 FORKS_TO_SHOW 里的。
  每个仓库取默认分支上自己最近一条 feat 提交，标题去掉 `feat(scope):` 前缀和末尾的 PR 号。
- 博客：Atom feed 里的文章，按发布时间取，同名文章只留一篇。
- 合并后按时间倒序，只保留 WINDOW_DAYS 天内的前 MAX_ITEMS 条。
- 任何数据源重试后仍拉取失败，README 保持不变并以非零码退出，Actions 里会显示失败。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

USER = "mirsery"
BLOG_URL = "https://mirsery.github.io"
FEED_URL = f"{BLOG_URL}/atom.xml"
README = Path(__file__).resolve().parent.parent / "README.md"
START = "<!-- recent:start -->"
END = "<!-- recent:end -->"

SKIP_REPOS = {USER, f"{USER}.github.io"}  # 主页仓库自身；博客文章单独从 feed 取
FORKS_TO_SHOW = {"lark-coding-agent-bridge"}
MAX_ITEMS = 6
WINDOW_DAYS = 180
CANDIDATE_REPOS = 10  # 按最近推送时间取前几个仓库去查提交
COMMITS_PER_REPO = 50
SUBJECT_MAX = 48
TZ = timezone(timedelta(hours=8))  # 日期按北京时间显示
EMPTY_TEXT = "- 最近半年没有新的公开动态。"

ATOM = {"a": "http://www.w3.org/2005/Atom"}
FEAT = re.compile(r"^feat(?:\([^)]*\))?!?:\s*(?P<subject>.+)$", re.IGNORECASE)
PR_SUFFIX = re.compile(r"\s*\(#\d+\)$")
MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|~#])")


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Item:
    when: datetime
    label: str  # 排序时同一时刻的次序依据，保证输出稳定
    markdown: str


def fetch(url: str, token: str | None = None, attempts: int = 3) -> bytes:
    headers = {"User-Agent": f"{USER}-profile-refresh"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if token:
            headers["Authorization"] = f"Bearer {token}"
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise FetchError(f"{url}: HTTP {e.code}", e.code) from e
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        if attempt + 1 < attempts:
            time.sleep(3 * (attempt + 1))
    raise FetchError(f"{url}: {last}")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def md_escape(text: str) -> str:
    return MD_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def safe_url(url: str) -> str:
    if not url.startswith("https://"):
        raise ValueError(f"只接受 https 链接：{url!r}")
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def truncate(text: str, limit: int = SUBJECT_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def feature_subject(message: str) -> str | None:
    """feat 提交的标题去掉前缀、PR 号和句末标点；不是 feat 提交时返回 None。"""
    match = FEAT.match(message.splitlines()[0].strip()) if message.strip() else None
    if not match:
        return None
    subject = PR_SUFFIX.sub("", match.group("subject")).strip().rstrip("。.")
    if subject[:1].isascii() and subject[:1].islower():
        subject = subject[0].upper() + subject[1:]
    return subject or None


def latest_feature(commits: list[dict]) -> tuple[datetime, str] | None:
    """commits 是 GitHub API 的提交列表（新的在前），返回自己最近一条 feat 提交的时间和标题。"""
    for commit in commits:
        if (commit.get("author") or {}).get("login") != USER:
            continue
        subject = feature_subject(commit["commit"]["message"])
        if subject:
            return parse_time(commit["commit"]["committer"]["date"]), subject
    return None


def collect_repo_items(now: datetime, get_json: Callable[[str], object]) -> list[Item]:
    repos = get_json(f"https://api.github.com/users/{USER}/repos?type=owner&sort=pushed&per_page=100")
    cutoff = now - timedelta(days=WINDOW_DAYS)
    candidates = [
        r for r in repos
        if not r.get("private") and r.get("visibility", "public") == "public"
        and not r.get("archived") and r["name"] not in SKIP_REPOS
        and (not r.get("fork") or r["name"] in FORKS_TO_SHOW)
        and r.get("pushed_at") and parse_time(r["pushed_at"]) >= cutoff
    ]
    candidates.sort(key=lambda r: parse_time(r["pushed_at"]), reverse=True)

    items = []
    for repo in candidates[:CANDIDATE_REPOS]:
        url = (f"https://api.github.com/repos/{USER}/{repo['name']}/commits"
               f"?sha={repo['default_branch']}&per_page={COMMITS_PER_REPO}")
        try:
            commits = get_json(url)
        except FetchError as e:
            if e.status == 409:  # 空仓库
                continue
            raise
        found = latest_feature(commits)
        if not found:
            continue
        when, subject = found
        link = f"[{md_escape(repo['name'])}]({safe_url(repo['html_url'])})"
        items.append(Item(when, repo["name"], f"{link} · {md_escape(truncate(subject))}"))
    return items


def parse_feed(xml: bytes) -> list[tuple[datetime, str, str]]:
    """返回 (发布时间, 标题, 链接)，新的在前，同名文章只留最新一篇。"""
    root = ET.fromstring(xml)
    posts = []
    for entry in root.findall("a:entry", ATOM):
        title = " ".join((entry.findtext("a:title", default="", namespaces=ATOM)).split())
        stamp = entry.findtext("a:published", namespaces=ATOM) or entry.findtext("a:updated", namespaces=ATOM)
        links = entry.findall("a:link", ATOM)
        href = next((l.get("href") for l in links if l.get("rel", "alternate") == "alternate"), None)
        if title and stamp and href:
            posts.append((parse_time(stamp), title, href))
    posts.sort(key=lambda p: p[0], reverse=True)
    seen, unique = set(), []
    for post in posts:
        if post[1] not in seen:
            seen.add(post[1])
            unique.append(post)
    return unique


def collect_blog_items(xml: bytes) -> list[Item]:
    blog = f"[博客]({safe_url(BLOG_URL)})"
    return [
        Item(when, title, f"{blog} · [{md_escape(truncate(title))}]({safe_url(href)})")
        for when, title, href in parse_feed(xml)
    ]


def select(items: list[Item], now: datetime) -> list[Item]:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    recent = [i for i in items if i.when >= cutoff]
    recent.sort(key=lambda i: (i.when, i.label), reverse=True)
    return recent[:MAX_ITEMS]


def format_date(when: datetime, now: datetime) -> str:
    local = when.astimezone(TZ)
    return local.strftime("%m-%d") if local.year == now.astimezone(TZ).year else local.strftime("%Y-%m-%d")


def render(items: list[Item], now: datetime) -> str:
    if not items:
        return EMPTY_TEXT
    return "\n".join(f"- `{format_date(i.when, now)}` {i.markdown}" for i in items)


def replace_section(readme: str, body: str) -> str:
    start = readme.find(START)
    end = readme.find(END, start + len(START)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ValueError(f"README 里找不到 {START} … {END} 标记")
    return readme[: start + len(START)] + "\n" + body + "\n" + readme[end:]


def main() -> int:
    now = datetime.now(TZ)
    token = os.environ.get("GITHUB_TOKEN") or None

    def get_json(url: str) -> object:
        return json.loads(fetch(url, token))

    try:
        items = collect_repo_items(now, get_json) + collect_blog_items(fetch(FEED_URL))
    except (FetchError, ET.ParseError) as e:
        print(f"拉取失败，README 保持不变：{e}", file=sys.stderr)
        return 1

    body = render(select(items, now), now)
    old = README.read_text(encoding="utf-8")
    new = replace_section(old, body)
    if new == old:
        print("最近动态没有变化")
        return 0
    README.write_text(new, encoding="utf-8")
    print("已更新最近动态：\n" + body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
