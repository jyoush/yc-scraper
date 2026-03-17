"""
YC Founders Explorer — Streamlit web app.

Run with:  streamlit run app.py
"""

import io
import time
from dataclasses import asdict

import pandas as pd
import streamlit as st

from scraper import (
    Company,
    Founder,
    fetch_facets,
    fetch_companies,
    scrape_founders_batch,
)

# ── page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="YC Founders Explorer",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── custom CSS ───────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    h1 { margin-bottom: 0.2rem; }
    .stDataFrame { font-size: 0.85rem; }
    div[data-testid="stMetric"] {
        background: #f8f9fb; border-radius: 8px;
        padding: 12px 16px; border: 1px solid #e4e7ec;
    }
</style>
""", unsafe_allow_html=True)

# ── session state ────────────────────────────────────────────────────────────

if "companies" not in st.session_state:
    st.session_state.companies = []
if "founders_scraped" not in st.session_state:
    st.session_state.founders_scraped = False

# ── helpers ──────────────────────────────────────────────────────────────────

def _batch_sort_key(batch: str) -> tuple:
    """Sort batches reverse-chronologically: F25 > S25 > W25 > S24 ..."""
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


def companies_to_dataframe(companies: list[Company]) -> pd.DataFrame:
    rows = []
    for c in companies:
        if c.founders:
            for f in c.founders:
                rows.append({
                    "Founder Name": f.name,
                    "Title": f.title,
                    "Email": f.email,
                    "LinkedIn": f.linkedin,
                    "GitHub": f.github,
                    "Company": c.name,
                    "Batch": c.batch,
                    "Status": c.status,
                    "Industries": ", ".join(c.industries),
                    "One-Liner": c.one_liner,
                    "Website": c.website,
                    "Location": c.location,
                    "Team Size": c.team_size,
                    "YC Page": c.yc_url,
                })
        else:
            rows.append({
                "Founder Name": "",
                "Title": "",
                "Email": "",
                "LinkedIn": "",
                "GitHub": "",
                "Company": c.name,
                "Batch": c.batch,
                "Status": c.status,
                "Industries": ", ".join(c.industries),
                "One-Liner": c.one_liner,
                "Website": c.website,
                "Location": c.location,
                "Team Size": c.team_size,
                "YC Page": c.yc_url,
            })
    return pd.DataFrame(rows)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    return buf.getvalue()


# ── sidebar: load facets & build filters ─────────────────────────────────────

st.sidebar.title("Filters")

with st.sidebar:
    with st.spinner("Loading filter options..."):
        try:
            facets = fetch_facets()
        except Exception as exc:
            st.error(f"Could not load facets: {exc}")
            st.stop()

    batches_raw: dict = facets.get("batch", {})
    industries_raw: dict = facets.get("industries", {})
    statuses_raw: dict = facets.get("status", {})

    sorted_batches = sorted(batches_raw.keys(), key=_batch_sort_key)
    sorted_industries = sorted(industries_raw.keys())
    sorted_statuses = sorted(statuses_raw.keys())

    selected_batch = st.selectbox(
        "YC Batch",
        options=["All"] + sorted_batches,
        index=0,
        help="Filter by YC batch (e.g. S21, W24)",
    )

    selected_industry = st.selectbox(
        "Industry",
        options=["All"] + sorted_industries,
        index=0,
        help="Filter by primary industry",
    )

    selected_status = st.selectbox(
        "Company Status",
        options=["All"] + sorted_statuses,
        index=0,
        help="Active, Acquired, Public, Inactive",
    )

    search_query = st.text_input("Keyword search", "", help="Search company names / descriptions")

    st.divider()

    scrape_founders_toggle = st.toggle(
        "Scrape founder details",
        value=True,
        help="Fetch individual company pages for founder names, titles, and LinkedIn profiles. Slower but gives you the full picture.",
    )

    discover_emails_toggle = st.toggle(
        "Discover public emails",
        value=True,
        help="Search YC pages, company websites, and GitHub profiles for publicly listed email addresses. Only verified public emails are included.",
    )

    max_companies = st.slider(
        "Max companies to scrape",
        min_value=10,
        max_value=2000,
        value=200,
        step=10,
        help="Limit the number of companies to scrape founder details for (to control speed).",
    )

    run_button = st.button("🔍  Fetch Data", use_container_width=True, type="primary")

# ── main area ────────────────────────────────────────────────────────────────

st.title("YC Founders Explorer")
st.caption("Search and export Y Combinator founder & company data with filters for batch, industry, and more.")

if run_button:
    batch_arg = None if selected_batch == "All" else selected_batch
    industry_arg = None if selected_industry == "All" else selected_industry

    with st.status("Fetching companies from YC directory...", expanded=True) as status:
        st.write("Querying Algolia index...")
        companies = fetch_companies(
            batch_filter=batch_arg,
            industry_filter=industry_arg,
            query=search_query,
        )

        if selected_status != "All":
            companies = [c for c in companies if c.status == selected_status]

        st.write(f"Found **{len(companies)}** companies matching your filters.")

        if scrape_founders_toggle and companies:
            to_scrape = companies[:max_companies]
            emails_enabled = discover_emails_toggle
            extra = " (with email discovery)" if emails_enabled else ""
            st.write(f"Scraping founder details{extra} for {len(to_scrape)} companies...")

            progress_bar = st.progress(0)
            progress_text = st.empty()

            def _progress(done, total):
                progress_bar.progress(done / total)
                progress_text.text(f"{done}/{total} companies scraped")

            scrape_founders_batch(
                to_scrape,
                max_workers=10,
                delay=0.1,
                discover_emails=emails_enabled,
                progress_callback=_progress,
            )

            founder_count = sum(len(c.founders) for c in to_scrape)
            email_count = sum(
                1 for c in to_scrape for f in c.founders if f.email
            )
            st.write(f"Found **{founder_count}** founders across {len(to_scrape)} companies.")
            if emails_enabled:
                st.write(f"Discovered **{email_count}** public email addresses.")

            if len(companies) > max_companies:
                remaining = companies[max_companies:]
                companies = to_scrape + remaining
            else:
                companies = to_scrape

        status.update(label="Done!", state="complete")

    st.session_state.companies = companies
    st.session_state.founders_scraped = scrape_founders_toggle

# ── display results ──────────────────────────────────────────────────────────

companies: list[Company] = st.session_state.companies

if companies:
    df = companies_to_dataframe(companies)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Companies", df["Company"].nunique())
    col2.metric("Founders", df[df["Founder Name"] != ""].shape[0])
    col3.metric("Emails Found", df[df["Email"] != ""].shape[0])
    col4.metric("Batches", df["Batch"].nunique())
    col5.metric("Industries", df["Industries"].str.split(", ").explode().nunique())

    st.divider()

    tab_founders, tab_companies = st.tabs(["Founders View", "Companies View"])

    with tab_founders:
        founders_df = df[df["Founder Name"] != ""].reset_index(drop=True)
        if founders_df.empty:
            st.info("No founder details available. Enable **Scrape founder details** in the sidebar and re-fetch.")
        else:
            st.dataframe(
                founders_df,
                use_container_width=True,
                height=600,
                column_config={
                    "Email": st.column_config.TextColumn("Email"),
                    "Website": st.column_config.LinkColumn("Website"),
                    "YC Page": st.column_config.LinkColumn("YC Page"),
                    "LinkedIn": st.column_config.LinkColumn("LinkedIn"),
                    "GitHub": st.column_config.LinkColumn("GitHub"),
                },
            )
            st.download_button(
                "📥  Download Founders CSV",
                data=to_csv_bytes(founders_df),
                file_name="yc_founders.csv",
                mime="text/csv",
                use_container_width=True,
            )

    with tab_companies:
        companies_df = df.drop_duplicates(subset=["Company"]).drop(
            columns=["Founder Name", "Title", "Email", "LinkedIn", "GitHub"]
        ).reset_index(drop=True)
        st.dataframe(
            companies_df,
            use_container_width=True,
            height=600,
            column_config={
                "Website": st.column_config.LinkColumn("Website"),
                "YC Page": st.column_config.LinkColumn("YC Page"),
            },
        )
        st.download_button(
            "📥  Download Companies CSV",
            data=to_csv_bytes(companies_df),
            file_name="yc_companies.csv",
            mime="text/csv",
            use_container_width=True,
        )
else:
    st.info("Configure your filters in the sidebar and click **Fetch Data** to get started.")
