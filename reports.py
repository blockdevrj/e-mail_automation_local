"""Daily report: builds plain-text + HTML, saves a copy to disk, emails it."""
import html as htmllib
import os
from datetime import datetime

import mailer
import storage


def _esc(v):
    return htmllib.escape(str(v if v is not None else ""))


def _duration(first, last):
    try:
        a = datetime.strptime(first, "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return "n/a"
    secs = int((b - a).total_seconds())
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


def collect(day):
    logs = storage.query("SELECT * FROM send_log WHERE day=? AND status IN ('sent','failed') ORDER BY id", (day,))
    sent = [r for r in logs if r["status"] == "sent"]
    failed = [r for r in logs if r["status"] == "failed"]
    total_attempted = len(logs)
    per_template = {}
    for r in sent:
        per_template[r["template_id"]] = per_template.get(r["template_id"], 0) + 1
    reasons = {}
    for r in failed:
        key = (r["error"] or "Unknown error")[:120]
        reasons[key] = reasons.get(key, 0) + 1
    templates = {t["id"]: t for t in storage.list_templates()}
    c = storage.counts()
    return {
        "day": day,
        "sent": sent,
        "failed": failed,
        "attempted": total_attempted,
        "rate": (len(sent) / total_attempted * 100) if total_attempted else 0.0,
        "first": logs[0]["at"] if logs else None,
        "last": logs[-1]["at"] if logs else None,
        "per_template": per_template,
        "reasons": reasons,
        "templates": templates,
        "queue": c,
    }


def _template_label(data, tid):
    t = data["templates"].get(tid)
    if not t:
        return f"Template #{tid}" if tid else "Template (deleted)"
    subj = t["subject"]
    return f'#{tid} "{subj[:45]}{"..." if len(subj) > 45 else ""}"'


def build(day=None):
    day = day or storage.today_str()
    d = collect(day)
    q = d["queue"]
    lines = [
        f"OutreachDesk daily report for {day}",
        "=" * 44,
        f"Sent:        {len(d['sent'])}",
        f"Failed:      {len(d['failed'])}",
        f"Success rate: {d['rate']:.0f}%",
        f"Time window: {d['first'] or 'n/a'} to {d['last'] or 'n/a'} ({_duration(d['first'], d['last'])})",
        f"Still queued: {q['pending']} of {q['total']} contacts",
        "",
        "Sent per template:",
    ]
    lines += [f"  {_template_label(d, t)}: {n}" for t, n in sorted(d["per_template"].items(), key=lambda x: str(x[0]))] or ["  none"]
    lines += ["", "Failure reasons:"]
    lines += [f"  {n} x {r}" for r, n in sorted(d["reasons"].items(), key=lambda x: -x[1])] or ["  none"]
    if d["failed"]:
        lines += ["", "Failed addresses:"] + [f"  {r['email']}  ({(r['error'] or '')[:100]})" for r in d["failed"]]
    if d["sent"]:
        lines += ["", "Sent addresses:"] + [f"  {r['at'][11:]}  {r['email']}" for r in d["sent"]]
    text = "\n".join(lines)

    def row(cells, head=False):
        tag = "th" if head else "td"
        style = "padding:6px 10px;border-bottom:1px solid #e3e7eb;text-align:left;font-size:13px;"
        return "<tr>" + "".join(f'<{tag} style="{style}">{_esc(c)}</{tag}>' for c in cells) + "</tr>"

    def table(rows, head):
        if not rows:
            return '<p style="color:#5b6b7a;font-size:13px">None</p>'
        return ('<table style="border-collapse:collapse;width:100%;margin:6px 0 18px">'
                + row(head, True) + "".join(row(r) for r in rows) + "</table>")

    def stat(label, value, color):
        return (f'<td style="padding:0 28px 0 0"><div style="font-size:30px;font-weight:600;color:{color}">{_esc(value)}</div>'
                f'<div style="font-size:12px;color:#5b6b7a">{_esc(label)}</div></td>')

    html = f"""<div style="font-family:Segoe UI,Arial,sans-serif;color:#16212c;max-width:680px">
<h2 style="margin:0 0 4px">Daily outreach report</h2>
<div style="color:#5b6b7a;font-size:13px;margin-bottom:18px">{_esc(day)} &middot; window {_esc(d['first'] or 'n/a')} to {_esc(d['last'] or 'n/a')} ({_esc(_duration(d['first'], d['last']))})</div>
<table style="margin-bottom:22px"><tr>
{stat('sent', len(d['sent']), '#1f7a4d')}{stat('failed', len(d['failed']), '#c0392b')}
{stat('success rate', f"{d['rate']:.0f}%", '#16212c')}{stat('still queued', q['pending'], '#8a5a00')}
</tr></table>
<h3 style="margin:0">Sent per template</h3>
{table([[_template_label(d, t), n] for t, n in sorted(d['per_template'].items(), key=lambda x: str(x[0]))], ['Template', 'Sent'])}
<h3 style="margin:0">Failure reasons</h3>
{table([[n, r] for r, n in sorted(d['reasons'].items(), key=lambda x: -x[1])], ['Count', 'Reason'])}
<h3 style="margin:0">Failed addresses</h3>
{table([[r['email'], r['error']] for r in d['failed']], ['Address', 'Error'])}
<h3 style="margin:0">Sent addresses</h3>
{table([[r['at'][11:], r['email']] for r in d['sent']], ['Time', 'Address'])}
</div>"""
    return {"day": day, "text": text, "html": html, "sent": len(d["sent"]), "failed": len(d["failed"])}


def generate(day=None, send=True):
    """Build, save to disk and database, optionally email. Returns the stored report row."""
    rep = build(day)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(storage.data_dir(), "reports", f"report_{stamp}.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"<!doctype html><meta charset='utf-8'><title>Report {rep['day']}</title>{rep['html']}")
    rid, _ = storage.execute(
        "INSERT INTO reports(day, created_at, text, html, path) VALUES(?,?,?,?,?)",
        (rep["day"], storage.now_str(), rep["text"], rep["html"], path),
    )
    if send:
        email_report(rid)
    return storage.query("SELECT id, day, created_at, emailed, email_error, path FROM reports WHERE id=?", (rid,))[0]


def email_report(report_id):
    s = storage.get_settings()
    rows = storage.query("SELECT * FROM reports WHERE id=?", (report_id,))
    if not rows:
        return False, "Report not found."
    rep = rows[0]
    to = (s.get("report_to") or "").strip()
    if not to:
        storage.execute("UPDATE reports SET email_error=? WHERE id=?", ("No report recipient set in Settings.", report_id))
        return False, "No report recipient set in Settings."
    if s.get("practice_mode") == "1":
        storage.execute("UPDATE reports SET email_error=? WHERE id=?", ("Practice mode: report saved but not emailed.", report_id))
        return False, "Practice mode: report saved but not emailed."
    try:
        mailer.send(s, to, f"Outreach report {rep['day']}", rep["text"], rep["html"], outreach=False)
    except mailer.SendError as exc:
        storage.execute("UPDATE reports SET emailed=0, email_error=? WHERE id=?", (str(exc), report_id))
        return False, str(exc)
    storage.execute("UPDATE reports SET emailed=1, email_error=NULL WHERE id=?", (report_id,))
    return True, f"Report emailed to {to}."


def emailed_today():
    return bool(storage.query("SELECT 1 FROM reports WHERE day=? AND emailed=1 LIMIT 1", (storage.today_str(),)))


def had_activity_today():
    return bool(storage.query(
        "SELECT 1 FROM send_log WHERE day=? AND status IN ('sent','failed') LIMIT 1", (storage.today_str(),)))
