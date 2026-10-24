"""update_recent.py 的单元测试：python3 -m unittest discover -s scripts -v"""
import unittest
from datetime import datetime, timedelta, timezone

import update_recent as rr

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=rr.TZ)

FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>New post [draft]*</title>
    <link href="https://mirsery.github.io/2026/10/14/a/"/>
    <published>2026-10-14T04:27:00.000Z</published>
  </entry>
  <entry>
    <title>Dup</title>
    <link href="https://mirsery.github.io/2024/01/18/x/"/>
    <published>2024-01-18T00:00:00.000Z</published>
  </entry>
  <entry>
    <title>Dup</title>
    <link href="https://mirsery.github.io/2024/01/17/y/"/>
    <published>2024-01-17T00:00:00.000Z</published>
  </entry>
</feed>"""


def commit(message, when="2026-10-21T08:00:00Z", login="mirsery"):
    return {
        "author": {"login": login} if login else None,
        "commit": {"message": message, "committer": {"date": when}},
    }


def repo(name, pushed="2026-10-24T00:00:00Z", **extra):
    data = {
        "name": name, "private": False, "visibility": "public", "archived": False, "fork": False,
        "pushed_at": pushed, "default_branch": "main", "html_url": f"https://github.com/mirsery/{name}",
    }
    data.update(extra)
    return data


class FeatureSubjectTest(unittest.TestCase):
    def test_strips_prefix_scope_and_pr_number(self):
        self.assertEqual(rr.feature_subject("feat(app): 输入法进程、候选窗\n\n正文"), "输入法进程、候选窗")
        self.assertEqual(rr.feature_subject("feat: send a morning brief (#11)"), "Send a morning brief")
        self.assertEqual(rr.feature_subject("feat!: 去掉旧接口。"), "去掉旧接口")

    def test_ignores_other_types(self):
        for message in ["fix: x", "docs(readme): y", "Merge pull request #3 from a/b", "featuring: z", ""]:
            self.assertIsNone(rr.feature_subject(message), message)


class LatestFeatureTest(unittest.TestCase):
    def test_skips_other_authors_and_non_feat(self):
        commits = [
            commit("feat: upstream change", login="someone"),
            commit("fix: bug", "2026-10-22T00:00:00Z"),
            commit("feat: mine", "2026-10-20T00:00:00Z"),
            commit("feat: older", "2026-10-01T00:00:00Z"),
        ]
        self.assertEqual(rr.latest_feature(commits), (datetime(2026, 10, 20, tzinfo=timezone.utc), "Mine"))

    def test_none_without_own_feat(self):
        self.assertIsNone(rr.latest_feature([commit("chore: init"), commit("feat: x", login=None)]))


class CollectRepoItemsTest(unittest.TestCase):
    def test_keeps_only_public_active_own_repos(self):
        repos = [
            repo("GlossType"),
            repo("secret", private=True),
            repo("old", archived=True),
            repo("someone-fork", fork=True),
            repo("lark-coding-agent-bridge", fork=True),
            repo("mirsery"),
            repo("mirsery.github.io"),
            repo("stale", pushed="2025-01-01T00:00:00Z"),
            repo("empty"),
        ]
        calls = []

        def get_json(url):
            calls.append(url)
            if "/users/" in url:
                return repos
            if "/empty/" in url:
                raise rr.FetchError("empty repository", 409)
            return [commit("feat(core): 新功能 (#1)")]

        items = rr.collect_repo_items(NOW, get_json)
        self.assertEqual(sorted(i.label for i in items), ["GlossType", "lark-coding-agent-bridge"])
        self.assertIn("[GlossType](https://github.com/mirsery/GlossType) · 新功能", [i.markdown for i in items])
        self.assertFalse([u for u in calls if "/secret/" in u or "/stale/" in u or "/someone-fork/" in u])

    def test_other_fetch_errors_propagate(self):
        def get_json(url):
            if "/users/" in url:
                return [repo("GlossType")]
            raise rr.FetchError("server error", 500)

        with self.assertRaises(rr.FetchError):
            rr.collect_repo_items(NOW, get_json)


class FeedTest(unittest.TestCase):
    def test_dedupes_titles_and_escapes_markdown(self):
        items = rr.collect_blog_items(FEED)
        self.assertEqual([i.label for i in items], ["New post [draft]*", "Dup"])
        self.assertEqual(
            items[0].markdown,
            "[博客](https://mirsery.github.io) · [New post \\[draft\\]\\*](https://mirsery.github.io/2026/10/14/a/)",
        )
        self.assertIn("/x/", items[1].markdown)  # 同名文章只留最新一篇


class RenderTest(unittest.TestCase):
    def test_select_applies_window_order_and_limit(self):
        items = [rr.Item(NOW - timedelta(days=d), f"i{d}", f"m{d}") for d in (3, 1, 200, 2, 5, 4, 6, 7)]
        self.assertEqual([i.label for i in rr.select(items, NOW)], ["i1", "i2", "i3", "i4", "i5", "i6"])

    def test_format_date_uses_beijing_time(self):
        self.assertEqual(rr.format_date(datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc), NOW), "10-24")
        self.assertEqual(rr.format_date(datetime(2024, 6, 13, tzinfo=timezone.utc), NOW), "2024-06-13")

    def test_render(self):
        self.assertEqual(rr.render([], NOW), rr.EMPTY_TEXT)
        item = rr.Item(datetime(2026, 10, 21, tzinfo=timezone.utc), "x", "[x](https://github.com/mirsery/x)")
        self.assertEqual(rr.render([item], NOW), "- `10-21` [x](https://github.com/mirsery/x)")

    def test_replace_section_is_idempotent(self):
        text = f"head\n{rr.START}\nold\n{rr.END}\ntail\n"
        once = rr.replace_section(text, "- new")
        self.assertEqual(once, f"head\n{rr.START}\n- new\n{rr.END}\ntail\n")
        self.assertEqual(rr.replace_section(once, "- new"), once)

    def test_replace_section_requires_markers(self):
        with self.assertRaises(ValueError):
            rr.replace_section("no markers here", "- x")

    def test_safe_url(self):
        with self.assertRaises(ValueError):
            rr.safe_url("javascript:alert(1)")
        self.assertEqual(rr.safe_url("https://a.b/c (d)"), "https://a.b/c%20%28d%29")


if __name__ == "__main__":
    unittest.main()
