"""Local web server. Listens on 127.0.0.1 only; the dashboard is a single HTML page."""
import csv
import io
import os
import sys
import time

from flask import Flask, Response, abort, jsonify, request, send_file

import importer
import mailer
import reports
import storage
from engine import engine


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

SECRET_KEYS = {"smtp_password"}


@app.before_request
def guard():
    # Only accept requests addressed to this machine, and require a custom header on writes
    # so another website open in your browser cannot trigger actions.
    host = (request.host or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        abort(403)
    if request.method in ("POST", "PUT", "DELETE") and request.headers.get("X-Requested-With") != "OutreachDesk":
        abort(403)


def ok(**kw):
    return jsonify({"ok": True, **kw})


def fail(message, code=400):
    return jsonify({"ok": False, "error": message}), code


@app.get("/")
def index():
    return send_file(resource_path("web", "index.html"))


@app.get("/api/state")
def state():
    s = storage.get_settings()
    return jsonify({
        "engine": {
            "state": engine.state,
            "message": engine.message,
            "next_send_at": engine.next_send_at,
            "now": time.time(),
            "current": engine.current_email,
            "practice": s["practice_mode"] == "1",
        },
        "counts": storage.counts(),
        "sent_today": storage.sent_today(),
        "daily_cap": int(s["daily_cap"]),
        "strip": storage.strip_statuses(),
        "log": storage.recent_log(40),
        "failed": storage.list_contacts(status="failed", limit=200),
        "templates_active": len(storage.list_templates(active_only=True)),
    })


# ---------- contacts ----------
@app.get("/api/contacts")
def contacts():
    return jsonify(storage.list_contacts(request.args.get("status", ""), request.args.get("q", ""), 1000))


@app.post("/api/import/contacts")
def import_contacts():
    f = request.files.get("file")
    if not f:
        return fail("Choose a file first.")
    try:
        rows, invalid = importer.read_contacts(f.filename or "", f.read())
    except importer.ImportProblem as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail(f"Could not read that file: {exc}")
    added, dups = storage.add_contacts(rows)
    return ok(added=added, duplicates=dups, invalid=invalid)


@app.post("/api/contacts/retry-failed")
def retry_failed():
    return ok(count=storage.retry_failed())


@app.post("/api/contacts/<int:cid>/retry")
def retry_one(cid):
    return ok(count=storage.retry_failed(cid))


@app.post("/api/contacts/<int:cid>/skip")
def skip_one(cid):
    storage.set_skipped(cid, True)
    return ok()


@app.post("/api/contacts/<int:cid>/unskip")
def unskip_one(cid):
    storage.set_skipped(cid, False)
    return ok()


@app.post("/api/contacts/reset")
def reset_contacts():
    if engine.is_alive():
        return fail("Stop the campaign first.")
    storage.reset_all_to_pending()
    return ok()


@app.post("/api/contacts/clear")
def clear_contacts():
    if engine.is_alive():
        return fail("Stop the campaign first.")
    storage.clear_contacts()
    return ok()


@app.get("/api/export/failed.csv")
def export_failed():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "name", "company", "error"])
    for c in storage.list_contacts(status="failed", limit=100000):
        w.writerow([c["email"], c["name"], c["company"], c["error"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=failed_emails.csv"})


@app.get("/api/sample.xlsx")
def sample():
    return Response(importer.sample_workbook(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=OutreachDesk_sample.xlsx"})


# ---------- templates ----------
@app.get("/api/templates")
def templates():
    return jsonify(storage.list_templates())


@app.post("/api/import/templates")
def import_templates():
    f = request.files.get("file")
    if not f:
        return fail("Choose a file first.")
    try:
        rows = importer.read_templates(f.filename or "", f.read())
    except importer.ImportProblem as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail(f"Could not read that file: {exc}")
    return ok(added=storage.add_templates(rows))


@app.post("/api/templates")
def add_template():
    data = request.get_json(force=True, silent=True) or {}
    subject, body = (data.get("subject") or "").strip(), (data.get("body") or "").strip()
    if not subject or not body:
        return fail("Both subject and body are required.")
    storage.add_templates([{"subject": subject, "body": body}])
    return ok()


@app.put("/api/templates/<int:tid>")
def update_template(tid):
    data = request.get_json(force=True, silent=True) or {}
    subject, body = (data.get("subject") or "").strip(), (data.get("body") or "").strip()
    if not subject or not body:
        return fail("Both subject and body are required.")
    storage.execute("UPDATE templates SET subject=?, body=?, active=? WHERE id=?",
                    (subject, body, 1 if data.get("active", True) else 0, tid))
    return ok()


@app.delete("/api/templates/<int:tid>")
def delete_template(tid):
    storage.execute("DELETE FROM templates WHERE id=?", (tid,))
    return ok()


# ---------- settings ----------
@app.get("/api/settings")
def get_settings():
    s = storage.get_settings()
    out = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    out["has_password"] = bool(s["smtp_password"])
    return jsonify(out)


@app.post("/api/settings")
def save_settings():
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("smtp_password"):
        data.pop("smtp_password", None)       # blank means keep the saved one
    try:
        lo, hi = int(data.get("delay_min", 60)), int(data.get("delay_max", 90))
        cap = int(data.get("daily_cap", 100))
        if lo < 1 or hi < lo or cap < 1:
            raise ValueError
    except (TypeError, ValueError):
        return fail("Delay must be whole seconds with max at least min, and the daily cap at least 1.")
    storage.save_settings(data)
    return ok()


@app.post("/api/settings/test-login")
def test_login():
    good, msg = mailer.test_connection(storage.get_settings())
    return jsonify({"ok": good, "message": msg})


@app.post("/api/settings/test-email")
def test_email():
    s = storage.get_settings()
    to = (request.get_json(force=True, silent=True) or {}).get("to") or s["report_to"] or s["smtp_user"]
    if not to:
        return fail("Enter an address to send the test to.")
    try:
        mailer.send(s, to, "OutreachDesk test email", "If you can read this, sending works.", outreach=False)
    except mailer.SendError as exc:
        return jsonify({"ok": False, "message": str(exc)})
    return jsonify({"ok": True, "message": f"Test email sent to {to}."})


# ---------- campaign control ----------
@app.post("/api/start")
def start():
    good, msg = engine.start()
    return (ok(message=msg) if good else fail(msg))


@app.post("/api/pause")
def pause():
    engine.pause()
    return ok()


@app.post("/api/resume")
def resume():
    engine.resume()
    return ok()


@app.post("/api/stop")
def stop():
    engine.stop()
    return ok()


# ---------- reports ----------
@app.get("/api/reports")
def list_reports():
    return jsonify(storage.query(
        "SELECT id, day, created_at, emailed, email_error, path FROM reports ORDER BY id DESC LIMIT 60"))


@app.post("/api/reports/generate")
def generate_report():
    data = request.get_json(force=True, silent=True) or {}
    rep = reports.generate(send=bool(data.get("send", True)))
    return ok(report=rep)


@app.post("/api/reports/<int:rid>/email")
def email_report(rid):
    good, msg = reports.email_report(rid)
    return jsonify({"ok": good, "message": msg})


@app.get("/api/reports/<int:rid>/html")
def report_html(rid):
    rows = storage.query("SELECT html FROM reports WHERE id=?", (rid,))
    if not rows:
        abort(404)
    return Response(f"<!doctype html><meta charset='utf-8'><body style='margin:16px'>{rows[0]['html']}",
                    mimetype="text/html")
