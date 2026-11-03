import json
import os
from typing import Dict, Any, Tuple
import pymysql
import psycopg

CONFIG_FILE = "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "connection_mode": "ssh",  # "ssh" (Remote Server) or "direct" (Direct / Local)
    "ssh": {
        "host": "10.21.0.143",
        "port": 22,
        "user": "root",
        "auth_type": "password",  # "password" or "key"
        "password": "",
        "key_path": "",
        "remote_backup_dir": "/var/backups"
    },
    "server_storage": {
        "backup_dir": r"D:\Narak\Sql file",
        "scan_subdirectories": False
    },
    "mariadb": {
        "host": "127.0.0.1",
        "port": 3307,
        "user": "app_dev",
        "password": "",
        "cli_path": ""  # optional path to mariadb.exe or mysql.exe
    },
    "postgresql": {
        "host": "127.0.0.1",
        "port": 5432,
        "user": "app_dev",
        "password": "",
        "maintenance_db": "postgres",
        "cli_path": ""  # optional path to psql.exe / pg_restore.exe
    },
    "ad": {
        "base_url": os.environ.get("AD_BASE_URL", "https://ad.mjqeducation.edu.kh/api/v1"),
        "api_key": os.environ.get("AD_API_KEY", ""),
        "app_name": os.environ.get("AD_APP_NAME", "https://epurchase.mjqeducation.edu.kh")
    }
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure sections exist
            for section in DEFAULT_CONFIG.keys():
                if section not in data:
                    data[section] = DEFAULT_CONFIG[section]
                elif isinstance(DEFAULT_CONFIG[section], dict) and isinstance(data[section], dict):
                    for k, v in DEFAULT_CONFIG[section].items():
                        if k not in data[section]:
                            data[section][k] = v
            return data
    except Exception:
        return DEFAULT_CONFIG.copy()


def list_server_backup_files(backup_dir: str, scan_subdirs: bool = False) -> list:
    """Scans the server directory for database backup / dump files."""
    if not backup_dir or not os.path.exists(backup_dir) or not os.path.isdir(backup_dir):
        return []

    valid_exts = {".sql", ".dump", ".tar", ".backup", ".gz"}
    results = []

    def process_entry(entry_path: str):
        basename = os.path.basename(entry_path)
        ext = os.path.splitext(basename)[1].lower()
        # Handle compound extensions like .sql.gz
        is_valid = ext in valid_exts or basename.lower().endswith(".sql.gz")
        if is_valid:
            try:
                stat = os.stat(entry_path)
                size_mb = stat.st_size / (1024 * 1024)
                mtime_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                results.append({
                    "name": basename,
                    "path": os.path.abspath(entry_path),
                    "size_mb": round(size_mb, 2),
                    "raw_size": stat.st_size,
                    "modified": mtime_str
                })
            except Exception:
                pass

    from datetime import datetime

    if scan_subdirs:
        for root, _, files in os.walk(backup_dir):
            for file in files:
                process_entry(os.path.join(root, file))
    else:
        for item in os.listdir(backup_dir):
            full_path = os.path.join(backup_dir, item)
            if os.path.isfile(full_path):
                process_entry(full_path)

    # Sort newest modified first
    results.sort(key=lambda x: x["modified"], reverse=True)
    return results

def save_config(config: Dict[str, Any]) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def test_mariadb_connection(config: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        conn = pymysql.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 3306)),
            user=config.get("user", "root"),
            password=config.get("password", ""),
            connect_timeout=5
        )
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION();")
            ver = cur.fetchone()[0]
        conn.close()
        return True, f"Connected to MariaDB/MySQL successfully (Version: {ver})"
    except Exception as e:
        return False, str(e)

def test_postgresql_connection(config: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        conn_str = (
            f"host={config.get('host', 'localhost')} "
            f"port={config.get('port', 5432)} "
            f"user={config.get('user', 'postgres')} "
            f"password={config.get('password', '')} "
            f"dbname={config.get('maintenance_db', 'postgres')} "
            f"connect_timeout=5"
        )
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                ver = cur.fetchone()[0]
        return True, f"Connected to PostgreSQL successfully ({ver.split(',')[0]})"
    except Exception as e:
        return False, str(e)

def list_mariadb_databases(config: Dict[str, Any]) -> list:
    try:
        conn = pymysql.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 3306)),
            user=config.get("user", "root"),
            password=config.get("password", ""),
            connect_timeout=5
        )
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES;")
            dbs = [row[0] for row in cur.fetchall()]
        conn.close()
        # Exclude system databases
        system_dbs = {"information_schema", "mysql", "performance_schema", "sys"}
        return [db for db in dbs if db.lower() not in system_dbs]
    except Exception:
        return []

def list_postgresql_databases(config: Dict[str, Any]) -> list:
    try:
        conn_str = (
            f"host={config.get('host', 'localhost')} "
            f"port={config.get('port', 5432)} "
            f"user={config.get('user', 'postgres')} "
            f"password={config.get('password', '')} "
            f"dbname={config.get('maintenance_db', 'postgres')} "
            f"connect_timeout=5"
        )
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false;")
                dbs = [row[0] for row in cur.fetchall()]
        return dbs
    except Exception:
        return []
