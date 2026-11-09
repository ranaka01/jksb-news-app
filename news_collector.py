from __future__ import annotations

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


COLOMBO = ZoneInfo("Asia/Colombo")
# Several publisher CDNs reject descriptive bot-style user agents even for public RSS.
USER_AGENT = "Mozilla/5.0"


@dataclass
class Article:
    source: str
    title: str
    url: str
    summary: str
    published_at: datetime | None
    time_verified: bool
    score: int = 0
    category: str = "Other"

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.source}|{self.url}|{self.title}".encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        result = asdict(self)
        result["published_at"] = self.published_at.isoformat() if self.published_at else None
        result["id"] = self.id
        return result


FEEDS: dict[str, list[str]] = {
    "EconomyNext": ["https://economynext.com/economy/feed/"],
    "Daily FT": [
        "https://www.ft.lk/rss/top-story/26",
        "https://www.ft.lk/rss/front-page/44",
        "https://www.ft.lk/rss/business/34",
        "https://www.ft.lk/rss/markets/31",
        "https://www.ft.lk/rss/sectors/20",
    ],
    "BBC Business": ["https://feeds.bbci.co.uk/news/business/rss.xml"],
}

SECTION_PAGES = {
    "BBC Business": "https://www.bbc.com/business",
    "EconomyNext": "https://economynext.com/economy/",
    "Daily FT": "https://www.ft.lk/",
    "Al Jazeera Economy": "https://www.aljazeera.com/economy/",
    "Ada Derana Biz": "https://bizenglish.adaderana.lk/",
}

SOURCE_NAMES = list(SECTION_PAGES)

HIGH_VALUE_TERMS = {
    "cse": 8,
    "colombo stock": 8,
    "listed company": 6,
    "earnings": 5,
    "profit": 4,
    "revenue": 4,
    "dividend": 5,
    "acquisition": 5,
    "merger": 5,
    "ipo": 6,
    "rights issue": 6,
    "share price": 5,
    "shares": 3,
    "stock market": 5,
    "bond": 4,
    "treasury bill": 5,
    "interest rate": 5,
    "central bank": 5,
    "inflation": 5,
    "exchange rate": 5,
    "rupee": 5,
    "foreign investor": 5,
    "imf": 5,
    "debt": 4,
    "gdp": 5,
    "pmi": 5,
    "exports": 4,
    "imports": 3,
    "current account": 5,
    "trade deficit": 5,
    "budget": 4,
    "tax": 3,
    "tariff": 3,
    "fuel price": 5,
    "oil price": 4,
    "commodity": 3,
    "banking": 3,
    "tourism": 3,
    "investment": 3,
}

SRI_LANKA_TERMS = ("sri lanka", "sri lankan", "colombo", "cse", "rupee", "central bank of sri lanka")

CATEGORY_TERMS = {
    "Company / CSE": ("company", "earnings", "profit", "revenue", "dividend", "shares", "stock", "cse", "acquisition", "merger", "ipo"),
    "Rates / FX / Debt": ("interest rate", "central bank", "bond", "treasury", "rupee", "currency", "debt", "imf"),
    "Macro economy": ("inflation", "gdp", "pmi", "exports", "imports", "current account", "trade", "budget", "tax"),
    "Energy / Commodities": ("fuel", "oil", "gas", "energy", "commodity", "gold"),
    "Policy / Regulation": ("policy", "regulation", "law", "tariff", "ministry", "government"),
}


def report_window(report_date: date, cutoff: time = time(8, 30)) -> tuple[datetime, datetime]:
    end = datetime.combine(report_date, cutoff, tzinfo=COLOMBO)
    return end - timedelta(days=1), end


def latest_report_date(now: datetime | None = None, cutoff: time = time(8, 30)) -> date:
    now = now.astimezone(COLOMBO) if now else datetime.now(COLOMBO)
    return now.date() if now.time() >= cutoff else now.date() - timedelta(days=1)


def clean_text(value: str | None, limit: int = 700) -> str:
    if not value:
        return ""
    text = BeautifulSoup(html.unescape(value), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(Read More|Read more)\s*[:\-]?\s*", "", text)
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0]
    return shortened.rstrip(".,;:") + "…"


def canonical_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
        return urlunsplit((parts.scheme or "https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    except ValueError:
        return value.strip()


def parse_datetime(value: str | None, naive_timezone=timezone.utc) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_timezone)
        return parsed.astimezone(COLOMBO)
    except (ValueError, TypeError, OverflowError):
        return None


def score_article(article: Article) -> Article:
    text = f"{article.title} {article.summary}".lower()
    score = sum(weight for term, weight in HIGH_VALUE_TERMS.items() if term in text)
    if any(term in text for term in SRI_LANKA_TERMS):
        score += 7
    if article.source in {"EconomyNext", "Daily FT", "Ada Derana Biz"}:
        score += 2

    category = "Other"
    category_score = 0
    for candidate, terms in CATEGORY_TERMS.items():
        matches = sum(1 for term in terms if term in text)
        if matches > category_score:
            category, category_score = candidate, matches

    article.score = score
    article.category = category
    return article


def _fetch_feed(source: str, url: str, session: requests.Session) -> list[Article]:
    response = session.get(url, timeout=25)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    articles: list[Article] = []
    for entry in parsed.entries:
        title = clean_text(entry.get("title"), 300)
        link = canonical_url(entry.get("link", ""))
        if not title or not link:
            continue
        date_value = entry.get("published") or entry.get("updated") or entry.get("dc_date")
        # Daily FT timestamps are local Sri Lankan time but omit the UTC offset.
        published = parse_datetime(date_value, COLOMBO if source == "Daily FT" else timezone.utc)
        summary = clean_text(entry.get("summary") or entry.get("description") or "")
        articles.append(score_article(Article(source, title, link, summary, published, published is not None)))
    return articles


def _jsonld_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_objects(item)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in value:
                yield from _jsonld_objects(value[key])


def _article_metadata(source: str, url: str) -> Article:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    def meta(*, prop: str | None = None, name: str | None = None) -> str:
        attrs = {"property": prop} if prop else {"name": name}
        tag = soup.find("meta", attrs=attrs)
        return tag.get("content", "").strip() if tag else ""

    title = meta(prop="og:title") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    summary = meta(name="description") or meta(prop="og:description")
    published_value = meta(prop="article:published_time") or meta(name="parsely-pub-date")

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in _jsonld_objects(payload):
            if not published_value and obj.get("datePublished"):
                published_value = str(obj["datePublished"])
            if not title and obj.get("headline"):
                title = str(obj["headline"])
            if not summary and obj.get("description"):
                summary = str(obj["description"])

    if source == "Daily FT" and not published_value:
        stamp = soup.select_one(".gtime")
        published_value = stamp.get_text(" ", strip=True) if stamp else ""
    elif source == "Ada Derana Biz" and not published_value:
        match = re.search(r'publishedAt\\?"\s*:\s*\\?"([^"\\]+)', response.text)
        published_value = match.group(1) if match else ""

    if not summary:
        paragraphs = []
        article_node = soup.find("article") or soup.find("main")
        if article_node:
            for paragraph in article_node.find_all("p"):
                value = clean_text(paragraph.get_text(" ", strip=True), 500)
                if len(value) >= 40:
                    paragraphs.append(value)
                if len(paragraphs) == 2:
                    break
        summary = " ".join(paragraphs)

    title = re.sub(r"\s*\|\s*(?:Daily FT|EconomyNext).*$", "", clean_text(title, 300)).strip()
    published = parse_datetime(published_value)
    return score_article(
        Article(source, title, canonical_url(response.url), clean_text(summary), published, published is not None)
    )


def _section_links(source: str, soup: BeautifulSoup, base_url: str) -> list[str]:
    from urllib.parse import urljoin

    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        full_url = urljoin(base_url, href)
        path = urlsplit(full_url).path
        include = False
        if source == "Al Jazeera Economy":
            include = "article-card__link" in (anchor.get("class") or [])
        elif source == "Ada Derana Biz":
            include = bool(re.fullmatch(r"/news/\d+/?", path))
        if include:
            links.append(canonical_url(full_url))
    return list(dict.fromkeys(links))


def _fetch_section_page(source: str, url: str, session: requests.Session) -> tuple[list[Article], list[str]]:
    response = session.get(url, timeout=25)
    response.raise_for_status()
    links = _section_links(source, BeautifulSoup(response.text, "html.parser"), response.url)
    limit = 40 if source == "Ada Derana Biz" else 30
    links = links[:limit]
    articles: list[Article] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_article_metadata, source, link): link for link in links}
        for future in as_completed(futures):
            try:
                article = future.result()
                if article.title and article.published_at:
                    articles.append(article)
            except Exception as exc:
                errors.append(f"{source} article {futures[future]}: {exc}")
    return articles, errors


def _fetch_the_morning(session: requests.Session) -> list[Article]:
    response = session.get("https://www.themorning.lk/", timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    payload = soup.find("script", id="__NEXT_DATA__")
    if not payload or not payload.string:
        raise RuntimeError("The Morning page data was not found")

    import json

    data = json.loads(payload.string)
    items = data.get("props", {}).get("pageProps", {}).get("latestNews", [])
    articles: list[Article] = []
    for item in items:
        title = clean_text(item.get("title"), 300)
        article_id = item.get("id", "")
        if not title or not article_id:
            continue
        url = f"https://www.themorning.lk/articles/{article_id}"
        metadata = item.get("meta") or {}
        published = parse_datetime(metadata.get("createdAt"))
        # The site currently exposes a calendar date but no publication time.
        if published:
            published = datetime.combine(published.date(), time.min, tzinfo=COLOMBO)
        summary = clean_text(metadata.get("excerpt") or item.get("content") or "")
        articles.append(score_article(Article("The Morning", title, url, summary, published, False)))
    return articles


def _in_window(article: Article, start: datetime, end: datetime) -> bool:
    if article.published_at is None:
        return False
    if article.time_verified:
        return start <= article.published_at < end
    # Date-only items are candidates if their date overlaps the 24-hour window.
    return start.date() <= article.published_at.date() <= end.date()


def _deduplicate(items: Iterable[Article]) -> list[Article]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    result: list[Article] = []
    for item in sorted(items, key=lambda a: (a.score, a.published_at or datetime.min.replace(tzinfo=COLOMBO)), reverse=True):
        title_key = re.sub(r"[^a-z0-9]", "", item.title.lower())
        if item.url in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(item.url)
        seen_titles.add(title_key)
        result.append(item)
    return result


def collect_news(
    selected_sources: Iterable[str], start: datetime, end: datetime
) -> tuple[list[Article], list[str]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/rss+xml,application/xml"})
    articles: list[Article] = []
    errors: list[str] = []

    for source in selected_sources:
        if source in {"Al Jazeera Economy", "Ada Derana Biz"}:
            try:
                page_articles, page_errors = _fetch_section_page(source, SECTION_PAGES[source], session)
                articles.extend(page_articles)
                errors.extend(page_errors)
            except Exception as exc:  # Keep other publishers usable.
                errors.append(f"{source}: {exc}")
            continue
        for url in FEEDS.get(source, []):
            try:
                articles.extend(_fetch_feed(source, url, session))
            except Exception as exc:
                errors.append(f"{source} ({url}): {exc}")

    filtered = [article for article in articles if _in_window(article, start, end)]
    return _deduplicate(filtered), errors

