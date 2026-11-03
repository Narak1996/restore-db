import os
import re
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from verify_restore import execute_custom_query
from ssh_manager import get_ssh_client

def get_available_connection_options(config: Dict[str, Any]) -> List[str]:
    """Returns a list of formatted connection names available for validation."""
    options = []
    # 1. Configured validation connections
    for vc in config.get("validation_connections", []):
        name = vc.get("name", "").strip()
        host = vc.get("host", "").strip()
        if name:
            options.append(name)
        elif host:
            options.append(f"Host {host}")

    # 2. Main PostgreSQL
    pg = config.get("postgresql", {})
    pg_host = pg.get("host", "10.21.0.143")
    pg_opt = f"Pgsql {pg_host}"
    if pg_opt not in options:
        options.append(pg_opt)

    # 3. Main MariaDB
    maria = config.get("mariadb", {})
    maria_host = maria.get("host", "10.21.0.143")
    maria_opt = f"MariaDB {maria_host}"
    if maria_opt not in options:
        options.append(maria_opt)

    # 4. SSH Host option
    ssh = config.get("ssh", {})
    ssh_host = ssh.get("host", "10.21.0.143")
    ssh_opt = f"SSH {ssh_host}"
    if ssh_opt not in options:
        options.append(ssh_opt)

    # Add default Pgsql 172.168.0.130 if not present
    default_sample = "Pgsql 172.168.0.130"
    if default_sample not in options:
        options.append(default_sample)

    return options


def resolve_connection_params(conn_name: str, config: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Resolves connection name to (db_type, db_cfg, ssh_cfg_if_any).
    """
    clean_name = conn_name.strip()
    clean_lower = clean_name.lower()

    # Check if matches any validation connection in config
    for vc in config.get("validation_connections", []):
        if vc.get("name", "").strip() == clean_name or vc.get("host", "").strip() == clean_name:
            v_host = vc.get("host", "10.21.0.143")
            v_port = int(vc.get("port", 5432 if "maria" not in clean_lower else 3307))
            v_user = vc.get("user", "app_dev" if "root" not in vc.get("user", "") else "app_dev")
            v_pass = vc.get("password", "")
            db_type = "MariaDB" if any(k in clean_lower for k in ["maria", "mysql"]) else "PostgreSQL"
            db_cfg = {
                "host": v_host,
                "port": v_port,
                "user": v_user,
                "password": v_pass,
                "maintenance_db": "postgres"
            }
            ssh_cfg = {
                "host": v_host,
                "port": 22,
                "user": vc.get("user", "user"),
                "auth_type": vc.get("auth_type", "password"),
                "password": v_pass,
                "key_path": vc.get("key_path", "")
            }
            return db_type, db_cfg, ssh_cfg

    # Check IP in name
    ip_match = re.search(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', clean_name)
    host_ip = ip_match.group(0) if ip_match else "10.21.0.143"

    if any(k in clean_lower for k in ["maria", "mysql"]):
        db_type = "MariaDB"
        base_cfg = dict(config.get("mariadb", {}))
        base_cfg["host"] = host_ip
    else:
        db_type = "PostgreSQL"
        base_cfg = dict(config.get("postgresql", {}))
        base_cfg["host"] = host_ip

    ssh_cfg = dict(config.get("ssh", {}))
    if ip_match:
        ssh_cfg["host"] = host_ip

    return db_type, base_cfg, ssh_cfg


def execute_validation_task(task: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a validation SQL query against the resolved database connection.
    Attempts direct execution first, falls back to SSH execution if direct fails.
    """
    conn_name = task.get("connection_name", "").strip()
    db_name = task.get("db", "").strip()
    sql_command = task.get("command", "").strip()

    # Extract target table name from SQL
    table_match = re.search(r'\bfrom\s+([^\s;,()]+)', sql_command, re.IGNORECASE)
    table_name = table_match.group(1).replace('`', '').replace('"', '').strip().split('.')[-1] if table_match else "table"

    db_type, db_cfg, ssh_cfg = resolve_connection_params(conn_name, config)
    target_ip = db_cfg.get("host", "127.0.0.1")

    # 1. Attempt direct socket / TCP connection
    ok, df, err_msg, duration = execute_custom_query(db_type, db_cfg, db_name, sql_command)
    if ok and df is not None:
        return {
            "ok": True,
            "df": df,
            "error": None,
            "latency": duration,
            "ip": target_ip,
            "db": db_name,
            "table": table_name,
            "task": task
        }

    # 2. If direct fails and SSH is configured, attempt execution over SSH command
    if ssh_cfg and ssh_cfg.get("host"):
        try:
            t0 = time.time()
            client = get_ssh_client(ssh_cfg)
            escaped_sql = sql_command.replace('"', '\\"').replace("'", "'\\''")
            
            if db_type == "PostgreSQL":
                pg_user = db_cfg.get("user", "app_dev")
                pg_pass = db_cfg.get("password", "")
                pg_port = db_cfg.get("port", 5432)
                cmd = (
                    f"PGPASSWORD='{pg_pass}' psql -h '127.0.0.1' -p {pg_port} -U '{pg_user}' -d '{db_name}' "
                    f"-c \"{escaped_sql}\" -F ',' --no-align"
                )
            else:
                m_user = db_cfg.get("user", "app_dev")
                m_pass = db_cfg.get("password", "")
                m_port = db_cfg.get("port", 3307)
                cmd = (
                    f"MYSQL_PWD='{m_pass}' mysql -h '127.0.0.1' -P {m_port} -u '{m_user}' -D '{db_name}' "
                    f"--batch --raw -e \"{escaped_sql}\""
                )

            stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
            out_bytes = stdout.read().decode("utf-8", errors="replace").strip()
            err_bytes = stderr.read().decode("utf-8", errors="replace").strip()
            elapsed = time.time() - t0
            client.close()

            if out_bytes:
                # Parse tabular/CSV output
                import io
                sep = ',' if db_type == "PostgreSQL" else '\t'
                try:
                    ssh_df = pd.read_csv(io.StringIO(out_bytes), sep=sep)
                    return {
                        "ok": True,
                        "df": ssh_df,
                        "error": None,
                        "latency": elapsed,
                        "ip": target_ip,
                        "db": db_name,
                        "table": table_name,
                        "task": task
                    }
                except Exception:
                    pass
        except Exception:
            pass

    return {
        "ok": False,
        "df": None,
        "error": err_msg or "Connection timed out or failed to execute query.",
        "latency": duration,
        "ip": target_ip,
        "db": db_name,
        "table": table_name,
        "task": task
    }


def save_table_screenshot(
    df: pd.DataFrame,
    ip: str,
    db_name: str,
    table_name: str,
    execution_num: int = 1
) -> Tuple[str, str]:
    """
    Renders the query result DataFrame into a professional high-resolution PNG image
    and saves to Desktop/validate-restore/{db}_{table}_{datetime}.png.
    Returns (absolute_file_path, filename).
    """
    desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop", "validate-restore")
    os.makedirs(desktop_dir, exist_ok=True)

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_db = re.sub(r'[^a-zA-Z0-9_-]', '', db_name) or "db"
    clean_tbl = re.sub(r'[^a-zA-Z0-9_-]', '', table_name) or "table"
    filename = f"{clean_db}_{clean_tbl}_{timestamp_str}.png"
    target_path = os.path.join(desktop_dir, filename)

    # Setup fonts
    try:
        font = ImageFont.truetype("arial.ttf", 13)
        bold_font = ImageFont.truetype("arialbd.ttf", 13)
        title_font = ImageFont.truetype("arialbd.ttf", 15)
    except Exception:
        font = ImageFont.load_default()
        bold_font = font
        title_font = font

    col_names = list(df.columns)
    # Limit rows to 100 for screenshot clarity
    display_rows = df.head(100).values.tolist()

    # Calculate column widths
    col_widths = []
    for c_idx, col in enumerate(col_names):
        try:
            bbox = font.getbbox(str(col))
            max_w = bbox[2] - bbox[0]
        except Exception:
            max_w = len(str(col)) * 8

        for r in display_rows:
            val_str = str(r[c_idx]) if r[c_idx] is not None else "NULL"
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            try:
                bbox_v = font.getbbox(val_str)
                w = bbox_v[2] - bbox_v[0]
            except Exception:
                w = len(val_str) * 8
            if w > max_w:
                max_w = w
        col_widths.append(max(max_w + 24, 75))

    total_w = sum(col_widths) + 40
    total_w = max(total_w, 750)
    row_h = 28
    header_h = 32
    banner_h = 56
    footer_h = 34
    total_h = banner_h + header_h + (len(display_rows) * row_h) + footer_h + 30

    img = Image.new("RGB", (total_w, total_h), color="#0b0f19")
    draw = ImageDraw.Draw(img)

    # 1. Header Banner
    draw.rectangle([(15, 12), (total_w - 15, banner_h)], fill="#131824", outline="#1f293d", width=1)
    banner_text = f"Execution {execution_num} Result   |   IP: {ip}   |   Database: {db_name}   |   Table: {table_name}"
    draw.text((28, 22), banner_text, fill="#60a5fa", font=title_font)

    # 2. Table Column Headers
    x_start = 18
    y_start = banner_h + 12
    curr_x = x_start
    for c_idx, col in enumerate(col_names):
        w = col_widths[c_idx]
        draw.rectangle([(curr_x, y_start), (curr_x + w, y_start + header_h)], fill="#1e293b", outline="#334155", width=1)
        draw.text((curr_x + 10, y_start + 8), str(col), fill="#f8fafc", font=bold_font)
        curr_x += w

    # 3. Data Rows
    curr_y = y_start + header_h
    for r_idx, r in enumerate(display_rows):
        curr_x = x_start
        bg = "#0f172a" if r_idx % 2 == 0 else "#162032"
        for c_idx, val in enumerate(r):
            w = col_widths[c_idx]
            val_str = str(val) if val is not None else "NULL"
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            draw.rectangle([(curr_x, curr_y), (curr_x + w, curr_y + row_h)], fill=bg, outline="#1e293b", width=1)
            draw.text((curr_x + 10, curr_y + 6), val_str, fill="#cbd5e1", font=font)
            curr_x += w
        curr_y += row_h

    # 4. Footer
    footer_text = f"Validated by DB Restore Studio  •  Total Rows: {len(df)}  •  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    draw.text((22, curr_y + 12), footer_text, fill="#64748b", font=font)

    img.save(target_path, format="PNG")
    return target_path, filename
