import os
import sys
import threading
import time
import webbrowser
import urllib.request

def open_browser(port=8000):
    url = f"http://localhost:{port}"
    # Wait until API server is ready
    for _ in range(50):
        time.sleep(0.2)
        try:
            with urllib.request.urlopen(f"{url}/api/config", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass

def main():
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        exec_dir = os.path.dirname(os.path.abspath(sys.executable))
        os.chdir(exec_dir)
        if base_dir not in sys.path:
            sys.path.insert(0, base_dir)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if base_dir not in sys.path:
            sys.path.insert(0, base_dir)

    port = int(os.environ.get("PORT", 8000))

    # Auto-open browser in background
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    import uvicorn
    from api_server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    main()
