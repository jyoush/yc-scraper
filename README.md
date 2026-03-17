# YC Founders Scraper

A CLI tool that scrapes Y Combinator company and founder data from the YC directory and exports it to CSV.

## Features

- **Bulk company data** from the YC directory (5,600+ companies across 48 batches)
- **Founder details** scraped from individual pages: name, title, LinkedIn, GitHub
- **Public email discovery** — scans YC pages, company websites, and GitHub profiles for publicly listed emails
- **Filters**: YC batch, industry, company status, keyword search
- **CSV export** to file or stdout
- **`--only-emails`** flag to output only founders with a discovered email

## Quick Start

```bash
pip install -r requirements.txt
python app.py --batch "Winter 2026" --founders -o founders.csv
```

## Usage

```bash
# List available batches / industries
python app.py --list-batches
python app.py --list-industries

# Fetch all companies in a batch (metadata only, fast)
python app.py --batch "Summer 2024" -o companies.csv

# With founder scraping (names, titles, LinkedIn, GitHub)
python app.py --batch "Winter 2026" --founders -o founders.csv

# With founder scraping + email discovery
python app.py --batch "Winter 2026" --founders --emails -o founders.csv

# Only output founders that have a public email
python app.py --batch "Winter 2026" --only-emails -o emails.csv

# Limit to first 50 companies (for speed)
python app.py --batch "Winter 2026" --founders --max 50 -o founders.csv

# Filter by industry
python app.py --industry "Healthcare" --founders -o healthcare.csv

# Filter by status
python app.py --status "Active" --batch "Winter 2026" --founders -o active.csv

# Keyword search
python app.py --query "robotics" --founders -o robotics.csv

# Print to stdout (pipe to other tools)
python app.py --batch "Summer 2024" --founders

# Verbose logging (debug output)
python app.py --batch "Winter 2026" --founders -v -o founders.csv
```

### All Options

| Flag | Description |
|------|-------------|
| `--batch` | Filter by YC batch (e.g. `"Winter 2026"`, `"Summer 2024"`) |
| `--industry` | Filter by industry (e.g. `"Healthcare"`, `"Fintech"`) |
| `--status` | Filter by status (`Active`, `Acquired`, `Public`, `Inactive`) |
| `--query` | Keyword search across company names and descriptions |
| `--founders` | Scrape individual company pages for founder details |
| `--emails` | Discover public emails (implies `--founders`) |
| `--only-emails` | Only include founders with a discovered email (implies `--emails`) |
| `--max N` | Max companies to scrape founders for |
| `--workers N` | Parallel workers for founder scraping (default: 6) |
| `-o PATH` | Output CSV path (default: stdout) |
| `--list-batches` | List all available batches and exit |
| `--list-industries` | List all available industries and exit |
| `-v` | Verbose logging |

## How It Works

1. **Algolia API** — The YC directory uses Algolia for search. The scraper fetches the current API key from the page and queries the index for company metadata (name, batch, industry, status, location, etc.).

2. **Page Scraping** — For founder details, each company's page at `ycombinator.com/companies/{slug}` is scraped to extract founder names, titles, LinkedIn profiles, and GitHub links.

3. **Email Discovery** — For each company, the scraper searches multiple public sources:
   - The YC company page (emails in bios/descriptions)
   - The company website homepage and common pages (`/about`, `/contact`, `/team`)
   - GitHub user/org profiles (public email field via the API)

   Emails are matched to specific founders by name when possible.

4. **Parallel Processing** — Founder scraping runs in parallel threads with connection pooling, automatic retry with exponential backoff, and rate limiting.

## Python API

```python
from scraper import fetch_facets, fetch_companies, scrape_founders_batch

# Get available filter options
facets = fetch_facets()
print(facets["batch"].keys())

# Fetch companies with filters
companies = fetch_companies(batch_filter="Winter 2026", industry_filter="Fintech")

# Scrape founder details + emails
scrape_founders_batch(companies, max_workers=6, discover_emails=True)

for company in companies:
    for founder in company.founders:
        print(f"{founder.name} | {founder.title} | {founder.email} | {company.name}")
```

## Data Fields

### Company
| Field | Description |
|-------|-------------|
| Name | Company name |
| Batch | YC batch (e.g. "Winter 2026") |
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

- **Rate limits**: The scraper includes delays and retry with backoff. Scraping 200 companies with email discovery takes ~1-2 minutes.
- **Emails**: Only publicly available emails are included. Many founders don't have public emails — those rows will have a blank email field (or be excluded with `--only-emails`).
- **API key rotation**: The Algolia API key is fetched fresh each session to handle rotation.
