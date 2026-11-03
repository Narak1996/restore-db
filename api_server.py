"""
DB Restore Enterprise Studio - FastAPI Backend Service
Provides high-performance REST APIs for React + MUI frontend.
Reuses existing engines: ssh_manager, restore_engine, validation_manager, config_manager, auth_manager.
"""

import os
import sys
import time
import base64
import json
import hmac
import hashlib
import threading
from typing import Dict, Any, List, Optional
import pandas as pd
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config_manager
import auth_manager
import ssh_manager
import restore_engine
import file_explorer
import history_manager
import validation_manager
import verify_restore

app = FastAPI(
    title="DB Restore Studio API",
    version="2.0.0",
    description="Enterprise REST API for Database Restoration & Validation"
)

# Enable CORS for Vite frontend (default port 5173 / 3000 / 8080)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active background restore state
active_restore = {
    "is_running": False,
    "status": "idle", # idle, running, success, error
    "step": "Ready",
    "progress": 0,
    "logs": [],
    "error": None,
    "target_db": "",
    "engine": "",
    "start_time": None,
    "elapsed_seconds": 0
}
restore_lock = threading.Lock()

# ------------------------------------------------------------------------------
# Pydantic Request Models & JWT Generator
# ------------------------------------------------------------------------------

def create_jwt_token(payload: dict, secret: str = "dbrestore-jwt-secret-key-2026") -> str:
    """Generates an RFC-compliant 3-part HS256 JWT token."""
    header = {"alg": "HS256", "typ": "JWT"}
    header_bytes = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    payload_bytes = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signing_input = f"{header_bytes}.{payload_bytes}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    sig_bytes = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{header_bytes}.{payload_bytes}.{sig_bytes}"

class LoginRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: str

class TestSSHRequest(BaseModel):
    host: str
    port: int = 22
    user: str
    auth_type: str = "password" # password or key
    password: Optional[str] = ""
    key_path: Optional[str] = ""

class TestDBRequest(BaseModel):
    engine: str # PostgreSQL or MariaDB
    host: str
    port: int
    user: str
    password: Optional[str] = ""
    maintenance_db: Optional[str] = "postgres"

class ExploreFilesRequest(BaseModel):
    mode: str = "ssh" # "ssh" or "local"
    path: Optional[str] = None
    search: Optional[str] = None

class RestoreExecuteRequest(BaseModel):
    mode: str = "ssh"
    dump_path: str
    target_db: str
    engine: str = "PostgreSQL"
    dump_format: str = "Auto-detect"
    drop_first: bool = True
    create_first: bool = True
    clean_target: bool = False
    stop_on_error: bool = True
    custom_owner: Optional[str] = None

class ValidationExecuteAllRequest(BaseModel):
    tasks: Optional[List[Dict[str, Any]]] = None

class ValidationScreenshotRequest(BaseModel):
    ip: str
    db: str
    table: str
    execution_num: int = 1
    columns: List[str]
    rows: List[List[Any]]

# ------------------------------------------------------------------------------
# AUTHENTICATION ENDPOINTS
# ------------------------------------------------------------------------------

@app.post("/api/auth/login")
def login(req: LoginRequest):
    """Authenticates against Active Directory SSO gateway or local fallback."""
    cfg = config_manager.load_config()
    user_key = (req.username or req.email or "").strip()
    if not user_key:
        raise HTTPException(status_code=400, detail="Username or Email is required.")
    
    # 1. Attempt AD authentication
    auth_ok, auth_msg, auth_user = auth_manager.authenticate_ad(user_key, req.password, cfg.get("ad"))
    
    # 2. If user_key contains '@', try without domain (e.g. 'user@domain.com' -> 'user')
    if not auth_ok and "@" in user_key:
        prefix = user_key.split("@")[0]
        auth_ok2, auth_msg2, auth_user2 = auth_manager.authenticate_ad(prefix, req.password, cfg.get("ad"))
        if auth_ok2:
            auth_ok, auth_msg, auth_user = auth_ok2, auth_msg2, auth_user2

    # 3. Local Admin fallback
    if not auth_ok:
        admin_user = os.environ.get("ADMIN_USER", "admin")
        admin_pass = os.environ.get("ADMIN_PASSWORD", "admin123")
        if (user_key.lower() == admin_user.lower() or user_key.lower() == "admin@dbrestore.local") and req.password == admin_pass:
            auth_ok = True
            auth_user = {
                "username": "admin",
                "display_name": "System Administrator",
                "email": "admin@dbrestore.local",
                "card_id": "ADMIN-01"
            }

    if not auth_ok:
        raise HTTPException(status_code=401, detail=auth_msg or "Invalid username or password.")

    # Create RFC-compliant JWT token for Minimal UI AuthProvider
    token_exp = int(time.time()) + (86400 * 7) # 7 days
    user_payload = {
        "sub": auth_user.get("username", user_key),
        "name": auth_user.get("display_name", user_key),
        "email": auth_user.get("email", user_key),
        "role": "admin",
        "iat": int(time.time()),
        "exp": token_exp
    }
    access_token = create_jwt_token(user_payload)

    user_info = {
        "id": auth_user.get("card_id") or "1",
        "displayName": auth_user.get("display_name") or user_key,
        "email": auth_user.get("email") or user_key,
        "username": auth_user.get("username") or user_key,
        "role": "admin",
        "accessToken": access_token
    }

    res_payload = {
        "success": True,
        "accessToken": access_token,
        "token": access_token,
        "user": user_info
    }
    res_payload["data"] = {
        "accessToken": access_token,
        "token": access_token,
        "user": user_info
    }
    return res_payload

@app.get("/api/auth/me")
def get_current_user(authorization: Optional[str] = Header(None)):
    """Returns authenticated user profile."""
    u = {
        "id": "1",
        "displayName": "System Administrator",
        "email": "admin@dbrestore.local",
        "role": "admin"
    }
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                payload_str = parts[1]
                # Add padding if needed
                payload_str += "=" * (-len(payload_str) % 4)
                payload_data = json.loads(base64.urlsafe_b64decode(payload_str.encode()).decode())
                u["displayName"] = payload_data.get("name") or payload_data.get("sub") or u["displayName"]
                u["email"] = payload_data.get("email") or u["email"]
                u["username"] = payload_data.get("sub") or u.get("username", "admin")
        except Exception:
            pass
    return {
        "user": u,
        "data": {"user": u}
    }

# ------------------------------------------------------------------------------
# CONFIGURATION ENDPOINTS
# ------------------------------------------------------------------------------

@app.get("/api/config")
def get_config():
    """Returns the current application configuration."""
    cfg = config_manager.load_config()
    return {"success": True, "config": cfg}

@app.post("/api/config")
def update_config(payload: Dict[str, Any]):
    """Updates and persists the application configuration."""
    cfg = payload.get("config", payload)
    current_cfg = config_manager.load_config()
    
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if k == "config" and isinstance(v, dict):
                current_cfg.update(v)
            else:
                current_cfg[k] = v

    ok = config_manager.save_config(current_cfg)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save config.json")
    return {"success": True, "message": "Configuration saved successfully."}

@app.post("/api/config/test-ssh")
def test_ssh(req: TestSSHRequest):
    """Pings and tests SSH reachability."""
    t0 = time.time()
    ok, msg = ssh_manager.test_ssh_connection({
        "host": req.host,
        "port": req.port,
        "user": req.user,
        "auth_type": req.auth_type,
        "password": req.password,
        "key_path": req.key_path
    })
    elapsed_ms = round((time.time() - t0) * 1000, 1)
    return {
        "success": ok,
        "message": msg,
        "latency_ms": elapsed_ms
    }

@app.post("/api/config/test-db")
def test_db(req: TestDBRequest):
    """Pings PostgreSQL or MariaDB instance."""
    if req.engine == "PostgreSQL":
        ok, msg = config_manager.test_postgresql_connection({
            "host": req.host,
            "port": req.port,
            "user": req.user,
            "password": req.password,
            "maintenance_db": req.maintenance_db or "postgres"
        })
    else:
        ok, msg = config_manager.test_mariadb_connection({
            "host": req.host,
            "port": req.port,
            "user": req.user,
            "password": req.password
        })
    return {"success": ok, "message": msg}

@app.post("/api/config/test-ad")
def test_ad(payload: Dict[str, Any]):
    """Tests Active Directory SSO reachability."""
    ad_cfg = payload.get("ad_config") or payload.get("ad") or payload
    ok, msg = auth_manager.test_ad_connection(ad_cfg)
    return {"success": ok, "message": msg}

@app.post("/api/config/test-storage")
def test_storage(payload: Dict[str, Any]):
    """Verifies filesystem directory accessibility."""
    path = payload.get("backup_dir", "").strip()
    if not path:
        return {"success": False, "message": "Path is empty."}
    if os.path.exists(path) and os.path.isdir(path):
        return {"success": True, "message": f"Directory verified accessible: {path}"}
    return {"success": False, "message": f"Directory does not exist or is not accessible: {path}"}

# ------------------------------------------------------------------------------
# FILE EXPLORER ENDPOINTS
# ------------------------------------------------------------------------------

@app.post("/api/files/explore")
def explore_files(req: ExploreFilesRequest):
    """Navigates local or remote SSH directory to inspect and select backup SQL files."""
    cfg = config_manager.load_config()
    default_path = (
        cfg.get("ssh", {}).get("remote_backup_dir", "/App2/source_backup")
        if req.mode == "ssh"
        else cfg.get("server_storage", {}).get("backup_dir", r"D:\Narak\Sql file")
    )
    browse_path = req.path or default_path
    res = file_explorer.explore_directory(
        mode=req.mode,
        current_path=browse_path,
        config=cfg,
        search_query=req.search
    )
    return res

# ------------------------------------------------------------------------------
# RESTORE OPERATIONS & PIPELINE ENDPOINTS
# ------------------------------------------------------------------------------

def _restore_worker(req_data: Dict[str, Any], cfg: Dict[str, Any]):
    """Background restore thread executing pipeline."""
    global active_restore
    with restore_lock:
        active_restore["is_running"] = True
        active_restore["status"] = "running"
        active_restore["progress"] = 5
        active_restore["step"] = "Initializing restore pipeline..."
        active_restore["logs"] = ["Starting restore pipeline..."]
        active_restore["error"] = None
        active_restore["target_db"] = req_data.get("target_db")
        active_restore["engine"] = req_data.get("engine")
        active_restore["start_time"] = time.time()

    def log_callback(msg: str):
        with restore_lock:
            active_restore["logs"].append(msg)
            # Rough progress heuristic based on logs
            if "Extracting" in msg or "Uncompressing" in msg:
                active_restore["progress"] = max(active_restore["progress"], 25)
                active_restore["step"] = "Decompressing dump archive..."
            elif "Dropping" in msg:
                active_restore["progress"] = max(active_restore["progress"], 45)
                active_restore["step"] = "Preparing target database schema..."
            elif "Restoring" in msg or "Executing SQL" in msg:
                active_restore["progress"] = max(active_restore["progress"], 65)
                active_restore["step"] = "Restoring tables & indexes..."

    try:
        t0 = time.time()
        success, final_logs, err = restore_engine.run_restore(
            mode=req_data.get("mode", "ssh"),
            file_path=req_data.get("dump_path"),
            target_db=req_data.get("target_db"),
            engine_type=req_data.get("engine", "PostgreSQL"),
            config=cfg,
            format_choice=req_data.get("dump_format", "Auto-detect"),
            drop_first=req_data.get("drop_first", True),
            create_first=req_data.get("create_first", True),
            clean_target=req_data.get("clean_target", False),
            stop_on_error=req_data.get("stop_on_error", True),
            custom_owner=req_data.get("custom_owner"),
            log_callback=log_callback
        )
        elapsed = round(time.time() - t0, 2)
        with restore_lock:
            active_restore["is_running"] = False
            active_restore["elapsed_seconds"] = elapsed
            if success:
                active_restore["status"] = "success"
                active_restore["progress"] = 100
                active_restore["step"] = "Restoration completed successfully!"
                active_restore["logs"].append(f"SUCCESS: Restoration finished in {elapsed}s.")
            else:
                active_restore["status"] = "error"
                active_restore["error"] = err or "Restoration process failed."
                active_restore["logs"].append(f"ERROR: {err}")

        # Persist restore execution to SQLite audit history
        try:
            f_size_mb = 0.0
            if req_data.get("file_size"):
                f_size_mb = float(req_data["file_size"]) / (1024 * 1024)
            history_manager.add_history(
                db_type=f"{req_data.get('engine', 'PostgreSQL')} ({'SSH: ' + cfg.get('ssh', {}).get('host', '') if req_data.get('mode') == 'ssh' else 'Local'})",
                db_name=req_data.get("target_db", ""),
                file_name=os.path.basename(req_data.get("dump_path", "")),
                file_size_mb=round(f_size_mb, 2),
                created_db=req_data.get("create_first", True),
                status="SUCCESS" if success else "ERROR",
                duration_sec=elapsed,
                logs="\n".join(active_restore.get("logs", [])),
                source_path=req_data.get("dump_path", ""),
                destination=req_data.get("target_db", ""),
                executed_by=req_data.get("executed_by") or "admin",
                user_name=req_data.get("executed_by") or "admin"
            )
        except Exception as hex:
            print(f"Error persisting restore audit history: {hex}")
    except Exception as e:
        with restore_lock:
            active_restore["is_running"] = False
            active_restore["status"] = "error"
            active_restore["error"] = str(e)
            active_restore["logs"].append(f"EXCEPTION: {e}")

@app.post("/api/restore/execute")
def execute_restore(req: RestoreExecuteRequest, background_tasks: BackgroundTasks):
    """Initiates database restoration in a background worker."""
    global active_restore
    if active_restore.get("is_running"):
        raise HTTPException(status_code=409, detail="A restoration is already running.")
    
    cfg = config_manager.load_config()
    background_tasks.add_task(_restore_worker, req.dict(), cfg)
    return {"success": True, "message": "Restoration process started."}

@app.get("/api/restore/status")
def get_restore_status():
    """Polls real-time progress and logs of the active restoration."""
    with restore_lock:
        return active_restore

@app.post("/api/restore/cancel")
def cancel_restore():
    """Cancels active restore."""
    with restore_lock:
        if active_restore.get("is_running"):
            active_restore["is_running"] = False
            active_restore["status"] = "error"
            active_restore["error"] = "Canceled by user."
            active_restore["logs"].append("Restore canceled by user.")
    return {"success": True}

# ------------------------------------------------------------------------------
# RESTORE HISTORY ENDPOINTS
# ------------------------------------------------------------------------------

def _enrich_history_record(r: Dict[str, Any]) -> Dict[str, Any]:
    target_db = r.get("db_name") or r.get("target_db") or ""
    dump_file = r.get("file_name") or r.get("dump_file") or ""
    raw_engine = r.get("db_type") or r.get("engine_type") or ""
    engine = "MariaDB" if "mariadb" in raw_engine.lower() or "mysql" in raw_engine.lower() else "PostgreSQL"
    mode = "SSH" if "ssh" in raw_engine.lower() or "ssh" in (r.get("mode") or "").lower() else "LOCAL"
    dur = r.get("duration_sec") if r.get("duration_sec") is not None else r.get("duration_seconds", 0)
    logs = r.get("logs") or r.get("log_output") or ""
    
    r["target_db"] = target_db
    r["db_name"] = target_db
    r["dump_file"] = dump_file
    r["file_name"] = dump_file
    r["engine_type"] = engine
    r["db_type"] = raw_engine or engine
    r["mode"] = mode
    r["duration_sec"] = dur
    r["duration_seconds"] = dur
    r["logs"] = logs
    r["log_output"] = logs
    return r

@app.api_route("/api/history", methods=["GET", "POST"])
def get_history(limit: int = 100, offset: int = 0, body: Optional[Dict[str, Any]] = None):
    """Returns restore history audit records from SQLite database formatted for AsynDataTable or simple fetchers."""
    records = history_manager.get_history(limit=500)
    enriched = [_enrich_history_record(dict(r)) for r in records]

    # Handle search filters from POST body or query params
    search_val = ""
    target_filter = ""
    dump_filter = ""
    if body and isinstance(body, dict):
        search_val = str(body.get("searchValue", "")).strip().lower()
        search_prop = body.get("searchProp", [])
        if isinstance(search_prop, list):
            for sp in search_prop:
                if isinstance(sp, dict):
                    p_name = sp.get("propertyname", "")
                    p_val = str(sp.get("value", "")).strip().lower()
                    if p_name in ("target_db", "db_name"):
                        target_filter = p_val
                    elif p_name in ("dump_file", "file_name"):
                        dump_filter = p_val
        elif isinstance(search_prop, dict):
            target_filter = str(search_prop.get("target_db") or search_prop.get("db_name", "")).strip().lower()
            dump_filter = str(search_prop.get("dump_file") or search_prop.get("file_name", "")).strip().lower()
        if "target_db" in body and not target_filter:
            target_filter = str(body.get("target_db", "")).strip().lower()
        if "dump_file" in body and not dump_filter:
            dump_filter = str(body.get("dump_file", "")).strip().lower()

    filtered = enriched
    if search_val:
        filtered = [
            r for r in filtered
            if search_val in r["target_db"].lower()
            or search_val in r["dump_file"].lower()
            or search_val in r["engine_type"].lower()
            or search_val in str(r.get("timestamp", "")).lower()
            or search_val in str(r.get("status", "")).lower()
        ]
    if target_filter:
        filtered = [r for r in filtered if target_filter in r["target_db"].lower()]
    if dump_filter:
        filtered = [r for r in filtered if dump_filter in r["dump_file"].lower()]

    total = len(filtered)
    # Apply pagination
    if body and isinstance(body, dict):
        start = int(body.get("start", offset))
        length = int(body.get("length", limit))
        paged = filtered[start:start + length]
    else:
        paged = filtered[offset:offset + limit]

    return {
        "success": True,
        "data": {
            "records": paged,
            "totalRecord": total,
            "recordsFiltered": total
        },
        "records": paged,
        "totalRecord": total,
        "recordsFiltered": total
    }

@app.get("/api/history/{record_id}")
def get_history_detail(record_id: int):
    """Returns details and full log output for a specific restore run."""
    rec = history_manager.get_restore_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found.")
    return {"success": True, "data": _enrich_history_record(rec)}

# ------------------------------------------------------------------------------
# RESTORE VALIDATION ENDPOINTS
# ------------------------------------------------------------------------------

@app.api_route("/api/validation/connections", methods=["GET", "POST"])
def validation_connections_endpoint(payload: Optional[Dict[str, Any]] = None):
    """Returns or updates connection options available for validation queries."""
    cfg = config_manager.load_config()
    vcs = cfg.get("validation_connections", [])

    if payload is not None and isinstance(payload, dict):
        if "connections" in payload:
            cfg["validation_connections"] = payload["connections"]
            config_manager.save_config(cfg)
            vcs = cfg["validation_connections"]
        elif "connection" in payload:
            new_con = payload["connection"]
            existing = [c for c in vcs if c.get("id") != new_con.get("id")]
            existing.append(new_con)
            cfg["validation_connections"] = existing
            config_manager.save_config(cfg)
            vcs = existing

    opts = validation_manager.get_available_connection_options(cfg)
    return {
        "success": True,
        "options": opts,
        "connections": vcs,
        "data": {
            "records": vcs,
            "totalRecord": len(vcs),
            "recordsFiltered": len(vcs)
        },
        "records": vcs,
        "totalRecord": len(vcs),
        "recordsFiltered": len(vcs)
    }

@app.delete("/api/validation/connections/{con_id}")
def delete_validation_connection(con_id: str):
    """Deletes a validation connection by id."""
    cfg = config_manager.load_config()
    vcs = cfg.get("validation_connections", [])
    updated = [c for c in vcs if str(c.get("id")) != str(con_id) and str(c.get("name")) != str(con_id)]
    cfg["validation_connections"] = updated
    config_manager.save_config(cfg)
    return {"success": True, "message": "Connection deleted"}

@app.api_route("/api/validation/tasks", methods=["GET", "POST"])
def validation_tasks_endpoint(payload: Optional[Dict[str, Any]] = None):
    """Returns or updates list of configured validation rules/tasks."""
    cfg = config_manager.load_config()
    tasks = cfg.get("validation_tasks", [])

    if payload is not None and isinstance(payload, dict):
        if "tasks" in payload:
            tasks = payload["tasks"]
            cfg["validation_tasks"] = tasks
            config_manager.save_config(cfg)
        elif "task" in payload:
            new_task = payload["task"]
            existing = [t for t in tasks if t.get("id") != new_task.get("id")]
            existing.append(new_task)
            cfg["validation_tasks"] = existing
            config_manager.save_config(cfg)
            tasks = existing

    return {
        "success": True,
        "tasks": tasks,
        "data": {
            "records": tasks,
            "totalRecord": len(tasks),
            "recordsFiltered": len(tasks)
        },
        "records": tasks,
        "totalRecord": len(tasks),
        "recordsFiltered": len(tasks)
    }

@app.delete("/api/validation/tasks/{task_id}")
def delete_validation_task(task_id: str):
    """Deletes a validation task by id."""
    cfg = config_manager.load_config()
    tasks = cfg.get("validation_tasks", [])
    updated = [t for t in tasks if str(t.get("id")) != str(task_id)]
    cfg["validation_tasks"] = updated
    config_manager.save_config(cfg)
    return {"success": True, "message": "Task deleted"}

@app.post("/api/validation/execute-all")
def execute_validation_all(req: ValidationExecuteAllRequest):
    """Executes validation queries across configured targets and returns formatted results."""
    cfg = config_manager.load_config()
    tasks = req.tasks or cfg.get("validation_tasks", [])
    if not tasks:
        return {"success": True, "results": [], "message": "No validation tasks to execute."}

    results = []
    for idx, t in enumerate(tasks, 1):
        res = validation_manager.execute_validation_task(t, cfg)
        res["execution_num"] = idx
        df = res.get("df")
        if df is not None:
            res["columns"] = [str(c) for c in df.columns]
            res["rows"] = df.fillna("").astype(str).values.tolist()
            res["row_count"] = len(df)
            res["df"] = None
        else:
            res["columns"] = []
            res["rows"] = []
            res["row_count"] = 0
        res["success"] = bool(res.get("ok", False))
        results.append(res)

    return {"success": True, "results": results}

@app.post("/api/validation/screenshot")
def capture_validation_screenshot(req: ValidationScreenshotRequest):
    """Renders table PNG image and saves to Desktop/validate-restore/."""
    try:
        df = pd.DataFrame(req.rows, columns=req.columns)
        file_path, filename = validation_manager.save_table_screenshot(
            df=df,
            ip=req.ip,
            db_name=req.db,
            table_name=req.table,
            execution_num=req.execution_num
        )
        return {
            "success": True,
            "path": file_path,
            "filename": filename,
            "message": f"Saved screenshot: {file_path}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------------------------------------------------------------
# STATIC ASSETS / PRODUCTION FRONTEND SERVING
# ------------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    REACT_DIST = os.path.join(base_dir, "dist")
else:
    REACT_DIST = r"D:\Narak\Reactjs\vite-ts\dist"

if os.path.exists(REACT_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(REACT_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_react(full_path: str):
        target = os.path.join(REACT_DIST, full_path)
        if os.path.exists(target) and os.path.isfile(target):
            return FileResponse(target)
        return FileResponse(os.path.join(REACT_DIST, "index.html"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting DB Restore Studio API on http://localhost:{port}")
    uvicorn.run("api_server:app", host="0.0.0.0", port=port, reload=True)
