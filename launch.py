"""Open a browser once this server is ready; never attach to an unknown occupied port."""
import socket
import threading
import time
import urllib.request
import webbrowser
import uvicorn


def open_browser():
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open("http://127.0.0.1:8080/")
                    return
        except OSError:
            time.sleep(.5)


if __name__ == "__main__":
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8080))
        except OSError:
            raise SystemExit("Port 8080 is already in use. Close the other ollama-web window or application and try again.")
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("app:app", host="127.0.0.1", port=8080, proxy_headers=False)
