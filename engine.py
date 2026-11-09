"""Background sending engine.

One worker thread walks the pending contacts, sends one email, writes the result
to SQLite, then waits a random 60-90 seconds (configurable). Pause and Stop are
checked every quarter second so the buttons respond immediately.
"""
import random
import threading
import time
from datetime import datetime

import mailer
import reports
import storage

CONSECUTIVE_FAIL_LIMIT = 3


class Engine:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()
        self.state = "idle"          # idle | running | paused | stopping
        self.message = "Ready."
        self.next_send_at = None     # epoch seconds
        self.current_email = None
        self.practice = False
        self._scheduler_started = False

    # ----- control -----
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Returns (ok, message)."""
        with self._lock:
            if self.is_alive():
                return False, "Already running."
            s = storage.get_settings()
            practice = s["practice_mode"] == "1"
            if not storage.next_pending():
                return False, "No pending contacts. Import a contact sheet first."
            if not storage.list_templates(active_only=True):
                return False, "No active templates. Import or add at least one."
            if not practice:
                if not s["smtp_host"] or not s["smtp_user"] or not s["smtp_password"]:
                    return False, "Mail server login is incomplete. Fill in Settings first."
            try:
                lo, hi = int(s["delay_min"]), int(s["delay_max"])
                assert 0 < lo <= hi
                assert int(s["daily_cap"]) > 0
            except (ValueError, AssertionError):
                return False, "Delay range or daily cap is invalid. Check Settings."
            self._stop.clear()
            self._paused.clear()
            self.practice = practice
            self.state = "running"
            self.message = "Started." if not practice else "Started in practice mode. No emails are sent."
            self._thread = threading.Thread(target=self._run, name="sender", daemon=True)
            self._thread.start()
            return True, self.message

    def pause(self):
        if self.state == "running":
            self._paused.set()
            self.state = "paused"
            self.message = "Paused. Press Resume to continue."

    def resume(self):
        if self.state == "paused":
            self._paused.clear()
            self.state = "running"
            self.message = "Resumed."

    def stop(self):
        if self.is_alive():
            self._stop.set()
            self._paused.clear()
            self.state = "stopping"
            self.message = "Stopping after the current email..."

    # ----- internals -----
    def _sleep(self, seconds):
        end = time.time() + seconds
        self.next_send_at = end
        while not self._stop.is_set():
            if self._paused.is_set():
                end += 0.25            # paused time does not count down
                self.next_send_at = end
            elif time.time() >= end:
                break
            time.sleep(0.25)
        self.next_send_at = None

    def _pick_template(self, settings, templates):
        if settings["template_mode"] == "random":
            return random.choice(templates)
        return templates[storage.log_total_sends() % len(templates)]

    def _pause_with(self, reason):
        self._paused.set()
        self.state = "paused"
        self.message = reason

    def _run(self):
        finished_naturally = False
        did_work = False
        consecutive_failures = 0
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    time.sleep(0.25)
                    continue
                s = storage.get_settings()
                practice = s["practice_mode"] == "1"
                self.practice = practice
                if storage.sent_today() >= int(s["daily_cap"]):
                    self.message = f"Daily cap of {s['daily_cap']} reached. Stopped for today."
                    finished_naturally = True
                    break
                contact = storage.next_pending()
                if not contact:
                    self.message = "Queue finished. Every contact has been processed."
                    finished_naturally = True
                    break
                templates = storage.list_templates(active_only=True)
                if not templates:
                    self._pause_with("No active templates left. Add one, then press Resume.")
                    continue

                tpl = self._pick_template(s, templates)
                subject = mailer.fill_template(tpl["subject"], contact)
                body = mailer.fill_template(tpl["body"], contact)
                self.current_email = contact["email"]
                self.message = f"Sending to {contact['email']}..."

                outcome = self._deliver(s, practice, contact, tpl, subject, body)
                self.current_email = None
                if outcome == "fatal":
                    continue                         # engine is paused; contact stays pending
                did_work = True
                consecutive_failures = consecutive_failures + 1 if outcome == "failed" else 0
                if consecutive_failures >= CONSECUTIVE_FAIL_LIMIT:
                    self._pause_with(f"{CONSECUTIVE_FAIL_LIMIT} emails failed in a row. Paused so you can check Settings and the failure reasons.")
                    consecutive_failures = 0
                    continue

                # wait before the next one, unless there is nothing left to send
                if storage.next_pending() and storage.sent_today() < int(s["daily_cap"]):
                    lo, hi = int(s["delay_min"]), int(s["delay_max"])
                    gap = random.uniform(1, 2) if practice else random.uniform(lo, hi)
                    self.message = f"Waiting before the next email..."
                    self._sleep(gap)
        except Exception as exc:  # noqa: BLE001 - never let the thread die silently
            self.message = f"Engine error: {exc}"
        finally:
            was_stopped = self._stop.is_set()
            self.current_email = None
            self.next_send_at = None
            self.state = "idle"
            if was_stopped:
                self.message = "Stopped."
            if finished_naturally and did_work and not self.practice:
                try:
                    reports.generate(send=True)
                    self.message += " Report generated."
                except Exception as exc:  # noqa: BLE001
                    self.message += f" Report failed: {exc}"

    def _deliver(self, s, practice, contact, tpl, subject, body):
        """Returns 'sent', 'failed' or 'fatal'."""
        if practice:
            storage.mark_sent(contact["id"], tpl["id"], note="practice")
            storage.log_send(contact["id"], contact["email"], tpl["id"], "practice")
            return "sent"
        retries = int(s["max_retries"])
        last_error = ""
        for attempt in range(1, retries + 2):
            try:
                mailer.send(s, contact["email"], subject, body)
                storage.mark_sent(contact["id"], tpl["id"])
                storage.log_send(contact["id"], contact["email"], tpl["id"], "sent")
                return "sent"
            except mailer.SendError as exc:
                last_error = str(exc)
                if exc.kind == "fatal":
                    self._pause_with(f"Paused: {last_error}")
                    return "fatal"
                if exc.kind == "permanent":
                    break
                if attempt <= retries and not self._stop.is_set():
                    self.message = f"Temporary problem, retrying ({attempt}/{retries})..."
                    self._sleep(10 * attempt)
                    if self._stop.is_set():
                        return "fatal"
        storage.mark_failed(contact["id"], tpl["id"], last_error, 1)
        storage.log_send(contact["id"], contact["email"], tpl["id"], "failed", last_error)
        return "failed"

    # ----- end-of-day scheduler -----
    def start_scheduler(self):
        if self._scheduler_started:
            return
        self._scheduler_started = True
        threading.Thread(target=self._schedule_loop, name="scheduler", daemon=True).start()

    def _schedule_loop(self):
        while True:
            time.sleep(30)
            try:
                s = storage.get_settings()
                if s["practice_mode"] == "1" or self.is_alive():
                    continue
                hhmm = datetime.now().strftime("%H:%M")
                if hhmm >= s["report_time"] and reports.had_activity_today() and not reports.emailed_today() and s["report_to"]:
                    reports.generate(send=True)
            except Exception:  # noqa: BLE001
                pass


engine = Engine()
