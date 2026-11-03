import os
import sys
import threading
import time
import webbrowser

import urllib.request

def open_browser():
    url = "http://localhost:8501"
    # Wait until Streamlit is ready to accept requests
    for _ in range(40):
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=1) as resp:
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
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    app_path = os.path.join(base_dir, "app.py")

    # Auto-open browser in background
    threading.Thread(target=open_browser, daemon=True).start()

    import streamlit.web.cli as stcli
    os.environ["STREAMLIT_THEME_BASE"] = "dark"
    os.environ["STREAMLIT_THEME_PRIMARY_COLOR"] = "#2563eb"
    os.environ["STREAMLIT_THEME_BACKGROUND_COLOR"] = "#0b0f19"
    os.environ["STREAMLIT_THEME_SECONDARY_BACKGROUND_COLOR"] = "#111827"
    os.environ["STREAMLIT_THEME_TEXT_COLOR"] = "#f8fafc"
    os.environ["STREAMLIT_THEME_FONT"] = "sans serif"

    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.port=8501",
        "--server.address=localhost",
        "--browser.gatherUsageStats=false",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=false",
        "--theme.base=dark",
        "--theme.primaryColor=#2563eb",
        "--theme.backgroundColor=#0b0f19",
        "--theme.secondaryBackgroundColor=#111827",
        "--theme.textColor=#f8fafc",
    ]
    sys.exit(stcli.main())

if __name__ == "__main__":
    main()
