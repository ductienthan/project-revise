from collections.abc import Callable

from preflight.capture.fetch import CaptureError, NeedsJavaScript


def render(url: str, allowed: Callable[[str], object]) -> str:
    """Render a JavaScript page with Playwright. Every request the page makes passes `allowed` first.

    The browser resolves DNS itself, so this check cannot pin addresses the way Fetcher does. That is why the
    fallback is off unless PREFLIGHT_CAPTURE_BROWSER is set.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise NeedsJavaScript(
            "This page needs JavaScript. Install the browser extra (preflight[browser]) to capture it."
        ) from None

    def guard(route):
        try:
            allowed(route.request.url)
        except CaptureError:
            route.abort()
            return
        route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", guard)
            page.goto(url, wait_until="networkidle", timeout=30_000)
            return page.content()
        finally:
            browser.close()
