# OutreachDesk

A small Windows app for sending a personalised email list slowly (60-90 seconds apart),
watching progress live, retrying failures, and getting a report at the end of the day.

## Get the .exe (pick one)

**A. On any Windows PC (easiest)**
1. Install Python 3.10+ from python.org. Tick "Add python.exe to PATH" in the installer.
2. Unzip this folder, double-click `build.bat`.
3. After a minute or two you get `dist\OutreachDesk.exe`. Copy it anywhere.

**B. No Python on your PC: let GitHub build it**
1. Create a free GitHub repository and upload everything in this folder (keep the `.github` folder).
2. Open the Actions tab, choose "Build Windows exe", click "Run workflow".
3. When it finishes, download `OutreachDesk-windows` from the run's Artifacts.

Windows may show a SmartScreen warning for an unsigned app. Choose "More info", then "Run anyway".

## First run
1. Double-click `OutreachDesk.exe`. A console window opens and your browser shows the dashboard.
   Keep the console window open while sending. Closing it quits the app.
2. **Settings:** enter your mail server, username and app password. Click "Test login",
   then "Send test email". For Gmail: turn on 2-Step Verification, create an App Password, use
   smtp.gmail.com, port 587, STARTTLS.
3. **Contacts:** import a sheet with an `email` column (`name` and `company` optional).
4. **Templates:** import a sheet with `subject` and `body` columns, or add them by hand.
   One workbook with a Contacts sheet and a Templates sheet works for both buttons.
   Download a ready-made example from the Contacts tab.
5. Tick **Practice mode** in Settings first and press Start to watch the dashboard with nothing sent.
   Afterwards use "Reset all to waiting" on the Contacts tab and untick Practice mode.
6. Press **Start sending**.

## Placeholders
`{name}`, `{first_name}`, `{company}`, `{email}`. A contact with no name becomes "there".

## What it does when things go wrong
- Bad address or rejected recipient: marked Failed, not retried. Retry or Skip from the dashboard.
- Network blip or temporary server error: retried automatically (default 2 times).
- Wrong password, sending limit or block from your provider: the whole campaign pauses and
  the contact stays in the queue. Fix the cause, then press Resume.
- Three failures in a row: the campaign pauses so a broken setup does not burn through your list.
- Daily cap reached: stops for the day. Press Start tomorrow to continue where it left off.
- Everything is saved after every email, so closing the app or a power cut loses nothing.

## Reports
Created automatically when the queue finishes or the cap is reached, and at your chosen time
if neither has happened. Each report is emailed to the address in Settings and saved as an HTML
file in the data folder.

## Your data
Windows: `%APPDATA%\OutreachDesk` (database, saved reports).
The mail password is stored in that database on your PC, so use an app password, not your main one.

## Responsible sending
Use a separate sending domain with SPF, DKIM and DMARC set up. Keep lists relevant, keep the
opt-out line, and mark anyone who replies "unsubscribe" as Skipped. Check the cold-email rules
that apply where your recipients live.

## For developers
`python main.py` runs it without building. `python tests/test_e2e.py` runs the test suite
against a fake mail server.
