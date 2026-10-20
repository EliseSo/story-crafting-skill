#!/usr/bin/env python3
"""Deterministic knowledge-base runtime for the story-crafting skill.

The script intentionally uses only the Python standard library. It resolves
global/project configuration, builds a traceable retrieval plan, records runs,
captures reusable learnings, promotes approved candidates, and lints the wiki.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python < 3.11
    raise SystemExit("story_kb.py requires Python 3.11+ (tomllib is missing)") from exc


GLOBAL_CONFIG = Path("~/.config/story-crafting/config.toml").expanduser()
PROJECT_CONFIG = ".story-crafting.toml"
DEFAULT_PRIORITY = ["source", "wiki", "skill-reference"]
REQUIRED_WIKI_FIELDS = {"title", "tags", "sources", "updated", "status"}
MANDATORY_HINT_KEYWORDS = [
    "修改",
    "改稿",
    "审查",
    "重写",
    "润色",
    "逻辑漏洞",
    "一致性",
    "修复",
    "卡壳",
    "写不下去",
    "中段垮",
    "动机断裂",
    "伏笔",
    "结尾泄气",
    "节奏有问题",
    "不知道怎么写",
    "长篇",
    "连载",
    "总纲",
    "卷纲",
    "章纲",
    "人物关系",
    "时间线",
    "按照知识库",
    "用知识库",
    "知识库方法",
    "补充方法论",
]


class StoryKBError(RuntimeError):
    """User-facing, expected runtime error."""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:72] or "learning"


def load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StoryKBError(f"无法读取配置 {path}: {exc}") from exc

    root = data.get("knowledge_base_root")
    if root:
        root_path = Path(os.path.expandvars(os.path.expanduser(str(root))))
        if not root_path.is_absolute():
            root_path = (path.parent / root_path).resolve()
        data["knowledge_base_root"] = str(root_path)
    return data


def find_project_config(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / PROJECT_CONFIG
        if candidate.is_file():
            return candidate
    return None


def resolve_config(
    project_root: Path,
    explicit_config: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    config: dict[str, Any] = {
        "version": 1,
        "wiki_index": "wiki/index.md",
        "source_priority": DEFAULT_PRIORITY,
    }
    sources: list[str] = []

    configured = explicit_config
    if configured is None:
        env_config = os.environ.get("STORY_CRAFTING_CONFIG")
        configured = Path(env_config).expanduser() if env_config else GLOBAL_CONFIG

    if configured.is_file():
        config.update(load_toml(configured))
        sources.append(str(configured))
    elif explicit_config is not None or os.environ.get("STORY_CRAFTING_CONFIG"):
        raise StoryKBError(f"指定配置不存在: {configured}")

    project_config = find_project_config(project_root)
    if project_config:
        config.update(load_toml(project_config))
        sources.append(str(project_config))

    if "knowledge_base_root" not in config:
        raise StoryKBError(
            "未配置 knowledge_base_root。请创建 "
            f"{GLOBAL_CONFIG} 或项目级 {PROJECT_CONFIG}"
        )

    config["source_priority"] = list(
        config.get("source_priority") or DEFAULT_PRIORITY
    )
    return config, sources


def knowledge_paths(
    project_root: Path,
    explicit_config: Path | None = None,
) -> tuple[dict[str, Any], list[str], Path, Path, Path]:
    config, config_sources = resolve_config(project_root, explicit_config)
    kb_root = Path(str(config["knowledge_base_root"])).expanduser().resolve()
    wiki_index = kb_root / str(config.get("wiki_index", "wiki/index.md"))
    router_path = kb_root / str(config.get("router", "wiki/router.json"))
    return config, config_sources, kb_root, wiki_index, router_path


def load_router(router_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(router_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoryKBError(f"无法读取知识库路由 {router_path}: {exc}") from exc
    if data.get("version") != 1:
        raise StoryKBError(f"不支持的 router.json 版本: {data.get('version')!r}")
    return data


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(keyword.casefold() in lowered for keyword in keywords)


def infer_stage(query: str, router: dict[str, Any]) -> str:
    for stage, keywords in router.get("stage_inference", {}).items():
        if contains_any(query, keywords):
            return stage
    return "ideation"


def add_route(
    route: dict[str, Any] | None,
    wiki: list[str],
    sources: list[str],
) -> None:
    if not route:
        return
    wiki.extend(route.get("wiki", []))
    sources.extend(route.get("sources", []))


def existing_plan_item(kb_root: Path, relative: str, kind: str) -> dict[str, Any]:
    absolute = (kb_root / relative).resolve()
    try:
        absolute.relative_to(kb_root)
    except ValueError as exc:
        raise StoryKBError(f"路由越出知识库根目录: {relative}") from exc
    return {
        "kind": kind,
        "path": relative,
        "absolute_path": str(absolute),
        "exists": absolute.is_file(),
    }


def build_retrieval_plan(
    *,
    project_root: Path,
    explicit_config: Path | None,
    query: str,
    stage: str | None,
    length: str,
    genre: str,
    method: str,
) -> tuple[dict[str, Any], int]:
    mandatory_hint = bool(
        stage == "revision"
        or genre
        or method
        or contains_any(query, MANDATORY_HINT_KEYWORDS)
        or (
            length == "long"
            and stage in {"outline", "character", "revision"}
        )
    )
    try:
        config, config_sources, kb_root, wiki_index, router_path = knowledge_paths(
            project_root, explicit_config
        )
    except StoryKBError as exc:
        result = {
            "status": "error" if mandatory_hint else "degraded",
            "mandatory": mandatory_hint,
            "errors": [str(exc)],
            "fallback": None if mandatory_hint else "skill-reference",
        }
        return result, 2 if mandatory_hint else 0

    errors: list[str] = []
    if not kb_root.is_dir():
        errors.append(f"知识库根目录不存在: {kb_root}")
    if not wiki_index.is_file():
        errors.append(f"Wiki 索引不存在: {wiki_index}")
    if not router_path.is_file():
        errors.append(f"知识库路由不存在: {router_path}")
    if errors:
        return {
            "status": "error" if mandatory_hint else "degraded",
            "mandatory": mandatory_hint,
            "config_sources": config_sources,
            "errors": errors,
            "fallback": None if mandatory_hint else "skill-reference",
        }, 2 if mandatory_hint else 0

    router = load_router(router_path)
    selected_stage = stage or infer_stage(query, router)
    wiki: list[str] = []
    sources: list[str] = []
    triggers: list[str] = []
    mandatory = False

    stage_route = router.get("stages", {}).get(selected_stage)
    add_route(stage_route, wiki, sources)

    if selected_stage == "revision":
        mandatory = True
        triggers.append("revision-stage")

    if method:
        mandatory = True
        triggers.append(f"method:{method}")
        add_route(router.get("methods", {}).get(method), wiki, sources)

    if genre:
        genres = router.get("genres", {})
        aliases = router.get("genre_aliases", {})
        normalized_genre = aliases.get(genre, aliases.get(genre.casefold(), genre))
        genre_route = genres.get(normalized_genre)
        if genre_route:
            mandatory = True
            triggers.append(f"genre:{normalized_genre}")
            add_route(genre_route, wiki, sources)

    if length == "long" and selected_stage in {
        "outline",
        "character",
        "revision",
    }:
        mandatory = True
        triggers.append("long-form-state")
        add_route(router.get("lengths", {}).get("long"), wiki, sources)

    for name, route in router.get("triggers", {}).items():
        if contains_any(query, route.get("keywords", [])):
            triggers.append(name)
            mandatory = mandatory or bool(route.get("mandatory"))
            add_route(route, wiki, sources)

    add_route(router.get("defaults"), wiki, sources)

    wiki = unique(wiki)
    sources = unique(sources)
    if mandatory:
        wiki = wiki[:4]
    else:
        wiki = wiki[:1]
        sources = []

    index_relative = str(wiki_index.relative_to(kb_root))
    items = [existing_plan_item(kb_root, index_relative, "index")]
    items.extend(existing_plan_item(kb_root, path, "wiki") for path in wiki)
    items.extend(existing_plan_item(kb_root, path, "source") for path in sources)
    missing = [item["path"] for item in items if not item["exists"]]

    if mandatory and missing:
        status = "error"
        errors.append("强制检索所需文件缺失: " + ", ".join(missing))
        exit_code = 2
    elif missing:
        status = "degraded"
        errors.append("普通检索文件缺失，允许使用 Skill fallback: " + ", ".join(missing))
        exit_code = 0
    else:
        status = "verified"
        exit_code = 0

    result = {
        "status": status,
        "mandatory": mandatory,
        "stage": selected_stage,
        "length": length,
        "genre": genre or None,
        "method": method or None,
        "triggers": unique(triggers),
        "config_sources": config_sources,
        "knowledge_base_root": str(kb_root),
        "authority_order": config["source_priority"],
        "retrieval_plan": items,
        "must_read": [item["absolute_path"] for item in items if item["exists"]],
        "errors": errors,
        "fallback": "skill-reference" if status == "degraded" else None,
    }
    return result, exit_code


def parse_ref(value: str) -> dict[str, str]:
    path, separator, section = value.partition("::")
    return {"path": path.strip(), "section": section.strip() if separator else ""}


def parse_method(value: str) -> dict[str, str]:
    parts = [part.strip() for part in value.split("::", 2)]
    if len(parts) != 3 or not all(parts):
        raise StoryKBError(
            "--method 必须使用 METHOD::APPLIED_TO::SOURCE 格式"
        )
    return {"method": parts[0], "applied_to": parts[1], "source": parts[2]}


def validate_relative_file(root: Path, relative: str) -> None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StoryKBError(f"路径越出知识库根目录: {relative}") from exc
    if not candidate.is_file():
        raise StoryKBError(f"knowledge_ref 不存在: {relative}")


def record_run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    project_root = Path(args.project_root).expanduser().resolve()
    config, config_sources, kb_root, _, _ = knowledge_paths(
        project_root, args.config
    )
    refs = [parse_ref(value) for value in args.knowledge_ref]
    for ref in refs:
        validate_relative_file(kb_root, ref["path"])
    methods = [parse_method(value) for value in args.method]

    outputs = [str(Path(value).expanduser().resolve()) for value in args.output]
    missing_outputs = [value for value in outputs if not Path(value).is_file()]
    if missing_outputs:
        raise StoryKBError("产出文件不存在: " + ", ".join(missing_outputs))

    record = {
        "run_id": str(uuid.uuid4()),
        "timestamp": now_iso(),
        "task": args.task,
        "stage": args.stage,
        "knowledge_status": args.status,
        "triggers": unique(args.trigger),
        "knowledge_refs": refs,
        "methods_applied": methods,
        "outputs": outputs,
        "knowledge_base_root": str(kb_root),
        "config_sources": config_sources,
        "authority_order": config["source_priority"],
    }
    ledger_dir = project_root / ".story-crafting"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger = ledger_dir / "runs.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"status": "recorded", "ledger": str(ledger), "record": record}, 0


def yaml_list(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def capture_learning(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    project_root = Path(args.project_root).expanduser().resolve()
    _, _, kb_root, _, _ = knowledge_paths(project_root, args.config)
    inbox = kb_root / "wiki" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output = inbox / f"{stamp}-{slugify(args.title)}.md"
    suffix = 1
    while output.exists():
        output = inbox / f"{stamp}-{slugify(args.title)}-{suffix}.md"
        suffix += 1

    sources = unique(args.source or [f"project-run:{args.run_id}"])
    tags = unique(["候选经验", *(args.tag or [])])
    target = args.target or f"wiki/guide-{slugify(args.title)}.md"
    content = (
        "---\n"
        f"title: {args.title}\n"
        f"tags: {yaml_list(tags)}\n"
        f"sources: {yaml_list(sources)}\n"
        f"updated: {today()}\n"
        "status: candidate\n"
        f"target: {target}\n"
        f"run_id: {args.run_id}\n"
        "---\n\n"
        f"# {args.title}\n\n"
        "## 观察\n\n"
        f"{args.observation.strip()}\n\n"
        "## 证据\n\n"
        f"{args.evidence.strip()}\n\n"
        "## 适用边界\n\n"
        f"{args.scope.strip()}\n\n"
        "## 晋升前检查\n\n"
        "- [ ] 已与现有 Wiki 查重\n"
        "- [ ] 已回查原始资料或项目运行证据\n"
        "- [ ] 已确认不是单一作品的偶然细节\n"
        "- [ ] 已处理冲突并获得用户确认\n"
    )
    output.write_text(content, encoding="utf-8")
    return {
        "status": "captured",
        "candidate": str(output),
        "target": target,
        "requires_confirmation": True,
    }, 0


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata, body


def replace_frontmatter_value(text: str, key: str, value: str) -> str:
    pattern = rf"(?m)^{re.escape(key)}:\s*.*$"
    replacement = f"{key}: {value}"
    if re.search(pattern, text):
        return re.sub(pattern, replacement, text, count=1)
    end = text.find("\n---\n", 4)
    if end == -1:
        raise StoryKBError("候选文件缺少有效 frontmatter")
    return text[:end] + f"\n{replacement}" + text[end:]


def promote_learning(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    project_root = Path(args.project_root).expanduser().resolve()
    _, _, kb_root, wiki_index, _ = knowledge_paths(project_root, args.config)
    inbox = (kb_root / "wiki" / "inbox").resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    if not candidate.is_file():
        raise StoryKBError(f"候选文件不存在: {candidate}")
    try:
        candidate.relative_to(inbox)
    except ValueError as exc:
        raise StoryKBError("只能晋升 wiki/inbox/ 下的候选") from exc

    text = candidate.read_text(encoding="utf-8")
    metadata, _ = parse_frontmatter(text)
    if metadata.get("status") != "candidate":
        raise StoryKBError("候选状态不是 candidate，不能晋升")
    target_relative = args.target or metadata.get("target")
    if not target_relative:
        raise StoryKBError("候选缺少 target，且未传入 --target")
    target = (kb_root / target_relative).resolve()
    wiki_root = (kb_root / "wiki").resolve()
    try:
        target.relative_to(wiki_root)
    except ValueError as exc:
        raise StoryKBError("晋升目标必须位于 wiki/ 内") from exc
    if inbox in target.parents:
        raise StoryKBError("晋升目标不能仍位于 wiki/inbox/")
    if target.exists():
        raise StoryKBError(f"目标已存在，请人工合并后再晋升: {target}")

    plan = {
        "status": "awaiting-confirmation",
        "candidate": str(candidate),
        "target": str(target),
        "title": metadata.get("title", candidate.stem),
    }
    if not args.confirm:
        return plan, 3

    promoted = replace_frontmatter_value(text, "status", "canonical")
    promoted = replace_frontmatter_value(promoted, "updated", today())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(promoted, encoding="utf-8")

    index_text = wiki_index.read_text(encoding="utf-8")
    link = os.path.relpath(target, wiki_index.parent)
    row = (
        f"| [{metadata.get('title', target.stem)}]({link}) | "
        f"{args.summary or '由创作复盘晋升的方法'} | "
        f"{metadata.get('sources', '[]')} |\n"
    )
    marker = "<!-- AUTO-EVOLVED -->"
    if marker not in index_text:
        raise StoryKBError(f"Wiki 索引缺少晋升标记: {marker}")
    index_text = index_text.replace(marker, marker + "\n" + row, 1)
    wiki_index.write_text(index_text, encoding="utf-8")

    candidate_text = replace_frontmatter_value(text, "status", "promoted")
    candidate_text = replace_frontmatter_value(
        candidate_text, "promoted_to", target_relative
    )
    candidate.write_text(candidate_text, encoding="utf-8")

    log_path = kb_root / "wiki" / "log.md"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n## [{today()}] 晋升 | {metadata.get('title', target.stem)}\n\n"
            f"- 候选：`{candidate.relative_to(kb_root)}`\n"
            f"- 页面：`{target.relative_to(kb_root)}`\n"
            f"- 说明：{args.summary or '由创作复盘晋升'}\n"
        )
    return {
        "status": "promoted",
        "candidate": str(candidate),
        "target": str(target),
        "index": str(wiki_index),
        "log": str(log_path),
    }, 0


def parse_sources(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def lint_wiki(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    project_root = Path(args.project_root).expanduser().resolve()
    _, config_sources, kb_root, wiki_index, router_path = knowledge_paths(
        project_root, args.config
    )
    errors: list[str] = []
    warnings: list[str] = []
    checked_pages = 0

    if not wiki_index.is_file():
        errors.append(f"缺少 Wiki 索引: {wiki_index}")
    if not router_path.is_file():
        errors.append(f"缺少路由: {router_path}")
    if errors:
        return {
            "status": "error",
            "errors": errors,
            "warnings": warnings,
            "config_sources": config_sources,
        }, 2

    try:
        router = load_router(router_path)
    except StoryKBError as exc:
        errors.append(str(exc))
        router = {}

    routed: list[str] = []
    for section in ("defaults",):
        route = router.get(section, {})
        routed.extend(route.get("wiki", []))
        routed.extend(route.get("sources", []))
    for section in ("stages", "triggers", "lengths", "genres", "methods"):
        for route in router.get(section, {}).values():
            routed.extend(route.get("wiki", []))
            routed.extend(route.get("sources", []))
    for relative in unique(routed):
        if not (kb_root / relative).is_file():
            errors.append(f"路由目标不存在: {relative}")

    index_text = wiki_index.read_text(encoding="utf-8")
    wiki_root = kb_root / "wiki"
    for page in sorted(wiki_root.glob("*.md")):
        checked_pages += 1
        text = page.read_text(encoding="utf-8")
        metadata, _ = parse_frontmatter(text)
        missing_fields = REQUIRED_WIKI_FIELDS - set(metadata)
        if missing_fields:
            errors.append(
                f"{page.relative_to(kb_root)} 缺少 frontmatter: "
                + ", ".join(sorted(missing_fields))
            )
        for source in parse_sources(metadata.get("sources", "")):
            if source.startswith(("project-run:", "conversation:", "external:")):
                continue
            if not (kb_root / source).exists():
                errors.append(
                    f"{page.relative_to(kb_root)} 的来源不存在: {source}"
                )
        for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if link.startswith(("http://", "https://", "#")):
                continue
            target = (page.parent / link.split("#", 1)[0]).resolve()
            if not target.exists():
                errors.append(
                    f"{page.relative_to(kb_root)} 存在断链: {link}"
                )
        if (
            page.name not in {"index.md", "log.md"}
            and metadata.get("status") == "canonical"
            and page.name not in index_text
        ):
            warnings.append(f"索引未引用 canonical 页面: wiki/{page.name}")

    result = {
        "status": "ok" if not errors else "error",
        "knowledge_base_root": str(kb_root),
        "config_sources": config_sources,
        "checked_pages": checked_pages,
        "errors": errors,
        "warnings": warnings,
    }
    return result, 0 if not errors else 2


def add_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=os.getcwd())
    parser.add_argument("--config", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="story-crafting knowledge-base runtime"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve", help="build a retrieval plan")
    add_config_arguments(resolve)
    resolve.add_argument("--query", required=True)
    resolve.add_argument(
        "--stage",
        choices=["intake", "ideation", "character", "outline", "writing", "revision", "packaging"],
    )
    resolve.add_argument(
        "--length", choices=["short", "novella", "long", "unknown"], default="unknown"
    )
    resolve.add_argument("--genre", default="")
    resolve.add_argument("--method", default="")

    record = subparsers.add_parser("record", help="append a project run ledger")
    add_config_arguments(record)
    record.add_argument("--task", required=True)
    record.add_argument("--stage", required=True)
    record.add_argument(
        "--status", choices=["verified", "degraded", "not-applicable"], required=True
    )
    record.add_argument("--trigger", action="append", default=[])
    record.add_argument("--knowledge-ref", action="append", default=[])
    record.add_argument("--method", action="append", default=[])
    record.add_argument("--output", action="append", default=[], required=True)

    capture = subparsers.add_parser("capture", help="capture a learning candidate")
    add_config_arguments(capture)
    capture.add_argument("--run-id", required=True)
    capture.add_argument("--title", required=True)
    capture.add_argument("--observation", required=True)
    capture.add_argument("--evidence", required=True)
    capture.add_argument("--scope", required=True)
    capture.add_argument("--source", action="append", default=[])
    capture.add_argument("--tag", action="append", default=[])
    capture.add_argument("--target")

    promote = subparsers.add_parser("promote", help="promote an approved candidate")
    add_config_arguments(promote)
    promote.add_argument("--candidate", required=True)
    promote.add_argument("--target")
    promote.add_argument("--summary")
    promote.add_argument("--confirm", action="store_true")

    lint = subparsers.add_parser("lint", help="validate the knowledge wiki")
    add_config_arguments(lint)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "resolve":
            result, exit_code = build_retrieval_plan(
                project_root=Path(args.project_root).expanduser().resolve(),
                explicit_config=args.config,
                query=args.query,
                stage=args.stage,
                length=args.length,
                genre=args.genre,
                method=args.method,
            )
        elif args.command == "record":
            result, exit_code = record_run(args)
        elif args.command == "capture":
            result, exit_code = capture_learning(args)
        elif args.command == "promote":
            result, exit_code = promote_learning(args)
        elif args.command == "lint":
            result, exit_code = lint_wiki(args)
        else:  # pragma: no cover
            raise StoryKBError(f"未知命令: {args.command}")
    except StoryKBError as exc:
        result = {"status": "error", "errors": [str(exc)]}
        exit_code = 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
