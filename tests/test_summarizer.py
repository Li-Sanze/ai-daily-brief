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
            "focus": {"index": 0, "editorial": "这条新闻值得关注。"},
            "highlights": [
                {"index": index, "editorial": ""}
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
        brief = {
            "focus": {
                "index": 0,
                "editorial": "这项变化会影响开发者的模型选择，建议先在测试环境验证。",
            },
            "highlights": [
                {
                    "index": index,
                    "editorial": "这条进展关系到开发成本与落地可靠性。",
                }
                for index in range(1, 6)
            ],
            "tools": [
                {
                    "index": 6,
                    "reason": "适合需要本地部署能力的开发团队试用。",
                }
            ],
        }
        client = MagicMock()
        client.chat.completions.create.return_value = completion(brief)

        with patch("summarizer.create_client", return_value=client):
            result = _run_stage2(candidates, {})

        self.assertEqual(result, brief)


if __name__ == "__main__":
    unittest.main()
