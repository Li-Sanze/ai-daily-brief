"""
AI News Aggregator - Daily Brief Output

Generates structured daily briefing in README.md and archive files.
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING_TZ = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)

BRIEF_HEADER = """# 🤖 AI Daily Brief

"""

BRIEF_FOOTER = "[往期简报](./archives/) · [项目说明](./README.md)\n"


def _source_badge(source: str) -> str:
    """Format source as inline badge."""
    return f"`{source}`"


def _truncate_title(title: str, max_len: int = 120) -> str:
    """Truncate long titles (e.g., GitHub repo descriptions)."""
    # GitHub-style "owner/repo: long description" — keep repo name, truncate desc at 80
    if ": " in title and "/" in title.split(": ")[0]:
        parts = title.split(": ", 1)
        repo_name = parts[0]
        desc = parts[1]
        # Only apply GitHub truncation if prefix looks like a repo (no spaces, short)
        if " " not in repo_name and len(repo_name) < 60:
            repo_max = 80
            avail = max(repo_max - len(repo_name) - 5, 10)
            if len(desc) > avail:
                desc = desc[:avail] + "..."
            return f"{repo_name}: {desc}"
    if len(title) > max_len:
        return title[:max_len - 3] + "..."
    return title


def _display_title(selection: dict, item: dict) -> str:
    """Prefer the editor's Chinese short title for selected items."""
    title_zh = selection.get("title_zh")
    if isinstance(title_zh, str) and title_zh.strip():
        return title_zh.strip()
    return _truncate_title(item["title"])


def _format_related(item: dict) -> str:
    """Format related sources as inline links."""
    related = item.get("related_sources", [])
    if not related:
        return ""
    links = " · ".join(f"[{r['source']}]({r['url']})" for r in related[:3])
    return f"  📎 延伸: {links}\n"


def _importance_star(item: dict, threshold: int = 9) -> str:
    """Show ⭐ only for high-importance items."""
    return " ⭐" if item.get("importance", 0) >= threshold else ""


def format_daily_brief(curation_result: dict, config: dict) -> str:
    """Generate daily brief README content."""
    candidates = curation_result["candidates"]
    brief = curation_result["brief"]
    date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_zh[datetime.now(BEIJING_TZ).weekday()]

    content = BRIEF_HEADER
    content += f"## 📅 {date} {weekday}\n\n"

    # === 今日焦点 ===
    focus = brief.get("focus", {})
    focus_idx = focus.get("index", 0)
    if isinstance(focus_idx, int) and 0 <= focus_idx < len(candidates):
        focus_item = candidates[focus_idx]
        star = _importance_star(focus_item)
        content += f"### 📌 今日焦点\n\n"
        content += f"**[{_display_title(focus, focus_item)}]({focus_item['url']})** · `{focus_item['source']}`{star}\n\n"
        content += f"> {focus.get('editorial', '')}\n\n"
        content += _format_related(focus_item)
        content += "\n---\n\n"

    # === 热点速览 ===
    highlights = brief.get("highlights", [])
    if highlights:
        content += f"### 🔥 热点速览\n\n"
        for num, hl in enumerate(highlights, 1):
            idx = hl.get("index", 0)
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                item = candidates[idx]
                editorial = hl.get("editorial", "")
                star = _importance_star(item)

                content += f"**{num}. [{_display_title(hl, item)}]({item['url']})** · `{item['source']}`{star}\n\n"
                if editorial:
                    content += f"{editorial}\n\n"
                related_text = _format_related(item)
                if related_text:
                    content += related_text
        content += "---\n\n"

    # === 今日工具 (skip items already in focus/highlights) ===
    tools = brief.get("tools", [])
    used_indices = {focus.get("index", -1)}
    for hl in highlights:
        used_indices.add(hl.get("index", -1))
    tools_to_show = [t for t in tools if t.get("index", -1) not in used_indices]
    if tools_to_show:
        content += f"### 🛠️ 今日工具\n\n"
        for tool in tools_to_show:
            idx = tool.get("index", 0)
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                item = candidates[idx]
                reason = tool.get("reason", "")
                content += f"**[{_display_title(tool, item)}]({item['url']})** · `{item['source']}`\n\n"
                if reason:
                    content += f"{reason}\n\n"
        content += "---\n\n"

    industry_data = brief.get("industry_data") or []
    if not isinstance(industry_data, list):
        industry_data = []
    industry_data = [item for item in industry_data if isinstance(item, dict)]
    industry_data_to_show = industry_data if len(industry_data) >= 2 else []

    def _candidate_link(idx) -> str:
        if isinstance(idx, int) and 0 <= idx < len(candidates):
            c = candidates[idx]
            return f"[{c['source']}]({c['url']})"
        return ""

    # Tail sections joined by dividers; last one must not emit a trailing ---
    # (it would collide with the footer's leading ---)
    tail_sections = []

    if industry_data_to_show:
        section = "### 📊 行业数据\n\n"
        section += "| 指标 | 数值 | 说明 | 来源 |\n|------|:---:|------|:---:|\n"
        for d in industry_data_to_show[:3]:
            metric = str(d.get("metric", "")).replace("|", "/")
            value = f"{d.get('value', '')} {d.get('unit', '')}".strip().replace("|", "/")
            context = str(d.get("context", "")).replace("|", "/")
            link = _candidate_link(d.get("source_index")) or "—"
            section += f"| {metric} | {value} | {context} | {link} |\n"
        tail_sections.append(section + "\n")

    content += "---\n\n".join(tail_sections)
    if tail_sections:
        content += "---\n\n"

    content += BRIEF_FOOTER
    return content


def write_readme(content: str, output_path: str = "README.md"):
    """Write README.md."""
    Path(output_path).write_text(content, encoding="utf-8")
    logger.info(f"README written to {output_path}")


def write_archive(curation_result: dict, config: dict):
    """Write daily archive file with full brief + raw data."""
    if not config.get("enabled", True):
        return

    directory = config.get("directory", "archives")
    date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_zh[datetime.now(BEIJING_TZ).weekday()]

    Path(directory).mkdir(parents=True, exist_ok=True)

    candidates = curation_result["candidates"]
    brief = curation_result["brief"]

    def _candidate_link(idx) -> str:
        if isinstance(idx, int) and 0 <= idx < len(candidates):
            c = candidates[idx]
            return f"[{c['source']}]({c['url']})"
        return ""

    content = f"# AI Daily Brief — {date} {weekday}\n\n"
    content += f"> Generated at {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M CST')}\n\n"
    content += f"候选条目: {len(candidates)} | "

    highlights = brief.get("highlights", [])
    content += f"精选: {1 + len(highlights) + len(brief.get('tools', []))} 条\n\n"

    # Focus
    focus = brief.get("focus", {})
    focus_idx = focus.get("index", 0)
    if isinstance(focus_idx, int) and 0 <= focus_idx < len(candidates):
        item = candidates[focus_idx]
        content += f"## 📌 焦点: [{_display_title(focus, item)}]({item['url']})\n\n"
        content += f"{focus.get('editorial', '')}\n\n"

    # Highlights
    content += f"## 🔥 速览\n\n"
    for hl in highlights:
        idx = hl.get("index", 0)
        if isinstance(idx, int) and 0 <= idx < len(candidates):
            item = candidates[idx]
            content += f"### [{_display_title(hl, item)}]({item['url']})\n\n"
            content += f"- **Source**: {item['source']}\n"
            content += f"- **编辑点评**: {hl.get('editorial', '')}\n\n"

    # Tools
    tools = brief.get("tools") or []
    if tools:
        content += "## 🛠️ 工具\n\n"
        for tool in tools:
            idx = tool.get("index", 0)
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                item = candidates[idx]
                content += f"### [{_display_title(tool, item)}]({item['url']})\n\n"
                content += f"{tool.get('reason', '')}\n\n"

    industry_data = brief.get("industry_data") or []
    if not isinstance(industry_data, list):
        industry_data = []
    industry_data = [item for item in industry_data if isinstance(item, dict)]
    if industry_data:
        content += "## 📊 行业数据\n\n"
        content += "| 指标 | 数值 | 说明 | 来源 |\n|------|:---:|------|:---:|\n"
        for d in industry_data[:3]:
            metric = str(d.get("metric", "")).replace("|", "/")
            value = f"{d.get('value', '')} {d.get('unit', '')}".strip().replace("|", "/")
            context = str(d.get("context", "")).replace("|", "/")
            link = _candidate_link(d.get("source_index")) or "—"
            content += f"| {metric} | {value} | {context} | {link} |\n"
        content += "\n"

    content += f"<details>\n<summary>完整候选与内部评分（{len(candidates)} 条）</summary>\n\n"
    for i, item in enumerate(candidates):
        content += f"{i+1}. [{item['title']}]({item['url']}) — {item['source']} "
        content += f"(importance: {item.get('importance', 5)}, topic: {item.get('topic_key', '')})\n"
    content += "\n</details>\n"

    path = Path(directory) / f"{date}.md"
    path.write_text(content, encoding="utf-8")
    logger.info(f"Archive written to {path}")


async def push_to_notion(items: list[dict], config: dict):
    """Push curated items to Notion database (optional)."""
    if not config.get("enabled", False):
        return

    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get(config.get("database_id_env", "NOTION_DATABASE_ID"))

    if not token or not db_id:
        logger.warning("Notion integration enabled but missing NOTION_TOKEN or database ID")
        return

    import httpx
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        for item in items[:20]:
            try:
                page = {
                    "parent": {"database_id": db_id},
                    "properties": {
                        "Title": {"title": [{"text": {"content": item["title"][:100]}}]},
                        "URL": {"url": item["url"]},
                        "Source": {"select": {"name": item["source"]}},
                        "Category": {"select": {"name": item.get("category", "tool")}},
                        "Importance": {"number": item.get("importance", 5)},
                        "Date": {"date": {"start": item["published"][:10]}},
                    },
                }
                resp = await client.post(
                    "https://api.notion.com/v1/pages",
                    headers=headers,
                    json=page,
                )
                if resp.status_code != 200:
                    logger.warning(f"Notion push failed for '{item['title'][:30]}': {resp.status_code}")
            except Exception as e:
                logger.warning(f"Notion push error: {e}")

    logger.info(f"Notion: pushed {min(len(items), 20)} items")
