from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import story_kb  # noqa: E402


PAGE_TEMPLATE = """---
title: {title}
tags: ["测试"]
sources: {sources}
updated: 2026-07-29
status: canonical
---

# {title}
"""


class StoryKBTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kb = self.root / "kb"
        self.project = self.root / "project"
        self.project.mkdir()
        (self.kb / "wiki" / "inbox").mkdir(parents=True)
        (self.kb / "sources").mkdir(parents=True)

        for name in (
            "idea.md",
            "revision.md",
            "outline.md",
            "nine-lines.md",
            "scifi.md",
        ):
            (self.kb / "sources" / name).write_text(f"# {name}\n", encoding="utf-8")

        pages = {
            "index.md": ("索引", []),
            "concept-core.md": ("核心", ["sources/idea.md"]),
            "guide-ideation.md": ("创意", ["sources/idea.md"]),
            "guide-revision.md": ("修改", ["sources/revision.md"]),
            "guide-outline.md": ("大纲", ["sources/outline.md", "sources/nine-lines.md"]),
            "guide-genre.md": ("题材", ["sources/scifi.md"]),
            "conflict-registry.md": ("冲突", ["sources/nine-lines.md"]),
            "log.md": ("日志", []),
        }
        for name, (title, sources) in pages.items():
            (self.kb / "wiki" / name).write_text(
                PAGE_TEMPLATE.format(
                    title=title,
                    sources=json.dumps(sources, ensure_ascii=False),
                ),
                encoding="utf-8",
            )

        index = (self.kb / "wiki" / "index.md").read_text(encoding="utf-8")
        index += (
            "\n## 页面\n\n"
            "- [核心](concept-core.md)\n"
            "- [创意](guide-ideation.md)\n"
            "- [修改](guide-revision.md)\n"
            "- [大纲](guide-outline.md)\n"
            "- [题材](guide-genre.md)\n"
            "- [冲突](conflict-registry.md)\n"
            "\n## 自进化方法\n\n"
            "| 页面 | 说明 | 来源 |\n|---|---|---|\n"
            "<!-- AUTO-EVOLVED -->\n"
        )
        (self.kb / "wiki" / "index.md").write_text(index, encoding="utf-8")

        router = {
            "version": 1,
            "stage_inference": {
                "revision": ["修改"],
                "outline": ["大纲"],
            },
            "defaults": {"wiki": ["wiki/concept-core.md"], "sources": []},
            "stages": {
                "ideation": {
                    "wiki": ["wiki/guide-ideation.md"],
                    "sources": ["sources/idea.md"],
                },
                "revision": {
                    "wiki": ["wiki/guide-revision.md"],
                    "sources": ["sources/revision.md"],
                },
                "outline": {
                    "wiki": ["wiki/guide-outline.md"],
                    "sources": ["sources/outline.md"],
                },
            },
            "triggers": {
                "revision": {
                    "keywords": ["修改", "重写"],
                    "mandatory": True,
                    "wiki": ["wiki/guide-revision.md"],
                    "sources": ["sources/revision.md"],
                },
                "stuck": {
                    "keywords": ["卡壳"],
                    "mandatory": True,
                    "wiki": ["wiki/guide-revision.md"],
                    "sources": ["sources/revision.md"],
                },
            },
            "lengths": {
                "long": {
                    "wiki": ["wiki/guide-outline.md"],
                    "sources": ["sources/nine-lines.md"],
                }
            },
            "genre_aliases": {
                "科幻": "scifi",
                "sci-fi": "scifi",
            },
            "genres": {
                "scifi": {
                    "wiki": ["wiki/guide-genre.md"],
                    "sources": ["sources/scifi.md"],
                }
            },
            "methods": {
                "九线写法": {
                    "wiki": ["wiki/guide-outline.md", "wiki/conflict-registry.md"],
                    "sources": ["sources/nine-lines.md"],
                }
            },
        }
        (self.kb / "wiki" / "router.json").write_text(
            json.dumps(router, ensure_ascii=False), encoding="utf-8"
        )
        self.config = self.root / "config.toml"
        self.config.write_text(
            "\n".join(
                [
                    "version = 1",
                    f'knowledge_base_root = "{self.kb}"',
                    'wiki_index = "wiki/index.md"',
                    'router = "wiki/router.json"',
                    'source_priority = ["source", "wiki", "skill-reference"]',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def resolve(self, **overrides):
        params = {
            "project_root": self.project,
            "explicit_config": self.config,
            "query": "帮我想一个故事",
            "stage": "ideation",
            "length": "short",
            "genre": "",
            "method": "",
        }
        params.update(overrides)
        return story_kb.build_retrieval_plan(**params)

    def test_project_config_overrides_global(self) -> None:
        second_kb = self.root / "second-kb"
        second_kb.mkdir()
        (self.project / ".story-crafting.toml").write_text(
            f'knowledge_base_root = "{second_kb}"\n', encoding="utf-8"
        )
        config, sources = story_kb.resolve_config(self.project, self.config)
        self.assertEqual(config["knowledge_base_root"], str(second_kb))
        self.assertEqual(len(sources), 2)

    def test_normal_task_reads_index_and_one_wiki_page(self) -> None:
        result, exit_code = self.resolve()
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["mandatory"])
        kinds = [item["kind"] for item in result["retrieval_plan"]]
        self.assertEqual(kinds, ["index", "wiki"])

    def test_revision_is_mandatory_and_reads_source(self) -> None:
        result, exit_code = self.resolve(
            query="请修改这段故事并检查逻辑漏洞",
            stage="revision",
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["mandatory"])
        self.assertIn("sources/revision.md", [
            item["path"] for item in result["retrieval_plan"]
        ])

    def test_long_outline_reads_canonical_nine_line_source(self) -> None:
        result, exit_code = self.resolve(
            query="为长篇连载写卷纲",
            stage="outline",
            length="long",
            method="九线写法",
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["mandatory"])
        paths = [item["path"] for item in result["retrieval_plan"]]
        self.assertIn("sources/nine-lines.md", paths)
        self.assertIn("wiki/conflict-registry.md", paths)

    def test_chinese_genre_alias_reads_genre_source(self) -> None:
        result, exit_code = self.resolve(
            query="写一个科幻故事",
            genre="科幻",
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["mandatory"])
        paths = [item["path"] for item in result["retrieval_plan"]]
        self.assertIn("wiki/guide-genre.md", paths)
        self.assertIn("sources/scifi.md", paths)
        self.assertIn("genre:scifi", result["triggers"])

    def test_explicit_method_and_genre_wiki_survive_deep_limit(self) -> None:
        result, exit_code = self.resolve(
            query="修改卡壳的科幻长篇大纲",
            stage="revision",
            length="long",
            genre="科幻",
            method="九线写法",
        )
        self.assertEqual(exit_code, 0)
        wiki_paths = [
            item["path"]
            for item in result["retrieval_plan"]
            if item["kind"] == "wiki"
        ]
        self.assertLessEqual(len(wiki_paths), 4)
        self.assertIn("wiki/guide-genre.md", wiki_paths)
        self.assertIn("wiki/conflict-registry.md", wiki_paths)

    def test_mandatory_missing_source_fails_closed(self) -> None:
        (self.kb / "sources" / "revision.md").unlink()
        result, exit_code = self.resolve(
            query="修改这个大纲",
            stage="revision",
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "error")

    def test_missing_config_fails_closed_for_revision(self) -> None:
        missing = self.root / "missing.toml"
        result, exit_code = story_kb.build_retrieval_plan(
            project_root=self.project,
            explicit_config=missing,
            query="请修改这个长篇大纲",
            stage="revision",
            length="long",
            genre="",
            method="",
        )
        self.assertEqual(exit_code, 2)
        self.assertTrue(result["mandatory"])
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["fallback"])

    def test_record_writes_traceable_ledger(self) -> None:
        output = self.project / "outline.md"
        output.write_text("# outline\n", encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.project),
            config=self.config,
            task="长篇大纲",
            stage="outline",
            status="verified",
            trigger=["long-form-state"],
            knowledge_ref=["sources/nine-lines.md::九线总览"],
            method=["九线写法::大纲九线表::knowledge_refs[0]"],
            output=[str(output)],
        )
        result, exit_code = story_kb.record_run(args)
        self.assertEqual(exit_code, 0)
        ledger = Path(result["ledger"])
        record = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(record["knowledge_refs"][0]["section"], "九线总览")
        self.assertEqual(record["methods_applied"][0]["method"], "九线写法")

    def test_capture_requires_confirmation_before_promotion(self) -> None:
        capture_args = argparse.Namespace(
            project_root=str(self.project),
            config=self.config,
            run_id="run-1",
            title="冲突递进检查",
            observation="连续三场冲突应改变人物选择。",
            evidence="项目运行中两次修复后均改善节奏。",
            scope="适用于人物驱动的中长篇。",
            source=["project-run:run-1"],
            tag=["结构"],
            target="wiki/guide-conflict-progression.md",
        )
        captured, _ = story_kb.capture_learning(capture_args)
        candidate = Path(captured["candidate"])
        promote_args = argparse.Namespace(
            project_root=str(self.project),
            config=self.config,
            candidate=str(candidate),
            target=None,
            summary="冲突递进检查法",
            confirm=False,
        )
        planned, exit_code = story_kb.promote_learning(promote_args)
        self.assertEqual(exit_code, 3)
        self.assertEqual(planned["status"], "awaiting-confirmation")
        self.assertFalse((self.kb / "wiki" / "guide-conflict-progression.md").exists())

        promote_args.confirm = True
        promoted, exit_code = story_kb.promote_learning(promote_args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(promoted["status"], "promoted")
        self.assertTrue((self.kb / "wiki" / "guide-conflict-progression.md").is_file())

    def test_lint_valid_fixture(self) -> None:
        args = argparse.Namespace(
            project_root=str(self.project),
            config=self.config,
        )
        result, exit_code = story_kb.lint_wiki(args)
        self.assertEqual(exit_code, 0, result)
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
