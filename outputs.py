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

CATEGORY_EMOJI = {
    "product": "🚀",
    "tool": "🛠️",
    "research": "🔬",
    "industry": "📊",
    "tutorial": "💡",
}

BRIEF_HEADER = """# 🤖 AI Daily Brief

> 每日 AI / 开发者工具链精选简报 · GitHub Actions + GPT 自动策展

[![Daily Update](https://github.com/{repo}/actions/workflows/daily-news.yml/badge.svg)](https://github.com/{repo}/actions/workflows/daily-news.yml)

---

"""

BRIEF_FOOTER = """
---

## 📊 数据概览

{stats_line}

## 📚 往期简报

查看 [archives/](./archives/) 目录浏览历史简报。

## 🔧 工作原理

1. **数据采集**: HackerNews · GitHub Trending · HuggingFace · 阮一峰周刊 · Reddit · RSS (9 源)
2. **智能筛选**: GPT 两阶段策展 — 打分聚类 → 主编选稿
3. **每日更新**: GitHub Actions 定时运行，自动发布

👉 回到 [项目主页 (README)](./README.md)
"""


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


def format_daily_brief(curation_result: dict, config: dict, pipeline_stats: dict = None) -> str:
    """Generate daily brief README content."""
    candidates = curation_result["candidates"]
    brief = curation_result["brief"]
    repo = os.environ.get("GITHUB_REPOSITORY", "statefulai/ai-daily-brief")
    date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_zh[datetime.now(BEIJING_TZ).weekday()]

    content = BRIEF_HEADER.format(repo=repo)
    content += f"## 📅 {date} {weekday}\n\n"

    # === 今日焦点 ===
    focus = brief.get("focus", {})
    focus_idx = focus.get("index", 0)
    if focus_idx < len(candidates):
        focus_item = candidates[focus_idx]
        star = _importance_star(focus_item)
        content += f"### 📌 今日焦点\n\n"
        content += f"**[{_truncate_title(focus_item['title'])}]({focus_item['url']})** · `{focus_item['source']}`{star}\n\n"
        content += f"> {focus.get('editorial', '')}\n\n"
        content += _format_related(focus_item)
        content += "\n---\n\n"

    # === 热点速览 ===
    highlights = brief.get("highlights", [])
    if highlights:
        content += f"### 🔥 热点速览\n\n"
        for num, hl in enumerate(highlights, 1):
            idx = hl.get("index", 0)
            if idx < len(candidates):
                item = candidates[idx]
                editorial = hl.get("editorial", "")
                star = _importance_star(item)

                content += f"**{num}. [{_truncate_title(item['title'])}]({item['url']})** · `{item['source']}`{star}\n\n"
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
            if idx < len(candidates):
                item = candidates[idx]
                reason = tool.get("reason", "")
                content += f"**[{_truncate_title(item['title'])}]({item['url']})** · `{item['source']}`\n\n"
                if reason:
                    content += f"{reason}\n\n"
        content += "---\n\n"

    # === 延伸阅读 (remaining candidates not selected) ===
    selected_indices = set(used_indices)
    for tool in tools:
        selected_indices.add(tool.get("index", -1))

    remaining = [
        (i, item) for i, item in enumerate(candidates)
        if i not in selected_indices
    ]
    quote = brief.get("quote", "")
    industry_data = brief.get("industry_data") or []
    tech_trends = brief.get("tech_trends") or []
    expert_quotes = brief.get("expert_quotes") or []
    if not isinstance(industry_data, list):
        industry_data = []
    if not isinstance(tech_trends, list):
        tech_trends = []
    if not isinstance(expert_quotes, list):
        expert_quotes = []
    industry_data = [item for item in industry_data if isinstance(item, dict)]
    tech_trends = [item for item in tech_trends if isinstance(item, dict)]
    expert_quotes = [item for item in expert_quotes if isinstance(item, dict)]

    def _candidate_link(idx) -> str:
        if isinstance(idx, int) and 0 <= idx < len(candidates):
            c = candidates[idx]
            return f"[{c['source']}]({c['url']})"
        return ""

    # Tail sections joined by dividers; last one must not emit a trailing ---
    # (it would collide with the footer's leading ---)
    tail_sections = []

    if quote:
        tail_sections.append(f"### 💡 今日洞察\n\n> {quote}\n\n")

    if industry_data:
        section = "### 📊 行业数据\n\n"
        section += "| 事件 | 数据 | 来源 |\n|------|:---:|:---:|\n"
        for d in industry_data[:3]:
            event = str(d.get("event", "")).replace("|", "/")
            value = str(d.get("value", "")).replace("|", "/")
            link = _candidate_link(d.get("source_index")) or "—"
            section += f"| {event} | {value} | {link} |\n"
        tail_sections.append(section + "\n")

    if tech_trends:
        section = "### 🏗️ 技术趋势\n\n"
        for t in tech_trends[:2]:
            section += f"- **{t.get('trend', '')}**: {t.get('description', '')}\n"
            related_indices = t.get("related_indices") or []
            if not isinstance(related_indices, list):
                related_indices = []
            links = [l for l in (_candidate_link(i) for i in related_indices[:3]) if l]
            if links:
                section += f"  📎 {' · '.join(links)}\n"
        tail_sections.append(section + "\n")

    if expert_quotes:
        section = "### 💬 专家观点\n\n"
        for q in expert_quotes[:2]:
            link = _candidate_link(q.get("source_index"))
            if not link:
                continue
            person = q.get("person", "")
            person_title = q.get("title", "")
            who = f"**{person}**" + (f"（{person_title}）" if person_title else "")
            section += f"> {q.get('quote', '')} —— {who} · 来源：{link}\n\n"
        if section != "### 💬 专家观点\n\n":
            tail_sections.append(section)

    if remaining:
        section = "### 📎 延伸阅读\n\n"
        for i, item in remaining[:10]:
            cat_emoji = CATEGORY_EMOJI.get(item.get("category", ""), "•")
            section += f"- {cat_emoji} [{_truncate_title(item['title'])}]({item['url']}) · `{item['source']}`\n"
        tail_sections.append(section + "\n")

    content += "---\n\n".join(tail_sections)

    # Stats footer
    if pipeline_stats:
        gen_time = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M UTC+8")
        stats_line = (
            f"| 数据源 | 原始条目 | 过滤后 | AI 评分 | 精选 |\n"
            f"|:---:|:---:|:---:|:---:|:---:|\n"
            f"| {pipeline_stats.get('sources', '?')} 源 | "
            f"{pipeline_stats.get('raw', '?')} 篇 | "
            f"{pipeline_stats.get('filtered', '?')} 篇 | "
            f"{pipeline_stats.get('scored', '?')} 篇 | "
            f"**{pipeline_stats.get('selected', '?')} 篇** |\n\n"
            f"*生成于 {gen_time}*"
        )
    else:
        stats_line = ""
    content += BRIEF_FOOTER.format(stats_line=stats_line)
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
    if focus_idx < len(candidates):
        item = candidates[focus_idx]
        content += f"## 📌 焦点: [{item['title']}]({item['url']})\n\n"
        content += f"{focus.get('editorial', '')}\n\n"

    # Highlights
    content += f"## 🔥 速览\n\n"
    for hl in highlights:
        idx = hl.get("index", 0)
        if idx < len(candidates):
            item = candidates[idx]
            content += f"### [{item['title']}]({item['url']})\n\n"
            content += f"- **Source**: {item['source']}\n"
            content += f"- **Category**: {item.get('category', '')}\n"
            content += f"- **Importance**: {item.get('importance', 5)}/10\n"
            content += f"- **编辑点评**: {hl.get('editorial', '')}\n\n"

    # Full candidate list
    industry_data = brief.get("industry_data") or []
    if not isinstance(industry_data, list):
        industry_data = []
    industry_data = [item for item in industry_data if isinstance(item, dict)]
    if industry_data:
        content += "## 📊 行业数据\n\n"
        content += "| 事件 | 数据 | 来源 |\n|------|:---:|:---:|\n"
        for d in industry_data[:3]:
            event = str(d.get("event", "")).replace("|", "/")
            value = str(d.get("value", "")).replace("|", "/")
            link = _candidate_link(d.get("source_index")) or "—"
            content += f"| {event} | {value} | {link} |\n"
        content += "\n"

    tech_trends = brief.get("tech_trends") or []
    if not isinstance(tech_trends, list):
        tech_trends = []
    tech_trends = [item for item in tech_trends if isinstance(item, dict)]
    if tech_trends:
        content += "## 🏗️ 技术趋势\n\n"
        for t in tech_trends[:2]:
            content += f"- **{t.get('trend', '')}**: {t.get('description', '')}\n"
            related_indices = t.get("related_indices") or []
            if not isinstance(related_indices, list):
                related_indices = []
            links = [l for l in (_candidate_link(i) for i in related_indices[:3]) if l]
            if links:
                content += f"  📎 {' · '.join(links)}\n"
        content += "\n"

    expert_quotes = brief.get("expert_quotes") or []
    if not isinstance(expert_quotes, list):
        expert_quotes = []
    expert_quotes = [item for item in expert_quotes if isinstance(item, dict)]
    if expert_quotes:
        section = "## 💬 专家观点\n\n"
        for q in expert_quotes[:2]:
            link = _candidate_link(q.get("source_index"))
            if not link:
                continue
            person = q.get("person", "")
            person_title = q.get("title", "")
            who = f"**{person}**" + (f"（{person_title}）" if person_title else "")
            section += f"> {q.get('quote', '')} —— {who} · 来源：{link}\n\n"
        if section != "## 💬 专家观点\n\n":
            content += section

    content += f"## 📋 全部候选 ({len(candidates)} 条)\n\n"
    for i, item in enumerate(candidates):
        content += f"{i+1}. [{item['title']}]({item['url']}) — {item['source']} "
        content += f"(importance: {item.get('importance', 5)}, topic: {item.get('topic_key', '')})\n"

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
