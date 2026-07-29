"""
AI News Aggregator - Two-Stage Curation Pipeline

Stage 1: Score + classify + cluster similar topics
Stage 2: Editor-in-chief curation (pick 5-10, write editorial commentary)
"""

import os
import json
import logging
from openai import OpenAI
from sources import NewsItem

logger = logging.getLogger(__name__)

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
1. 选 1 条作为"今日焦点"，写 2 句编辑评论（第 1 句说为什么重要，第 2 句给开发者行动建议）
2. 选 5-8 条作为"热点速览"，每条写 1 句编辑观点（不是摘要，是"为什么开发者应该关注"，控制在 50 字以内）
3. 选 1-2 个作为"今日工具"（优先开源项目，不要和焦点/速览重复），写 1 句推荐理由
4. 提取或创作 1 条与 AI 相关的金句
5. 标记哪些候选条目是同一事件的补充来源
6. 行业数据（可选）：仅当候选新闻的标题或摘要中明确出现具体数字（融资金额、交易规模、用户量等）时，提取为 industry_data（最多 3 条）。数字必须来自原文，禁止推测或编造。没有则输出空数组
7. 技术趋势（可选）：仅当 2 条以上候选新闻反映同一技术方向时，归纳为 tech_trends（最多 2 条）。这是跨条目归纳，不是单条新闻复述，不要重复焦点/速览已表达的单条内容。没有则输出空数组
8. 专家观点（可选）：仅当标题或摘要中明确包含具名人物的直接引言时，提取为 expert_quotes（最多 2 条）。禁止创作或改写引言。没有则输出空数组

选稿标准：
- 重大模型发布/技术突破 > 工具更新 > 行业分析
- 全球影响力大的事件优先作为焦点
- importance 分数高的优先
- 避免同一事件重复占位，用"延伸阅读"聚合
- 工具区不要选已经出现在焦点或速览中的条目

Respond in JSON:
{
  "focus": {
    "index": 0,
    "editorial": "2句精炼评论..."
  },
  "highlights": [
    {
      "index": 1,
      "editorial": "1句编辑观点，50字以内"
    }
  ],
  "tools": [
    {
      "index": 5,
      "reason": "1句推荐理由"
    }
  ],
  "quote": "一句金句...",
  "industry_data": [
    {
      "event": "某公司完成 B 轮融资",
      "value": "$410M",
      "source_index": 3
    }
  ],
  "tech_trends": [
    {
      "trend": "趋势名（8字以内）",
      "description": "1-2句跨条目归纳...",
      "related_indices": [0, 5]
    }
  ],
  "expert_quotes": [
    {
      "person": "人名",
      "title": "身份",
      "quote": "原文引言...",
      "source_index": 7
    }
  ],
  "related_groups": [
    {
      "primary_index": 0,
      "related_indices": [3, 7],
      "topic": "GPT-5.5 发布"
    }
  ]
}"""


def create_client(config: dict) -> OpenAI:
    """Create OpenAI client with DuckCoding relay config."""
    return OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=config.get("base_url", "https://api.duckcoding.ai/v1"),
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
            for entry in result.get("items", []):
                idx = entry.get("index", 0)
                importance = entry.get("importance", 0)
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
                        f"{len(batch)} → {sum(1 for e in result.get('items', []) if e.get('importance', 0) >= 3)} scored (importance≥3)")

        except Exception as e:
            logger.error(f"Stage1 batch {i//batch_size + 1} failed: {e}")
            for item in batch:
                scored.append({
                    "title": item.title,
                    "url": item.url,
                    "source": item.source,
                    "score": item.score,
                    "published": item.published.isoformat(),
                    "summary": (item.summary or "")[:200],
                    "category": "tool",
                    "importance": 5,
                    "topic_key": f"fallback-{len(scored)}",
                    "tags": item.tags,
                })

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
    client = create_client(config)
    model = os.environ.get("AI_NEWS_MODEL", config.get("model", "gpt-5.4"))

    candidate_text = "\n".join(
        f"[{i}] {item['title']} | source={item['source']} | score={item['score']} | "
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
        logger.info(f"Stage2: focus={brief.get('focus', {}).get('index')}, "
                     f"highlights={len(brief.get('highlights', []))}, "
                     f"tools={len(brief.get('tools', []))}")
        return brief

    except Exception as e:
        logger.error(f"Stage2 curation failed: {e}")
        # Fallback: first item as focus, next 5 as highlights
        return {
            "focus": {"index": 0, "editorial": candidates[0]["title"] if candidates else ""},
            "highlights": [{"index": i, "editorial": ""} for i in range(1, min(6, len(candidates)))],
            "tools": [],
            "quote": "",
            "industry_data": [],
            "tech_trends": [],
            "expert_quotes": [],
            "related_groups": [],
        }


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
                "quote": "...",
                "related_groups": [...]
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
