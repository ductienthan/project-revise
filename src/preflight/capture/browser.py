from preflight.capture.fetch import NeedsJavaScript


def render(url: str) -> str:
    """Render a JavaScript page with Playwright, when the optional browser extra is installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise NeedsJavaScript(
            "This page needs JavaScript. Install the browser extra (preflight[browser]) to capture it."
        ) from None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30_000)
            return page.content()
        finally:
            browser.close()
