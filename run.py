"""
One-Click Launcher for GPS Save Our Drivers.
Starts the telemetry Flask server and opens the browser interface.
"""

import sys
import os
import webbrowser
import threading
import time

def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://127.0.0.1:5000")

if __name__ == "__main__":
    print("=" * 60)
    print("   APEX BUGGY - GPS SAVE OUR DRIVERS")
    print("   Starting Local Telemetry Console...")
    print("=" * 60)
    print("Bluetooth priority: ACTIVE (monitoring sync folders)")
    print("Server running at: http://127.0.0.1:5000")
    print("Press Ctrl+C in this terminal to shut down the server.")
    print("=" * 60)

    # Open browser in a separate thread
    threading.Thread(target=open_browser, daemon=True).start()

    from server import app
    app.run(host="127.0.0.1", port=5000, debug=False)
