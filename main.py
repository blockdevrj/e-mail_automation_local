"""OutreachDesk launcher. Double-click the .exe: it starts a local server and opens
the dashboard in your browser. Close the console window to quit."""
import logging
import socket
import threading
import time
import webbrowser

import storage
from engine import engine
from server import app

PREFERRED_PORT = 8765


def pick_port():
    for port in (PREFERRED_PORT, 0):
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", port))
            return sock.getsockname()[1]
        except OSError:
            continue
        finally:
            sock.close()
    raise RuntimeError("No free port available.")


def main():
    storage.init()
    engine.start_scheduler()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    port = pick_port()
    url = f"http://127.0.0.1:{port}/"
    print("=" * 60)
    print(" OutreachDesk is running")
    print(f" Dashboard: {url}")
    print(f" Your data: {storage.data_dir()}")
    print(" Keep this window open while a campaign is running.")
    print(" Close this window to quit (the campaign pauses safely).")
    print("=" * 60)
    threading.Thread(target=lambda: (time.sleep(1.0), webbrowser.open(url)), daemon=True).start()
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
