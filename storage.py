"""SQLite storage for OutreachDesk. Everything is written to disk immediately,
so a crash or restart never loses progress."""
import os
import sqlite3
import threading
from datetime import datetime

_init_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": "587",
    "smtp_security": "starttls",   # starttls | ssl | none
    "smtp_user": "",
    "smtp_password": "",
    "from_name": "",
    "delay_min": "60",
    "delay_max": "90",
    "daily_cap": "100",
    "max_retries": "2",
    "template_mode": "round_robin",  # round_robin | random
    "optout_line": 'If you would rather not hear from me, just reply "unsubscribe" and I will not email you again.',
    "report_to": "",
    "report_time": "20:00",
    "practice_mode": "0",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL UNIQUE,
    company TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | failed | skipped
    template_id INTEGER,
    sent_at TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS templates(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS send_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER,
    email TEXT NOT NULL,
    template_id INTEGER,
    at TEXT NOT NULL,
    day TEXT NOT NULL,
    status TEXT NOT NULL,                     -- sent | failed | practice
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_day ON send_log(day);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS reports(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL,
    text TEXT,
    html TEXT,
    emailed INTEGER NOT NULL DEFAULT 0,
    email_error TEXT,
    path TEXT
);
"""


def data_dir():
    override = os.environ.get("OUTREACHDESK_HOME")
    if override:
        path = override
    elif os.name == "nt":
        path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "OutreachDesk")
    else:
        path = os.path.join(os.path.expanduser("~"), ".outreachdesk")
    os.makedirs(os.path.join(path, "reports"), exist_ok=True)
    return path


def db_path():
    return os.path.join(data_dir(), "outreachdesk.db")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _connect():
    c = sqlite3.connect(db_path(), timeout=20)
    c.row_factory = sqlite3.Row
    return c


def query(sql, args=()):
    c = _connect()
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def execute(sql, args=()):
    """Run a write statement. Returns (lastrowid, rowcount)."""
    c = _connect()
    try:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid, cur.rowcount
    finally:
        c.close()


def init():
    with _init_lock:
        c = _connect()
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            for k, v in DEFAULT_SETTINGS.items():
                c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
            c.commit()
        finally:
            c.close()


# ---------- settings ----------
def get_settings():
    s = dict(DEFAULT_SETTINGS)
    for row in query("SELECT key, value FROM settings"):
        s[row["key"]] = row["value"]
    return s


def save_settings(values):
    c = _connect()
    try:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (k, str(v)))
        c.commit()
    finally:
        c.close()


# ---------- contacts ----------
def add_contacts(rows):
    """rows: list of dicts with name/email/company. Returns (added, duplicates)."""
    added = dups = 0
    c = _connect()
    try:
        for r in rows:
            cur = c.execute(
                "INSERT OR IGNORE INTO contacts(name, email, company) VALUES(?, ?, ?)",
                (r.get("name", ""), r["email"].lower(), r.get("company", "")),
            )
            if cur.rowcount:
                added += 1
            else:
                dups += 1
        c.commit()
    finally:
        c.close()
    return added, dups


def next_pending():
    rows = query("SELECT * FROM contacts WHERE status='pending' ORDER BY id LIMIT 1")
    return rows[0] if rows else None


def counts():
    base = {"pending": 0, "sent": 0, "failed": 0, "skipped": 0}
    for r in query("SELECT status, COUNT(*) AS n FROM contacts GROUP BY status"):
        base[r["status"]] = r["n"]
    base["total"] = sum(base.values())
    return base


def list_contacts(status="", search="", limit=500):
    sql = "SELECT * FROM contacts WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if search:
        sql += " AND (email LIKE ? OR name LIKE ? OR company LIKE ?)"
        args += [f"%{search}%"] * 3
    sql += " ORDER BY id LIMIT ?"
    args.append(int(limit))
    return query(sql, args)


def strip_statuses():
    return [r["status"] for r in query("SELECT status FROM contacts ORDER BY id")]


def mark_sent(contact_id, template_id, note=None):
    execute(
        "UPDATE contacts SET status='sent', template_id=?, sent_at=?, error=?, attempts=attempts+1 WHERE id=?",
        (template_id, now_str(), note, contact_id),
    )


def mark_failed(contact_id, template_id, error, attempts_used):
    execute(
        "UPDATE contacts SET status='failed', template_id=?, error=?, attempts=attempts+? WHERE id=?",
        (template_id, error[:500], attempts_used, contact_id),
    )


def retry_failed(contact_id=None):
    if contact_id:
        return execute("UPDATE contacts SET status='pending', error=NULL WHERE id=? AND status='failed'", (contact_id,))[1]
    return execute("UPDATE contacts SET status='pending', error=NULL WHERE status='failed'")[1]


def set_skipped(contact_id, skipped=True):
    if skipped:
        execute("UPDATE contacts SET status='skipped' WHERE id=? AND status IN ('pending','failed')", (contact_id,))
    else:
        execute("UPDATE contacts SET status='pending' WHERE id=? AND status='skipped'", (contact_id,))


def reset_all_to_pending():
    """Used after practice runs. Also removes practice log rows."""
    execute("UPDATE contacts SET status='pending', error=NULL, sent_at=NULL, template_id=NULL, attempts=0")
    execute("DELETE FROM send_log WHERE status='practice'")


def clear_contacts():
    execute("DELETE FROM contacts")


# ---------- templates ----------
def add_templates(rows):
    n = 0
    for r in rows:
        execute("INSERT INTO templates(subject, body) VALUES(?, ?)", (r["subject"], r["body"]))
        n += 1
    return n


def list_templates(active_only=False):
    sql = "SELECT * FROM templates"
    if active_only:
        sql += " WHERE active=1"
    return query(sql + " ORDER BY id")


# ---------- log ----------
def log_send(contact_id, email, template_id, status, error=None):
    execute(
        "INSERT INTO send_log(contact_id, email, template_id, at, day, status, error) VALUES(?,?,?,?,?,?,?)",
        (contact_id, email, template_id, now_str(), today_str(), status, error),
    )


def sent_today():
    return query("SELECT COUNT(*) AS n FROM send_log WHERE day=? AND status IN ('sent','practice')", (today_str(),))[0]["n"]


def recent_log(limit=40):
    return query("SELECT * FROM send_log ORDER BY id DESC LIMIT ?", (int(limit),))


def log_total_sends():
    return query("SELECT COUNT(*) AS n FROM send_log WHERE status IN ('sent','practice')")[0]["n"]
