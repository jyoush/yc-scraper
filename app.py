"""
YC Founders Scraper — CLI tool.

Usage:
    python app.py                          # all companies, no founder scraping
    python app.py --batch W25              # filter by batch
    python app.py --batch S24 --founders   # scrape founder details too
    python app.py --batch S24 --founders --emails   # + email discovery
    python app.py --list-batches           # show available batches
    python app.py --list-industries        # show available industries
"""

import argparse
import csv
import logging
import sys
import time

from scraper import (
    Company,
    Founder,
    fetch_facets,
    fetch_companies,
    scrape_founders_batch,
)

log = logging.getLogger("yc-scraper")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        level=level,
        stream=sys.stderr,
    )


def _batch_sort_key(batch: str) -> tuple:
    season_order = {"F": 0, "S": 1, "W": 2, "IK": 3}
    if not batch:
        return (9999, 9)
    season = batch[0] if batch[0] in season_order else batch[:2]
    num_str = batch[len(season) if season in season_order else 1:]
    try:
        num = int(num_str)
    except ValueError:
        return (9999, 9)
    return (-num, season_order.get(season, 9))


def list_facets(facet_name: str) -> None:
    facets = fetch_facets()
    values = facets.get(facet_name, {})
    if not values:
        log.error("No values found for facet %r", facet_name)
        sys.exit(1)

    if facet_name == "batch":
        items = sorted(values.items(), key=lambda kv: _batch_sort_key(kv[0]))
    else:
        items = sorted(values.items(), key=lambda kv: kv[0])

    print(f"\n{'Value':<30} {'Count':>8}")
    print("-" * 40)
    for name, count in items:
        print(f"{name:<30} {count:>8,}")
    print(f"\nTotal: {len(items)} values\n")


def write_csv(companies: list[Company], out_path: str, with_founders: bool, only_emails: bool = False) -> None:
    if with_founders:
        fieldnames = [
            "company", "batch", "status", "one_liner", "website",
            "industries", "location", "team_size", "yc_url",
            "founder_name", "founder_title", "founder_email",
            "founder_linkedin", "founder_github",
        ]
    else:
        fieldnames = [
            "company", "batch", "status", "one_liner", "website",
            "industries", "location", "team_size", "yc_url",
        ]

    fh = None
    if out_path != "-":
        fh = open(out_path, "w", newline="", encoding="utf-8-sig")
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)

    writer.writeheader()

    for c in companies:
        base = {
            "company": c.name,
            "batch": c.batch,
            "status": c.status,
            "one_liner": c.one_liner,
            "website": c.website,
            "industries": "; ".join(c.industries),
            "location": c.location,
            "team_size": c.team_size,
            "yc_url": c.yc_url,
        }
        if with_founders:
            founders = [f for f in c.founders if f.email] if only_emails else c.founders
            if founders:
                for f in founders:
                    writer.writerow({
                        **base,
                        "founder_name": f.name,
                        "founder_title": f.title,
                        "founder_email": f.email,
                        "founder_linkedin": f.linkedin,
                        "founder_github": f.github,
                    })
            elif not only_emails:
                writer.writerow({**base, "founder_name": "", "founder_title": "",
                                 "founder_email": "", "founder_linkedin": "", "founder_github": ""})
        else:
            writer.writerow(base)

    if fh:
        fh.close()


def _progress(done: int, total: int) -> None:
    pct = done * 100 // total
    bar = "#" * (pct // 2) + "-" * (50 - pct // 2)
    print(f"\r  [{bar}] {done}/{total} ({pct}%)", end="", file=sys.stderr, flush=True)
    if done == total:
        print(file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape YC company & founder data to CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--batch", type=str, default=None, help="Filter by YC batch (e.g. W25, S24)")
    parser.add_argument("--industry", type=str, default=None, help="Filter by industry")
    parser.add_argument("--status", type=str, default=None, help="Filter by status (Active, Acquired, Public, Inactive)")
    parser.add_argument("--query", type=str, default="", help="Keyword search")
    parser.add_argument("--founders", action="store_true", help="Scrape individual company pages for founder details")
    parser.add_argument("--emails", action="store_true", help="Discover public emails (implies --founders)")
    parser.add_argument("--only-emails", action="store_true", help="Only include founders with a discovered email in the output")
    parser.add_argument("--max", type=int, default=None, help="Max companies to scrape founders for (default: all)")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers for founder scraping (default: 6)")
    parser.add_argument("-o", "--output", type=str, default="-", help="Output CSV path (default: stdout)")
    parser.add_argument("--list-batches", action="store_true", help="List all available batches and exit")
    parser.add_argument("--list-industries", action="store_true", help="List all available industries and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if args.list_batches:
        list_facets("batch")
        return
    if args.list_industries:
        list_facets("industries")
        return

    if args.only_emails:
        args.emails = True
    if args.emails:
        args.founders = True

    log.info("Fetching companies from YC directory...")
    companies = fetch_companies(
        batch_filter=args.batch,
        industry_filter=args.industry,
        query=args.query,
    )

    if args.status:
        companies = [c for c in companies if c.status.lower() == args.status.lower()]

    log.info("Found %d companies.", len(companies))

    if not companies:
        log.warning("No companies matched your filters.")
        sys.exit(0)

    if args.founders:
        to_scrape = companies[:args.max] if args.max else companies
        action = "founder details + emails" if args.emails else "founder details"
        log.info("Scraping %s for %d companies (%d workers)...", action, len(to_scrape), args.workers)

        t0 = time.perf_counter()
        scrape_founders_batch(
            to_scrape,
            max_workers=args.workers,
            delay=0.2,
            discover_emails=args.emails,
            progress_callback=_progress,
        )
        elapsed = time.perf_counter() - t0

        founder_count = sum(len(c.founders) for c in to_scrape)
        email_count = sum(1 for c in to_scrape for f in c.founders if f.email)
        log.info(
            "Done in %.1fs — %d founders, %d emails across %d companies.",
            elapsed, founder_count, email_count, len(to_scrape),
        )

        if args.max and len(companies) > args.max:
            companies = to_scrape + companies[args.max:]

    out = args.output
    if out == "-":
        log.info("Writing CSV to stdout...")
    else:
        log.info("Writing CSV to %s...", out)

    write_csv(companies, out, with_founders=args.founders, only_emails=args.only_emails)

    if out != "-":
        log.info("Saved %s", out)


if __name__ == "__main__":
    main()
