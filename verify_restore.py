import os
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import pymysql
import psycopg

def get_database_proof(db_type: str, db_cfg: Dict[str, Any], db_name: str) -> Dict[str, Any]:
    """
    Inspects the restored database to prove and verify data restoration:
    - Total table count
    - Table names with row counts & sizes
    - Total database size
    """
    clean_db = db_name.replace("`", "").replace('"', "").strip()
    result = {
        "success": False,
        "db_name": clean_db,
        "total_tables": 0,
        "total_rows": 0,
        "size_mb": 0.0,
        "tables": [],
        "error": None
    }

    if db_type == "PostgreSQL":
        try:
            conn_str = (
                f"host={db_cfg.get('host', '127.0.0.1')} "
                f"port={db_cfg.get('port', 5432)} "
                f"user={db_cfg.get('user', 'postgres')} "
                f"password={db_cfg.get('password', '')} "
                f"dbname={clean_db} "
                f"connect_timeout=8"
            )
            with psycopg.connect(conn_str) as conn:
                with conn.cursor() as cur:
                    # 1. Total DB Size
                    cur.execute("SELECT pg_database_size(%s);", (clean_db,))
                    db_bytes = cur.fetchone()[0] or 0
                    result["size_mb"] = round(db_bytes / (1024 * 1024), 2)

                    # 2. List Tables & stats
                    cur.execute("""
                        SELECT 
                            c.relname AS table_name,
                            COALESCE(s.n_live_tup, c.reltuples::bigint, 0) AS approx_rows,
                            pg_total_relation_size(c.oid) AS total_bytes
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                        WHERE c.relkind = 'r' 
                          AND n.nspname = 'public'
                        ORDER BY c.relname;
                    """)
                    rows = cur.fetchall()

                    total_rows = 0
                    tables_list = []
                    for row in rows:
                        t_name, t_rows, t_bytes = row[0], int(row[1]), row[2] or 0
                        # If stat shows 0 or negative, do quick exact count for accuracy if small
                        if t_rows <= 0:
                            try:
                                cur.execute(f'SELECT count(*) FROM "{t_name}";')
                                t_rows = cur.fetchone()[0]
                            except Exception:
                                t_rows = 0

                        total_rows += t_rows
                        tables_list.append({
                            "name": t_name,
                            "rows": t_rows,
                            "size_mb": round(t_bytes / (1024 * 1024), 2)
                        })

                    result["total_tables"] = len(tables_list)
                    result["total_rows"] = total_rows
                    result["tables"] = tables_list
                    result["success"] = True
        except Exception as e:
            result["error"] = str(e)

    else: # MariaDB / MySQL
        try:
            conn = pymysql.connect(
                host=db_cfg.get("host", "127.0.0.1"),
                port=int(db_cfg.get("port", 3306)),
                user=db_cfg.get("user", "root"),
                password=db_cfg.get("password", ""),
                database=clean_db,
                connect_timeout=8
            )
            with conn.cursor() as cur:
                # 1. Query table names, rows, and sizes
                cur.execute("""
                    SELECT 
                        table_name,
                        COALESCE(table_rows, 0) AS table_rows,
                        COALESCE(data_length + index_length, 0) AS total_bytes
                    FROM information_schema.tables 
                    WHERE table_schema = %s 
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name;
                """, (clean_db,))
                rows = cur.fetchall()

                total_rows = 0
                total_bytes = 0
                tables_list = []
                for row in rows:
                    t_name, t_rows, t_bytes = row[0], int(row[1]), int(row[2])
                    # If InnoDB estimate is 0, do exact COUNT(*) for certainty
                    if t_rows == 0:
                        try:
                            cur.execute(f"SELECT COUNT(*) FROM `{t_name}`;")
                            t_rows = cur.fetchone()[0]
                        except Exception:
                            pass

                    total_rows += t_rows
                    total_bytes += t_bytes
                    tables_list.append({
                        "name": t_name,
                        "rows": t_rows,
                        "size_mb": round(t_bytes / (1024 * 1024), 2)
                    })

                result["total_tables"] = len(tables_list)
                result["total_rows"] = total_rows
                result["size_mb"] = round(total_bytes / (1024 * 1024), 2)
                result["tables"] = tables_list
                result["success"] = True
            conn.close()
        except Exception as e:
            result["error"] = str(e)

    return result


def fetch_table_sample_records(db_type: str, db_cfg: Dict[str, Any], db_name: str, table_name: str, limit: int = 10) -> Tuple[bool, Optional[pd.DataFrame], Optional[str]]:
    """
    Fetches the latest / sample records from a specific table to prove restoration.
    Returns (success, DataFrame, error_message).
    """
    clean_db = db_name.replace("`", "").replace('"', "").strip()
    clean_tbl = table_name.replace("`", "").replace('"', "").strip()

    if db_type == "PostgreSQL":
        try:
            conn_str = (
                f"host={db_cfg.get('host', '127.0.0.1')} "
                f"port={db_cfg.get('port', 5432)} "
                f"user={db_cfg.get('user', 'postgres')} "
                f"password={db_cfg.get('password', '')} "
                f"dbname={clean_db} "
                f"connect_timeout=8"
            )
            with psycopg.connect(conn_str) as conn:
                with conn.cursor() as cur:
                    cur.execute(f'SELECT * FROM "{clean_tbl}" LIMIT {limit};')
                    cols = [d.name for d in cur.description] if cur.description else []
                    rows = cur.fetchall()
                    df = pd.DataFrame(rows, columns=cols)
                    return True, df, None
        except Exception as e:
            return False, None, str(e)

    else: # MariaDB
        try:
            conn = pymysql.connect(
                host=db_cfg.get("host", "127.0.0.1"),
                port=int(db_cfg.get("port", 3306)),
                user=db_cfg.get("user", "root"),
                password=db_cfg.get("password", ""),
                database=clean_db,
                connect_timeout=8
            )
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM `{clean_tbl}` LIMIT {limit};")
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                df = pd.DataFrame(rows, columns=cols)
            conn.close()
            return True, df, None
        except Exception as e:
            return False, None, str(e)


def execute_custom_query(
    db_type: str,
    db_cfg: Dict[str, Any],
    db_name: str,
    sql_query: str
) -> Tuple[bool, Optional[pd.DataFrame], Optional[str], float]:
    """
    Executes a user-provided SQL query against the restored database.
    Returns (success, DataFrame, error_message, elapsed_seconds).
    """
    import time
    clean_db = db_name.replace("`", "").replace('"', "").strip()
    query = sql_query.strip()
    if not query:
        return False, None, "Query cannot be empty.", 0.0

    t0 = time.time()
    if db_type == "PostgreSQL":
        try:
            conn_str = (
                f"host={db_cfg.get('host', '127.0.0.1')} "
                f"port={db_cfg.get('port', 5432)} "
                f"user={db_cfg.get('user', 'postgres')} "
                f"password={db_cfg.get('password', '')} "
                f"dbname={clean_db} "
                f"connect_timeout=8"
            )
            with psycopg.connect(conn_str) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    elapsed = time.time() - t0
                    if cur.description:
                        cols = [d.name for d in cur.description]
                        rows = cur.fetchall()
                        df = pd.DataFrame(rows, columns=cols)
                    else:
                        df = pd.DataFrame([{"Status": f"Command executed successfully ({cur.rowcount} rows affected)"}])
                    return True, df, None, elapsed
        except Exception as e:
            return False, None, str(e), time.time() - t0

    else: # MariaDB
        try:
            conn = pymysql.connect(
                host=db_cfg.get("host", "127.0.0.1"),
                port=int(db_cfg.get("port", 3306)),
                user=db_cfg.get("user", "root"),
                password=db_cfg.get("password", ""),
                database=clean_db,
                connect_timeout=8
            )
            with conn.cursor() as cur:
                cur.execute(query)
                elapsed = time.time() - t0
                if cur.description:
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                    df = pd.DataFrame(rows, columns=cols)
                else:
                    df = pd.DataFrame([{"Status": f"Command executed successfully ({cur.rowcount} rows affected)"}])
            conn.close()
            return True, df, None, elapsed
        except Exception as e:
            return False, None, str(e), time.time() - t0
