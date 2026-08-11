"""
Entry point for Render's FREE Web Service tier.

Render's free tier only allows Web Services (something bound to a port),
not Background Workers (continuously running scripts with no port) - that
tier requires payment. This file is the workaround: a minimal Flask server
satisfies Render's requirement, while the actual bot loop runs in a
background thread inside the same process.

IMPORTANT: Render's free Web Service tier sleeps after ~15 minutes of no
inbound HTTP traffic. If that happens while a position is open, the bot
won't be running to manage/close it. This is solved by pinging the '/'
endpoint every few minutes from an external uptime service (see README) -
this keeps the process alive continuously, not just during trading hours.

Start command on Render should be: python3 app.py
(NOT python3 main.py - that has no web server and Render will fail the
port-binding health check.)
"""

import os
import threading
from datetime import datetime

from flask import Flask
import main as bot_main

app = Flask(__name__)

_bot_thread_started = False
_start_time = datetime.utcnow()


def start_bot_thread():
    global _bot_thread_started
    if _bot_thread_started:
        return
    _bot_thread_started = True
    thread = threading.Thread(target=bot_main.run_forever, daemon=True)
    thread.start()
    print("[app] Bot thread started.")


@app.route("/")
def health_check():
    """
    This is the endpoint an external uptime pinger should hit every few
    minutes to keep Render's free tier from sleeping the process.
    """
    uptime = datetime.utcnow() - _start_time
    return {
        "status": "running",
        "uptime_seconds": int(uptime.total_seconds()),
    }


# Start the bot thread as soon as the module loads, so it's running
# whether this is launched via `python3 app.py` or via a WSGI server.
start_bot_thread()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
