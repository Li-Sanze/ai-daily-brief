import tempfile
import unittest
from pathlib import Path

from outputs import format_daily_brief, write_archive


def candidate(index: int) -> dict:
    return {
        "title": f"Original English Title {index}",
        "url": f"https://example.com/{index}",
        "source": "Test",
        "summary": "摘要",
        "category": "research",
        "importance": 8,
        "topic_key": f"topic-{index}",
        "related_sources": [],
    }


def curation_result() -> dict:
    candidates = [candidate(index) for index in range(14)]
    candidates[6]["category"] = "tool"
    return {
        "candidates": candidates,
        "brief": {
            "focus": {
                "index": 0,
                "title_zh": "焦点中文标题",
                "editorial": "事实：模型已更新；影响：开发者需要重新评估选型。",
            },
            "highlights": [
                {
                    "index": index,
                    "title_zh": f"速览中文标题{index}",
                    "editorial": "新模型降低了推理延迟，可能影响部署成本和方式。",
                }
                for index in range(1, 6)
            ],
            "tools": [
                {
                    "index": 6,
                    "title_zh": "今日工具中文标题",
                    "reason": "入选依据：来自今日 Test 候选；用途：适合需要本地部署的团队试用。",
                }
            ],
        },
    }


class OutputsTest(unittest.TestCase):
    def test_daily_brief_prioritizes_explained_chinese_selections(self):
        content = format_daily_brief(
            curation_result(),
            {},
        )

        self.assertIn("[焦点中文标题]", content)
        self.assertIn("[速览中文标题1]", content)
        self.assertIn("[今日工具中文标题]", content)
        self.assertNotIn("Original English Title 0", content)
        self.assertNotIn("今日洞察", content)
        self.assertNotIn("专家观点", content)
        self.assertNotIn("## 📊 数据概览", content)
        self.assertNotIn("## 🔧 工作原理", content)
        self.assertNotIn("数据链路", content)
        self.assertNotIn("行业数据", content)
        self.assertNotIn("### 📎 延伸阅读", content)
        self.assertNotIn("Original English Title", content)
        self.assertNotIn("---\n\n\n---", content)

    def test_archive_hides_internal_candidates_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            write_archive(
                curation_result(),
                {"enabled": True, "directory": directory},
            )
            archive_path = next(Path(directory).glob("*.md"))
            content = archive_path.read_text(encoding="utf-8")

        self.assertIn("## 📌 焦点: [焦点中文标题]", content)
        self.assertIn("### [今日工具中文标题]", content)
        self.assertIn("<details>", content)
        self.assertIn("<summary>完整候选与内部评分（14 条）</summary>", content)
        self.assertNotIn("行业数据", content)
        self.assertNotIn("- **Category**:", content)
        self.assertNotIn("- **Importance**:", content)

if __name__ == "__main__":
    unittest.main()
