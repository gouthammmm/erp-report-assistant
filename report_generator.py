"""Data validation, analytics, anomaly detection, narrative, and chart generation."""

import base64
import io
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

REQUIRED_COLS = {"date", "revenue"}
CURRENCIES = {"AED": "UAE dirham", "USD": "US dollar", "EUR": "Euro", "GBP": "British pound", "INR": "Indian rupee", "SAR": "Saudi riyal"}


class ReportError(Exception):
    """An input or report configuration error that can be shown to the user."""


def _normalize_column(name: str) -> str:
    return "_".join(str(name).strip().lower().replace("-", " ").split())


def load_and_validate(file_stream):
    """Read a CSV, normalize its headers, and return clean rows plus a quality profile."""
    try:
        df = pd.read_csv(file_stream, skipinitialspace=True)
    except pd.errors.EmptyDataError as exc:
        raise ReportError("The CSV is empty. Add a header row and at least one data row.") from exc
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise ReportError("The file could not be read as a valid UTF-8 CSV.") from exc

    if df.empty and len(df.columns) == 0:
        raise ReportError("The CSV needs a header row and at least one data row.")

    normalized = [_normalize_column(name) for name in df.columns]
    if len(normalized) != len(set(normalized)):
        raise ReportError("Column names become duplicated after spaces and capitalization are normalized.")
    df.columns = normalized

    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ReportError(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Available columns: {', '.join(df.columns)}"
        )
    if df.empty:
        raise ReportError("The CSV has headers but no data rows.")

    rows_received = len(df)
    parsed_dates = pd.to_datetime(df["date"], errors="coerce", utc=True)
    parsed_revenue = pd.to_numeric(df["revenue"], errors="coerce")
    invalid_date = parsed_dates.isna()
    invalid_revenue = parsed_revenue.isna()
    valid = ~(invalid_date | invalid_revenue)
    if not valid.any():
        raise ReportError("No usable rows found. Check that date and revenue contain valid values.")

    quality = {
        "rows_received": int(rows_received),
        "rows_included": int(valid.sum()),
        "rows_excluded": int((~valid).sum()),
        "invalid_date_rows": int(invalid_date.sum()),
        "invalid_revenue_rows": int(invalid_revenue.sum()),
        "duplicate_rows": 0,
        "optional_numeric_issues": {},
    }

    df = df.loc[valid].copy()
    df["date"] = parsed_dates.loc[valid]
    df["revenue"] = parsed_revenue.loc[valid].astype(float)

    for column in ("cost", "units_sold", "unit_price"):
        if column in df.columns:
            original = df[column]
            numeric = pd.to_numeric(original, errors="coerce")
            non_empty = original.notna() & original.astype(str).str.strip().ne("")
            issue_count = int((numeric.isna() & non_empty).sum())
            if issue_count:
                quality["optional_numeric_issues"][column] = issue_count
            df[column] = numeric

    quality["duplicate_rows"] = int(df.duplicated().sum())
    for column in ("region", "product", "category"):
        if column in df.columns:
            df[column] = df[column].astype("string").str.strip().replace("", pd.NA).fillna("Unspecified")

    df["month"] = df["date"].dt.to_period("M").astype(str)
    return df, quality


def _monthly_series(df: pd.DataFrame, column: str) -> pd.Series:
    periods = pd.period_range(df["date"].min().to_period("M"), df["date"].max().to_period("M"), freq="M")
    values = df.groupby(df["date"].dt.to_period("M"))[column].sum(min_count=1)
    return values.reindex(periods, fill_value=0)


def summarize(df: pd.DataFrame, quality: dict, currency: str) -> dict:
    total_revenue = float(df["revenue"].sum())
    summary = {
        "row_count": int(len(df)),
        "date_range": (df["date"].min().date().isoformat(), df["date"].max().date().isoformat()),
        "currency": currency,
        "total_revenue": round(total_revenue, 2),
        "average_transaction_revenue": round(float(df["revenue"].mean()), 2),
        "median_transaction_revenue": round(float(df["revenue"].median()), 2),
        "data_quality": quality,
    }

    if "cost" in df.columns:
        cost_sum = df["cost"].sum(min_count=1)
        total_cost = float(cost_sum) if pd.notna(cost_sum) else 0.0
        gross_profit = total_revenue - total_cost
        summary["total_cost"] = round(total_cost, 2)
        summary["gross_profit"] = round(gross_profit, 2)
        summary["margin_pct"] = round(100 * gross_profit / total_revenue, 1) if total_revenue else 0

    if "units_sold" in df.columns:
        units_sum = df["units_sold"].sum(min_count=1)
        total_units = float(units_sum) if pd.notna(units_sum) else 0.0
        summary["total_units"] = round(total_units, 2)
        summary["revenue_per_unit"] = round(total_revenue / total_units, 2) if total_units else None

    monthly_revenue = _monthly_series(df, "revenue")
    summary["monthly_revenue"] = {str(period): round(float(value), 2) for period, value in monthly_revenue.items()}
    monthly_transactions = df.groupby(df["date"].dt.to_period("M")).size().reindex(monthly_revenue.index, fill_value=0)
    summary["monthly_transactions"] = {str(period): int(value) for period, value in monthly_transactions.items()}

    if "cost" in df.columns:
        monthly_cost = _monthly_series(df, "cost").fillna(0)
        summary["monthly_cost"] = {str(period): round(float(value), 2) for period, value in monthly_cost.items()}
        summary["monthly_profit"] = {
            str(period): round(float(monthly_revenue.loc[period] - monthly_cost.loc[period]), 2)
            for period in monthly_revenue.index
        }

    if len(monthly_revenue) >= 2:
        last, previous = float(monthly_revenue.iloc[-1]), float(monthly_revenue.iloc[-2])
        summary["mom_change_pct"] = round(100 * (last - previous) / previous, 1) if previous else None
        summary["last_month"] = str(monthly_revenue.index[-1])
        summary["previous_month"] = str(monthly_revenue.index[-2])
    else:
        summary["mom_change_pct"] = None

    for dimension in ("region", "product", "category"):
        if dimension in df.columns:
            totals = df.groupby(dimension, dropna=False)["revenue"].sum().sort_values(ascending=False)
            breakdown = {str(name): round(float(value), 2) for name, value in totals.items()}
            summary[f"by_{dimension}"] = breakdown
            summary[f"top_{dimension}"] = next(iter(breakdown), None)
            summary[f"bottom_{dimension}"] = next(reversed(breakdown), None) if breakdown else None

    return summary


def compare_months(summary: dict, earlier_month: str, later_month: str) -> dict:
    monthly = summary["monthly_revenue"]
    if earlier_month not in monthly or later_month not in monthly:
        raise ReportError("Choose two months that are included in this report.")
    if earlier_month == later_month:
        raise ReportError("Choose two different months to compare.")

    earlier, later = float(monthly[earlier_month]), float(monthly[later_month])
    change = later - earlier
    comparison = {
        "earlier_month": earlier_month,
        "later_month": later_month,
        "earlier_revenue": round(earlier, 2),
        "later_revenue": round(later, 2),
        "change_amount": round(change, 2),
        "change_pct": round(change * 100 / earlier, 1) if earlier else None,
        "currency": summary["currency"],
    }
    transactions = summary.get("monthly_transactions", {})
    comparison["earlier_transactions"] = transactions.get(earlier_month, 0)
    comparison["later_transactions"] = transactions.get(later_month, 0)
    if "monthly_profit" in summary:
        comparison["earlier_profit"] = summary["monthly_profit"].get(earlier_month, 0)
        comparison["later_profit"] = summary["monthly_profit"].get(later_month, 0)
    return comparison


def find_anomalies(df: pd.DataFrame, summary: dict) -> list:
    """Create explainable quality and business-rule alerts."""
    flags = []
    quality = summary["data_quality"]

    if quality["rows_excluded"]:
        flags.append({
            "level": "warning",
            "title": "Rows excluded from analysis",
            "detail": (
                f"{quality['rows_excluded']} of {quality['rows_received']} rows had an invalid date or revenue value. "
                "They were excluded from the calculations."
            ),
        })
    if quality["duplicate_rows"]:
        flags.append({
            "level": "info",
            "title": "Exact duplicate rows found",
            "detail": f"{quality['duplicate_rows']} exact duplicate row(s) were found. They remain included; review them if repeated records are unexpected.",
        })
    if quality["optional_numeric_issues"]:
        details = ", ".join(f"{name}: {count}" for name, count in quality["optional_numeric_issues"].items())
        flags.append({"level": "warning", "title": "Some optional values could not be read", "detail": details})

    if "cost" in df.columns:
        comparable = df["cost"].notna()
        loss_rows = df.loc[comparable & (df["revenue"] < df["cost"])]
        if len(loss_rows):
            impact = float((loss_rows["cost"] - loss_rows["revenue"]).sum())
            flags.append({
                "level": "warning",
                "title": "Transactions with negative gross margin",
                "detail": f"{len(loss_rows)} transaction(s) have cost above revenue, with {summary['currency']} {impact:,.2f} in combined shortfall.",
            })

    monthly = summary["monthly_revenue"]
    months = list(monthly)
    for previous_month, month in zip(months, months[1:]):
        previous, current = monthly[previous_month], monthly[month]
        if previous > 0:
            change = (current - previous) / previous
            if change < -0.15:
                flags.append({
                    "level": "warning",
                    "title": f"Revenue dropped {abs(change * 100):.1f}% in {month}",
                    "detail": f"Revenue moved from {summary['currency']} {previous:,.2f} in {previous_month} to {summary['currency']} {current:,.2f}.",
                })
            elif change > 0.30:
                flags.append({
                    "level": "info",
                    "title": f"Revenue jumped {change * 100:.1f}% in {month}",
                    "detail": f"Revenue moved from {summary['currency']} {previous:,.2f} in {previous_month} to {summary['currency']} {current:,.2f}.",
                })
        elif current > 0:
            flags.append({
                "level": "info",
                "title": f"Revenue activity resumed in {month}",
                "detail": f"No revenue was recorded in {previous_month}; {summary['currency']} {current:,.2f} was recorded in {month}.",
            })

    for dimension in ("product", "region"):
        breakdown = summary.get(f"by_{dimension}", {})
        total = sum(breakdown.values())
        if len(breakdown) > 1 and total > 0:
            name, value = next(iter(breakdown.items()))
            share = value / total
            if share > 0.5:
                flags.append({
                    "level": "info",
                    "title": f"Revenue concentration in {dimension}",
                    "detail": f"{name} accounts for {share * 100:.1f}% of revenue; performance is concentrated in this {dimension}.",
                })

    if not flags:
        flags.append({
            "level": "success",
            "title": "No major anomalies detected",
            "detail": "The checked data-quality and revenue rules did not find a major issue.",
        })
    return flags


def generate_narrative(summary: dict, flags: list) -> str:
    currency = summary["currency"]
    start, end = summary["date_range"]
    lines = [
        f"This report covers {summary['row_count']} usable transactions between {start} and {end}.",
        f"Total revenue was {currency} {summary['total_revenue']:,.2f}; average transaction revenue was {currency} {summary['average_transaction_revenue']:,.2f}.",
    ]
    if "gross_profit" in summary:
        lines.append(
            f"Total cost was {currency} {summary['total_cost']:,.2f}, producing gross profit of {currency} {summary['gross_profit']:,.2f} ({summary['margin_pct']}% margin)."
        )
    if summary.get("mom_change_pct") is not None:
        direction = "up" if summary["mom_change_pct"] >= 0 else "down"
        lines.append(
            f"Revenue in {summary['last_month']} was {direction} {abs(summary['mom_change_pct'])}% versus {summary['previous_month']}."
        )
    for dimension in ("region", "product", "category"):
        if summary.get(f"top_{dimension}"):
            lines.append(
                f"{summary[f'top_{dimension}']} led revenue by {dimension}; {summary[f'bottom_{dimension}']} was lowest."
            )
    lines.extend(["", "Review these report flags:"])
    for flag in flags:
        lines.append(f"• {flag['title']}: {flag['detail']}")
    return "\n".join(lines)


def _fig_to_base64(fig) -> str:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", dpi=140)
    plt.close(fig)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("ascii")


def make_charts(df: pd.DataFrame, summary: dict) -> dict:
    charts = {}
    monthly = summary["monthly_revenue"]
    labels = list(monthly)
    values = list(monthly.values())

    fig, ax = plt.subplots(figsize=(8, 3.8))
    positions = list(range(len(labels)))
    ax.plot(positions, values, marker="o", linewidth=2.5, color="#4f46e5", label="Revenue")
    if len(values) >= 3:
        moving_average = pd.Series(values).rolling(3, min_periods=1).mean().tolist()
        ax.plot(positions, moving_average, linewidth=2, linestyle="--", color="#0f766e", label="3-month average")
        ax.legend(frameon=False)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_title("Monthly revenue trend")
    ax.set_ylabel(summary["currency"])
    ax.grid(axis="y", alpha=0.2)
    charts["monthly_revenue"] = _fig_to_base64(fig)

    for dimension in ("product", "region"):
        breakdown = summary.get(f"by_{dimension}")
        if breakdown:
            items = sorted(breakdown.items(), key=lambda item: item[1])[-8:]
            fig, ax = plt.subplots(figsize=(8, max(3.4, 0.42 * len(items))))
            ax.barh([item[0] for item in items], [item[1] for item in items], color="#5b5ce2")
            ax.set_title(f"Revenue by {dimension}")
            ax.set_xlabel(summary["currency"])
            ax.grid(axis="x", alpha=0.2)
            charts[f"by_{dimension}"] = _fig_to_base64(fig)

    return charts


def build_report(file_stream, currency: str = "AED") -> dict:
    currency = str(currency).upper()
    if currency not in CURRENCIES:
        raise ReportError("Choose a supported display currency.")
    df, quality = load_and_validate(file_stream)
    summary = summarize(df, quality, currency)
    flags = find_anomalies(df, summary)
    return {
        "summary": summary,
        "flags": flags,
        "narrative": generate_narrative(summary, flags),
        "charts": make_charts(df, summary),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


