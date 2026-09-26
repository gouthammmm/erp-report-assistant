# LedgerLens — ERP Report Assistant

LedgerLens turns an ERP-style CSV export into an interactive monthly performance report. It is a local Flask application built with pandas and Matplotlib.

## Features

- KPI dashboard: revenue, average transaction value, gross profit, margin, units sold, revenue per unit, and latest-month change when the data supports them.
- Flexible month comparison: choose any two months in the report and compare revenue, transaction counts, and gross profit.
- Data-quality review: normalizes column headers, excludes rows with invalid dates or revenue, reports exact duplicates, and flags unreadable optional numeric values.
- Explainable alerts: highlights negative-margin transactions, large month-to-month changes, revenue concentration, and data issues.
- Charts and downloads: monthly trend with a rolling average, product and region breakdowns, a text narrative, and a monthly CSV export.
- Configurable display currency: AED, USD, EUR, GBP, INR, or SAR. This formats the output; it does not convert source data.
- Privacy-aware demo behavior: uploaded files are processed in memory, and reports are kept in a small, session-linked process cache. Nothing is written to the project folder.

The narrative is generated from report statistics using templates. It does not call an AI model or external API.

## Run locally

Create a virtual environment, activate it, install the requirements, and run the app:

    python -m venv .venv
    .venv\Scripts\Activate.ps1  (Windows PowerShell)
    source .venv/bin/activate    (macOS/Linux)
    pip install -r requirements.txt
    python app.py

Open http://127.0.0.1:5000. Select “Use the bundled sample dataset” to try the app without your own file.

To use a persistent session secret, set ERP_REPORT_SECRET_KEY before starting the app. The built-in Flask server is for local demos; use a production WSGI server and persistent storage before deploying for multiple users.

## CSV format

Only date and revenue are required. Header capitalization and extra spaces are normalized. Optional columns add further metrics and breakdowns:

date,region,product,category,units_sold,unit_price,revenue,cost
2026-01-05,Dubai,Widget A,Hardware,95,45.00,4275.00,2850.00

CSV uploads are limited to 16 MB. Invalid date/revenue rows are excluded and counted in the report. Optional numeric fields (cost, units_sold, and unit_price) are converted when possible; the report flags values that could not be read.

## Project structure

app.py                  Flask routes, session-linked report cache, and downloads
report_generator.py     CSV validation, KPI analysis, alerts, narrative, and charts
sample_data/            Bundled demo dataset
templates/               Upload and report pages
static/style.css         Responsive dashboard styling
requirements.txt         Python dependencies
