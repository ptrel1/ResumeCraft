"""markdown 解析器：将简历 md 转成结构化 Resume 模型。

解析规则（约定式，见 docs/markdown-format.md）：
- 文件开头可选 YAML front matter（--- 包裹），解析进 meta
- `# 姓名` 一级标题 → 基本信息 name
- `## 区块名` 二级标题 → 划分区块
- `### 标题 · 时间` 三级标题 → 一个条目，`·` 后为时间；`` `标签` `` 提取标签
- 三级标题后的首行 `**副标题**` 或普通文本 → 条目 subtitle
- `- ` 列表项 → 要点；技能区按 `**类别**：值` 归入 skills
- `# 姓名` 后、首个 `##` 前的 `**标签** 值` 行 → contact.fields
- 基本信息区 `> 引言` → contact.summary
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .frontmatter import loads as _fm_loads

from ..models import Contact, Item, Meta, Resume, Section

SKILL_SECTIONS = {"专业技能", "技能", "技能特长", "个人技能", "IT技能", "技术栈", "核心技能", "技能清单"}


def is_skill_section(name: str) -> bool:
    """判断是否为技能相关区块（支持包含技能/skill/stack等关键词）。"""
    if not name:
        return False
    name_clean = name.strip().lower()
    if name in SKILL_SECTIONS:
        return True
    return any(k in name_clean for k in ("技能", "skill", "stack", "tech", "技术栈", "专长"))


def apply_metrics_rules(content: str, metrics: dict) -> str:
    """在 Markdown 解析前，根据多平台指标动态替换占位符（如 {{ metrics.deepseek.total_tokens_str }}）。"""
    if not content or not metrics:
        return content
        
    ds = metrics.get("deepseek", {})
    git = metrics.get("git", {})
    sec = metrics.get("security", {})
    cem = metrics.get("cem", {})

    replacements = {
        "{{ metrics.deepseek.total_tokens_str }}": str(ds.get("total_tokens_str", "10.7 亿+")),
        "{{ metrics.deepseek.total_cost }}": f"{ds.get('total_cost', 1716.35):.2f}",
        "{{ metrics.deepseek.balance }}": f"{ds.get('balance', 83.65):.2f}",
        "{{ metrics.git.total_commits_str }}": str(git.get("total_commits_str", "")),
        "{{ metrics.git.local_repos_count }}": str(git.get("local_repos_count", "")),
        "{{ metrics.security.blocked_ips_str }}": str(sec.get("blocked_ips_str", "")),
        "{{ metrics.cem.scheduled_tasks_str }}": str(cem.get("scheduled_tasks_str", "")),
        "{{ metrics.cem.agv_managed_str }}": str(cem.get("agv_managed_str", "")),
        "{{ metrics.cem.dispatch_success_rate }}": str(cem.get("dispatch_success_rate", "")),
        "{{ metrics.cem.hot_upgrade_str }}": str(cem.get("hot_upgrade_str", "")),
    }

    for tag, val in replacements.items():
        content = content.replace(tag, val)
    return content


def parse_md(content: str) -> Resume:
    """核心入口：md 文本 → Resume 模型。"""
    # 动态应用客观指标占位符替换
    try:
        from ..connectors.storage import get_cached_metrics
        metrics = get_cached_metrics()
        content = apply_metrics_rules(content, metrics)
    except Exception:
        pass

    metadata, body = _fm_loads(content)
    meta = Meta(
        template=metadata.get("template", "minimal"),
        avatar=metadata.get("avatar"),
        theme=metadata.get("theme", {}),
        layout=metadata.get("layout", "split"),
        pdf=metadata.get("pdf", {}),
        title=metadata.get("title"),
    )

    contact = Contact()
    sections: List[Section] = []
    current_section: Optional[Section] = None
    current_item: Optional[Item] = None
    in_intro = True          # 是否处于 # 姓名 之后、第一个 ## 之前的简介区

    lines = body.splitlines()
    i = 0
    n = len(lines)

    def flush_item():
        nonlocal current_item
        if current_item and current_section is not None:
            current_section.items.append(current_item)
        current_item = None

    while i < n:
        line = lines[i].rstrip()

        # 表格块：连续以 | 开头的行 → 解析成表格存入 current_section.tables
        if line.strip().startswith("|"):
            table_rows = []
            while i < n and lines[i].strip().startswith("|"):
                row_line = lines[i].strip().strip("|").strip()
                cells = [c.strip() for c in row_line.split("|")]
                # 跳过分隔行（如 |------|------|）
                if cells and all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    i += 1
                    continue
                table_rows.append(cells)
                i += 1
            if current_section is not None:
                current_section.tables.append(table_rows)
            continue

        # 一级标题：# 姓名
        if re.match(r"^#\s+", line) and not line.startswith("##"):
            flush_item()
            contact.name = line.lstrip("#").strip()

        # 二级标题：## 区块
        elif re.match(r"^##\s+", line):
            flush_item()
            in_intro = False
            current_section = Section(name=line.lstrip("#").strip())
            sections.append(current_section)

        # 三级标题：### 条目
        elif re.match(r"^###\s+", line):
            flush_item()
            title, date, tags = _parse_title(line.lstrip("#").strip())
            current_item = Item(title=title, date=date, tags=tags)
            sec_name = current_section.name if current_section else ""
            is_proj = sec_name in ("项目经验", "项目")
            i += 1
            # 预读条目首行：
            # - 非项目区块：`**副标题**` 或普通文本行 → subtitle
            # - 项目区块：仅 `**副标题**` 作 subtitle，普通文本留给主循环作 points
            while i < n and not lines[i].strip():
                i += 1
            if i < n and current_item:
                next_line = lines[i].rstrip()
                if re.match(r"^\*\*.+\*\*$", next_line):
                    current_item.subtitle = next_line.strip("*").strip()
                    i += 1
                elif not is_proj and next_line and not next_line.startswith(("##", "###", "-", "*", ">")):
                    current_item.subtitle = next_line.strip()
                    i += 1
            continue

        # 列表项
        elif re.match(r"^\s*[-*]\s+", line):
            text = line.strip().lstrip("-*").strip()
            if current_section and is_skill_section(current_section.name):
                m = re.match(r"\*\*(.+?)\*\*\s*[：:]\s*(.+)", text)
                if m:
                    current_section.skills.append(f"{m.group(1)}：{m.group(2)}")
                else:
                    current_section.skills.append(text)
            else:
                if current_item is None:
                    # 未开条目的列表项：视为属于当前区块的独立条目要点
                    current_item = Item(title="", points=[text])
                else:
                    current_item.points.append(text)

        # 普通文本行：作为当前条目的要点（如项目简介、教育说明）
        elif line.strip() and not in_intro:
            if current_item is not None:
                current_item.points.append(line.strip())
            elif current_section is not None:
                if is_skill_section(current_section.name):
                    m = re.match(r"\*\*(.+?)\*\*\s*[：:]\s*(.+)", line.strip())
                    if m:
                        current_section.skills.append(f"{m.group(1)}：{m.group(2)}")
                    else:
                        current_section.skills.append(line.strip())
                else:
                    # 未开条目时的普通文本（如独立说明），暂存到独立 item 或要点
                    current_item = Item(title="", points=[line.strip()])

        # 简介区（# 之后，第一个 ## 之前）的 kv 与引言
        elif in_intro and line.strip():
            if line.strip().startswith(">"):
                contact.summary = line.strip().lstrip(">").strip()
            else:
                m = re.match(r"\*\*(.+?)\*\*\s*[：:]\s*(.+)", line.strip())
                if m:
                    label = m.group(1).strip()
                    val = m.group(2).strip()
                    if label in ("求职方向", "求职意向", "意向岗位", "岗位", "Role", "Position"):
                        contact.role = val
                    contact.fields.append({"label": label, "value": val})

        i += 1

    flush_item()
    return Resume(meta=meta, contact=contact, sections=sections)


def _parse_title(line: str) -> Tuple[str, str, List[str]]:
    """从 `标题 · 时间` 提取 (标题, 时间, 标签)。"""
    tags = re.findall(r"`([^`]+)`", line)
    clean = re.sub(r"`[^`]*`", "", line).strip()
    if "·" in clean:
        title, date = clean.split("·", 1)
        return title.strip(), date.strip(), tags
    return clean.strip(), "", tags
