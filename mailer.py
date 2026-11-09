"""SMTP sending with error classification.

Error kinds:
  permanent - this recipient will never work (bad address, rejected). Do not retry.
  temporary - network blip or 4xx reply. Retry a couple of times.
  fatal     - the account itself has a problem (bad login, sending limit). Pause everything.
"""
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

LIMIT_WORDS = re.compile(r"limit|quota|exceed|too many|rate|blocked|suspend|spam", re.I)


class SendError(Exception):
    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


def fill_template(text, contact):
    name = (contact.get("name") or "").strip()
    values = {
        "name": name or "there",
        "first_name": (name.split()[0] if name else "there"),
        "company": (contact.get("company") or "").strip(),
        "email": contact.get("email", ""),
    }
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1).lower(), m.group(0)), text)


def _connect(s):
    host = s["smtp_host"].strip()
    port = int(s["smtp_port"])
    security = s.get("smtp_security", "starttls")
    if not host:
        raise SendError("SMTP host is empty. Open Settings and fill in your mail server.", "fatal")
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        if security == "starttls":
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
    if s.get("smtp_user"):
        server.login(s["smtp_user"], s.get("smtp_password", ""))
    return server


def _classify(exc):
    if isinstance(exc, SendError):
        return exc
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return SendError("Login failed. Check the username and app password in Settings.", "fatal")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return SendError(f"Sender refused: {_text(exc)}", "fatal")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        detail = "; ".join(f"{code} {_clean(msg)}" for code, msg in exc.recipients.values())
        return SendError(f"Recipient refused: {detail}", "permanent")
    if isinstance(exc, smtplib.SMTPResponseException):
        code = exc.smtp_code
        text = _text(exc)
        if 400 <= code < 500:
            return SendError(f"Temporary error {text}", "temporary")
        if LIMIT_WORDS.search(text):
            return SendError(f"Server limit or block: {text}", "fatal")
        return SendError(f"Rejected {text}", "permanent")
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout,
                        TimeoutError, ConnectionError, socket.gaierror, ssl.SSLError, OSError)):
        return SendError(f"Network problem: {exc}", "temporary")
    return SendError(f"Unexpected error: {exc}", "temporary")


def _clean(msg):
    return msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)


def _text(exc):
    return f"{exc.smtp_code} {_clean(exc.smtp_error)}"


def build_message(s, to_email, subject, text, html=None, outreach=True):
    msg = EmailMessage()
    sender = s.get("smtp_user") or "noreply@localhost"
    msg["From"] = formataddr((s.get("from_name", ""), sender)) if s.get("from_name") else sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
    body = text
    if outreach:
        optout = (s.get("optout_line") or "").strip()
        if optout:
            body = f"{text.rstrip()}\n\n--\n{optout}"
        msg["List-Unsubscribe"] = f"<mailto:{sender}?subject=unsubscribe>"
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send(s, to_email, subject, text, html=None, outreach=True):
    """Send one message. Raises SendError on any failure."""
    server = None
    try:
        server = _connect(s)
        server.send_message(build_message(s, to_email, subject, text, html, outreach))
    except Exception as exc:  # noqa: BLE001 - everything is classified
        raise _classify(exc) from exc
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass


def test_connection(s):
    """Connect and log in without sending. Returns (ok, message)."""
    try:
        server = _connect(s)
        server.quit()
        return True, "Connected and logged in."
    except Exception as exc:  # noqa: BLE001
        return False, str(_classify(exc))
