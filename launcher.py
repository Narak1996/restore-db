import os
import sys
import socket
import threading
import time
import webbrowser
import urllib.request

def find_available_port(start_port=8505, max_attempts=50):
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start_port

def open_browser(port):
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

    env_port = os.environ.get("PORT")
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            port = find_available_port(8505)
    else:
        port = find_available_port(8505)

    print(f"=======================================================")
    print(f"  DB Restore Studio - Starting on http://localhost:{port}")
    print(f"=======================================================")

    # Auto-open browser in background
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    import uvicorn
    from api_server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    main()
