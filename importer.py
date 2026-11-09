"""Read contacts and templates from .xlsx or .csv files.

A workbook may hold both: one sheet with an email column (contacts) and one
sheet with subject + body columns (templates). Each import picks the sheet that
matches, so you can upload the same file to both import buttons.
"""
import csv
import io
import re

from openpyxl import load_workbook

EMAIL_RE = re.compile(r"^[^@\s,;<>()]+@[^@\s,;<>()]+\.[A-Za-z]{2,}$")

EMAIL_HEADERS = {"email", "e-mail", "email address", "mail", "emailid", "email id", "e-mail address"}
NAME_HEADERS = {"name", "full name", "contact", "contact name", "first name", "firstname"}
COMPANY_HEADERS = {"company", "organisation", "organization", "business", "firm"}
SUBJECT_HEADERS = {"subject", "subject line", "title"}
BODY_HEADERS = {"body", "email body", "message", "content", "text", "email"}


class ImportProblem(Exception):
    pass


def _norm(v):
    return str(v).strip().lower() if v is not None else ""


def _sheets(filename, data):
    """Yield (sheet_name, rows) where rows is a list of lists of cell values."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        for ws in wb.worksheets:
            yield ws.title, [list(r) for r in ws.iter_rows(values_only=True)]
    elif lower.endswith((".csv", ".txt")):
        text = data.decode("utf-8-sig", errors="replace")
        yield "csv", [r for r in csv.reader(io.StringIO(text))]
    else:
        raise ImportProblem("Unsupported file type. Use .xlsx or .csv.")


def _find(headers, wanted):
    for i, h in enumerate(headers):
        if h in wanted:
            return i
    return None


def _cell(row, idx):
    if idx is None or idx >= len(row) or row[idx] is None:
        return ""
    return str(row[idx]).strip()


def read_contacts(filename, data):
    """Returns (rows, invalid_count, skipped_blank)."""
    for sheet, rows in _sheets(filename, data):
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        if not rows:
            continue
        headers = [_norm(c) for c in rows[0]]
        ei = _find(headers, EMAIL_HEADERS)
        if ei is None:
            # header-less sheet with a first column of addresses
            if EMAIL_RE.match(_cell(rows[0], 0)) or (len(rows) > 1 and EMAIL_RE.match(_cell(rows[1], 0))):
                ei, ni, ci, body = 0, None, None, rows
            else:
                continue
        else:
            ni, ci, body = _find(headers, NAME_HEADERS), _find(headers, COMPANY_HEADERS), rows[1:]
        # A sheet with subject+body headers is the templates sheet, not contacts
        if _find(headers, SUBJECT_HEADERS) is not None and _find(headers, BODY_HEADERS - {"email"}) is not None:
            continue
        out, invalid = [], 0
        for r in body:
            email = _cell(r, ei).lower()
            if not email:
                continue
            if not EMAIL_RE.match(email):
                invalid += 1
                continue
            out.append({"email": email, "name": _cell(r, ni), "company": _cell(r, ci)})
        return out, invalid
    raise ImportProblem("Could not find a sheet with an 'email' column.")


def read_templates(filename, data):
    """Returns list of {subject, body}."""
    for sheet, rows in _sheets(filename, data):
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        if not rows:
            continue
        headers = [_norm(c) for c in rows[0]]
        si = _find(headers, SUBJECT_HEADERS)
        bi = _find(headers, BODY_HEADERS - {"email"})
        if si is None or bi is None:
            continue
        out = []
        for r in rows[1:]:
            subject, body = _cell(r, si), _cell(r, bi)
            if subject and body:
                out.append({"subject": subject, "body": body})
        if out:
            return out
    raise ImportProblem("Could not find a sheet with 'subject' and 'body' columns.")


def sample_workbook():
    """Bytes of a starter workbook with a Contacts sheet and a Templates sheet."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append(["name", "email", "company"])
    ws.append(["Asha Rao", "asha@example.com", "Rao Textiles"])
    ws.append(["Dev Mehta", "dev@example.com", "Mehta Logistics"])
    tp = wb.create_sheet("Templates")
    tp.append(["subject", "body"])
    tp.append(["Quick question, {first_name}",
               "Hi {first_name},\n\nI came across {company} and had a quick idea I wanted to share.\n\nWould you be open to a short chat this week?\n\nBest,\nYour Name"])
    tp.append(["An idea for {company}",
               "Hello {first_name},\n\nI help businesses like {company} with ...\n\nIf it is useful, I can send over details.\n\nRegards,\nYour Name"])
    for sheet in (ws, tp):
        for col, width in zip("ABC", (28, 60, 28)):
            sheet.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
