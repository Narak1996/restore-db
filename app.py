import os
import re
import time
from datetime import datetime
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from config_manager import (
    load_config,
    save_config,
    test_mariadb_connection,
    test_postgresql_connection,
    list_mariadb_databases,
    list_postgresql_databases,
    list_server_backup_files,
)
from history_manager import (
    add_history,
    get_history,
    clear_history,
    delete_history_item,
)
from restore_engine import (
    restore_database,
    find_tool_executable,
)
from ssh_manager import (
    test_ssh_connection,
    list_remote_backup_files,
    list_remote_postgresql_databases,
    list_remote_mariadb_databases,
    remote_restore_database,
    get_ssh_client,
)
from file_explorer import (
    browse_local_directory,
    browse_remote_directory,
    pick_windows_file_dialog,
)
from verify_restore import (
    get_database_proof,
    fetch_table_sample_records,
    execute_custom_query,
)
from auth_manager import authenticate_ad

# Declare custom zero-reload login component
LOGIN_COMP_PATH = os.path.join(os.path.dirname(__file__), "login_component")
login_component = components.declare_component("login_component", path=LOGIN_COMP_PATH)

st.set_page_config(
    page_title="DB Restore Studio",
    page_icon="🗄️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Studio styles will be loaded after authentication for 100% style separation

# Ensure upload directory exists
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Load configuration
config = load_config()
ssh_cfg = config.get("ssh", {})
saved_mode = config.get("connection_mode", "ssh")

# Authentication state
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None

# Active Directory Authentication handled asynchronously via login_component without page reload

if "current_page" not in st.session_state:
    st.session_state["current_page"] = "Restore Operations"

# State initialization to keep dialogs open across navigations/reruns
if "show_file_dialog" not in st.session_state:
    st.session_state["show_file_dialog"] = False
if "show_proof_dialog" not in st.session_state:
    st.session_state["show_proof_dialog"] = False
if "proof_dialog_info" not in st.session_state:
    st.session_state["proof_dialog_info"] = {}
if "remote_explorer_dir" not in st.session_state:
    st.session_state["remote_explorer_dir"] = ssh_cfg.get("remote_backup_dir", "/App2/source_backup")
if "local_explorer_dir" not in st.session_state:
    st.session_state["local_explorer_dir"] = config.get("server_storage", {}).get("backup_dir", r"D:\Narak\Sql file")
if "dlg_path_in" not in st.session_state:
    st.session_state["dlg_path_in"] = st.session_state["remote_explorer_dir"] if saved_mode == "ssh" else st.session_state["local_explorer_dir"]

def on_file_dialog_dismissed():
    st.session_state["show_file_dialog"] = False

def on_proof_dialog_dismissed():
    st.session_state["show_proof_dialog"] = False

def open_proof_modal(p_type: str, p_cfg: dict, p_name: str, file_path: str = "", file_size_mb: float = 0.0):
    st.session_state["proof_dialog_info"] = {
        "type": p_type,
        "cfg": p_cfg,
        "name": p_name,
        "file_path": file_path,
        "file_size_mb": file_size_mb
    }
    st.session_state["show_proof_dialog"] = True
    st.rerun()

# ==============================================================================
# MODAL DIALOG: FILE BROWSER
# ==============================================================================
@st.dialog("🗂️ Select Database Dump File", width="large", on_dismiss=on_file_dialog_dismissed)
def file_browser_dialog(is_ssh_mode: bool, config: dict, ssh_cfg: dict):
    tab_browse, tab_upload, tab_manual = st.tabs([
        "📁 **File Explorer**",
        "📤 **Upload from PC**",
        "📝 **Direct Path**"
    ])

    with tab_browse:
        with st.spinner("Loading directory contents..."):
            if is_ssh_mode:
                current_dir = st.session_state.get("remote_explorer_dir", ssh_cfg.get("remote_backup_dir", "/App2/source_backup"))
                explorer_data = browse_remote_directory(ssh_cfg, current_dir)
            else:
                default_local = config.get("server_storage", {}).get("backup_dir", r"D:\Narak\Sql file")
                current_dir = st.session_state.get("local_explorer_dir", default_local)
                explorer_data = browse_local_directory(current_dir)

        if "dlg_path_in" not in st.session_state:
            st.session_state["dlg_path_in"] = explorer_data.get("current_path", current_dir)

        def _nav_to(new_path: str):
            if is_ssh_mode:
                st.session_state["remote_explorer_dir"] = new_path
            else:
                st.session_state["local_explorer_dir"] = new_path
            st.session_state["dlg_path_in"] = new_path

        def _on_path_entered():
            val = st.session_state.get("dlg_path_in", "").strip()
            if val:
                _nav_to(val)

        # Quick Shortcuts / Drives
        if is_ssh_mode:
            st.caption("Quick Shortcuts:")
            shortcuts = ["/App2/source_backup", "/var/backups", "/home", "/root", "/tmp", "/"]
            sc_cols = st.columns(len(shortcuts))
            for i, sc in enumerate(shortcuts):
                sc_cols[i].button(
                    sc,
                    key=f"dlg_sc_{i}",
                    on_click=_nav_to,
                    args=(sc,),
                    use_container_width=True
                )
        else:
            sc1, sc2 = st.columns([3, 2])
            with sc1:
                st.caption("Drives:")
                drvs = explorer_data.get("drives", [])
                if drvs:
                    dcols = st.columns(len(drvs))
                    for i, drv in enumerate(drvs):
                        dcols[i].button(
                            drv,
                            key=f"dlg_drv_{drv}",
                            on_click=_nav_to,
                            args=(drv,),
                            use_container_width=True
                        )
            with sc2:
                st.caption("Native OS:")
                if st.button("🖥️ Open Windows File Picker", use_container_width=True):
                    picked = pick_windows_file_dialog()
                    if picked:
                        pstat = os.stat(picked)
                        st.session_state["selected_file_info"] = {
                            "name": os.path.basename(picked),
                            "path": picked,
                            "size_mb": round(pstat.st_size / (1024 * 1024), 2),
                            "type": "Local File",
                            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(pstat.st_mtime))
                        }
                        st.session_state["show_file_dialog"] = False
                        st.rerun()

        # Address Bar
        col_up, col_ref, col_addr, col_go = st.columns([1, 1, 7.5, 1])
        with col_up:
            has_parent = bool(explorer_data.get("parent_path"))
            parent_path = explorer_data.get("parent_path") or ("/" if is_ssh_mode else "")
            st.button(
                "⬆️",
                disabled=not has_parent,
                on_click=_nav_to,
                args=(parent_path,),
                use_container_width=True,
                help="Parent Folder"
            )
        with col_ref:
            st.button("🔄", use_container_width=True, help="Refresh Folder")
        with col_addr:
            st.text_input(
                "Path",
                key="dlg_path_in",
                on_change=_on_path_entered,
                label_visibility="collapsed"
            )
        with col_go:
            st.button("Go", on_click=_on_path_entered, use_container_width=True)

        # Search Filter
        search_kw = st.text_input("Filter items...", placeholder="🔍 Search by filename in current folder...", key="dlg_search_kw", label_visibility="collapsed").strip().lower()

        # Items List Container
        if explorer_data.get("error"):
            st.error(explorer_data["error"])
        else:
            items = explorer_data.get("items", [])
            if search_kw:
                items = [it for it in items if search_kw in it["name"].lower()]

            with st.container(height=320):
                h1, h2, h3, h4 = st.columns([5, 2, 2.5, 2])
                h1.markdown("**Name**")
                h2.markdown("**Size**")
                h3.markdown("**Modified**")
                h4.markdown("**Action**")
                st.divider()

                if not items:
                    st.caption("No folders or database dump files found in this directory.")
                else:
                    for idx, itm in enumerate(items):
                        c1, c2, c3, c4 = st.columns([5, 2, 2.5, 2])
                        c1.write(f"{itm['icon']} **{itm['name']}**")
                        if itm["is_dir"]:
                            c2.write("—")
                            c3.write(itm.get("modified", "—"))
                            c4.button(
                                "📂 Open",
                                key=f"dlg_open_{idx}",
                                on_click=_nav_to,
                                args=(itm["path"],),
                                use_container_width=True
                            )
                        else:
                            c2.write(f"{itm['size_mb']} MB")
                            c3.write(itm.get("modified", "—"))
                            if c4.button("✅ Select", key=f"dlg_sel_{idx}", type="primary", use_container_width=True):
                                st.session_state["selected_file_info"] = itm
                                st.session_state["show_file_dialog"] = False
                                st.rerun()

    with tab_upload:
        uploaded_file = st.file_uploader(
            "Upload Dump File from PC",
            type=["sql", "dump", "tar", "backup", "gz"]
        )
        if uploaded_file is not None:
            temp_local = os.path.join(UPLOAD_DIR, uploaded_file.name)
            with open(temp_local, "wb") as f:
                f.write(uploaded_file.getbuffer())
            fsize_mb = os.path.getsize(temp_local) / (1024 * 1024)

            if is_ssh_mode:
                dest = f"{ssh_cfg.get('remote_backup_dir', '/App2/source_backup').rstrip('/')}/{uploaded_file.name}"
                if st.button(f"⬆️ Upload to Remote Server ({fsize_mb:.2f} MB)", type="primary", use_container_width=True):
                    with st.spinner("Uploading to server..."):
                        try:
                            client = get_ssh_client(ssh_cfg)
                            sftp = client.open_sftp()
                            sftp.put(temp_local, dest)
                            sftp.close()
                            client.close()
                            st.session_state["selected_file_info"] = {
                                "name": uploaded_file.name,
                                "path": dest,
                                "size_mb": round(fsize_mb, 2),
                                "type": "Uploaded Dump",
                                "modified": time.strftime("%Y-%m-%d %H:%M")
                            }
                            st.session_state["show_file_dialog"] = False
                            st.rerun()
                        except Exception as e:
                            st.error(f"Upload failed: {str(e)}")
            else:
                st.session_state["selected_file_info"] = {
                    "name": uploaded_file.name,
                    "path": temp_local,
                    "size_mb": round(fsize_mb, 2),
                    "type": "Uploaded Dump",
                    "modified": time.strftime("%Y-%m-%d %H:%M")
                }
                st.session_state["show_file_dialog"] = False
                st.rerun()

    with tab_manual:
        manual_in = st.text_input("Enter Full Path", placeholder=r"e.g. /App2/source_backup/dump.sql or D:\backups\db.sql")
        if st.button("Set Path", use_container_width=True) and manual_in:
            st.session_state["selected_file_info"] = {
                "name": os.path.basename(manual_in.strip()),
                "path": manual_in.strip(),
                "size_mb": 0.0,
                "type": "Manual Path",
                "modified": "N/A"
            }
            st.session_state["show_file_dialog"] = False
            st.rerun()

    # Cancel / Close Button at bottom of dialog
    st.divider()
    c_dlg_space, c_dlg_cancel = st.columns([4, 1.2])
    with c_dlg_cancel:
        if st.button("✖️ Close", key="dlg_close_btn", use_container_width=True):
            st.session_state["show_file_dialog"] = False
            st.rerun()


# ==============================================================================
# MODAL DIALOG: DATABASE RESTORE PROOF & DATA INSPECTOR
# ==============================================================================
@st.dialog("📋 Database Verification & Data Proof", width="large", on_dismiss=on_proof_dialog_dismissed)
def database_proof_dialog(db_type: str, db_cfg: dict, db_name: str, file_path: str = "", file_size_mb: float = 0.0):
    with st.spinner(f"Querying restored database '{db_name}'..."):
        proof = get_database_proof(db_type, db_cfg, db_name)

    if not proof["success"]:
        st.error(f"Could not verify database: {proof.get('error')}")
        return

    # Fallback resolution for source dump file path & size
    if not file_path:
        sel = st.session_state.get("selected_file_info") or {}
        file_path = sel.get("path") or st.session_state.get("proof_file_path", "")
        file_size_mb = sel.get("size_mb") or st.session_state.get("proof_file_size", 0.0)

    display_source_path = file_path if file_path else "(Dump file not recorded)"
    size_badge = f"({file_size_mb:.2f} MB)" if file_size_mb else ""
    server_ip = db_cfg.get("host") or ssh_cfg.get("host", "10.21.0.143")
    server_port = db_cfg.get("port", 3307 if db_type == "MariaDB" else 5432)
    cur_auth = st.session_state.get("auth_user") or {}
    active_user = cur_auth.get("username") or cur_auth.get("display_name") or "AD User"
    srv_usr = ssh_cfg.get("user", "user") if is_ssh_mode else os.getenv("USERNAME", "local")
    db_usr = db_cfg.get("user", "app_dev")

    # Source & Destination Summary Card
    st.markdown(
        f"""
        <div style="background:#16191f; padding:8px 14px; border-radius:6px; border:1px solid #2d3139; font-size:0.83rem; margin-bottom:10px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; border-bottom:1px solid #22262e; padding-bottom:4px;">
                <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:70%;">
                    📁 <b style="color:#8b949e;">Source:</b> <span style="color:#58a6ff; font-family:monospace;">{display_source_path}</span> <span style="color:#aaa;">{size_badge}</span>
                </div>
                <div>
                    👤 <b style="color:#8b949e;">Execute by:</b> <b style="color:#58a6ff;">{active_user}</b> &nbsp;|&nbsp; Engine: <b style="color:#e6edf3;">{db_type}</b>
                </div>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    🎯 <b style="color:#8b949e;">Destination:</b> 
                    <span style="color:#7ee787; font-family:monospace;">{server_ip}:{server_port}</span>
                    &nbsp;➔&nbsp; <b style="color:#58a6ff;">{db_name}</b>
                    &nbsp;|&nbsp; 🖥️ <span style="color:#aaa;">{srv_usr}</span> &nbsp;|&nbsp; 🗄️ <span style="color:#aaa;">{db_usr}</span>
                </div>
                <div style="color:#c9d1d9;">
                    📊 <b>{proof['total_tables']}</b> tables &nbsp;|&nbsp; 
                    🔢 <b>{proof['total_rows']:,}</b> rows &nbsp;|&nbsp; 
                    💾 <b>{proof['size_mb']} MB</b>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    if proof["total_tables"] == 0:
        st.warning(f"Database '{db_name}' is currently empty (0 tables). The restore may still be in progress or the dump was empty.")
        return

    tab_query, tab_tables = st.tabs(["⚡ **SQL Query Console**", "📋 **Restored Tables & Row Counts**"])

    with tab_query:
        default_query = "select id,event,auditable_type,url,created_at from audits order by created_at desc limit 100;"
        query_key = f"sql_query_text_{db_name}"
        if query_key not in st.session_state:
            st.session_state[query_key] = default_query

        # Compact SQL Editor row: Text area + Run button side-by-side
        col_sql, col_btn = st.columns([8.3, 1.7], gap="small")
        with col_sql:
            sql_text = st.text_area(
                "SQL Query",
                value=st.session_state.get(query_key, default_query),
                height=65,
                key=f"area_{query_key}",
                label_visibility="collapsed",
                help="Type any valid SQL statement to execute against this database"
            )
            st.session_state[query_key] = sql_text
        with col_btn:
            execute_clicked = st.button("▶️ Run Query", type="primary", use_container_width=True)

        # Trigger execution if button clicked or on first modal opening
        should_run = execute_clicked or st.session_state.get(f"run_now_{db_name}", False)
        if f"first_load_{db_name}" not in st.session_state:
            st.session_state[f"first_load_{db_name}"] = True
            should_run = True

        if should_run:
            st.session_state[f"run_now_{db_name}"] = False
            with st.spinner("Executing SQL query..."):
                ok, df_res, err_msg, duration = execute_custom_query(db_type, db_cfg, db_name, sql_text)

                # Extract table name from query
                m = re.search(r'\bfrom\s+([^\s;,()]+)', sql_text, re.IGNORECASE)
                table_name = m.group(1).replace('`', '').replace('"', '').strip().split('.')[-1] if m else "audits"

                exec_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                st.session_state[f"query_result_{db_name}"] = {
                    "ok": ok,
                    "df": df_res,
                    "err": err_msg,
                    "duration": duration,
                    "ip": server_ip,
                    "db": db_name,
                    "table": table_name,
                    "time": exec_time_str
                }

                if ok:
                    st.toast(f"✅ Executed in {duration:.3f}s — {len(df_res)} rows returned")
                else:
                    st.toast(f"❌ Query execution failed in {duration:.3f}s")

        res_data = st.session_state.get(f"query_result_{db_name}")
        if res_data:
            if res_data["ok"]:
                df_res = res_data["df"]
                st.markdown(
                    f"""
                    <div style="background:#13161c; border-left:3px solid #388bfd; padding:7px 12px; border-radius:5px; font-size:0.82rem; margin-bottom:8px; border-top:1px solid #21262d; border-right:1px solid #21262d; border-bottom:1px solid #21262d;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                            <div>
                                🕒 <b style="color:#8b949e;">Executed At:</b> <span style="color:#e6edf3; font-family:monospace; font-weight:600;">{res_data['time']}</span>
                                &nbsp;&nbsp;|&nbsp;&nbsp;
                                ⚡ <b style="color:#8b949e;">Query Latency:</b> <span style="color:#7ee787; font-family:monospace; font-weight:600;">{res_data['duration']:.3f}s</span>
                                &nbsp;&nbsp;|&nbsp;&nbsp;
                                📊 <b style="color:#8b949e;">Dataset:</b> <b style="color:#58a6ff;">{len(df_res):,} rows</b> &times; <b>{len(df_res.columns)} cols</b>
                            </div>
                            <div>
                                🎯 <b style="color:#8b949e;">Target:</b> <span style="color:#7ee787; font-family:monospace;">{res_data['ip']}:{server_port}</span> ➔ <b style="color:#58a6ff;">{res_data['db']}.{res_data['table']}</b>
                            </div>
                        </div>
                        <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#8b949e; font-size:0.78rem;">
                            📁 <b style="color:#8b949e;">Source:</b> <span style="color:#58a6ff; font-family:monospace;">{display_source_path}</span> <span style="color:#888;">{size_badge}</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                st.dataframe(df_res, use_container_width=True, height=270)
            else:
                st.error(
                    f"❌ **Query Execution Failed**  \n"
                    f"🕒 **Time:** `{res_data['time']}` &nbsp;|&nbsp; ⚡ **Latency:** `{res_data['duration']:.3f}s` &nbsp;|&nbsp; 🎯 **Target:** `{res_data['ip']}:{server_port}` ➔ `{res_data['db']}.{res_data['table']}`  \n"
                    f"📁 **Source:** `{display_source_path}`  \n"
                    f"**Error Details:** {res_data['err']}"
                )

    with tab_tables:
        st.caption(f"Restored into `{server_ip}:{server_port} > {db_name}` from `{os.path.basename(display_source_path)}`")
        df_tables = pd.DataFrame(proof["tables"])
        df_tables = df_tables.rename(columns={"name": "Table Name", "rows": "Record Count", "size_mb": "Size (MB)"})
        st.dataframe(df_tables, use_container_width=True, hide_index=True)

    # Close button for inspector dialog
    st.divider()
    c_prf_space, c_prf_close = st.columns([4, 1.2])
    with c_prf_close:
        if st.button("✖️ Close", key="prf_close_btn", use_container_width=True):
            st.session_state["show_proof_dialog"] = False
            st.rerun()


# ==============================================================================
# ACTIVE DIRECTORY LOGIN GATEWAY (AJAX / ZERO-RELOAD STREAMLIT COMPONENT)
# ==============================================================================
if not st.session_state.get("authenticated", False):
    st.markdown("""
    <style>
        header[data-testid="stHeader"], footer, #MainMenu, [data-testid="stSidebar"], [data-testid="stSidebarCollapseButton"] {
            display: none !important;
        }
        html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"], .stMain, .stApp {
            background: linear-gradient(135deg, #7c4d3a 0%, #764d49 45%, #6e4856 100%) !important;
            margin: 0 !important;
            padding: 0 !important;
            overflow: hidden !important;
        }
        .block-container {
            padding: 0 !important;
            margin: 0 !important;
            max-width: 100vw !important;
            width: 100vw !important;
            height: 100vh !important;
        }
        iframe {
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            width: 100vw !important;
            height: 100vh !important;
            min-height: 100vh !important;
            border: none !important;
            z-index: 999999 !important;
        }
    </style>
    """, unsafe_allow_html=True)

    err_msg = st.session_state.get("login_error", "")
    auth_data = login_component(error=err_msg, key="ad_login_widget")

    if auth_data and isinstance(auth_data, dict):
        req_id = auth_data.get("reqId")
        if req_id and req_id != st.session_state.get("last_auth_req_id"):
            st.session_state["last_auth_req_id"] = req_id
            email = auth_data.get("email", "").strip()
            password = auth_data.get("password", "")

            ad_cfg = config.get("ad", {})
            success, msg, user_info = authenticate_ad(email, password, ad_cfg)
            if success and user_info:
                st.session_state["authenticated"] = True
                st.session_state["auth_user"] = user_info
                st.session_state.pop("login_error", None)
                st.toast(f"Welcome back, {user_info.get('display_name')}!", icon="👋")
                st.rerun()
            else:
                st.session_state["login_error"] = msg
                st.rerun()

    st.stop()

# ==============================================================================
# PRELINE STUDIO & DASHBOARD STYLING (ISOLATED - RUNS ONLY WHEN AUTHENTICATED)
# ==============================================================================
st.markdown("""
<style>
    /* Hide Streamlit default fixed header to prevent clipping top content */
    header[data-testid="stHeader"] {
        display: none !important;
    }
    #MainMenu {
        visibility: hidden;
    }
    footer {
        visibility: hidden;
    }
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 1.5rem !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        max-width: 100% !important;
    }
    .header-bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.8rem;
        padding-bottom: 0.4rem;
        border-bottom: 1px solid #333;
    }
    .header-title {
        font-size: 1.6rem;
        font-weight: 700;
        margin: 0;
        line-height: 1.3;
    }
    .compact-card {
        padding: 0.8rem 1rem;
        border-radius: 8px;
        background-color: #1e1e24;
        border: 1px solid #2e2e38;
        margin-bottom: 0.8rem;
    }
    .file-selected-box {
        padding: 0.6rem 0.9rem;
        border-radius: 6px;
        background-color: #162a1d;
        border: 1px solid #28a745;
        margin-bottom: 0.6rem;
    }
    .file-empty-box {
        padding: 0.6rem 0.9rem;
        border-radius: 6px;
        background-color: #222;
        border: 1px dashed #555;
        margin-bottom: 0.6rem;
        color: #aaa;
    }
    button[data-baseweb="tab"] {
        padding-top: 0.4rem;
        padding-bottom: 0.4rem;
    }
    /* Fixed max height and auto scroll for live console */
    div[data-testid="stCodeBlock"] {
        max-height: 360px !important;
        overflow-y: auto !important;
    }
    div[data-testid="stCodeBlock"] pre {
        max-height: 360px !important;
        overflow-y: auto !important;
        white-space: pre-wrap !important;
        word-break: break-all !important;
    }

    /* =========================================================================
       PRELINE ADMIN SIDEBAR STYLING
       ========================================================================= */
    section[data-testid="stSidebar"] {
        background-color: #0d1117 !important;
        border-right: 1px solid #1f293d !important;
        width: 270px !important;
        min-width: 270px !important;
    }
    section[data-testid="stSidebar"] > div:first-child {
        padding-top: 1.2rem !important;
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
        padding-bottom: 1.2rem !important;
    }
    [data-testid="stSidebarCollapseButton"] {
        color: #8b949e !important;
    }

    .preline-brand {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 18px;
        padding: 2px 4px 14px 4px;
        border-bottom: 1px solid #1e2638;
    }
    .preline-logo-box {
        width: 36px;
        height: 36px;
        border-radius: 10px;
        background: linear-gradient(135deg, rgba(37,99,235,0.25), rgba(99,102,241,0.2));
        border: 1px solid rgba(59,130,246,0.45);
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        box-shadow: 0 4px 12px rgba(37,99,235,0.2);
    }
    .preline-title {
        font-weight: 700;
        font-size: 1.05rem;
        color: #f8fafc;
        letter-spacing: -0.2px;
        line-height: 1.2;
    }
    .preline-subtitle {
        font-size: 0.63rem;
        font-weight: 700;
        color: #60a5fa;
        letter-spacing: 0.9px;
        text-transform: uppercase;
    }
    .preline-nav-header {
        font-size: 0.68rem;
        font-weight: 700;
        color: #64748b;
        letter-spacing: 0.8px;
        text-transform: uppercase;
        margin-bottom: 8px;
        padding-left: 6px;
    }


    /* =========================================================================
       SIDEBAR REAL NAVIGATION MENU STYLING (NO CHECKBOXES / NO RADIO CIRCLES)
       ========================================================================= */
    section[data-testid="stSidebar"] div[data-testid="stButton"] {
        margin-bottom: 4px !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stButton"] button {
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        text-align: left !important;
        padding: 9px 12px !important;
        border-radius: 8px !important;
        font-size: 0.88rem !important;
        font-weight: 500 !important;
        letter-spacing: 0.01em !important;
        width: 100% !important;
        transition: all 0.15s ease-in-out !important;
    }
    /* Inactive Menu Item */
    section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="secondary"] {
        background: transparent !important;
        color: #94a3b8 !important;
        border: 1px solid transparent !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="secondary"]:hover {
        background: rgba(255, 255, 255, 0.05) !important;
        color: #f8fafc !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
    }
    /* Active Menu Item */
    section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"] {
        background: rgba(59, 130, 246, 0.16) !important;
        color: #60a5fa !important;
        border: 1px solid rgba(59, 130, 246, 0.38) !important;
        font-weight: 600 !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25) !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"]:hover {
        background: rgba(59, 130, 246, 0.24) !important;
        color: #93c5fd !important;
        border: 1px solid rgba(59, 130, 246, 0.5) !important;
    }

    /* Submenu button styling in sidebar */
    section[data-testid="stSidebar"] div[data-testid="stButton"]:has(button[key^="sidebar_sub_"]) button {
        padding-left: 18px !important;
        font-size: 0.81rem !important;
        height: 32px !important;
        min-height: 32px !important;
        margin-bottom: 2px !important;
        border-left: 2px solid rgba(59, 130, 246, 0.3) !important;
        border-radius: 0 6px 6px 0 !important;
        margin-left: 8px !important;
        width: calc(100% - 8px) !important;
    }

    /* =========================================================================
       GLOBAL NEON PROGRESS LOADER & SPINNER STYLING
       ========================================================================= */
    div[data-testid="stStatusWidget"] {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        right: 0 !important;
        width: 100vw !important;
        height: 3px !important;
        background: transparent !important;
        z-index: 9999999 !important;
        border: none !important;
        overflow: hidden !important;
    }
    div[data-testid="stStatusWidget"] > div {
        background: linear-gradient(90deg, #3b82f6 0%, #60a5fa 50%, #818cf8 100%) !important;
        background-size: 200% 100% !important;
        animation: topBarLoadingAnim 1.2s linear infinite !important;
        height: 3px !important;
        box-shadow: 0 0 12px rgba(59, 130, 246, 0.9), 0 0 6px rgba(96, 165, 250, 0.8) !important;
    }
    @keyframes topBarLoadingAnim {
        0% { background-position: 200% 0; }
        100% { background-position: -200% 0; }
    }

    /* Modern Streamlit Spinner & Toast */
    div[data-testid="stSpinner"] {
        background: rgba(13, 17, 23, 0.92) !important;
        border: 1px solid rgba(59, 130, 246, 0.35) !important;
        border-radius: 8px !important;
        padding: 10px 16px !important;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4) !important;
        backdrop-filter: blur(8px) !important;
    }
    div[data-testid="stSpinner"] > div {
        border-top-color: #38bdf8 !important;
    }
    div[data-testid="stProgress"] > div > div > div {
        background: linear-gradient(90deg, #2563eb, #38bdf8) !important;
        border-radius: 9999px !important;
    }

    /* =========================================================================
       TOP BAR NAVBAR (CONNECTION CONTROL & PROFILE DROPDOWN)
       ========================================================================= */
    div[data-testid="stSegmentedControl"] {
        background: #0d1117 !important;
        border: 1px solid #1f293d !important;
        border-radius: 8px !important;
        padding: 2px !important;
        height: 38px !important;
        display: flex !important;
        align-items: center !important;
    }
    div[data-testid="stSegmentedControl"] > div {
        height: 32px !important;
    }
    div[data-testid="stSegmentedControl"] button {
        height: 32px !important;
        font-size: 0.82rem !important;
        padding: 4px 12px !important;
        border-radius: 6px !important;
    }
    div[data-testid="stPopover"] > button {
        background: #0d1117 !important;
        border: 1px solid #1f293d !important;
        color: #e2e8f0 !important;
        border-radius: 8px !important;
        padding: 0 14px !important;
        height: 38px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
        transition: all 0.15s ease !important;
    }
    div[data-testid="stPopover"] > button:hover {
        background: #161f30 !important;
        border-color: #3b82f6 !important;
        color: #60a5fa !important;
    }
    div[data-testid="stPopoverBody"] {
        background: #0d1117 !important;
        border: 1px solid #1f293d !important;
        border-radius: 12px !important;
        box-shadow: 0 12px 28px -4px rgba(0, 0, 0, 0.6) !important;
        padding: 14px !important;
    }

    /* Preline Sidebar Navigation Pill Styling */
    section[data-testid="stSidebar"] div[data-testid="stRadio"] {
        margin-bottom: 0.3rem !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] {
        display: flex !important;
        flex-direction: column !important;
        gap: 4px !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        display: flex !important;
        align-items: center !important;
        padding: 8px 12px !important;
        border-radius: 8px !important;
        font-size: 0.86rem !important;
        font-weight: 500 !important;
        color: #94a3b8 !important;
        background-color: transparent !important;
        transition: all 0.15s ease-in-out !important;
        cursor: pointer !important;
        border: 1px solid transparent !important;
        margin: 0 !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child {
        display: none !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label p {
        margin: 0 !important;
        font-size: 0.86rem !important;
        font-weight: 500 !important;
        color: inherit !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background-color: rgba(255, 255, 255, 0.04) !important;
        color: #f1f5f9 !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
        background-color: rgba(37, 99, 235, 0.15) !important;
        color: #60a5fa !important;
        border: 1px solid rgba(59, 130, 246, 0.35) !important;
        font-weight: 600 !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25) !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {
        color: #60a5fa !important;
        font-weight: 600 !important;
    }

    /* Target status box in sidebar */
    .preline-status-box {
        background: #131824;
        border: 1px solid #1f293d;
        border-radius: 8px;
        padding: 9px 12px;
        margin-top: 6px;
        margin-bottom: 8px;
    }

    /* User Profile card in sidebar */
    .preline-user-card {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 9px 10px;
        background: #131824;
        border: 1px solid #1f293d;
        border-radius: 8px;
        margin-top: 14px;
        margin-bottom: 8px;
    }
    .preline-avatar {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        background: linear-gradient(135deg, #2563eb, #8b5cf6);
        color: #ffffff;
        font-weight: 700;
        font-size: 0.8rem;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        box-shadow: 0 2px 6px rgba(0,0,0,0.3);
    }
    .preline-user-info {
        overflow: hidden;
        min-width: 0;
        flex: 1;
    }
    .preline-user-name {
        font-size: 0.83rem;
        font-weight: 600;
        color: #f1f5f9;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .preline-user-id {
        font-size: 0.72rem;
        color: #8b949e;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    /* Top Bar in Main Content */
    .preline-topbar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-bottom: 12px;
        margin-bottom: 18px;
        border-bottom: 1px solid #1e2433;
    }
    .preline-topbar-breadcrumb {
        font-size: 0.7rem;
        font-weight: 700;
        color: #64748b;
        letter-spacing: 0.8px;
        text-transform: uppercase;
        margin-bottom: 2px;
    }
    .preline-topbar-title {
        font-size: 1.45rem;
        font-weight: 700;
        color: #f8fafc;
        line-height: 1.2;
    }
    .preline-topbar-desc {
        font-size: 0.8rem;
        color: #8b949e;
        margin-top: 2px;
    }
    .preline-topbar-chips {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .preline-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: #161b26;
        border: 1px solid #242c3d;
        padding: 5px 12px;
        border-radius: 6px;
        font-size: 0.78rem;
        font-weight: 500;
        color: #cbd5e1;
    }
    .preline-chip-subtle {
        background: transparent;
        border-color: #1e2433;
        color: #8b949e;
    }
    .chip-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background-color: #10b981;
    }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# PRELINE SIDEBAR & USER PROFILE
# ==============================================================================
auth_user = st.session_state.get("auth_user") or {}
display_name = auth_user.get("display_name") or auth_user.get("username") or "AD User"
user_name_str = auth_user.get("username") or ""
card_id_str = f" • #{auth_user.get('card_id')}" if auth_user.get("card_id") else ""
name_parts = display_name.strip().split()
user_initials = (name_parts[0][0] + (name_parts[1][0] if len(name_parts) > 1 else "")).upper() if name_parts else "AD"

with st.sidebar:
    # Brand Header
    st.markdown("""
    <div class="preline-brand">
        <div class="preline-logo-box">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
                <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
                <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
            </svg>
        </div>
        <div>
            <div class="preline-title">DB Restore</div>
            <div class="preline-subtitle">ENTERPRISE STUDIO</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Main Navigation: Real Menu Items (NO CHECKBOXES / NO RADIO CIRCLES)
    st.markdown('<div class="preline-nav-header">MAIN MENU</div>', unsafe_allow_html=True)

    # 1. Restore Operations
    op_active = (st.session_state.get("current_page", "Restore Operations") == "Restore Operations")
    if st.button("🚀  Restore Operations", key="sidebar_nav_ops", type="primary" if op_active else "secondary", use_container_width=True):
        st.session_state["current_page"] = "Restore Operations"
        st.rerun()

    # 2. Restore History
    hist_active = (st.session_state.get("current_page") == "Restore History")
    if st.button("📜  Restore History", key="sidebar_nav_hist", type="primary" if hist_active else "secondary", use_container_width=True):
        st.session_state["current_page"] = "Restore History"
        st.rerun()

    # 3. Configuration Menu with Dropdown / Submenu
    cfg_active = ("Configuration" in st.session_state.get("current_page", ""))
    if "cfg_expanded" not in st.session_state:
        st.session_state["cfg_expanded"] = True

    c_cfg_btn, c_cfg_toggle = st.columns([4.2, 1.2])
    with c_cfg_btn:
        if st.button("⚙️  Configuration", key="sidebar_nav_cfg_main", type="primary" if cfg_active else "secondary", use_container_width=True):
            st.session_state["current_page"] = "Configuration"
            st.rerun()
    with c_cfg_toggle:
        arr_icon = "▼" if st.session_state["cfg_expanded"] else "▶"
        if st.button(arr_icon, key="sidebar_nav_cfg_toggle", help="Toggle Configuration categories", use_container_width=True):
            st.session_state["cfg_expanded"] = not st.session_state["cfg_expanded"]
            st.rerun()

    # Dropdown sub-categories under Configuration
    if st.session_state.get("cfg_expanded", True):
        cfg_subitems = [
            ("🌐 Remote SSH", "Remote SSH"),
            ("🗄️ Database Engines", "Databases"),
            ("🏢 Active Directory", "Active Directory"),
            ("📁 Server Storage", "Storage"),
            ("📋 Full Overview", "Overview")
        ]
        curr_cat = st.session_state.get("config_category", "Remote SSH")
        for sub_label, sub_id in cfg_subitems:
            is_sub_selected = (cfg_active and curr_cat == sub_id)
            if st.button(
                f"↳ {sub_label}",
                key=f"sidebar_sub_{sub_id}",
                type="primary" if is_sub_selected else "secondary",
                use_container_width=True
            ):
                st.session_state["current_page"] = "Configuration"
                st.session_state["config_category"] = sub_id
                st.rerun()

    # Spacer
    st.markdown("<div style='flex:1; min-height:24px;'></div>", unsafe_allow_html=True)

    # Active Target Info Box in Sidebar
    is_ssh_curr = (saved_mode == "ssh")
    if is_ssh_curr:
        srv_disp = f"{ssh_cfg.get('user', 'user')}@{ssh_cfg.get('host', '10.21.0.143')}:{ssh_cfg.get('port', 22)}"
        dir_disp = ssh_cfg.get('remote_backup_dir', '/App2/source_backup')
        badge_html = '<span style="display:inline-flex; align-items:center; gap:4px; font-size:0.68rem; font-weight:600; color:#10b981;"><span style="width:6px; height:6px; border-radius:50%; background:#10b981; display:inline-block;"></span> SSH</span>'
    else:
        srv_disp = "Direct Socket (Localhost)"
        dir_disp = config.get('server_storage', {}).get('backup_dir', r'D:\Narak\Sql file')
        badge_html = '<span style="display:inline-flex; align-items:center; gap:4px; font-size:0.68rem; font-weight:600; color:#38bdf8;"><span style="width:6px; height:6px; border-radius:50%; background:#38bdf8; display:inline-block;"></span> SOCKET</span>'

    st.markdown(f"""
    <div class="preline-status-box">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <span style="font-size:0.68rem; font-weight:700; color:#64748b; letter-spacing:0.5px;">ACTIVE TARGET</span>
            {badge_html}
        </div>
        <div style="font-family:monospace; font-size:0.77rem; color:#e2e8f0; font-weight:600; overflow:hidden; text-overflow:ellipsis;" title="{srv_disp}">
            {srv_disp}
        </div>
        <div style="font-size:0.68rem; color:#94a3b8; margin-top:2px; overflow:hidden; text-overflow:ellipsis;" title="{dir_disp}">
            📁 {dir_disp}
        </div>
    </div>
    """, unsafe_allow_html=True)


# ==============================================================================
# TOP BAR NAVBAR (ENTERPRISE APP HEADER)
# ==============================================================================
nav_choice = st.session_state.get("current_page", "Restore Operations")

if "Restore Operations" in nav_choice:
    page_title = "Restore Operations"
    page_icon = "🚀"
    page_desc = "Zero-downtime database restoration pipeline & live query verification"
elif "Restore History" in nav_choice:
    page_title = "Restore History"
    page_icon = "📜"
    page_desc = "Audit trail of executed restorations, console logs, and target details"
else:
    curr_cat_name = st.session_state.get("config_category", "Remote SSH")
    page_title = f"Configuration › {curr_cat_name}" if curr_cat_name != "Overview" else "System Configuration"
    page_icon = "⚙️"
    page_desc = "Categorized management for SSH, Database Engines, Active Directory, and Storage"

# TOP BAR ROW: BREADCRUMBS, CONNECTION MODE, USER DROPDOWN
with st.container():
    c_tb_left, c_tb_conn, c_tb_user = st.columns([3.8, 3.8, 2.4], vertical_alignment="center")

    with c_tb_left:
        st.markdown(f"""
        <div style="display:flex; align-items:center; gap:8px; height:38px;">
            <div style="width:28px; height:28px; border-radius:6px; background:rgba(59,130,246,0.15); border:1px solid rgba(59,130,246,0.3); display:flex; align-items:center; justify-content:center; font-size:0.85rem; flex-shrink:0;">
                {page_icon}
            </div>
            <div style="display:flex; align-items:center; gap:6px; overflow:hidden;">
                <span style="font-weight:700; font-size:0.68rem; color:#64748b; letter-spacing:0.8px; text-transform:uppercase;">STUDIO</span>
                <span style="color:#334155; font-size:0.75rem;">/</span>
                <span style="font-weight:600; font-size:0.86rem; color:#f8fafc; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">{page_title}</span>
            </div>
            <span style="display:inline-flex; align-items:center; gap:4px; font-size:0.64rem; font-weight:600; color:#10b981; background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.25); padding:1px 6px; border-radius:9999px; margin-left:4px; flex-shrink:0;">
                <span style="width:5px; height:5px; border-radius:50%; background:#10b981; display:inline-block; box-shadow:0 0 6px #10b981;"></span> LIVE
            </span>
        </div>
        """, unsafe_allow_html=True)

    with c_tb_conn:
        mode_options = ["🌐 Remote SSH", "💻 Direct / Local"]
        curr_default = mode_options[0] if saved_mode == "ssh" else mode_options[1]
        selected_mode = st.segmented_control(
            "Connection Mode",
            mode_options,
            default=curr_default,
            key="header_conn_mode_ctrl",
            label_visibility="collapsed",
            required=True
        )
        is_ssh_mode = selected_mode.startswith("🌐") if selected_mode else (saved_mode == "ssh")

    with c_tb_user:
        pop_label = f"👤  {display_name}  ▾"
        with st.popover(pop_label, use_container_width=True):
            st.markdown(f"""
            <div style="display: flex; align-items: center; gap: 12px; padding: 4px 0 10px 0;">
                <div style="width: 42px; height: 42px; border-radius: 50%; background: linear-gradient(135deg, #2563eb, #7c3aed); color: #ffffff; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1rem; flex-shrink: 0; box-shadow: 0 4px 12px rgba(37,99,235,0.3);">
                    {user_initials}
                </div>
                <div style="overflow: hidden;">
                    <div style="font-weight: 700; font-size: 0.95rem; color: #f8fafc; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">
                        {display_name}
                    </div>
                    <div style="font-size: 0.76rem; color: #94a3b8; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; margin-top: 1px;">
                        {user_name_str}{card_id_str}
                    </div>
                </div>
            </div>
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 12px;">
                <span style="font-size: 0.70rem; font-weight: 600; color: #38bdf8; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.25); padding: 2px 8px; border-radius: 9999px;">
                    {auth_user.get('real_position', 'AD Staff')}
                </span>
                <span style="font-size: 0.70rem; font-weight: 600; color: #10b981; background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.25); padding: 2px 8px; border-radius: 9999px;">
                    Active Directory
                </span>
            </div>
            """, unsafe_allow_html=True)
            st.divider()
            if st.button("🚪 Sign Out", key="header_dropdown_logout_btn", use_container_width=True, type="primary"):
                st.session_state["authenticated"] = False
                st.session_state["auth_user"] = None
                st.session_state["current_page"] = "Restore Operations"
                st.rerun()

# SUB-HEADER: TARGET STATUS STRIP
if is_ssh_mode:
    active_host_text = f"{ssh_cfg.get('user', 'user')}@{ssh_cfg.get('host', '10.21.0.143')}:{ssh_cfg.get('port', 22)}"
    remote_dir = ssh_cfg.get('remote_backup_dir', '/App2/source_backup')
    target_badge = f'<span style="color:#10b981; font-weight:600;">● SSH ACTIVE</span> &nbsp;•&nbsp; <span style="color:#e2e8f0; font-family:monospace;">{active_host_text}</span> &nbsp;•&nbsp; <span style="color:#94a3b8;">📁 {remote_dir}</span>'
else:
    local_dir = config.get('server_storage', {}).get('backup_dir', r'D:\Narak\Sql file')
    target_badge = f'<span style="color:#38bdf8; font-weight:600;">● SOCKET ACTIVE</span> &nbsp;•&nbsp; <span style="color:#e2e8f0;">Localhost Direct</span> &nbsp;•&nbsp; <span style="color:#94a3b8;">📁 {local_dir}</span>'

st.markdown(f"""
<div style="display:flex; justify-content:space-between; align-items:center; background:#0d1117; border:1px solid #1f293d; border-radius:8px; padding:6px 14px; margin-top:2px; margin-bottom:14px; font-size:0.74rem;">
    <div>{target_badge}</div>
    <div style="color:#64748b; font-size:0.70rem;">Gateway: <span style="color:#38bdf8; font-weight:600;">Production Enterprise Studio</span></div>
</div>
""", unsafe_allow_html=True)

# PAGE BANNER HEADER (TITLE & DESCRIPTION)
st.markdown(f"""
<div style="margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid #1e2638;">
    <div style="font-size:1.45rem; font-weight:700; color:#f8fafc; letter-spacing:-0.3px; line-height:1.2;">
        {page_title}
    </div>
    <div style="font-size:0.78rem; color:#94a3b8; margin-top:4px;">
        {page_desc}
    </div>
</div>
""", unsafe_allow_html=True)

# ==============================================================================
# VIEW 1: ZERO-SCROLL RESTORE OPERATIONS
# ==============================================================================
if "Restore Operations" in nav_choice:
    col_left, col_right = st.columns([4.2, 5.8], gap="medium")

    with col_left:
        selected_info = st.session_state.get("selected_file_info")
        final_file_path = selected_info["path"] if selected_info else None
        file_size_mb = selected_info["size_mb"] if selected_info else 0.0

        # CARD 1: DATABASE SETUP
        with st.container():
            st.markdown("**1. Database Engine & Target**")
            
            c_eng, c_mode = st.columns([1, 1])
            with c_eng:
                st.caption("Database Engine:")
                db_type_raw = st.radio("Engine", ["🐘 PostgreSQL", "🦭 MariaDB"], horizontal=True, label_visibility="collapsed")
                db_type = "PostgreSQL" if "PostgreSQL" in db_type_raw else "MariaDB"
            with c_mode:
                st.caption("Target Action:")
                db_mode = st.radio("Mode", ["Create New", "Select Existing"], horizontal=True, label_visibility="collapsed")

            cfg_key = "postgresql" if db_type == "PostgreSQL" else "mariadb"
            current_cfg = config[cfg_key]

            target_db = ""
            create_db_flag = False
            drop_existing = False

            if db_mode == "Create New":
                c_name, c_drop = st.columns([2.6, 1.4])
                with c_name:
                    target_db = st.text_input("New DB Name", placeholder="e.g. epurchasing_oct", label_visibility="collapsed").strip()
                with c_drop:
                    drop_existing = st.checkbox("Re-create", value=False, help="Drop database first if it already exists")
                create_db_flag = True
            else:
                with st.spinner(f"Fetching {db_type} databases..."):
                    if is_ssh_mode:
                        if db_type == "PostgreSQL":
                            existing_dbs = list_remote_postgresql_databases(ssh_cfg, current_cfg)
                        else:
                            existing_dbs = list_remote_mariadb_databases(ssh_cfg, current_cfg)
                    else:
                        if db_type == "PostgreSQL":
                            existing_dbs = list_postgresql_databases(current_cfg)
                        else:
                            existing_dbs = list_mariadb_databases(current_cfg)

                c_sel, c_drop = st.columns([2.6, 1.4])
                with c_sel:
                    if existing_dbs:
                        target_db = st.selectbox("Existing DB", existing_dbs, label_visibility="collapsed")
                    else:
                        target_db = st.text_input("DB Name", placeholder="e.g. mydb", label_visibility="collapsed").strip()
                with c_drop:
                    drop_existing = st.checkbox("Re-create", value=False)
                create_db_flag = drop_existing

            if target_db:
                sel_path = final_file_path or ""
                sel_sz = file_size_mb
                if st.button(f"🔍 Inspect '{target_db}' Tables & Data", use_container_width=True):
                    open_proof_modal(db_type, current_cfg, target_db, sel_path, sel_sz)

        st.divider()

        # CARD 2: DUMP FILE SELECTION
        with st.container():
            st.markdown("**2. Dump / SQL File**")

            if selected_info:
                st.markdown(f"""
                <div class="file-selected-box">
                    <div style="font-weight:600; font-size:0.95rem;">📄 {selected_info['name']} ({selected_info['size_mb']} MB)</div>
                    <div style="font-size:0.8rem; color:#aaa; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{selected_info['path']}</div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown("""
                <div class="file-empty-box">
                    No dump file selected yet. Click below to browse.
                </div>
                """, unsafe_allow_html=True)

            col_browse_btn, col_clear_btn = st.columns([3.3, 1.7])
            with col_browse_btn:
                if st.button("📁 Browse Server Files (Dialog)", use_container_width=True, type="secondary"):
                    st.session_state["show_file_dialog"] = True
                    st.rerun()
            with col_clear_btn:
                if st.button("Clear File", use_container_width=True, disabled=not bool(selected_info)):
                    st.session_state["selected_file_info"] = None
                    st.rerun()

        st.divider()

        # CARD 3: RESTORE TRIGGER & ROUTING SUMMARY
        can_run = bool(target_db and final_file_path)
        dest_host = ssh_cfg.get('host', '10.21.0.143') if is_ssh_mode else current_cfg.get('host', 'localhost')
        dest_port = current_cfg.get('port', 3307 if db_type == 'MariaDB' else 5432)
        destination_str = f"{dest_host}:{dest_port} / {target_db}"
        srv_usr = ssh_cfg.get('user', 'user') if is_ssh_mode else os.getenv('USERNAME', 'local')
        db_usr = current_cfg.get('user', 'app_dev')
        auth_usr_obj = st.session_state.get("auth_user") or {}
        exec_by = auth_usr_obj.get("username") or auth_usr_obj.get("display_name") or "AD User"

        if can_run:
            st.markdown(f"""
            <div style="background:#16191f; border-left:3px solid #238636; padding:8px 10px; border-radius:4px; font-size:0.8rem; margin-bottom:8px;">
                <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">📁 <b style="color:#8b949e;">Source:</b> <span style="color:#58a6ff; font-family:monospace;">{final_file_path}</span></div>
                <div style="margin-top:2px;">🎯 <b style="color:#8b949e;">Destination:</b> <span style="color:#7ee787; font-family:monospace;">{destination_str}</span> &nbsp;|&nbsp; 👤 <b style="color:#8b949e;">Execute By:</b> <span style="color:#58a6ff;">{exec_by}</span></div>
                <div style="margin-top:2px; font-size:0.75rem; color:#8b949e;">🖥️ Server: <code style="color:#aaa;">{srv_usr}</code> &nbsp;|&nbsp; 🗄️ DB: <code style="color:#aaa;">{db_usr}</code></div>
            </div>
            """, unsafe_allow_html=True)

        start_btn = st.button("🚀 Execute Database Restore", type="primary", disabled=not can_run, use_container_width=True)

    with col_right:
        st.markdown("**Live Console & Execution Log**")
        status_box = st.empty()
        with st.container(height=390):
            log_box = st.empty()

        if start_btn:
            start_time = time.time()
            all_logs = []
            progress_slot = st.empty()
            progress_slot.progress(10, text=f"🚀 Initializing restoration pipeline for '{target_db}'...")

            def stream_log(line: str):
                all_logs.append(line)
                recent = "\n".join(all_logs)
                log_box.code(recent, language="bash")
                line_lower = line.lower()
                if "transfer" in line_lower or "upload" in line_lower or "sftp" in line_lower:
                    progress_slot.progress(35, text="📤 Transferring database dump to target server...")
                elif "restor" in line_lower or "execut" in line_lower or "import" in line_lower or "psql" in line_lower:
                    progress_slot.progress(70, text="⚙️ Streaming and applying SQL into database engine...")
                elif "success" in line_lower or "finish" in line_lower or "complete" in line_lower:
                    progress_slot.progress(95, text="✅ Validating database tables & indexes...")

            spinner_label = f"Restoring {db_type} database '{target_db}' on {ssh_cfg.get('host')} via SSH..." if is_ssh_mode else f"Restoring {db_type} database '{target_db}'..."
            with st.spinner(spinner_label):
                if is_ssh_mode:
                    success, full_log = remote_restore_database(
                        ssh_cfg=ssh_cfg,
                        db_type=db_type,
                        db_cfg=current_cfg,
                        db_name=target_db,
                        remote_file_path=final_file_path,
                        create_db=create_db_flag,
                        drop_existing=drop_existing,
                        log_fn=stream_log
                    )
                else:
                    success, full_log = restore_database(
                        db_type=db_type,
                        config=current_cfg,
                        target_db=target_db,
                        file_path=final_file_path,
                        create_db=create_db_flag,
                        drop_existing=drop_existing,
                        log_fn=stream_log
                    )

            if success:
                progress_slot.progress(100, text="🎉 Restoration completed successfully!")
            else:
                progress_slot.progress(100, text="⚠️ Restoration failed. See execution logs below.")

            elapsed = time.time() - start_time
            status_str = "SUCCESS" if success else "FAILED"
            engine_record_label = f"{db_type} (SSH: {ssh_cfg.get('host')})" if is_ssh_mode else db_type

            # Record to History with Server user, Database user, and Executed By
            add_history(
                db_type=engine_record_label,
                db_name=target_db,
                file_name=os.path.basename(final_file_path),
                file_size_mb=file_size_mb,
                created_db=create_db_flag,
                status=status_str,
                duration_sec=elapsed,
                logs=full_log,
                source_path=final_file_path,
                destination=destination_str,
                server_user=srv_usr,
                db_user=db_usr,
                executed_by=exec_by,
                user_name=f"{exec_by} ({db_usr})"
            )

            if success:
                st.balloons()
                status_box.success(
                    f"🎉 **Success!** Restored `{target_db}` in {elapsed:.2f}s  \n"
                    f"📁 **Source:** `{final_file_path}`  \n"
                    f"🎯 **Destination:** `{destination_str}`"
                )
                st.session_state["proof_target_db"] = target_db
                st.session_state["proof_db_type"] = db_type
                st.session_state["proof_current_cfg"] = current_cfg
                st.session_state["proof_file_path"] = final_file_path
                st.session_state["proof_file_size"] = file_size_mb
                open_proof_modal(db_type, current_cfg, target_db, final_file_path, file_size_mb)
            else:
                status_box.error(
                    f"❌ **Restore failed** after {elapsed:.2f}s  \n"
                    f"📁 **Source:** `{final_file_path}`  \n"
                    f"🎯 **Destination:** `{destination_str}`"
                )
        else:
            if st.session_state.get("proof_target_db"):
                p_db = st.session_state["proof_target_db"]
                p_type = st.session_state["proof_db_type"]
                p_cfg = st.session_state["proof_current_cfg"]
                col_pf1, col_pf2 = st.columns([3, 1.5])
                with col_pf1:
                    st.info(f"✨ Restored Database **{p_db}** proof is available.")
                with col_pf2:
                    if st.button("📋 View Proof & Data", key="btn_view_proof", use_container_width=True, type="primary"):
                        open_proof_modal(
                            p_type,
                            p_cfg,
                            p_db,
                            st.session_state.get("proof_file_path", ""),
                            st.session_state.get("proof_file_size", 0.0)
                        )
            else:
                target_server_str = f"{ssh_cfg.get('user', 'user')}@{dest_host}:{ssh_cfg.get('port', 22)}" if is_ssh_mode else "Direct Socket"
                log_box.code(
                    f"# ================================================================\n"
                    f"# DB RESTORE TERMINAL CONSOLE\n"
                    f"# Source File   : {final_file_path or '(No dump file selected)'}\n"
                    f"# Destination   : {destination_str if target_db else '(Target DB not set)'}\n"
                    f"# Engine        : {db_type}\n"
                    f"# Server User   : {srv_usr}\n"
                    f"# Database User : {db_usr}\n"
                    f"# Execute By    : {exec_by}\n"
                    f"# SSH Target    : {target_server_str}\n"
                    f"# Status        : Ready — Waiting to execute restore...\n"
                    f"# ================================================================\n"
                    f"# Console output will stream live here during execution.",
                    language="bash"
                )


# ==============================================================================
# TAB 2: RESTORE HISTORY
# ==============================================================================
elif "Restore History" in nav_choice:
    records = get_history()

    if not records:
        st.info("No restore operations recorded yet.")
    else:
        fcol1, fcol2, fcol3 = st.columns([1, 1, 1])
        with fcol1:
            engine_filter = st.selectbox("Engine", ["All", "PostgreSQL", "MariaDB"], label_visibility="collapsed")
        with fcol2:
            status_filter = st.selectbox("Status", ["All", "SUCCESS", "FAILED"], label_visibility="collapsed")
        with fcol3:
            search_query = st.text_input("Search", placeholder="Filter DB, source, or user...", label_visibility="collapsed").strip().lower()

        filtered = []
        for r in records:
            if engine_filter != "All" and engine_filter not in r["db_type"]:
                continue
            if status_filter != "All" and r["status"] != status_filter:
                continue
            if search_query:
                combined = f"{r.get('db_name', '')} {r.get('file_name', '')} {r.get('source_path', '')} {r.get('destination', '')} {r.get('server_user', '')} {r.get('db_user', '')} {r.get('executed_by', '')}".lower()
                if search_query not in combined:
                    continue
            filtered.append(r)

        df = pd.DataFrame(filtered)
        if not df.empty:
            for c in ["source_path", "destination", "server_user", "db_user", "executed_by"]:
                if c not in df.columns:
                    df[c] = ""
            df["source_path"] = df["source_path"].fillna(df["file_name"])
            df["destination"] = df["destination"].fillna(df["db_name"])
            df["server_user"] = df["server_user"].fillna("user")
            df["db_user"] = df["db_user"].fillna("app_dev")
            df["executed_by"] = df["executed_by"].fillna("-")

            display_df = df[[
                "id", "timestamp", "db_type", "source_path", "destination",
                "server_user", "db_user", "executed_by",
                "file_size_mb", "status", "duration_sec"
            ]].copy()
            display_df = display_df.rename(columns={
                "id": "ID",
                "timestamp": "Timestamp",
                "db_type": "Engine",
                "source_path": "Source (Dump File)",
                "destination": "Destination",
                "server_user": "Server user",
                "db_user": "Database user",
                "executed_by": "Execute by",
                "file_size_mb": "Size (MB)",
                "status": "Status",
                "duration_sec": "Duration (s)"
            })
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            selected_id = st.selectbox(
                "Inspect Log",
                [r["id"] for r in filtered],
                format_func=lambda x: f"ID #{x} — {next(r['db_type'] + ' | ' + (r.get('destination') or r['db_name']) + ' [by ' + (r.get('executed_by') or 'AD') + '] (' + r['status'] + ')' for r in filtered if r['id'] == x)}"
            )
            rec = next((r for r in filtered if r["id"] == selected_id), None)
            if rec:
                rec_src = rec.get("source_path") or rec.get("file_name", "")
                rec_dest = rec.get("destination") or rec.get("db_name", "")
                rec_exec_by = rec.get("executed_by") or rec.get("user_name") or "AD User"
                rec_srv_usr = rec.get("server_user") or "user"
                rec_db_usr = rec.get("db_user") or "app_dev"
                rec_status = rec.get("status", "SUCCESS")
                status_color = "#3fb950" if rec_status == "SUCCESS" else "#f85149"

                st.markdown(
                    f"""
                    <div style="background:#16191f; padding:8px 14px; border-radius:6px; border:1px solid #2d3139; font-size:0.83rem; margin-bottom:8px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; border-bottom:1px solid #22262e; padding-bottom:4px;">
                            <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:70%;">
                                📁 <b style="color:#8b949e;">Source:</b> <code style="color:#58a6ff;">{rec_src}</code> <span style="color:#aaa;">({rec.get('file_size_mb', 0)} MB)</span>
                            </div>
                            <div>
                                👤 <b style="color:#8b949e;">Execute by:</b> <b style="color:#58a6ff;">{rec_exec_by}</b>
                            </div>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                            <div>
                                🎯 <b style="color:#8b949e;">Destination:</b> <code style="color:#7ee787;">{rec_dest}</code>
                            </div>
                            <div>
                                🖥️ <b style="color:#8b949e;">Server user:</b> <code style="color:#e6edf3;">{rec_srv_usr}</code>
                                &nbsp;|&nbsp;
                                🗄️ <b style="color:#8b949e;">Database user:</b> <code style="color:#e6edf3;">{rec_db_usr}</code>
                            </div>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.78rem; color:#8b949e;">
                            <div>
                                ⏱️ Duration: <b>{rec.get('duration_sec', 0)}s</b> &nbsp;|&nbsp; 
                                Status: <b style="color:{status_color};">{rec_status}</b>
                            </div>
                            <div>
                                🕒 {rec.get('timestamp')}
                            </div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                h_c1, h_c2 = st.columns([3, 1.5])
                with h_c1:
                    st.caption(f"Console log recorded at {rec['timestamp']}:")
                with h_c2:
                    h_engine = "PostgreSQL" if "PostgreSQL" in rec["db_type"] else "MariaDB"
                    h_cfg = config["postgresql"] if h_engine == "PostgreSQL" else config["mariadb"]
                    if st.button("📋 Inspect DB Tables & Data", key=f"hist_inspect_{rec['id']}", use_container_width=True):
                        open_proof_modal(h_engine, h_cfg, rec["db_name"], rec_src, rec.get("file_size_mb", 0.0))

                st.code(rec["logs"] if rec["logs"] else "No logs recorded.", language="bash")


# ==============================================================================
# TAB 3: CATEGORIZED CONFIGURATION (SEPARATE FUNCTIONS UNDER DROPDOWN)
# ==============================================================================
elif "Configuration" in nav_choice:
    cat_map = {
        "Remote SSH": ("🌐 Remote SSH Server", "Remote Linux host connection, SFTP credentials, and backup paths"),
        "Databases": ("🗄️ Database Engines", "PostgreSQL and MariaDB target connection endpoints and credentials"),
        "Active Directory": ("🏢 Active Directory Gateway", "Corporate Active Directory single sign-on service settings"),
        "Storage": ("📁 Server Storage & Backups", "Local system storage directories and backup search paths"),
        "Overview": ("📋 All Settings (Overview)", "Consolidated management view across all system configurations")
    }
    cat_keys = list(cat_map.keys())
    current_cat = st.session_state.get("config_category", "Remote SSH")
    if current_cat not in cat_keys:
        current_cat = "Remote SSH"

    ssh_data = config.get("ssh", {})
    pg_cfg = config.get("postgresql", {})
    maria_cfg = config.get("mariadb", {})
    ad_data = config.get("ad", {})
    storage_data = config.get("server_storage", {})

    def do_save_all():
        new_config = {
            "connection_mode": "ssh" if is_ssh_mode else "direct",
            "ssh": {
                "host": st.session_state.get("cfg_ssh_host", ssh_data.get("host", "10.21.0.143")).strip(),
                "port": int(st.session_state.get("cfg_ssh_port", ssh_data.get("port", 22))),
                "user": st.session_state.get("cfg_ssh_user", ssh_data.get("user", "user")).strip(),
                "auth_type": "password" if st.session_state.get("cfg_ssh_auth_type", "Password") == "Password" else "key",
                "password": st.session_state.get("cfg_ssh_pass", ssh_data.get("password", "")),
                "key_path": st.session_state.get("cfg_ssh_key", ssh_data.get("key_path", "")).strip(),
                "remote_backup_dir": st.session_state.get("cfg_ssh_dir", ssh_data.get("remote_backup_dir", "/App2/source_backup")).strip()
            },
            "server_storage": {
                "backup_dir": st.session_state.get("cfg_storage_dir", storage_data.get("backup_dir", r"D:\Narak\Sql file")).strip(),
                "scan_subdirectories": bool(st.session_state.get("cfg_storage_subdirs", storage_data.get("scan_subdirectories", False)))
            },
            "postgresql": {
                "host": st.session_state.get("cfg_pg_host", pg_cfg.get("host", "10.21.0.143")).strip(),
                "port": int(st.session_state.get("cfg_pg_port", pg_cfg.get("port", 5432))),
                "user": st.session_state.get("cfg_pg_user", pg_cfg.get("user", "app_dev")).strip(),
                "password": st.session_state.get("cfg_pg_pass", pg_cfg.get("password", "")),
                "maintenance_db": st.session_state.get("cfg_pg_maint", pg_cfg.get("maintenance_db", "postgres")).strip(),
                "cli_path": ""
            },
            "mariadb": {
                "host": st.session_state.get("cfg_maria_host", maria_cfg.get("host", "10.21.0.143")).strip(),
                "port": int(st.session_state.get("cfg_maria_port", maria_cfg.get("port", 3307))),
                "user": st.session_state.get("cfg_maria_user", maria_cfg.get("user", "app_dev")).strip(),
                "password": st.session_state.get("cfg_maria_pass", maria_cfg.get("password", "")),
                "cli_path": ""
            },
            "ad": {
                "base_url": st.session_state.get("cfg_ad_base", ad_data.get("base_url", "https://ad.mjqeducation.edu.kh/api/v1")).strip(),
                "api_key": st.session_state.get("cfg_ad_key", ad_data.get("api_key", "")).strip(),
                "app_name": st.session_state.get("cfg_ad_app", ad_data.get("app_name", "https://epurchase.mjqeducation.edu.kh")).strip()
            }
        }
        with st.spinner("Persisting configuration settings..."):
            save_config(new_config)
            time.sleep(0.3)
        st.toast("Configuration saved successfully!", icon="💾")
        st.success("✅ Configuration settings saved to config.json!")

    # Categorized Dropdown Selector Header
    c_cat_drop, c_cat_save = st.columns([3.8, 1.4], vertical_alignment="center")
    with c_cat_drop:
        selected_cat = st.selectbox(
            "Configuration Function",
            cat_keys,
            format_func=lambda k: cat_map[k][0],
            index=cat_keys.index(current_cat),
            key="config_function_dropdown_widget"
        )
        st.session_state["config_category"] = selected_cat

    with c_cat_save:
        if st.button("💾 Save Configuration", key="cfg_top_save_btn", type="primary", use_container_width=True):
            do_save_all()

    st.markdown(f"<div style='font-size:0.78rem; color:#94a3b8; margin: 2px 0 14px 0;'>{cat_map[selected_cat][1]}</div>", unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # CATEGORY 1: REMOTE SSH SERVER
    # --------------------------------------------------------------------------
    if selected_cat in ["Remote SSH", "Overview"]:
        with st.container():
            st.markdown("#### 🌐 Remote SSH Server Parameters")
            st.caption("Settings for accessing the remote backup server via SFTP / SSH")
            ssh_c1, ssh_c2, ssh_c3 = st.columns([2, 1, 2])
            with ssh_c1:
                ssh_host_in = st.text_input("SSH Host / IP", value=ssh_data.get("host", "10.21.0.143"), key="cfg_ssh_host")
            with ssh_c2:
                ssh_port_in = st.number_input("SSH Port", min_value=1, max_value=65535, value=int(ssh_data.get("port", 22)), key="cfg_ssh_port")
            with ssh_c3:
                ssh_user_in = st.text_input("SSH Username", value=ssh_data.get("user", "user"), key="cfg_ssh_user")

            ssh_auth_c1, ssh_auth_c2 = st.columns(2)
            with ssh_auth_c1:
                ssh_auth_type = st.radio("Authentication Method", ["Password", "Private Key File (.pem / .id_rsa)"], index=0 if ssh_data.get("auth_type", "password") == "password" else 1, horizontal=True, key="cfg_ssh_auth_type")
            with ssh_auth_c2:
                if ssh_auth_type == "Password":
                    ssh_pass_in = st.text_input("SSH Password", value=ssh_data.get("password", ""), type="password", key="cfg_ssh_pass")
                    ssh_key_in = ""
                else:
                    ssh_key_in = st.text_input("Private Key Path on PC", value=ssh_data.get("key_path", ""), placeholder=r"e.g. C:\Users\user\.ssh\id_rsa", key="cfg_ssh_key")
                    ssh_pass_in = ""

            ssh_dir_in = st.text_input(
                "Remote Backup Directory (on Server)",
                value=ssh_data.get("remote_backup_dir", "/App2/source_backup"),
                help="Directory on the remote Linux server where dump files are stored",
                key="cfg_ssh_dir"
            )

            col_ssh_test, col_ssh_save = st.columns([1, 1])
            with col_ssh_test:
                if st.button("🔌 Test SSH Connection", key="cfg_test_ssh_btn", use_container_width=True):
                    test_temp = {
                        "host": ssh_host_in,
                        "port": ssh_port_in,
                        "user": ssh_user_in,
                        "auth_type": "password" if ssh_auth_type == "Password" else "key",
                        "password": ssh_pass_in,
                        "key_path": ssh_key_in
                    }
                    with st.spinner("Connecting to remote SSH server & testing SFTP..."):
                        ok, msg = test_ssh_connection(test_temp)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
            with col_ssh_save:
                if selected_cat == "Remote SSH":
                    if st.button("💾 Save SSH Configuration", key="cfg_save_ssh_only", type="primary", use_container_width=True):
                        do_save_all()

        st.divider()

    # --------------------------------------------------------------------------
    # CATEGORY 2: DATABASE ENGINES (POSTGRESQL & MARIADB)
    # --------------------------------------------------------------------------
    if selected_cat in ["Databases", "Overview"]:
        with st.container():
            st.markdown("#### 🗄️ Database Engines (PostgreSQL & MariaDB)")
            st.caption("Target database server instances for restoration operations")
            cfg_col1, cfg_col2 = st.columns(2, gap="medium")
            with cfg_col1:
                st.markdown("**🐘 PostgreSQL Target**")
                pg_host = st.text_input("Host", value=pg_cfg.get("host", "10.21.0.143"), key="cfg_pg_host")
                pg_port = st.number_input("Port", min_value=1, max_value=65535, value=int(pg_cfg.get("port", 5432)), key="cfg_pg_port")
                pg_user = st.text_input("Username", value=pg_cfg.get("user", "app_dev"), key="cfg_pg_user")
                pg_pass = st.text_input("Password", value=pg_cfg.get("password", ""), type="password", key="cfg_pg_pass")
                pg_maint = st.text_input("Maintenance DB", value=pg_cfg.get("maintenance_db", "postgres"), key="cfg_pg_maint")
                if st.button("🔌 Test PostgreSQL Connection", key="cfg_test_pg_btn", use_container_width=True):
                    with st.spinner("Pinging PostgreSQL host..."):
                        t_ok, t_msg = test_postgresql_connection({"host": pg_host, "port": pg_port, "user": pg_user, "password": pg_pass, "maintenance_db": pg_maint})
                    if t_ok:
                        st.success(t_msg)
                    else:
                        st.error(t_msg)

            with cfg_col2:
                st.markdown("**🦭 MariaDB / MySQL Target**")
                maria_host = st.text_input("Host", value=maria_cfg.get("host", "10.21.0.143"), key="cfg_maria_host")
                maria_port = st.number_input("Port", min_value=1, max_value=65535, value=int(maria_cfg.get("port", 3307)), key="cfg_maria_port")
                maria_user = st.text_input("Username", value=maria_cfg.get("user", "app_dev"), key="cfg_maria_user")
                maria_pass = st.text_input("Password", value=maria_cfg.get("password", ""), type="password", key="cfg_maria_pass")
                if st.button("🔌 Test MariaDB Connection", key="cfg_test_maria_btn", use_container_width=True):
                    with st.spinner("Pinging MariaDB host..."):
                        t_ok, t_msg = test_mariadb_connection({"host": maria_host, "port": maria_port, "user": maria_user, "password": maria_pass})
                    if t_ok:
                        st.success(t_msg)
                    else:
                        st.error(t_msg)

            if selected_cat == "Databases":
                st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
                if st.button("💾 Save Database Configuration", key="cfg_save_db_only", type="primary", use_container_width=True):
                    do_save_all()

        st.divider()

    # --------------------------------------------------------------------------
    # CATEGORY 3: ACTIVE DIRECTORY GATEWAY
    # --------------------------------------------------------------------------
    if selected_cat in ["Active Directory", "Overview"]:
        with st.container():
            st.markdown("#### 🏢 Active Directory (AD) Service Settings")
            st.caption("MJQE Employee Single Sign-On and Active Directory Gateway credentials")
            ad_c1, ad_c2, ad_c3 = st.columns([2.5, 2.5, 3])
            with ad_c1:
                ad_base_url_in = st.text_input("AD Base URL", value=ad_data.get("base_url", "https://ad.mjqeducation.edu.kh/api/v1"), key="cfg_ad_base")
            with ad_c2:
                ad_app_name_in = st.text_input("AD App Name", value=ad_data.get("app_name", "https://epurchase.mjqeducation.edu.kh"), key="cfg_ad_app")
            with ad_c3:
                ad_api_key_in = st.text_input("AD API Key", value=ad_data.get("api_key", ""), type="password", key="cfg_ad_key")

            col_ad_test, col_ad_save = st.columns([1, 1])
            with col_ad_test:
                if st.button("🔌 Test AD Service Reachability", key="cfg_test_ad_btn", use_container_width=True):
                    with st.spinner(f"Testing reachability to Active Directory at {ad_base_url_in}..."):
                        import requests
                        try:
                            r = requests.post(
                                f"{ad_base_url_in.strip().rstrip('/')}/auth/loginUsername",
                                headers={
                                    "Content-Type": "application/json",
                                    "X-API-KEY": ad_api_key_in.strip(),
                                    "x-app-name": ad_app_name_in.strip()
                                },
                                json={"username": "ping", "password": "ping"},
                                timeout=6
                            )
                            if r.status_code in [200, 400, 401, 404]:
                                st.success(f"✅ Active Directory service reachable! (HTTP {r.status_code})")
                            else:
                                st.warning(f"⚠️ AD Service responded with HTTP {r.status_code}: {r.text}")
                        except Exception as e:
                            st.error(f"❌ Failed to reach AD service: {str(e)}")
            with col_ad_save:
                if selected_cat == "Active Directory":
                    if st.button("💾 Save Active Directory Configuration", key="cfg_save_ad_only", type="primary", use_container_width=True):
                        do_save_all()

        st.divider()

    # --------------------------------------------------------------------------
    # CATEGORY 4: SERVER STORAGE & LOCAL BACKUPS
    # --------------------------------------------------------------------------
    if selected_cat in ["Storage", "Overview"]:
        with st.container():
            st.markdown("#### 📁 Server Storage & Backup Files")
            st.caption("Local filesystem directory paths for direct socket mode and file scans")
            c_st1, c_st2 = st.columns([3.5, 1.5])
            with c_st1:
                st_backup_dir = st.text_input(
                    "Local Backup Directory Path",
                    value=storage_data.get("backup_dir", r"D:\Narak\Sql file"),
                    key="cfg_storage_dir",
                    help="Directory on this machine where .sql / .dump files are stored"
                )
            with c_st2:
                st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
                st_subdirs = st.checkbox(
                    "Scan Subdirectories",
                    value=storage_data.get("scan_subdirectories", False),
                    key="cfg_storage_subdirs"
                )

            col_st_test, col_st_save = st.columns([1, 1])
            with col_st_test:
                if st.button("📂 Verify Local Folder Access", key="cfg_test_storage_btn", use_container_width=True):
                    with st.spinner("Inspecting storage path..."):
                        if os.path.exists(st_backup_dir) and os.path.isdir(st_backup_dir):
                            valid_exts = {".sql", ".dump", ".tar", ".backup", ".gz"}
                            found_count = 0
                            try:
                                for root, _, files in os.walk(st_backup_dir) if st_subdirs else [(st_backup_dir, [], os.listdir(st_backup_dir))]:
                                    for f in files:
                                        if any(f.lower().endswith(ext) for ext in valid_exts):
                                            found_count += 1
                                st.success(f"✅ Folder accessible! Found {found_count} database backup file(s).")
                            except Exception as ex:
                                st.warning(f"Folder exists but scan had warning: {str(ex)}")
                        else:
                            st.error(f"❌ Folder not found or inaccessible: {st_backup_dir}")
            with col_st_save:
                if selected_cat == "Storage":
                    if st.button("💾 Save Storage Configuration", key="cfg_save_storage_only", type="primary", use_container_width=True):
                        do_save_all()


# ==============================================================================
# MODAL DIALOG INVOCATION HANDLERS
# ==============================================================================
if st.session_state.get("show_file_dialog"):
    file_browser_dialog(is_ssh_mode, config, ssh_cfg)

if st.session_state.get("show_proof_dialog"):
    pinfo = st.session_state.get("proof_dialog_info", {})
    if pinfo and pinfo.get("name"):
        database_proof_dialog(
            pinfo["type"],
            pinfo["cfg"],
            pinfo["name"],
            pinfo.get("file_path", ""),
            pinfo.get("file_size_mb", 0.0)
        )
