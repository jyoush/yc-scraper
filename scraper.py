"""
YC Founders Scraper — fetches company metadata from the YC Algolia index,
then scrapes individual company pages for founder details.
"""

import json
import time
import logging
import concurrent.futures
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

ALGOLIA_URL = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"
INDEX_NAME = "YCCompany_By_Launch_Date_production"
YC_BASE = "https://www.ycombinator.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_cached_algolia_params: Optional[dict] = None


def _get_algolia_params() -> dict:
    """
    Fetch the current Algolia credentials from the YC companies page.
    The API key rotates, so we scrape it fresh and cache it for the session.
    """
    global _cached_algolia_params
    if _cached_algolia_params is not None:
        return _cached_algolia_params

    import re
    resp = requests.get(f"{YC_BASE}/companies", headers=HEADERS, timeout=15)
    resp.raise_for_status()

    match = re.search(r"window\.AlgoliaOpts\s*=\s*(\{[^}]+\})", resp.text)
    if not match:
        raise RuntimeError("Could not find AlgoliaOpts on YC companies page")

    opts = json.loads(match.group(1))
    _cached_algolia_params = {
        "x-algolia-agent": "Algolia for JavaScript (3.35.1); Browser; JS Helper (3.16.1)",
        "x-algolia-application-id": opts["app"],
        "x-algolia-api-key": opts["key"],
    }
    log.info("Fetched fresh Algolia credentials (app=%s)", opts["app"])
    return _cached_algolia_params


@dataclass
class Founder:
    name: str
    title: str
    bio: str = ""
    linkedin: str = ""


@dataclass
class Company:
    name: str
    slug: str
    batch: str
    status: str
    one_liner: str
    website: str
    industries: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    location: str = ""
    team_size: int = 0
    long_description: str = ""
    yc_url: str = ""
    founders: list[Founder] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Algolia bulk fetch
# ---------------------------------------------------------------------------

def _algolia_post(body: dict) -> dict:
    params = _get_algolia_params()
    resp = requests.post(ALGOLIA_URL, params=params, json=body, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_facets() -> dict:
    """Return a dict of facet name -> {value: count}."""
    body = {
        "requests": [
            {
                "indexName": INDEX_NAME,
                "params": urlencode({
                    "query": "",
                    "hitsPerPage": 0,
                    "facets": json.dumps([
                        "batch", "industries", "subindustry",
                        "status", "regions", "top_company",
                        "isHiring", "nonprofit", "tags",
                    ]),
                    "maxValuesPerFacet": 1000,
                    "tagFilters": "",
                }),
            }
        ]
    }
    data = _algolia_post(body)
    return data["results"][0].get("facets", {})


def fetch_companies(
    batch_filter: Optional[str] = None,
    industry_filter: Optional[str] = None,
    query: str = "",
) -> list[Company]:
    """
    Fetch companies from the Algolia index.
    Paginates automatically (1000 hits per page).
    """
    facet_filters = []
    if batch_filter:
        facet_filters.append(f"batch:{batch_filter}")
    if industry_filter:
        facet_filters.append(f"industries:{industry_filter}")

    companies: list[Company] = []
    page = 0

    while True:
        params = {
            "query": query,
            "hitsPerPage": 1000,
            "page": page,
            "facets": json.dumps([
                "batch", "industries", "subindustry",
                "status", "regions", "top_company",
                "isHiring", "nonprofit", "tags",
            ]),
            "maxValuesPerFacet": 1000,
            "tagFilters": "",
        }
        if facet_filters:
            params["facetFilters"] = json.dumps(facet_filters)

        body = {
            "requests": [
                {"indexName": INDEX_NAME, "params": urlencode(params)}
            ]
        }
        data = _algolia_post(body)
        result = data["results"][0]
        hits = result.get("hits", [])

        for h in hits:
            companies.append(Company(
                name=h.get("name", ""),
                slug=h.get("slug", ""),
                batch=h.get("batch", ""),
                status=h.get("status", ""),
                one_liner=h.get("one_liner", ""),
                website=h.get("website", ""),
                industries=h.get("industries", []),
                tags=h.get("tags", []),
                location=h.get("all_locations", ""),
                team_size=h.get("team_size", 0),
                long_description=h.get("long_description", ""),
                yc_url=f"{YC_BASE}/companies/{h.get('slug', '')}",
            ))

        nb_pages = result.get("nbPages", 1)
        page += 1
        if page >= nb_pages:
            break

    return companies


# ---------------------------------------------------------------------------
# Individual page scraper — founder details
# ---------------------------------------------------------------------------

def _extract_next_data(html: str) -> Optional[dict]:
    """Pull __NEXT_DATA__ JSON from the page if present."""
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            return json.loads(script.string)
        except json.JSONDecodeError:
            return None
    return None


def _parse_founders_from_next_data(data: dict) -> list[Founder]:
    """Navigate the Next.js payload to find founder objects."""
    founders: list[Founder] = []
    try:
        props = data.get("props", {}).get("pageProps", {})

        # The company object lives at different paths depending on
        # the page version; try the most common ones.
        company = props.get("company") or props.get("data", {}).get("company") or {}

        founder_list = company.get("founders", [])
        for f in founder_list:
            founders.append(Founder(
                name=f.get("full_name", f.get("name", "")),
                title=f.get("title", ""),
                bio=f.get("bio", f.get("description", "")),
                linkedin=f.get("linkedin_url", f.get("linkedin", "")),
            ))
    except Exception:
        pass
    return founders


_ROLE_KEYWORDS = frozenset([
    "founder", "ceo", "cto", "cpo", "coo", "cfo", "cmo",
    "president", "head", "director", "engineer", "vp",
    "chief", "partner", "managing", "co-founder",
])
_BOGUS_NAMES = frozenset(["uploaded image", "default-avatar", "avatar"])


def _looks_like_role(text: str) -> bool:
    """Return True if text looks like a job title / role."""
    lower = text.lower()
    return any(kw in lower for kw in _ROLE_KEYWORDS)


def _is_valid_name(name: str) -> bool:
    words = name.strip().split()
    return (
        len(words) >= 2
        and len(name) < 80
        and name.lower() not in _BOGUS_NAMES
        and not name.startswith("http")
    )


def _extract_title_near(name_div) -> str:
    """
    Walk siblings / parent-siblings of the name div to find a short
    role string like 'Founder/CEO'.
    """
    # Check immediate next siblings of the name_div's parent row
    row = name_div.find_parent("div")
    if row:
        for sibling in row.find_next_siblings("div"):
            txt = sibling.get_text(strip=True)
            if txt and len(txt) < 80 and _looks_like_role(txt):
                return txt

    # Walk all leaf divs inside the card (same level) looking for short role text
    card = name_div
    for _ in range(5):
        p = card.find_parent("div")
        if p:
            card = p
        cls = card.get("class", [])
        if any("ycdc-card" in c for c in cls):
            break

    for div in card.find_all("div"):
        if div.find("div"):
            continue
        txt = div.get_text(strip=True)
        if (
            txt
            and txt != name_div.get_text(strip=True)
            and 3 < len(txt) < 60
            and _looks_like_role(txt)
        ):
            return txt

    return ""


def _parse_founders_from_html(html: str) -> list[Founder]:
    """Parse founders from the YC company page HTML."""
    soup = BeautifulSoup(html, "lxml")
    founders: list[Founder] = []
    seen_names: set[str] = set()

    heading = None
    for div in soup.find_all("div", class_=lambda c: c and "font-bold" in c):
        text = div.get_text(strip=True)
        if text in ("Founders", "Active Founders"):
            heading = div
            break

    if heading:
        container = heading.find_next_sibling("div")
        if container:
            cards = container.find_all(
                "div", class_=lambda c: c and "ycdc-card" in c, recursive=False
            )
            for card in cards:
                name = ""
                title = ""
                linkedin = ""
                bio = ""

                name_div = card.find(
                    "div", class_=lambda c: c and "font-bold" in c and "text-xl" in c
                )
                if name_div:
                    name = name_div.get_text(strip=True)

                if not name or not _is_valid_name(name):
                    img = card.find("img", alt=True)
                    if img and img["alt"] and _is_valid_name(img["alt"]):
                        name = img["alt"]

                if not name or not _is_valid_name(name):
                    continue

                if name_div:
                    title = _extract_title_near(name_div)

                for a in card.find_all("a", href=True):
                    href = a.get("href", "")
                    label = a.get("aria-label", "").lower()
                    if "linkedin.com" in href or "linkedin" in label:
                        linkedin = href if "linkedin.com" in href else ""
                        break

                bio_div = card.find(
                    "div", class_=lambda c: c and "prose" in c
                )
                if bio_div:
                    bio = bio_div.get_text(strip=True)

                if name not in seen_names:
                    seen_names.add(name)
                    founders.append(Founder(
                        name=name, title=title, bio=bio, linkedin=linkedin
                    ))

    # Fallback: scan all ycdc-card divs for img alts that look like person names
    if not founders:
        for card in soup.find_all("div", class_=lambda c: c and "ycdc-card" in c):
            img = card.find("img", alt=True)
            if not img or not _is_valid_name(img["alt"]):
                continue
            name = img["alt"]
            if name in seen_names:
                continue

            title = ""
            linkedin = ""
            for div in card.find_all("div"):
                if div.find("div"):
                    continue
                txt = div.get_text(strip=True)
                if txt and txt != name and 3 < len(txt) < 60 and _looks_like_role(txt):
                    title = txt
                    break
            for a in card.find_all("a", href=True):
                if "linkedin.com" in a["href"]:
                    linkedin = a["href"]
                    break

            seen_names.add(name)
            founders.append(Founder(name=name, title=title, linkedin=linkedin))

    return founders


def scrape_founders(slug: str, retries: int = 2) -> list[Founder]:
    """Scrape a single company page for founder info."""
    url = f"{YC_BASE}/companies/{slug}"
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()

            next_data = _extract_next_data(resp.text)
            if next_data:
                founders = _parse_founders_from_next_data(next_data)
                if founders:
                    return founders

            return _parse_founders_from_html(resp.text)
        except requests.RequestException as exc:
            log.warning("Failed to scrape %s (attempt %d): %s", slug, attempt + 1, exc)
            if attempt < retries:
                time.sleep(1)
    return []


def scrape_founders_batch(
    companies: list[Company],
    max_workers: int = 8,
    delay: float = 0.15,
    progress_callback=None,
) -> list[Company]:
    """
    Scrape founder details for a list of companies in parallel.
    Mutates each Company.founders in-place and returns the list.
    """
    total = len(companies)

    def _work(idx_company):
        idx, company = idx_company
        if delay:
            time.sleep(delay * (idx % max_workers))
        company.founders = scrape_founders(company.slug)
        return idx

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, (i, c)): i for i, c in enumerate(companies)}
        done_count = 0
        for future in concurrent.futures.as_completed(futures):
            done_count += 1
            try:
                future.result()
            except Exception as exc:
                log.warning("Scrape error: %s", exc)
            if progress_callback:
                progress_callback(done_count, total)

    return companies
