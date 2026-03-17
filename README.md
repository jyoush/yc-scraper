# YC Founders Explorer

A web scraper and interactive explorer for Y Combinator founder and company data. Fetches company metadata from YC's Algolia search index and scrapes individual company pages for detailed founder information.

## Features

- **Bulk company data** from the YC directory (5,600+ companies across 48 batches)
- **Founder details** scraped from individual pages: name, title, LinkedIn profile
- **Public email discovery** — scans YC pages, company websites (homepage + /about, /contact, /team), and GitHub profiles for publicly listed emails; only verified emails are included
- **Filters**: YC batch, industry, company status, keyword search
- **Export**: Download results as CSV
- **Interactive web UI** built with Streamlit

## Quick Start

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

## How It Works

1. **Algolia API** — The YC companies directory uses Algolia for search. The scraper fetches the current API key from the page and queries the index for company metadata (name, batch, industry, status, location, etc.).

2. **Page Scraping** — For founder details, each company's page at `ycombinator.com/companies/{slug}` is scraped to extract founder names, titles, LinkedIn profiles, and GitHub links.

3. **Email Discovery** — For each company, the scraper searches multiple public sources for email addresses:
   - The YC company page itself (emails embedded in bios/descriptions)
   - The company website homepage and common pages (`/about`, `/contact`, `/team`)
   - GitHub user/org profiles (public email field via the API)
   
   Emails are matched to specific founders by name when possible. Only genuinely public emails are included — if no email is found, the field is left blank.

4. **Parallel Processing** — Founder scraping runs in parallel threads for speed, with built-in rate limiting and retries.

## Usage

### Web UI

The Streamlit app provides an interactive interface:

- **Sidebar filters** — Select batch (e.g. "Winter 2024"), industry, and company status
- **Keyword search** — Search across company names and descriptions
- **Founder scraping toggle** — Enable/disable detailed founder scraping
- **Email discovery toggle** — Enable/disable public email lookup
- **Max companies slider** — Control how many pages to scrape (affects speed)
- **Two views** — Switch between Founders and Companies tabs
- **CSV export** — Download filtered results

### Python API

```python
from scraper import fetch_facets, fetch_companies, scrape_founders_batch

# Get available filter options
facets = fetch_facets()
print(facets["batch"].keys())    # ['Winter 2024', 'Summer 2023', ...]
print(facets["industries"].keys())  # ['B2B', 'Fintech', 'Healthcare', ...]

# Fetch companies with filters
companies = fetch_companies(batch_filter="Winter 2024", industry_filter="Fintech")

# Scrape founder details
scrape_founders_batch(companies, max_workers=8)

for company in companies:
    for founder in company.founders:
        print(f"{founder.name} | {founder.title} | {company.name}")
```

## Data Fields

### Company
| Field | Description |
|-------|-------------|
| Name | Company name |
| Batch | YC batch (e.g. "Winter 2024") |
| Status | Active, Acquired, Public, Inactive |
| Industries | List of industries |
| One-Liner | Short description |
| Website | Company URL |
| Location | Headquarters location |
| Team Size | Number of employees |
| YC Page | Link to YC directory page |

### Founder
| Field | Description |
|-------|-------------|
| Name | Full name |
| Title | Role (e.g. "Founder/CEO") |
| Email | Public email (blank if not found) |
| LinkedIn | LinkedIn profile URL |
| GitHub | GitHub profile URL |

## Notes

- **Rate limits**: The scraper includes delays between requests. Scraping 200 companies with email discovery takes ~1-2 minutes.
- **Emails**: Only publicly available emails are included. The scraper checks YC pages, company websites, and GitHub profiles. Many founders don't have public emails — those rows will have a blank email field.
- **API key rotation**: The Algolia API key is fetched fresh from the YC website each session to handle rotation.
