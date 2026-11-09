# JKSB Opening Bell News Collector

A local Streamlit application that collects market-relevant news from:

- [BBC Business](https://www.bbc.com/business)
- [EconomyNext Economy](https://economynext.com/economy/)
- [Daily FT](https://www.ft.lk/)
- [Al Jazeera Economy](https://www.aljazeera.com/economy/)
- [Ada Derana Biz](https://bizenglish.adaderana.lk/)

It defaults to **previous day 8:30 a.m. through report day 8:30 a.m. in Asia/Colombo**. Both dates and times can be changed in the sidebar. The app ranks candidate stories and lets an analyst select and edit the final JKSB Opening Bell snippet.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run each morning

Double-click `run_app.bat`, or run the following commands in PowerShell:

```powershell
.venv\Scripts\Activate.ps1
streamlit run app.py
```

The browser interface opens automatically. Confirm or change the start and end dates/times, click **Collect news**, select the required stories, edit the wording, and download or locally save the final text.

## How the time window works

For a report dated `09.11.2026`, the default period includes timestamped articles published from `08.11.2026 08:30` up to—but not including—`09.11.2026 08:30` in `Asia/Colombo`. Use the sidebar controls for a custom period.

The collector uses the publishers' section feeds where available and discovers article links from the specified section pages for Al Jazeera Economy and Ada Derana Biz. Article metadata is used to enforce the selected time window.

## Relevance ranking

The ranking is transparent and keyword-based. Sri Lankan/CSE company news, earnings, rates, foreign exchange, debt, inflation, trade, policy, fuel, and commodity stories receive higher scores. Adjust the minimum score in the sidebar to widen or narrow the candidate list.

The generated summaries are source-feed excerpts, not AI-written investment analysis. Always verify numerical claims and wording using the original article link before distribution.
