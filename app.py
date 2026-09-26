"""Flask routes for the ERP report assistant."""

import csv
import io
import os
import secrets

from flask import (
    Flask, Response, flash, redirect, render_template, request, send_file,
    session, url_for,
)
from werkzeug.utils import secure_filename

from report_generator import CURRENCIES, ReportError, build_report, compare_months

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CSV = os.path.join(BASE_DIR, "sample_data", "erp_sample.csv")
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_CACHED_REPORTS = 10

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("ERP_REPORT_SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Reports stay in process memory and are referenced by an unguessable id kept
# in the signed browser session. This is suitable for a local portfolio demo.
_report_cache = {}


def _current_report():
    report_id = session.get("report_id")
    return _report_cache.get(report_id) if report_id else None


def _save_report(item):
    report_id = secrets.token_urlsafe(24)
    _report_cache[report_id] = item
    while len(_report_cache) > MAX_CACHED_REPORTS:
        _report_cache.pop(next(iter(_report_cache)))
    session["report_id"] = report_id


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", currencies=CURRENCIES)


@app.route("/analyze", methods=["POST"])
def analyze():
    use_sample = request.form.get("use_sample") == "1"
    uploaded = request.files.get("csv_file")
    currency = request.form.get("currency", "AED").upper()

    try:
        if currency not in CURRENCIES:
            raise ReportError("Choose a supported display currency.")
        if use_sample:
            with open(SAMPLE_CSV, "rb") as sample_file:
                stream = io.BytesIO(sample_file.read())
            source_name = "Bundled sample dataset"
        elif uploaded and uploaded.filename:
            safe_name = secure_filename(uploaded.filename)
            if not safe_name or not safe_name.lower().endswith(".csv"):
                raise ReportError("Please choose a CSV file.")
            content = uploaded.read()
            if not content:
                raise ReportError("The selected CSV file is empty.")
            stream = io.BytesIO(content)
            source_name = safe_name
        else:
            raise ReportError("Choose a CSV file or select the bundled sample dataset.")

        report = build_report(stream, currency=currency)
        _save_report({"report": report, "source_name": source_name})
        return render_template("report.html", report=report, source_name=source_name)
    except ReportError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except Exception:
        app.logger.exception("Report generation failed")
        flash("The report could not be generated. Check that the CSV is valid and try again.", "error")
        return redirect(url_for("index"))


@app.route("/compare", methods=["POST"])
def compare():
    item = _current_report()
    if not item:
        flash("Generate a report before comparing months.", "error")
        return redirect(url_for("index"))
    try:
        summary = item["report"]["summary"]
        comparison = compare_months(
            summary,
            request.form.get("earlier_month", ""),
            request.form.get("later_month", ""),
        )
        return render_template(
            "report.html",
            report=item["report"],
            source_name=item["source_name"],
            comparison=comparison,
        )
    except ReportError as exc:
        flash(str(exc), "error")
        return render_template("report.html", report=item["report"], source_name=item["source_name"])


@app.route("/download")
def download():
    item = _current_report()
    if not item:
        flash("Generate a report before downloading it.", "error")
        return redirect(url_for("index"))
    buffer = io.BytesIO(item["report"]["narrative"].encode("utf-8"))
    return send_file(
        buffer,
        mimetype="text/plain; charset=utf-8",
        as_attachment=True,
        download_name="erp_report.txt",
    )


@app.route("/download/monthly.csv")
def download_monthly_csv():
    item = _current_report()
    if not item:
        flash("Generate a report before downloading it.", "error")
        return redirect(url_for("index"))

    summary = item["report"]["summary"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    headers = ["month", "currency", "revenue", "transactions"]
    if "monthly_cost" in summary:
        headers.extend(["cost", "gross_profit"])
    writer.writerow(headers)
    for month, revenue in summary["monthly_revenue"].items():
        row = [month, summary["currency"], f"{revenue:.2f}", summary["monthly_transactions"].get(month, 0)]
        if "monthly_cost" in summary:
            row.extend([
                f"{summary['monthly_cost'].get(month, 0):.2f}",
                f"{summary['monthly_profit'].get(month, 0):.2f}",
            ])
        writer.writerow(row)

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=monthly_report.csv"},
    )


@app.errorhandler(413)
def request_too_large(_error):
    flash("The file is larger than the 16 MB upload limit.", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # The built-in server is for a local demo. Debug mode stays off by default.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)


