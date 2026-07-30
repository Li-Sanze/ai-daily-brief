"""
AI News Aggregator - Two-Stage Curation Pipeline

Stage 1: Score + classify + cluster similar topics
Stage 2: Editor-in-chief curation (pick focus + 5 highlights + tools)
"""

import os
import json
import logging
import re
from openai import OpenAI
from sources import NewsItem

logger = logging.getLogger(__name__)


class CurationError(RuntimeError):
    """Raised when the LLM cannot produce a publishable daily brief."""


# Stage 1: Score and classify
STAGE1_PROMPT = """You are an AI news analyst. Score and classify these items.

Respond in JSON:
{
  "items": [
    {
      "index": 0,
      "relevance": "core",
      "category": "tool",
      "importance": 8,
      "topic_key": "gpt-5.5-release"
    }
  ]
}

Rules:
- relevance: "core" (directly about AI/ML/dev-tools) | "adjacent" (tech industry or developer-adjacent) | "off-topic" (unrelated to AI/developers)
- category: "product" | "tool" | "research" | "industry" | "tutorial"
- importance: 1-10 (impact on AI developers; off-topic items MUST receive importance ≤ 2, adjacent items typically 3-5)
- topic_key: short identifier for the topic (same key = same event across sources)"""

# Stage 2: Editor-in-chief curation
STAGE2_PROMPT = """你是一位面向开发者的 AI 技术日报主编。从候选新闻中策展今日简报。

要求：
1. 选 1 条作为"今日焦点"，提供 18 字以内的中文短标题；编辑评论严格写成"事实：……；影响：……"，只写候选信息能支持的内容
2. 候选不少于 6 条时，恰好选 5 条作为"热点速览"；每条提供 18 字以内的中文短标题，点评用 1 句写清"事实和影响"，控制在 50 字以内
3. 选 0-2 个作为"今日工具"（优先开源项目，不要和焦点/速览重复）。只有能从来源或标题摘要说明"为什么今天入选"时才选择；理由严格写成"入选依据：来自今日实际来源名，……；用途：……"，不能只写常规简介
4. 行业数据（可选）：仅提取未进入焦点/速览/工具的候选中，标题或摘要明确给出的、能回答"什么指标、多少、什么单位、在什么语境下"的数据（最多 3 条）。日期、月份、内部评分、importance 和缺少单位的数字都不是行业数据。没有则输出空数组

选稿标准：
- 重大模型发布/技术突破 > 工具更新 > 行业分析
- 全球影响力大的事件优先作为焦点
- importance 分数高的优先
- 避免同一事件重复占位
- 每条入选内容必须不点链接也能理解：写清具体对象、动作或结果及其影响
- 禁止用"两个设置、两项案例、该研究、这一方法"等指代词代替关键信息；候选信息不足以说明具体内容时不要入选，也不要补写猜测
- 工具区不要选已经出现在焦点或速览中的条目
- 面向普通中文读者，不默认读者了解英文缩写或专业术语；无法避免时用短语解释
- 生僻缩写或英文术语首次出现时，用 4-8 个中文字补充含义
- 中文和英文或数字相邻时留一个空格，例如"评估 GPT-5.6 成本"
- 避免"值得关注、释放信号、敲响警钟、预示未来"等空泛套话，优先写可验证的变化、影响对象和行动含义

Respond in JSON:
{
  "focus": {
    "index": 0,
    "title_zh": "中文短标题",
    "editorial": "事实：……；影响：……"
  },
  "highlights": [
    {
      "index": 1,
      "title_zh": "中文短标题",
      "editorial": "1句具体点评，50字以内"
    }
  ],
  "tools": [
    {
      "index": 5,
      "title_zh": "中文短标题",
      "reason": "入选依据：来自今日 GitHub Trending；用途：适合……"
    }
  ],
  "industry_data": [
    {
      "metric": "融资规模",
      "value": "410",
      "unit": "百万美元",
      "context": "某公司完成 B 轮融资",
      "source_index": 3
    }
  ]
}"""


def create_client(config: dict) -> OpenAI:
    """Create OpenAI client with DuckCoding relay config."""
    return OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=(
            os.environ.get("OPENAI_BASE_URL")
            or config.get("base_url")
            or "https://api.duckcoding.ai/v1"
        ),
        timeout=120.0,
    )


def _run_stage1(items: list[NewsItem], config: dict) -> list[dict]:
    """Stage 1: Score, classify, and assign topic keys."""
    client = create_client(config)
    model = os.environ.get("AI_NEWS_MODEL", config.get("model", "gpt-5.4"))
    scored = []
    batch_size = 20

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        batch_text = "\n".join(
            f"[{j}] {item.title} | source={item.source} | score={item.score} | "
            f"url={item.url}"
            for j, item in enumerate(batch)
        )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": STAGE1_PROMPT},
                    {"role": "user", "content": batch_text},
                ],
                max_tokens=config.get("max_tokens", 1024),
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)
            entries = result.get("items")
            expected_indices = set(range(len(batch)))
            if (
                not isinstance(entries, list)
                or len(entries) != len(batch)
                or {
                    entry.get("index")
                    for entry in entries
                    if isinstance(entry, dict)
                } != expected_indices
            ):
                raise CurationError(
                    f"Stage1 batch {i//batch_size + 1} returned incomplete scoring"
                )

            for entry in entries:
                idx = entry.get("index", 0)
                importance = entry.get("importance", 0)
                if (
                    type(idx) is not int
                    or type(importance) is not int
                    or not 1 <= importance <= 10
                    or entry.get("category") not in {
                        "product", "tool", "research", "industry", "tutorial"
                    }
                    or not isinstance(entry.get("topic_key"), str)
                    or not entry["topic_key"].strip()
                ):
                    raise CurationError(
                        f"Stage1 batch {i//batch_size + 1} returned invalid scoring"
                    )

                if idx < len(batch) and importance >= 3:
                    item = batch[idx]
                    scored.append({
                        "title": item.title,
                        "url": item.url,
                        "source": item.source,
                        "score": item.score,
                        "published": item.published.isoformat(),
                        "summary": (item.summary or "")[:200],
                        "category": entry.get("category", "tool"),
                        "importance": importance,
                        "topic_key": entry.get("topic_key", f"item-{i+idx}"),
                        "tags": item.tags,
                    })

            logger.info(f"Stage1 batch {i//batch_size + 1}: "
                        f"{len(batch)} → {sum(1 for e in entries if e.get('importance', 0) >= 3)} scored (importance≥3)")

        except CurationError:
            raise
        except Exception as e:
            raise CurationError(
                f"Stage1 batch {i//batch_size + 1} failed: {e}"
            ) from e

    scored.sort(key=lambda x: (-x["importance"], -x["score"]))
    return scored


def _cluster_and_select_candidates(scored: list[dict], max_candidates: int = 20) -> list[dict]:
    """Cluster by topic_key, keep best per topic, return top candidates."""
    topic_best: dict[str, dict] = {}
    topic_all: dict[str, list[dict]] = {}

    for item in scored:
        key = item["topic_key"]
        topic_all.setdefault(key, []).append(item)
        if key not in topic_best or item["importance"] > topic_best[key]["importance"]:
            topic_best[key] = item

    # Attach related sources to each best item
    candidates = []
    for key, best in topic_best.items():
        related = [it for it in topic_all[key] if it["url"] != best["url"]]
        best["related_sources"] = [
            {"title": r["title"], "url": r["url"], "source": r["source"]}
            for r in related[:3]
        ]
        candidates.append(best)

    candidates.sort(key=lambda x: (-x["importance"], -x["score"]))

    # Enforce source diversity: max 5 items per source in candidates
    source_count: dict[str, int] = {}
    diverse = []
    for item in candidates:
        src = item["source"]
        source_count[src] = source_count.get(src, 0) + 1
        if source_count[src] <= 5:
            diverse.append(item)
        if len(diverse) >= max_candidates:
            break

    logger.info(f"Clustering: {len(scored)} scored → {len(topic_best)} topics → {len(diverse)} candidates")
    return diverse


def _run_stage2(candidates: list[dict], config: dict) -> dict:
    """Stage 2: Editor-in-chief curation."""
    if not candidates:
        raise CurationError("Stage1 produced no publishable candidates")

    client = create_client(config)
    model = os.environ.get("AI_NEWS_MODEL", config.get("model", "gpt-5.4"))

    candidate_text = "\n".join(
        f"[{i}] {item['title']} | source={item['source']} | "
        f"category={item['category']} | importance={item['importance']} | "
        f"related={len(item.get('related_sources', []))} sources"
        + (f"\n    摘要: {item['summary']}" if item.get("summary") else "")
        for i, item in enumerate(candidates)
    )

    try:
        # Use editorial model (higher quality) for Stage 2
        editorial_model = config.get("model_editorial", model)
        response = client.chat.completions.create(
            model=editorial_model,
            messages=[
                {"role": "system", "content": STAGE2_PROMPT},
                {"role": "user", "content": f"今日候选新闻（{len(candidates)} 条）:\n\n{candidate_text}"},
            ],
            max_tokens=4096,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        brief = json.loads(response.choices[0].message.content)
        _validate_brief(brief, candidates)
        logger.info(f"Stage2: focus={brief.get('focus', {}).get('index')}, "
                     f"highlights={len(brief.get('highlights', []))}, "
                     f"tools={len(brief.get('tools', []))}")
        return brief

    except CurationError:
        raise
    except Exception as e:
        raise CurationError(f"Stage2 curation failed: {e}") from e


def _validate_brief(brief: dict, candidates: list[dict]):
    """Reject incomplete or non-Chinese editorial output before publication."""
    if not isinstance(brief, dict):
        raise CurationError("Stage2 returned an invalid brief")

    candidate_count = len(candidates)

    def valid_index(value) -> bool:
        return type(value) is int and 0 <= value < candidate_count

    def readable_chinese(value) -> bool:
        return (
            isinstance(value, str)
            and bool(value.strip())
            and any("\u4e00" <= char <= "\u9fff" for char in value)
        )

    def readable_title(value) -> bool:
        return readable_chinese(value) and len(value.strip()) <= 30

    opaque_phrases = (
        "两个设置", "两项设置", "两个案例", "两项案例",
        "该研究", "这项研究", "这一方法", "该方法",
        "这条进展", "这项变化", "该事件",
    )

    def concrete_editorial(value) -> bool:
        return (
            readable_chinese(value)
            and not any(phrase in value for phrase in opaque_phrases)
        )

    focus = brief.get("focus")
    if (
        not isinstance(focus, dict)
        or not valid_index(focus.get("index"))
        or not readable_title(focus.get("title_zh"))
        or not concrete_editorial(focus.get("editorial"))
        or not focus["editorial"].strip().startswith("事实：")
        or "影响：" not in focus["editorial"]
    ):
        raise CurationError("Stage2 returned an unusable focus editorial")

    highlights = brief.get("highlights")
    expected_highlights = min(5, max(candidate_count - 1, 0))
    if (
        not isinstance(highlights, list)
        or len(highlights) != expected_highlights
    ):
        raise CurationError("Stage2 returned an invalid highlight selection")

    selected_indices = {focus["index"]}
    for highlight in highlights:
        if (
            not isinstance(highlight, dict)
            or not valid_index(highlight.get("index"))
            or highlight["index"] in selected_indices
            or not readable_title(highlight.get("title_zh"))
            or not concrete_editorial(highlight.get("editorial"))
        ):
            raise CurationError("Stage2 returned an unusable highlight editorial")
        selected_indices.add(highlight["index"])

    tools = brief.get("tools", [])
    if not isinstance(tools, list) or len(tools) > 2:
        raise CurationError("Stage2 returned an invalid tool selection")
    for tool in tools:
        if (
            not isinstance(tool, dict)
            or not valid_index(tool.get("index"))
            or tool["index"] in selected_indices
            or candidates[tool["index"]].get("category") != "tool"
            or not readable_title(tool.get("title_zh"))
            or not readable_chinese(tool.get("reason"))
            or "入选依据" not in tool["reason"]
            or "用途" not in tool["reason"]
            or candidates[tool["index"]].get("source") not in tool["reason"]
        ):
            raise CurationError("Stage2 returned an unusable tool recommendation")
        selected_indices.add(tool["index"])

    # Optional data should disappear when it cannot be traced to a candidate.
    # This keeps a malformed optional section from blocking the whole brief.
    industry_data = brief.get("industry_data") or []
    if not isinstance(industry_data, list):
        industry_data = []
    date_units = {
        "年", "月", "日", "季度", "月份", "日期",
        "year", "years", "month", "months", "day", "days",
    }
    validated_data = []
    for item in industry_data[:3]:
        if (
            not isinstance(item, dict)
            or not valid_index(item.get("source_index"))
            or item["source_index"] in selected_indices
        ):
            continue
        metric = item.get("metric")
        value = item.get("value")
        unit = item.get("unit")
        context = item.get("context")
        if (
            not readable_chinese(metric)
            or not isinstance(value, str)
            or not value.strip()
            or not isinstance(unit, str)
            or not unit.strip()
            or unit.strip().lower() in date_units
            or not readable_chinese(context)
        ):
            continue

        numeric_tokens = {
            token.replace(",", "")
            for token in re.findall(r"\d+(?:[.,]\d+)*", value)
        }
        source = candidates[item["source_index"]]
        source_text = f"{source.get('title', '')} {source.get('summary', '')}"
        source_numbers = {
            token.replace(",", "")
            for token in re.findall(r"\d+(?:[.,]\d+)*", source_text)
        }
        if not numeric_tokens or numeric_tokens.isdisjoint(source_numbers):
            continue
        validated_data.append(item)
    brief["industry_data"] = validated_data


def curate_daily_brief(items: list[NewsItem], config: dict) -> dict:
    """
    Full two-stage curation pipeline.

    Returns:
        {
            "candidates": [...],  # all scored candidates
            "brief": {            # editor's selections
                "focus": {...},
                "highlights": [...],
                "tools": [...],
                "industry_data": [...]
            }
        }
    """
    logger.info("=== Stage 1: Score & Classify ===")
    scored = _run_stage1(items, config)

    logger.info("=== Clustering & Candidate Selection ===")
    candidates = _cluster_and_select_candidates(scored)

    logger.info("=== Stage 2: Editorial Curation ===")
    brief = _run_stage2(candidates, config)

    return {"candidates": candidates, "brief": brief}
