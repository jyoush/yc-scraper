# YC Founders Scraper

A CLI tool that scrapes Y Combinator company and founder data from the YC directory and exports it to CSV.

## Features

- **Bulk company data** from the YC directory (5,600+ companies across 48 batches)
- **Founder details** scraped from individual pages: name, title, LinkedIn, GitHub
- **Public email discovery** — scans YC pages, company websites, and GitHub profiles for publicly listed emails
- **Filters**: YC batch, industry, company status, keyword search
- **CSV export** to file or stdout
- **`--only-emails`** flag to output only founders with a discovered email

