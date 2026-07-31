"""Feishu (Lark) webhook bot push — reads daily-brief.md and sends as interactive card.

Usage:
    FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx python feishu_push.py

Environment variables:
    FEISHU_WEBHOOK_URL   - Required. Webhook URL from Feishu custom bot.
    FEISHU_WEBHOOK_SECRET - Optional. Signing secret for webhook verification.
    GITHUB_REPOSITORY    - Auto-provided by GitHub Actions. Used for button URL.
    BRIEF_URL            - Optional. Override button URL (for local testing).
"""

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import httpx
except ImportError:
    print("error: httpx is required. Install with: pip install httpx", file=sys.stderr)
    sys.exit(1)

BRIEF_PATH = os.environ.get("BRIEF_PATH", "daily-brief.md")
BEIJING_TZ = timezone(timedelta(hours=8))


def read_brief() -> str | None:
    try:
        with open(BRIEF_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"⚠️ {BRIEF_PATH} not found, skipping push.", file=sys.stderr)
        return None


def check_date(content: str) -> bool:
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    if f"## 📅 {today}" not in content:
        print(f"⚠️ Brief date does not match today ({today}), skipping push.", file=sys.stderr)
        return False
    return True


def extract_date_line(content: str) -> str:
    m = re.search(r"## 📅 (\d{4}-\d{2}-\d{2})\s*(.+)?", content)
    if m:
        return f"{m.group(1)} {(m.group(2) or '').strip()}"
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")


def clean_markdown(text: str) -> str:
    """Remove Feishu-incompatible markdown elements."""
    # Remove badge images: [![alt](img)](url)
    text = re.sub(r"\[!\[.*?\]\(.*?\)\]\(.*?\)", "", text)
    # Remove standalone images: ![alt](url)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # Convert blockquotes to 「」(Feishu cards don't support >)
    text = re.sub(r"^>\s*(.+)$", r"「\1」", text, flags=re.MULTILINE)
    # Convert inline code `source` to 「source」for better card readability
    text = re.sub(r"`([^`]+)`", r"「\1」", text)
    # Feishu cannot resolve repository-relative Markdown links.
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repository:
        text = text.replace(
            "](./archives/)",
            f"](https://github.com/{repository}/tree/main/archives)",
        )
        text = text.replace(
            "](./README.md)",
            f"](https://github.com/{repository}/blob/main/README.md)",
        )
    # Convert markdown tables to key:value text
    lines = text.split("\n")
    result = []
    in_table = False
    headers = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                in_table = True
                continue
            if not in_table:
                headers = cells
                in_table = True
                continue
            if headers and len(cells) == len(headers):
                pairs = [f"{h}: {v}" for h, v in zip(headers, cells) if v]
                result.append(" · ".join(pairs))
            else:
                result.append(" · ".join(cells))
        else:
            in_table = False
            headers = []
            result.append(line)
    return "\n".join(result).strip()


def parse_sections(content: str) -> list[dict]:
    """Parse daily-brief.md into sections by ### headers."""
    # Remove header and footer sections
    # Find first ### marker
    first_section = content.find("\n### ")
    if first_section == -1:
        return []

    # Find the footer (## 📊 数据概览)
    footer_start = content.find("\n## 📊 数据概览")
    body = content[first_section:footer_start] if footer_start != -1 else content[first_section:]

    parts = re.split(r"\n### ", body)
    sections = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.split("\n", 1)
        title = lines[0].strip()
        body_text = lines[1].strip() if len(lines) > 1 else ""
        # Remove trailing --- separators
        body_text = re.sub(r"\n---\s*$", "", body_text).strip()
        sections.append({"title": title, "body": clean_markdown(body_text)})
    return sections


def extract_stats(content: str) -> str | None:
    """Extract stats line from ## 📊 数据概览 section."""
    m = re.search(
        r"\|\s*(\d+)\s*源\s*\|\s*(\d+)\s*篇\s*\|\s*(\d+)\s*篇\s*\|\s*(\d+)\s*篇\s*\|\s*\**(\d+)\s*篇\**\s*\|",
        content,
    )
    if m:
        return f"📊 {m.group(1)}源 · {m.group(2)}篇 → {m.group(5)}精选"

    gen_m = re.search(r"\*生成于 (.+?)\*", content)
    gen_time = gen_m.group(1) if gen_m else ""
    if gen_time:
        return f"📊 {gen_time}"
    return None


def get_brief_url() -> str | None:
    explicit = os.environ.get("BRIEF_URL")
    if explicit:
        return explicit
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return f"https://github.com/{repo}/blob/main/daily-brief.md"
    return None


def build_card(content: str) -> dict:
    """Build Feishu interactive card from daily-brief.md content."""
    date_line = extract_date_line(content)
    sections = parse_sections(content)
    stats = extract_stats(content)
    brief_url = get_brief_url()

    elements = []
    for i, section in enumerate(sections):
        if i > 0:
            elements.append({"tag": "hr"})
        body = section["body"]
        # 延伸阅读: limit to 5 items, add spacing between items
        if "延伸阅读" in section["title"]:
            body_lines = body.split("\n")
            items = [l for l in body_lines if l.strip().startswith("- ")]
            total = len(items)
            if total > 5:
                body = "\n".join(body_lines[:body_lines.index(items[5])])
            # Add spacing between list items for readability
            body = re.sub(r"\n(- )", r"\n\n\1", body)
            if total > 5:
                body += f"\n\n*…共 {total} 条，查看完整简报了解更多*"
        md = f"**{section['title']}**\n\n{body}"
        elements.append({"tag": "markdown", "content": md})

    # Truncate content sections if oversized (~30KB limit), before adding footer
    temp_card = {"msg_type": "interactive", "card": {"header": {}, "elements": elements}}
    card_json = json.dumps(temp_card, ensure_ascii=False)
    truncated = False
    while len(card_json.encode("utf-8")) > 24000 and len(elements) > 2:
        elements.pop(-1)
        if elements and elements[-1].get("tag") == "hr":
            elements.pop(-1)
        truncated = True
        card_json = json.dumps(temp_card, ensure_ascii=False)
    if truncated:
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "⚠️ 内容已截断，请查看完整简报"}],
        })

    # Footer: stats note + button (always kept, not subject to truncation)
    if stats:
        elements.append({"tag": "hr"})
        gen_m = re.search(r"\*生成于 (.+?)\*", content)
        gen_time = f" | 生成于 {gen_m.group(1)}" if gen_m else ""
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": f"{stats}{gen_time}"}],
        })

    if brief_url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📰 查看完整简报"},
                "url": brief_url,
                "type": "primary",
            }],
        })

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🤖 AI Daily Brief · {date_line}"},
                "template": "blue",
            },
            "elements": elements,
        },
    }

    card_json = json.dumps(card, ensure_ascii=False)
    if len(card_json.encode("utf-8")) > 28000:
        print(f"⚠️ Card size {len(card_json.encode('utf-8'))} bytes, may exceed limit.", file=sys.stderr)

    return card


def sign_webhook(secret: str) -> tuple[str, str]:
    """Generate timestamp + signature for signed webhooks."""
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return timestamp, sign


def post_to_feishu(webhook_url: str, card: dict, secret: str | None = None) -> bool:
    """POST card to Feishu webhook. Returns True on success."""
    for attempt in range(2):
        payload = card.copy()
        if secret:
            timestamp, sign = sign_webhook(secret)
            payload["timestamp"] = timestamp
            payload["sign"] = sign

        try:
            resp = httpx.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            data = resp.json()
            if data.get("StatusCode") == 0 or data.get("code") == 0:
                print(f"✅ Feishu push succeeded.", file=sys.stderr)
                return True
            msg = data.get("StatusMessage") or data.get("msg") or str(data)
            print(f"❌ Feishu returned error: {msg}", file=sys.stderr)
            return False
        except Exception as e:
            if attempt == 0:
                print(f"⚠️ POST failed ({e}), retrying in 5s...", file=sys.stderr)
                time.sleep(5)
            else:
                print(f"❌ POST failed after retry: {e}", file=sys.stderr)
                return False
    return False


def main():
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("⚠️ FEISHU_WEBHOOK_URL not set, skipping push.", file=sys.stderr)
        sys.exit(0)

    content = read_brief()
    if content is None:
        sys.exit(0)

    if not check_date(content):
        sys.exit(0)

    card = build_card(content)
    secret = os.environ.get("FEISHU_WEBHOOK_SECRET", "").strip() or None

    if not post_to_feishu(webhook_url, card, secret):
        sys.exit(1)


if __name__ == "__main__":
    main()
