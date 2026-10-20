# 知识库运行指南

> 本文件用于排查配置和执行命令。正常创作必须先运行 `scripts/story_kb.py resolve`，不要凭本页手工猜测路径。

## 配置

全局配置：`~/.config/story-crafting/config.toml`

项目覆盖：从项目根目录向上查找最近的 `.story-crafting.toml`

```toml
version = 1
knowledge_base_root = "/absolute/path/to/knowledge-base"
wiki_index = "wiki/index.md"
router = "wiki/router.json"
source_priority = ["source", "wiki", "skill-reference"]
```

也可用 `STORY_CRAFTING_CONFIG` 临时指定配置文件。项目配置优先于全局配置。

## Resolve：生成检索计划

```bash
python3 scripts/story_kb.py resolve \
  --project-root "/path/to/project" \
  --stage outline \
  --length long \
  --genre scifi \
  --method "九线写法" \
  --query "为科幻长篇生成卷纲"
```

输出中的 `must_read` 是本次必须真实读取的文件。`authority_order` 是冲突时的来源优先级。

### 状态

- `verified`：路由、Wiki 和所需原始资料完整。
- `degraded`：普通任务可用 Skill fallback；产出必须显式标记。
- `error`：强制检索失败，停止正式产出。

## Record：记录产出引用

`--knowledge-ref` 使用 `PATH::SECTION`；`--method` 使用 `METHOD::APPLIED_TO::SOURCE`。

```bash
python3 scripts/story_kb.py record \
  --project-root "/path/to/project" \
  --task "长篇大纲" \
  --stage outline \
  --status verified \
  --trigger long-form-state \
  --knowledge-ref "2-大纲与构思/3-大纲写法与流程/3.2-九线写法详解.md::九线总览" \
  --method "九线写法::大纲九线表::knowledge_refs[0]" \
  --output "/path/to/project/大纲.md"
```

运行台账写入项目 `.story-crafting/runs.jsonl`。

## Capture：暂存可复用经验

仅在有证据的可复用经验、稳定失败模式或用户确认有效时调用：

```bash
python3 scripts/story_kb.py capture \
  --project-root "/path/to/project" \
  --run-id "<record 返回的 run_id>" \
  --title "方法名称" \
  --observation "观察到的规律" \
  --evidence "支持它的运行或文件证据" \
  --scope "适用与不适用条件"
```

候选进入 `wiki/inbox/`，不会自动成为正式知识。

## Promote：确认后晋升

先不带 `--confirm` 查看计划：

```bash
python3 scripts/story_kb.py promote \
  --project-root "/path/to/project" \
  --candidate "/path/to/kb/wiki/inbox/candidate.md"
```

完成查重、原文回查、冲突检查并获得用户确认后，再添加 `--confirm`。晋升会创建正式 Wiki 页、更新索引、标记候选并追加日志；目标已存在时停止，要求人工合并。

## Lint：知识库健康检查

```bash
python3 scripts/story_kb.py lint --project-root "/path/to/project"
```

检查配置、路由目标、Wiki frontmatter、来源、交叉链接和索引孤立页。

## 强制检索

以下任务不能降级：

- 修改、审查、重写、润色、逻辑或一致性修复
- 卡壳、中段垮、动机断裂、伏笔、结尾或节奏问题
- 长篇/连载的总纲、卷纲、章纲、关系、伏笔和时间线
- 指定题材、指定方法或明确要求使用知识库

强制任务读取索引、2–4 个相关 Wiki 页面和对应原始资料。普通任务读取索引和至少一个阶段 Wiki 页面。

## 来源治理

1. 原始知识库文档是事实源。
2. Wiki 是带来源的整合与路由层。
3. Skill references 只作普通任务的故障降级。
4. 模型通用知识不得覆盖前三者。
5. 同名方法冲突先查 `wiki/conflict-registry.md`；未决冲突必须报告。
