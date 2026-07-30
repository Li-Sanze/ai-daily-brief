#!/usr/bin/env python3
"""
AI News Aggregator - Main Entry Point

Usage:
    python main.py                  # full pipeline
    python main.py --sources-only   # fetch sources only (no LLM)
    python main.py --dry-run        # print items without writing files
"""

import asyncio
import argparse
import json
import logging
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env file (won't override existing env vars)
load_dotenv()

from sources import (
    NewsItem,
    fetch_hackernews,
    fetch_github_trending,
    fetch_huggingface,
    fetch_rss_feeds,
    fetch_ruanyf_weekly,
    fetch_reddit,
)
from summarizer import curate_daily_brief
from outputs import format_daily_brief, write_readme, write_archive, push_to_notion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ai-news")


def load_config(path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        logger.warning(f"Config file {path} not found, using defaults")
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def filter_by_age(items: list[NewsItem], max_age_hours: float) -> list[NewsItem]:
    """Filter items by age."""
    return [item for item in items if item.age_hours <= max_age_hours]


def filter_by_keywords(
    items: list[NewsItem],
    include: list[str],
    exclude: list[str],
) -> list[NewsItem]:
    """Keyword-based pre-filter before LLM."""
    filtered = []
    for item in items:
        text = f"{item.title} {item.summary} {' '.join(item.tags)}".lower()

        # Check exclude first
        if any(kw in text for kw in exclude):
            continue

        # Check include (at least one keyword must match)
        if include and not any(kw in text for kw in include):
            continue

        filtered.append(item)
    return filtered


def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    """Remove duplicate items by URL."""
    seen_urls: set[str] = set()
    unique = []
    for item in items:
        normalized = item.url.rstrip("/").lower()
        if normalized not in seen_urls:
            seen_urls.add(normalized)
            unique.append(item)
    return unique


def load_previous_urls(archive_dir: str = "archives", lookback_days: int = 2) -> set[str]:
    """Load URLs from recent archive files to enable cross-day dedup."""
    from datetime import datetime, timedelta, timezone

    BEIJING_TZ = timezone(timedelta(hours=8))
    urls: set[str] = set()
    archive_path = Path(archive_dir)
    if not archive_path.exists():
        return urls

    today = datetime.now(BEIJING_TZ)
    for delta in range(1, lookback_days + 1):
        date_str = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
        md_file = archive_path / f"{date_str}.md"
        if md_file.exists():
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to read archive {md_file}: {e}")
                continue
            # Match markdown links, handling URLs that may contain balanced parentheses
            for match in re.finditer(r'\[.*?\]\((https?://(?:[^\s\(\)]+|\([^\)]*\))*)\)', content):
                urls.add(match.group(1).rstrip("/").lower())

    return urls


def cross_day_dedup(items: list[NewsItem], previous_urls: set[str]) -> list[NewsItem]:
    """Remove items that were already published in previous days."""
    if not previous_urls:
        return items
    filtered = []
    removed = 0
    for item in items:
        normalized = item.url.rstrip("/").lower()
        if normalized in previous_urls:
            removed += 1
        else:
            filtered.append(item)
    if removed:
        logger.info(f"Cross-day dedup removed {removed} previously published items")
    return filtered


async def fetch_all_sources(config: dict) -> list[NewsItem]:
    """Fetch from all configured sources concurrently."""
    sources_cfg = config.get("sources", {})

    tasks = [
        fetch_hackernews(sources_cfg.get("hackernews", {})),
        fetch_github_trending(sources_cfg.get("github_trending", {})),
        fetch_huggingface(sources_cfg.get("huggingface", {})),
        fetch_rss_feeds(sources_cfg.get("rss_feeds", {})),
        fetch_ruanyf_weekly(sources_cfg.get("ruanyf_weekly", {})),
        fetch_reddit(sources_cfg.get("reddit", {})),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[NewsItem] = []
    source_names = ["HackerNews", "GitHub", "HuggingFace", "RSS", "阮一峰周刊", "Reddit"]

    for name, result in zip(source_names, results):
        if isinstance(result, Exception):
            logger.error(f"{name} source failed: {result}")
        else:
            all_items.extend(result)

    return all_items


async def run(args: argparse.Namespace):
    """Main pipeline."""
    config = load_config(args.config)

    # 1. Fetch all sources
    logger.info("=== Fetching sources ===")
    raw_items = await fetch_all_sources(config)
    logger.info(f"Total raw items: {len(raw_items)}")

    # 2. Deduplicate (within same run)
    items = deduplicate(raw_items)
    logger.info(f"After dedup: {len(items)}")

    # 2b. Cross-day dedup (remove items already published yesterday)
    archive_dir = config.get("output", {}).get("archive", {}).get("directory", "archives")
    previous_urls = load_previous_urls(archive_dir, lookback_days=2)
    items = cross_day_dedup(items, previous_urls)
    logger.info(f"After cross-day dedup: {len(items)}")

    # 3. Filter by age
    filter_cfg = config.get("filter", {})
    max_age = filter_cfg.get("max_age_hours", 48)
    items = filter_by_age(items, max_age)
    logger.info(f"After age filter ({max_age}h): {len(items)}")

    # 4. Keyword pre-filter
    keywords = filter_cfg.get("keywords", {})
    items = filter_by_keywords(
        items,
        include=keywords.get("include", []),
        exclude=keywords.get("exclude", []),
    )
    logger.info(f"After keyword filter: {len(items)}")

    if args.sources_only:
        for item in items:
            print(f"[{item.source}] {item.title} ({item.url})")
        return

    # 5. Two-stage curation
    logger.info("=== Two-Stage Curation ===")
    curation_result = curate_daily_brief(items, config.get("llm", {}))
    candidates = curation_result["candidates"]
    brief = curation_result["brief"]
    logger.info(f"Curation: {len(candidates)} candidates → "
                f"focus + {len(brief.get('highlights', []))} highlights + "
                f"{len(brief.get('tools', []))} tools")

    if args.dry_run:
        print(json.dumps(curation_result, ensure_ascii=False, indent=2))
        return

    # 6. Output
    logger.info("=== Writing outputs ===")
    output_cfg = config.get("output", {})

    # README (daily brief format)
    if output_cfg.get("github_readme", {}).get("enabled", True):
        readme_content = format_daily_brief(curation_result, output_cfg.get("github_readme", {}))
        readme_path = output_cfg.get("github_readme", {}).get("file", "README.md")
        write_readme(readme_content, readme_path)

    # Archive
    write_archive(curation_result, output_cfg.get("archive", {}))

    # Notion
    await push_to_notion(candidates[:20], output_cfg.get("notion", {}))

    selected_count = 1 + len(brief.get("highlights", [])) + len(brief.get("tools", []))
    logger.info(f"=== Done! {selected_count} items in daily brief ===")


def main():
    parser = argparse.ArgumentParser(description="AI Daily Brief")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--sources-only", action="store_true", help="Fetch sources only, no LLM")
    parser.add_argument("--dry-run", action="store_true", help="Print results, don't write files")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
