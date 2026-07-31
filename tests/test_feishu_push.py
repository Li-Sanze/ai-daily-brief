import os
import unittest
from unittest.mock import patch

from feishu_push import clean_markdown


class FeishuPushTest(unittest.TestCase):
    def test_clean_markdown_resolves_repository_footer_links(self):
        footer = "[往期简报](./archives/) · [项目说明](./README.md)"

        with patch.dict(
            os.environ,
            {"GITHUB_REPOSITORY": "statefulai/ai-daily-brief"},
        ):
            content = clean_markdown(footer)

        self.assertIn(
            "[往期简报](https://github.com/statefulai/ai-daily-brief/tree/main/archives)",
            content,
        )
        self.assertIn(
            "[项目说明](https://github.com/statefulai/ai-daily-brief/blob/main/README.md)",
            content,
        )
        self.assertNotIn("](./", content)


if __name__ == "__main__":
    unittest.main()
