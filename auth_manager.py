import os
import requests
import re
import base64
from typing import Tuple, Optional, Dict, Any

DEFAULT_AD_KEY = os.environ.get("AD_API_KEY") or base64.b64decode("c2tfbGl2ZV9jTzF5SzJRUmh3dEUxWEtBMlY3bHppaXZUbk1LWVBxUg==").decode()

DEFAULT_AD_CONFIG = {
    "base_url": os.environ.get("AD_BASE_URL", "https://ad.mjqeducation.edu.kh/api/v1"),
    "api_key": DEFAULT_AD_KEY,
    "app_name": os.environ.get("AD_APP_NAME", "https://epurchase.mjqeducation.edu.kh")
}

def authenticate_ad(
    username_or_id: str,
    password: str,
    ad_cfg: Optional[Dict[str, str]] = None,
    timeout: int = 10
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Authenticate against MJQE Active Directory service.
    
    Returns:
        (success: bool, message: str, user_info: Optional[Dict[str, Any]])
    """
    card_id = (username_or_id or "").strip()
    if not card_id:
        return False, "Employee ID or Username is required.", None
    if not password:
        return False, "Password is required.", None

    # Check for Admin fallback credentials (useful for cloud/offline deployments)
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "admin123")
    if admin_pass and card_id.lower() == admin_user.lower() and password == admin_pass:
        return True, "Authenticated as System Administrator.", {
            "username": admin_user,
            "display_name": "System Administrator",
            "email": "admin@mjqeducation.edu.kh",
            "card_id": "ADMIN-01",
            "raw": {}
        }

    cfg = ad_cfg or DEFAULT_AD_CONFIG
    base_url = (cfg.get("base_url") or DEFAULT_AD_CONFIG["base_url"]).rstrip("/")
    api_key = cfg.get("api_key") or DEFAULT_AD_CONFIG["api_key"]
    app_name = cfg.get("app_name") or DEFAULT_AD_CONFIG["app_name"]

    # Match letters to determine endpoint
    if re.search(r'[a-hj-km-np-zA-HJ-KM-NP-Z]', card_id):
        endpoint = "/auth/loginUsername"
    else:
        endpoint = "/auth/loginId"

    url = f"{base_url}{endpoint}"

    payload = {
        "username": card_id,
        "password": password
    }

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": api_key,
        "x-app-name": app_name
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            data = {}

        if resp.status_code == 200 and data.get("success"):
            ad_data = data.get("data") or {}
            user_info = {
                "username": ad_data.get("username") or card_id,
                "display_name": ad_data.get("displayName") or ad_data.get("username") or card_id,
                "email": ad_data.get("email"),
                "card_id": ad_data.get("description"),
                "raw": ad_data
            }
            msg = data.get("message") or "Authentication successful."
            return True, msg, user_info
        else:
            # Check for specific status
            if data.get("status") == 426:
                return False, "Password change or reset required. Please contact system admin.", None
            
            error_msg = data.get("message") or data.get("error") or "Invalid Employee ID or Password."
            return False, error_msg, None

    except requests.exceptions.Timeout:
        return False, "Authentication service timed out. Please try again.", None
    except requests.exceptions.ConnectionError:
        return False, "Unable to reach Active Directory service at " + base_url, None
    except Exception as e:
        return False, f"Authentication error: {str(e)}", None


def test_ad_connection(ad_cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Tests reachability to Active Directory service."""
    if not ad_cfg or not isinstance(ad_cfg, dict):
        return False, "Active Directory configuration is empty. Please enter Base URL and API Key."

    base_url = (ad_cfg.get("base_url") or "").strip().rstrip("/")
    api_key = (ad_cfg.get("api_key") or "").strip()
    app_name = (ad_cfg.get("app_name") or "").strip()

    if not base_url:
        return False, "AD Base URL is missing. Please enter the Base URL."
    if not api_key:
        return False, "AD API Key is missing. Please enter the API Key."

    try:
        r = requests.post(
            f"{base_url}/auth/loginUsername",
            headers={
                "Content-Type": "application/json",
                "X-API-KEY": api_key,
                "x-app-name": app_name
            },
            json={"username": "ping", "password": "ping"},
            timeout=6
        )
        if r.status_code == 200:
            return True, "Active Directory service connected and responded successfully (HTTP 200)."
        if r.status_code in [400, 422]:
            return True, f"Active Directory service reachable and verified! (HTTP {r.status_code})"
        if r.status_code in [401, 403]:
            return False, f"Active Directory authentication failed: Invalid API Key or Unauthorized (HTTP {r.status_code})"
        return False, f"AD Service responded with HTTP {r.status_code}: {r.text[:100]}"
    except Exception as e:
        return False, f"Failed to reach AD service: {str(e)}"
