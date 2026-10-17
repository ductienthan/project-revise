import re
from dataclasses import dataclass

import trafilatura

from preflight.capture.fetch import LoginRequired, NeedsJavaScript

MIN_TEXT_CHARS = 200
PASSWORD_FIELD = re.compile(r"<input[^>]+type=[\"']?password", re.IGNORECASE)
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Extracted:
    title: str
    text: str


def extract(html: str, url: str) -> Extracted:
    """Main text with headings and tables as Markdown. Raises when the page is a login form or a JavaScript shell."""
    text = trafilatura.extract(html, url=url, output_format="markdown", include_tables=True, favor_recall=True) or ""
    if PASSWORD_FIELD.search(html) and len(text) < 2000:
        raise LoginRequired("This page needs a login, so it was saved as a reference only.")
    if len(text) < MIN_TEXT_CHARS:
        raise NeedsJavaScript("This page has almost no text without JavaScript.")
    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "") or _title_tag(html)
    return Extracted(title.strip(), text)


def _title_tag(html: str) -> str:
    match = TITLE_TAG.search(html)
    return match.group(1).strip() if match else ""
