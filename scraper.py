"""
YC Founders Scraper — fetches company metadata from the YC Algolia index,
then scrapes individual company pages for founder details and emails.
"""

import re
import json
import time
import logging
import threading
import concurrent.futures
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlencode, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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

_thread_local = threading.local()


def _get_session() -> requests.Session:
    """Return a thread-local session with retry/backoff and connection pooling."""
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.headers.update(HEADERS)
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry, pool_connections=20, pool_maxsize=20
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return _thread_local.session


def _get_algolia_params() -> dict:
    """
    Fetch the current Algolia credentials from the YC companies page.
    The API key rotates, so we scrape it fresh and cache it for the session.
    """
    global _cached_algolia_params
    if _cached_algolia_params is not None:
        return _cached_algolia_params

    resp = _get_session().get(f"{YC_BASE}/companies", timeout=15)
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
    email: str = ""
    github: str = ""


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
# Email discovery
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_NOISE_DOMAINS = frozenset([
    "sentry.io", "algolia.net", "amazonaws.com", "example.com",
    "wixpress.com", "schema.org", "w3.org", "googleapis.com",
    "cloudflare.com", "webpack.js", "reactjs.org", "github.com",
    "googletagmanager.com", "google-analytics.com", "facebook.com",
    "twitter.com", "gstatic.com", "intercom.io", "segment.io",
    "segment.com", "hotjar.com", "hubspot.com", "clarity.ms",
    "pendo.io", "sentry-next.wixpress.com", "ycombinator.com",
    "gravatar.com", "shields.io", "vercel.app", "herokuapp.com",
    "netlify.app", "stripe.com", "typeform.com", "mailchimp.com",
    "sendgrid.net", "postmarkapp.com", "mixpanel.com", "fullstory.com",
    "crisp.chat", "zendesk.com", "freshdesk.com", "tawk.to",
])


def _is_noise_email(email: str) -> bool:
    domain = email.split("@")[1].lower()
    return any(noise in domain for noise in _NOISE_DOMAINS)


def _extract_emails_from_html(html: str) -> set[str]:
    """Pull all plausible email addresses from an HTML page."""
    soup = BeautifulSoup(html, "lxml")
    emails: set[str] = set()

    for a in soup.find_all("a", href=True):
        if a["href"].startswith("mailto:"):
            addr = a["href"][7:].split("?")[0].strip()
            if _EMAIL_RE.match(addr):
                emails.add(addr.lower())

    for match in _EMAIL_RE.finditer(html):
        addr = match.group()
        if not addr.endswith((".png", ".jpg", ".svg", ".gif", ".webp", ".css", ".js")):
            emails.add(addr.lower())

    return {e for e in emails if not _is_noise_email(e)}


def _fetch_page_safe(url: str, timeout: int = 10) -> Optional[str]:
    """GET a URL and return the text, or None on any error."""
    try:
        resp = _get_session().get(url, timeout=timeout, allow_redirects=True)
        if resp.ok and "text/html" in resp.headers.get("content-type", ""):
            return resp.text
    except Exception:
        pass
    return None


def _discover_emails_for_company(
    yc_page_html: str,
    website: str,
    github_urls: list[str],
) -> set[str]:
    """
    Gather publicly available emails from multiple sources for one company.
    Returns a set of clean, de-duped email addresses.
    """
    emails: set[str] = set()

    # Source 1: YC company page
    emails.update(_extract_emails_from_html(yc_page_html))

    # Source 2: Company website — homepage + common sub-pages
    if website:
        homepage_html = _fetch_page_safe(website, timeout=8)
        if homepage_html:
            emails.update(_extract_emails_from_html(homepage_html))

            for path in ("/about", "/contact", "/team"):
                sub_url = urljoin(website.rstrip("/") + "/", path.lstrip("/"))
                sub_html = _fetch_page_safe(sub_url, timeout=6)
                if sub_html:
                    emails.update(_extract_emails_from_html(sub_html))

    # Source 3: GitHub org/user profiles via API (public email field)
    for gh_url in github_urls:
        username = gh_url.rstrip("/").split("/")[-1]
        if not username:
            continue
        try:
            resp = _get_session().get(
                f"https://api.github.com/users/{username}",
                timeout=8,
            )
            if resp.ok:
                gh_email = resp.json().get("email")
                if gh_email and _EMAIL_RE.match(gh_email) and not _is_noise_email(gh_email):
                    emails.add(gh_email.lower())
        except Exception:
            pass

    return emails


def _match_email_to_founder(email: str, founder_name: str) -> bool:
    """
    Heuristic: does this email likely belong to this founder?
    Checks if the email local part contains the founder's first or last name.
    """
    local = email.split("@")[0].lower().replace(".", " ").replace("_", " ").replace("-", " ")
    parts = founder_name.lower().split()
    if not parts:
        return False
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    return first in local or (last and last in local)


def _assign_emails_to_founders(
    founders: list[Founder],
    emails: set[str],
) -> None:
    """
    Try to match discovered emails to specific founders by name.
    If only one founder and one email, assign directly.
    Unmatched emails are left unassigned (not forced onto anyone).
    """
    if not emails or not founders:
        return

    remaining = set(emails)

    # Pass 1: assign emails that clearly match a founder's name
    for founder in founders:
        for email in list(remaining):
            if _match_email_to_founder(email, founder.name):
                founder.email = email
                remaining.discard(email)
                break

    # Pass 2: if there's exactly one founder without an email and one remaining email, assign it
    no_email = [f for f in founders if not f.email]
    if len(no_email) == 1 and len(remaining) == 1:
        no_email[0].email = remaining.pop()


# ---------------------------------------------------------------------------
# Algolia bulk fetch
# ---------------------------------------------------------------------------

def _algolia_post(body: dict) -> dict:
    params = _get_algolia_params()
    resp = _get_session().post(ALGOLIA_URL, params=params, json=body, timeout=30)
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

                github = ""
                for a in card.find_all("a", href=True):
                    href = a.get("href", "")
                    label = a.get("aria-label", "").lower()
                    if "linkedin.com" in href or "linkedin" in label:
                        if not linkedin:
                            linkedin = href if "linkedin.com" in href else ""
                    if "github.com" in href or "github" in label:
                        if not github and "/companies/" not in href:
                            github = href if "github.com" in href else ""

                bio_div = card.find(
                    "div", class_=lambda c: c and "prose" in c
                )
                if bio_div:
                    bio = bio_div.get_text(strip=True)

                if name not in seen_names:
                    seen_names.add(name)
                    founders.append(Founder(
                        name=name, title=title, bio=bio,
                        linkedin=linkedin, github=github,
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


def scrape_founders(
    slug: str,
    website: str = "",
    discover_emails: bool = True,
    retries: int = 2,
) -> list[Founder]:
    """Scrape a single company page for founder info and optionally emails."""
    url = f"{YC_BASE}/companies/{slug}"
    for attempt in range(retries + 1):
        try:
            resp = _get_session().get(url, timeout=15)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            html = resp.text

            next_data = _extract_next_data(html)
            if next_data:
                founders = _parse_founders_from_next_data(next_data)
                if founders:
                    if discover_emails:
                        github_urls = [f.github for f in founders if f.github]
                        emails = _discover_emails_for_company(html, website, github_urls)
                        _assign_emails_to_founders(founders, emails)
                    return founders

            founders = _parse_founders_from_html(html)

            if discover_emails and founders:
                github_urls = [f.github for f in founders if f.github]
                emails = _discover_emails_for_company(html, website, github_urls)
                _assign_emails_to_founders(founders, emails)

            return founders
        except requests.RequestException as exc:
            log.warning("Failed to scrape %s (attempt %d): %s", slug, attempt + 1, exc)
            if attempt < retries:
                time.sleep(1)
    return []


def scrape_founders_batch(
    companies: list[Company],
    max_workers: int = 6,
    delay: float = 0.2,
    discover_emails: bool = True,
    progress_callback=None,
    per_company_timeout: int = 90,
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
        company.founders = scrape_founders(
            company.slug,
            website=company.website,
            discover_emails=discover_emails,
        )
        return idx

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, (i, c)): i for i, c in enumerate(companies)}
        done_count = 0
        for future in concurrent.futures.as_completed(futures):
            done_count += 1
            try:
                future.result(timeout=per_company_timeout)
            except concurrent.futures.TimeoutError:
                log.warning("Company scrape timed out after %ds", per_company_timeout)
            except Exception as exc:
                log.warning("Scrape error: %s", exc)
            if progress_callback:
                progress_callback(done_count, total)

    return companies
