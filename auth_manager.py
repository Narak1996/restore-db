import os
import requests
import re
from typing import Tuple, Optional, Dict, Any

DEFAULT_AD_CONFIG = {
    "base_url": os.environ.get("AD_BASE_URL", "http://172.168.0.118:8086/api/v1"),
    "api_key": os.environ.get("AD_API_KEY", ""),
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
