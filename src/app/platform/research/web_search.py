"""Fresh web retrieval for assistant knowledge context."""
from __future__ import annotations

from app.config.env import env_str, environment

import os
import re
from collections.abc import Callable
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
import logging

from app.conversation.contracts import AssistantContextItem

_DEFAULT_TIMEOUT_SECONDS = 8.0
_LOGGER = logging.getLogger(__name__)
_AUTO_SEARCH_PATTERNS = (
    r"\b(search|look up|find online|browse|on the web|internet)\b",
    r"\b(latest|current|today|tonight|yesterday|tomorrow|recent|newest|news)\b",
    r"\b(price|release date|schedule|score|weather|forecast|exchange rate|stock)\b",
    r"\b(who is the current|what is the current|is .* still)\b",
)


def should_search_automatically(query: str) -> bool:
    normalized = " ".join(query.lower().split())
    return any(re.search(pattern, normalized) for pattern in _AUTO_SEARCH_PATTERNS)


def _search_query_variants(query: str, *, today: date) -> list[str]:
    clean = " ".join(query.split()).strip()
    if not clean:
        return []
    variants = [clean]
    normalized = clean.casefold()
    date_label = today.strftime("%B %-d, %Y") if os.name != "nt" else today.strftime("%B %#d, %Y")
    if re.search(r"\b(today|tonight|yesterday|tomorrow|current|latest|recent|newest)\b", normalized):
        variants.append(f"{clean} {date_label}")
    if re.search(r"\b(result|results|score|scores|winner|won|price|release|status|schedule|news)\b", normalized):
        variants.append(f"{clean} latest")
    return list(dict.fromkeys(variants))


class WebSearchClient:
    """Provider-neutral web retrieval with a keyless DuckDuckGo fallback."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.Client | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.provider = (provider or environment().get("OMNIX_WEB_SEARCH_PROVIDER") or "duckduckgo").strip().lower()
        self.api_key = api_key if api_key is not None else environment().get("OMNIX_WEB_SEARCH_API_KEY", "")
        self.timeout_seconds = timeout_seconds or float(environment().get("OMNIX_WEB_SEARCH_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS))
        self.client = client
        self.today = today

    def search(self, query: str, max_results: int = 5) -> list[AssistantContextItem]:
        clean_query = " ".join(query.split()).strip()
        if not clean_query:
            return []
        client = self.client or httpx.Client(timeout=self.timeout_seconds, follow_redirects=True)
        close_client = self.client is None
        try:
            if self.provider == "brave":
                return self._search_brave(client, clean_query, max_results)
            if self.provider == "tavily":
                return self._search_tavily(client, clean_query, max_results)
            if self.provider == "playwright":
                return self._search_playwright(clean_query, max_results)
            return self._search_duckduckgo(client, clean_query, max_results)
        finally:
            if close_client:
                client.close()

    def _search_duckduckgo(self, client: httpx.Client, query: str, max_results: int) -> list[AssistantContextItem]:
        rows: list[dict[str, str]] = []
        for search_query in _search_query_variants(query, today=self.today()):
            rows.extend(self._search_duckduckgo_rows(client, search_query, max_results))
        return self._context_items(rows, max_results, provider="duckduckgo")

    def _search_duckduckgo_rows(
        self,
        client: httpx.Client,
        query: str,
        max_results: int,
    ) -> list[dict[str, str]]:
        response = client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1", "no_redirect": "1"},
        )
        response.raise_for_status()
        payload = response.json()
        rows: list[dict[str, str]] = []
        abstract = str(payload.get("AbstractText") or "").strip()
        abstract_url = str(payload.get("AbstractURL") or "").strip()
        heading = str(payload.get("Heading") or query).strip()
        if abstract:
            rows.append({"title": heading, "url": abstract_url, "snippet": abstract})
        for row in payload.get("Results") or []:
            self._append_duckduckgo_row(rows, row)
        for row in payload.get("RelatedTopics") or []:
            if isinstance(row, dict) and isinstance(row.get("Topics"), list):
                for nested in row["Topics"]:
                    self._append_duckduckgo_row(rows, nested)
            else:
                self._append_duckduckgo_row(rows, row)
        if not rows:
            rows.extend(self._search_duckduckgo_html(client, query, max_results))
        return rows

    def _search_duckduckgo_html(
        self,
        client: httpx.Client,
        query: str,
        max_results: int,
    ) -> list[dict[str, str]]:
        response = client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "OmnixResearch/1.0"},
        )
        response.raise_for_status()
        parser = _DuckDuckGoHtmlParser(max_results=max_results)
        parser.feed(response.text)
        return parser.rows

    def _search_brave(self, client: httpx.Client, query: str, max_results: int) -> list[AssistantContextItem]:
        if not self.api_key:
            raise RuntimeError("OMNIX_WEB_SEARCH_API_KEY is required for the Brave provider")
        response = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results, "text_decorations": "false"},
            headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
        )
        response.raise_for_status()
        results = response.json().get("web", {}).get("results", [])
        rows = [
            {
                "title": str(row.get("title") or query),
                "url": str(row.get("url") or ""),
                "snippet": str(row.get("description") or ""),
            }
            for row in results
        ]
        return self._context_items(rows, max_results, provider="brave")

    def _search_tavily(self, client: httpx.Client, query: str, max_results: int) -> list[AssistantContextItem]:
        if not self.api_key:
            raise RuntimeError("OMNIX_WEB_SEARCH_API_KEY is required for the Tavily provider")
        response = client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
            },
        )
        response.raise_for_status()
        rows = [
            {
                "title": str(row.get("title") or query),
                "url": str(row.get("url") or ""),
                "snippet": str(row.get("content") or ""),
            }
            for row in response.json().get("results", [])
        ]
        return self._context_items(rows, max_results, provider="tavily")

    def _search_playwright(self, query: str, max_results: int) -> list[AssistantContextItem]:
        """Use a normal browser page as a transparent keyless search fallback."""

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError("playwright is required for the Playwright search provider") from exc

        timeout_ms = max(1000, int(self.timeout_seconds * 1000))
        rows: list[dict[str, str]] = []
        failures: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=_playwright_headless())
            try:
                page = browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36 OmnixResearch/1.0"
                    )
                )
                for search_url, parser in (
                    (
                        f"https://search.brave.com/search?q={quote_plus(query)}",
                        lambda: _playwright_external_link_rows(page, max_results, internal_hosts={"search.brave.com"}),
                    ),
                    (
                        f"https://duckduckgo.com/html/?q={quote_plus(query)}",
                        lambda: _playwright_duckduckgo_rows(page, max_results),
                    ),
                ):
                    try:
                        page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
                        try:
                            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                        except PlaywrightTimeoutError:
                            pass
                        _raise_if_playwright_search_blocked(page)
                        rows = parser()
                    except Exception as exc:
                        failures.append(f"{type(exc).__name__}: {exc}")
                        rows = []
                    if rows:
                        break
            finally:
                browser.close()
        if failures and not rows:
            raise RuntimeError(f"Playwright search did not return usable results: {'; '.join(failures)}")
        return self._context_items(rows, max_results, provider="playwright")

    @staticmethod
    def _append_duckduckgo_row(rows: list[dict[str, str]], row: Any) -> None:
        if not isinstance(row, dict):
            return
        snippet = str(row.get("Text") or "").strip()
        url = str(row.get("FirstURL") or "").strip()
        if not snippet:
            return
        title = snippet.split(" - ", 1)[0].strip() or "Web result"
        rows.append({"title": title, "url": url, "snippet": snippet})

    @staticmethod
    def _context_items(rows: list[dict[str, str]], max_results: int, *, provider: str) -> list[AssistantContextItem]:
        items: list[AssistantContextItem] = []
        seen_urls: set[str] = set()
        for row in rows:
            snippet = " ".join(row.get("snippet", "").split()).strip()
            url = row.get("url", "").strip()
            if not snippet or (url and url in seen_urls):
                continue
            if url:
                seen_urls.add(url)
            items.append(
                AssistantContextItem(
                    source_id="web_search",
                    title=" ".join(row.get("title", "Web result").split())[:240] or "Web result",
                    content=snippet[:1800],
                    url=url or None,
                    metadata={"provider": provider},
                )
            )
            if len(items) >= max_results:
                break
        return items


class _DuckDuckGoHtmlParser(HTMLParser):
    def __init__(self, *, max_results: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_results = max(1, max_results)
        self.rows: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._pending_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name: value or "" for name, value in attrs}
        classes = set(attr.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._in_title = True
            self._title_parts = []
            self._pending_href = _clean_duckduckgo_url(attr.get("href", ""))
        elif "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            title = " ".join("".join(self._title_parts).split()).strip()
            if title and self._pending_href and len(self.rows) < self.max_results:
                self.rows.append({"title": title, "url": self._pending_href, "snippet": title})
            self._in_title = False
            self._title_parts = []
            self._pending_href = ""
        elif self._in_snippet and tag in {"a", "div"}:
            snippet = " ".join("".join(self._snippet_parts).split()).strip()
            if snippet:
                for row in reversed(self.rows):
                    if row.get("snippet") == row.get("title"):
                        row["snippet"] = snippet
                        break
            self._in_snippet = False
            self._snippet_parts = []


def _clean_duckduckgo_url(value: str) -> str:
    parsed = urlparse(value)
    uddg = parse_qs(parsed.query).get("uddg", [""])[0]
    if uddg:
        return unquote(uddg)
    return value


def _playwright_headless() -> bool:
    value = environment().get("OMNIX_PLAYWRIGHT_SEARCH_HEADLESS", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _playwright_duckduckgo_rows(page: Any, max_results: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    anchors = page.locator("a.result__a")
    for index in range(min(anchors.count(), max(1, max_results))):
        anchor = anchors.nth(index)
        title = " ".join(anchor.inner_text(timeout=1000).split()).strip()
        url = _clean_duckduckgo_url(anchor.get_attribute("href") or "")
        snippet = title
        try:
            result = anchor.locator("xpath=ancestor::*[contains(@class, 'result')][1]")
            candidate = " ".join(result.locator(".result__snippet").inner_text(timeout=1000).split())
            snippet = candidate or snippet
        except Exception as exc:
            _LOGGER.debug(
                "Playwright result snippet unavailable error_type=%s",
                type(exc).__name__,
            )
        if title and url:
            rows.append({"title": title, "url": url, "snippet": snippet})
    return rows


def _raise_if_playwright_search_blocked(page: Any) -> None:
    body = ""
    try:
        body = page.locator("body").inner_text(timeout=1000).lower()
    except Exception as exc:
        raise RuntimeError("could not inspect Playwright search response") from exc
    blocked_markers = (
        "one last step",
        "solve the challenge",
        "unexpected error",
        "if error persists",
        "captcha",
    )
    if any(marker in body for marker in blocked_markers):
        raise RuntimeError("search page returned a block, challenge, or error page")


def _playwright_external_link_rows(
    page: Any,
    max_results: int,
    *,
    internal_hosts: set[str],
) -> list[dict[str, str]]:
    raw_rows = page.evaluate(
        """(maxResults) => Array.from(document.querySelectorAll('a[href]')).map((anchor) => {
            const href = anchor.href || '';
            const title = (anchor.innerText || anchor.textContent || '').replace(/\\s+/g, ' ').trim();
            const container = anchor.closest('article, section, div');
            const snippet = ((container && container.innerText) || title).replace(/\\s+/g, ' ').trim();
            return { title, url: href, snippet };
        }).filter((row) => row.title && row.url).slice(0, maxResults * 12)""",
        max(1, max_results),
    )
    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for row in raw_rows:
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "").strip()
        snippet = str(row.get("snippet") or title).strip()
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if not parsed.scheme.startswith("http") or host in internal_hosts:
            continue
        if url in seen_urls or not title:
            continue
        seen_urls.add(url)
        rows.append({"title": title, "url": url, "snippet": snippet or title})
        if len(rows) >= max_results:
            break
    return rows
