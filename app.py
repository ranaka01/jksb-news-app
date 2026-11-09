from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

import streamlit as st

from news_collector import COLOMBO, SOURCE_NAMES, Article, collect_news, latest_report_date


st.set_page_config(page_title="JKSB Opening Bell", page_icon="📰", layout="wide")


def format_timestamp(article: Article) -> str:
    if article.published_at is None:
        return "Publication time unavailable"
    if not article.time_verified:
        return article.published_at.strftime("%d %b %Y") + " · exact time unavailable"
    return article.published_at.strftime("%d %b %Y · %I:%M %p")


def build_snippet(articles: list[Article], report_date) -> str:
    blocks = ["*JKSB Opening Bell – News Snippets*", f"*{report_date:%d.%m.%Y}*"]
    for article in articles:
        title = st.session_state.get(f"edit_title_{article.id}", article.title).strip()
        summary = st.session_state.get(f"edit_summary_{article.id}", article.summary).strip()
        blocks.extend(["", f"*{title}*", f"“{summary}”", "", article.url])
    return "\n".join(blocks)


def save_report(content: str, report_date) -> Path:
    folder = Path("data") / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"jksb_opening_bell_{report_date:%Y-%m-%d}.txt"
    target.write_text(content, encoding="utf-8")
    return target.resolve()


st.title("JKSB Opening Bell – News Collector")
st.caption("Collect, review, select, and format market-relevant news from the previous 24-hour window.")

with st.sidebar:
    st.header("Report settings")
    end_date = st.date_input(
        "End date",
        value=latest_report_date(),
        help="This date is also used in the generated JKSB heading.",
    )
    end_time = st.time_input("End time", value=time(8, 30), step=300)
    start_date = st.date_input("Start date", value=end_date - timedelta(days=1))
    start_time = st.time_input("Start time", value=time(8, 30), step=300)
    selected_sources = st.multiselect("Publishers", SOURCE_NAMES, default=SOURCE_NAMES)
    minimum_score = st.slider(
        "Minimum relevance score",
        min_value=0,
        max_value=15,
        value=3,
        help="Use 0 to inspect every collected article. Higher values focus on markets and the Sri Lankan economy.",
    )
    st.caption("Timezone: Asia/Colombo")

start = datetime.combine(start_date, start_time, tzinfo=COLOMBO)
end = datetime.combine(end_date, end_time, tzinfo=COLOMBO)
report_date = end_date
valid_window = start < end
duration = end - start if valid_window else timedelta(0)
st.info(
    f"Collection window: **{start:%d %b %Y, %I:%M %p}** to "
    f"**{end:%d %b %Y, %I:%M %p}** (end time excluded)"
)
if valid_window:
    hours = duration.total_seconds() / 3600
    st.caption(f"Selected period: {hours:g} hours")
else:
    st.error("The start date and time must be earlier than the end date and time.")

if st.button("Collect news", type="primary", disabled=not selected_sources or not valid_window):
    with st.spinner("Reading publisher feeds and ranking articles…"):
        articles, errors = collect_news(selected_sources, start, end)
    st.session_state["articles"] = articles
    st.session_state["collection_errors"] = errors
    st.session_state["collection_window"] = (start.isoformat(), end.isoformat())

articles: list[Article] = st.session_state.get("articles", [])
errors: list[str] = st.session_state.get("collection_errors", [])
current_window = (start.isoformat(), end.isoformat())

if articles and st.session_state.get("collection_window") != current_window:
    st.warning("The time period has changed. Click **Collect news** to refresh the candidate list for this period.")

if errors:
    with st.expander(f"{len(errors)} source warning(s)"):
        for error in errors:
            st.warning(error)

if articles:
    visible = [article for article in articles if article.score >= minimum_score]
    selected_count = sum(bool(st.session_state.get(f"pick_{article.id}")) for article in articles)
    metric_a, metric_b, metric_c = st.columns(3)
    metric_a.metric("Collected in window", len(articles))
    metric_b.metric("Shown after filter", len(visible))
    metric_c.metric("Selected", selected_count)

    st.subheader("Candidate news")
    st.caption("Select stories, then edit their headline and summary below. Open the source link to verify wording before distribution.")

    if not visible:
        st.warning("No articles meet this score. Lower the relevance score in the sidebar.")

    for article in visible:
        left, right = st.columns([0.06, 0.94])
        with left:
            st.checkbox("Select", key=f"pick_{article.id}", label_visibility="collapsed")
        with right:
            st.markdown(f"**[{article.title}]({article.url})**")
            st.caption(
                f"{article.source} · {format_timestamp(article)} · "
                f"{article.category} · relevance {article.score}"
            )
            if article.summary:
                st.write(article.summary)
            else:
                st.write("_No publisher summary was supplied; add one during review._")
        st.divider()

    selected = [article for article in articles if st.session_state.get(f"pick_{article.id}")]
    if selected:
        st.subheader("Review selected stories")
        st.warning("Automated text may be incomplete. Verify figures, names, and quotations against each linked article.")
        for index, article in enumerate(selected, 1):
            with st.expander(f"{index}. {article.title}", expanded=True):
                st.text_input("Headline", value=article.title, key=f"edit_title_{article.id}")
                st.text_area(
                    "Snippet text",
                    value=article.summary,
                    height=130,
                    key=f"edit_summary_{article.id}",
                    help="Keep this concise and fact-based. Quotation marks are added automatically.",
                )
                st.link_button("Open original article", article.url)

        snippet = build_snippet(selected, report_date)
        st.subheader("Final formatted snippet")
        st.code(snippet, language=None)
        st.download_button(
            "Download snippet",
            data=snippet,
            file_name=f"jksb_opening_bell_{report_date:%Y-%m-%d}.txt",
            mime="text/plain",
        )
        if st.button("Save report locally"):
            saved = save_report(snippet, report_date)
            st.success(f"Saved to {saved}")
else:
    st.markdown(
        "Click **Collect news** to retrieve candidates. The first run can take several seconds because each publisher is checked independently."
    )

st.caption(
    "This tool assists editorial research; it does not provide investment advice. Publisher timestamps and article text remain subject to source-site changes."
)

