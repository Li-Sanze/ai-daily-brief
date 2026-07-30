import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sources import NewsItem
from summarizer import (
    CurationError,
    _run_stage1,
    _run_stage2,
    create_client,
)


def completion(payload: dict):
    message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def candidate(index: int) -> dict:
    return {
        "title": f"News {index}",
        "url": f"https://example.com/{index}",
        "source": "Test",
        "score": 10 - index,
        "published": datetime.now(timezone.utc).isoformat(),
        "summary": "摘要",
        "category": "research",
        "importance": 8,
        "topic_key": f"topic-{index}",
        "tags": [],
        "related_sources": [],
    }


class SummarizerTest(unittest.TestCase):
    def test_create_client_prefers_environment_base_url(self):
        with (
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "https://env.example/v1",
                },
            ),
            patch("summarizer.OpenAI") as openai,
        ):
            create_client({"base_url": "https://config.example/v1"})

        openai.assert_called_once_with(
            api_key="test-key",
            base_url="https://env.example/v1",
            timeout=120.0,
        )

    def test_stage1_raises_instead_of_publishing_fallback_data(self):
        item = NewsItem(
            title="Test news",
            url="https://example.com/news",
            source="Test",
        )
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("rate limited")

        with (
            patch("summarizer.create_client", return_value=client),
            self.assertRaisesRegex(CurationError, "rate limited"),
        ):
            _run_stage1([item], {})

    def test_stage1_rejects_incomplete_scoring(self):
        items = [
            NewsItem(title=f"News {index}", url=f"https://example.com/{index}", source="Test")
            for index in range(2)
        ]
        client = MagicMock()
        client.chat.completions.create.return_value = completion({
            "items": [{
                "index": 0,
                "category": "research",
                "importance": 8,
                "topic_key": "topic-0",
            }]
        })

        with (
            patch("summarizer.create_client", return_value=client),
            self.assertRaisesRegex(CurationError, "incomplete scoring"),
        ):
            _run_stage1(items, {})

    def test_stage2_rejects_empty_editorial(self):
        candidates = [candidate(index) for index in range(6)]
        client = MagicMock()
        client.chat.completions.create.return_value = completion({
            "focus": {
                "index": 0,
                "title_zh": "焦点新闻",
                "editorial": "事实：发生了变化；影响：需要评估。",
            },
            "highlights": [
                {
                    "index": index,
                    "title_zh": f"速览新闻{index}",
                    "editorial": "",
                }
                for index in range(1, 6)
            ],
            "tools": [],
        })

        with (
            patch("summarizer.create_client", return_value=client),
            self.assertRaisesRegex(CurationError, "highlight editorial"),
        ):
            _run_stage2(candidates, {})

    def test_stage2_accepts_publishable_editorials(self):
        candidates = [candidate(index) for index in range(7)]
        candidates[6]["category"] = "tool"
        brief = {
            "focus": {
                "index": 0,
                "title_zh": "模型选择发生变化",
                "editorial": "事实：新模型降低了推理延迟；影响：开发团队可重新评估生产选型。",
            },
            "highlights": [
                {
                    "index": index,
                    "title_zh": f"开发进展{index}",
                    "editorial": "新模型降低了推理延迟，开发团队可减少线上等待时间。",
                }
                for index in range(1, 6)
            ],
            "tools": [
                {
                    "index": 6,
                    "title_zh": "本地部署工具",
                    "reason": "入选依据：来自今日 Test 候选，摘要明确给出新版本；用途：适合需要本地部署能力的团队试用。",
                }
            ],
            "industry_data": [],
        }
        client = MagicMock()
        client.chat.completions.create.return_value = completion(brief)

        with patch("summarizer.create_client", return_value=client):
            result = _run_stage2(candidates, {})

        self.assertEqual(result, brief)

    def test_stage2_rejects_selection_without_chinese_title(self):
        candidates = [candidate(index) for index in range(6)]
        brief = {
            "focus": {
                "index": 0,
                "title_zh": "English only",
                "editorial": "事实：发生了变化；影响：需要评估。",
            },
            "highlights": [
                {
                    "index": index,
                    "title_zh": f"速览新闻{index}",
                    "editorial": "新模型降低了推理延迟，可能影响部署成本。",
                }
                for index in range(1, 6)
            ],
            "tools": [],
        }
        client = MagicMock()
        client.chat.completions.create.return_value = completion(brief)

        with (
            patch("summarizer.create_client", return_value=client),
            self.assertRaisesRegex(CurationError, "focus editorial"),
        ):
            _run_stage2(candidates, {})

    def test_stage2_rejects_generic_tool_reason(self):
        candidates = [candidate(index) for index in range(7)]
        candidates[6]["category"] = "tool"
        brief = {
            "focus": {
                "index": 0,
                "title_zh": "焦点新闻",
                "editorial": "事实：发生了变化；影响：需要评估。",
            },
            "highlights": [
                {
                    "index": index,
                    "title_zh": f"速览新闻{index}",
                    "editorial": "新模型降低了推理延迟，可能影响部署成本。",
                }
                for index in range(1, 6)
            ],
            "tools": [
                {
                    "index": 6,
                    "title_zh": "开源工具",
                    "reason": "适合开发团队试用。",
                }
            ],
        }
        client = MagicMock()
        client.chat.completions.create.return_value = completion(brief)

        with (
            patch("summarizer.create_client", return_value=client),
            self.assertRaisesRegex(CurationError, "tool recommendation"),
        ):
            _run_stage2(candidates, {})

    def test_stage2_rejects_opaque_highlight_explanation(self):
        candidates = [candidate(index) for index in range(6)]
        brief = {
            "focus": {
                "index": 0,
                "title_zh": "焦点新闻",
                "editorial": "事实：新模型降低了推理延迟；影响：开发团队可减少等待时间。",
            },
            "highlights": [
                {
                    "index": index,
                    "title_zh": f"速览新闻{index}",
                    "editorial": (
                        "两项案例提供了早期证据。"
                        if index == 1
                        else "新模型降低了推理延迟，可能影响部署成本。"
                    ),
                }
                for index in range(1, 6)
            ],
            "tools": [],
        }
        client = MagicMock()
        client.chat.completions.create.return_value = completion(brief)

        with (
            patch("summarizer.create_client", return_value=client),
            self.assertRaisesRegex(CurationError, "highlight editorial"),
        ):
            _run_stage2(candidates, {})

    def test_stage2_keeps_only_traceable_industry_data(self):
        candidates = [candidate(index) for index in range(8)]
        candidates[6]["summary"] = "该系统可以 32 Hz 的频率实时运行。"
        brief = {
            "focus": {
                "index": 0,
                "title_zh": "实时模型提速",
                "editorial": "事实：系统达到实时运行；影响：端侧部署门槛降低。",
            },
            "highlights": [
                {
                    "index": index,
                    "title_zh": f"速览新闻{index}",
                    "editorial": "新模型降低了推理延迟，可能影响部署成本。",
                }
                for index in range(1, 6)
            ],
            "tools": [],
            "industry_data": [
                {
                    "metric": "实时运行频率",
                    "value": "32",
                    "unit": "Hz",
                    "context": "该系统达到实时运行速度",
                    "source_index": 6,
                },
                {
                    "metric": "项目热度",
                    "value": "86381",
                    "unit": "分",
                    "context": "内部采集器给出的热度",
                    "source_index": 1,
                },
            ],
        }
        client = MagicMock()
        client.chat.completions.create.return_value = completion(brief)

        with patch("summarizer.create_client", return_value=client):
            result = _run_stage2(candidates, {})

        self.assertEqual(
            result["industry_data"],
            [brief["industry_data"][0]],
        )


if __name__ == "__main__":
    unittest.main()
