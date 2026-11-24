"""A shared Chrome process for the listing scout's sequential public-page reads."""
from contextlib import contextmanager
from urllib.error import HTTPError

from .pages import Blocked, CHALLENGE_TITLES, Page, USER_AGENT


@contextmanager
def browser_loader():
    from patchright.sync_api import Error, sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True, args=["--enable-automation"],
                                         ignore_default_args=["--disable-blink-features=AutomationControlled"])
        try:
            context = browser.new_context(user_agent=USER_AGENT, accept_downloads=False, service_workers="block")
            # Search cards contain gallery addresses. Loading image pixels is a separate photo-analysis job.
            context.route("**/*", lambda route: route.abort() if route.request.resource_type in {"image", "media", "font"}
                          else route.continue_())

            def load(url):
                page = context.new_page()
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    if response is None:
                        raise OSError("Missing browser navigation response")
                    status = response.status
                    if status in (403, 429):
                        raise Blocked(f"HTTP {status} from {url}", status)
                    if status >= 400:
                        raise HTTPError(url, status, response.status_text, {}, None)
                    if response.url != url:
                        raise ValueError("Listing page redirected")
                    # Keep the delivered HTML: adapters also read embedded state and publisher-specific markup
                    # whose quoting and whitespace can change when Chrome serializes the DOM.
                    body = response.body()
                    if len(body) > 8_000_000:
                        raise ValueError("Listing page exceeds size limit")
                    html = body.decode("utf-8", "replace")
                    if (status == 202 and not body) or CHALLENGE_TITLES.search(html[:20000]):
                        raise Blocked(f"Bot-check page from {url}", status)
                    return Page(html)
                except Error as exc:
                    raise OSError(f"Browser read failed for {url}: {exc}") from exc
                finally:
                    page.close()

            yield load
        finally:
            browser.close()
