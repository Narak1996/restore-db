import os
import stat
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import paramiko
from ssh_manager import get_ssh_client

VALID_EXTENSIONS = {".sql", ".dump", ".tar", ".backup", ".gz"}

def get_file_type_description(name: str, is_dir: bool) -> str:
    if is_dir:
        return "Folder"
    lower = name.lower()
    if lower.endswith(".sql"):
        return "SQL Script"
    if lower.endswith(".dump"):
        return "PostgreSQL Custom Dump"
    if lower.endswith(".tar"):
        return "TAR Archive"
    if lower.endswith(".sql.gz"):
        return "GZIP Compressed SQL"
    if lower.endswith(".backup"):
        return "Database Backup"
    return "File"


def get_file_icon(name: str, is_dir: bool) -> str:
    if is_dir:
        return "📁"
    lower = name.lower()
    if lower.endswith(".sql"):
        return "📜"
    if lower.endswith((".dump", ".backup")):
        return "🗄️"
    if lower.endswith((".tar", ".gz")):
        return "🗜️"
    return "📄"


def pick_windows_file_dialog() -> Optional[str]:
    """Opens the native Windows File Explorer dialog to pick a file."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        selected = filedialog.askopenfilename(
            title="Select Database Backup / Dump File",
            filetypes=[
                ("Database Backups (*.sql, *.dump, *.tar, *.gz)", "*.sql;*.dump;*.tar;*.backup;*.gz;*.sql.gz"),
                ("SQL Files (*.sql)", "*.sql"),
                ("PostgreSQL Dumps (*.dump)", "*.dump"),
                ("All Files (*.*)", "*.*")
            ]
        )
        root.destroy()
        return selected if selected else None
    except Exception:
        return None

def get_windows_drives() -> List[str]:
    """Returns available drive letters on Windows."""
    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        d_path = f"{letter}:\\"
        if os.path.exists(d_path):
            drives.append(d_path)
    return drives


def browse_local_directory(current_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Navigates a local Windows directory.
    Returns current_path, parent_path, folders, and valid dump files.
    """
    if not current_path or not os.path.exists(current_path):
        current_path = r"D:\Narak\Sql file"
        if not os.path.exists(current_path):
            current_path = os.getcwd()

    current_path = os.path.abspath(current_path)
    parent_path = os.path.dirname(current_path)
    if parent_path == current_path:
        parent_path = None  # At drive root (e.g. D:\)

    items = []
    try:
        with os.scandir(current_path) as entries:
            for entry in entries:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    name = entry.name
                    # Skip hidden system items
                    if name.startswith("$") or name in ("System Volume Information", "$RECYCLE.BIN"):
                        continue

                    ext = os.path.splitext(name.lower())[1]
                    is_dump = ext in VALID_EXTENSIONS or name.lower().endswith(".sql.gz")

                    if is_dir or is_dump:
                        stat_info = entry.stat(follow_symlinks=False)
                        size_mb = 0.0 if is_dir else (stat_info.st_size / (1024 * 1024))
                        mtime = datetime.fromtimestamp(stat_info.st_mtime).strftime("%Y-%m-%d %H:%M")
                        items.append({
                            "name": name,
                            "path": entry.path,
                            "is_dir": is_dir,
                            "size_mb": round(size_mb, 2),
                            "raw_size": stat_info.st_size if not is_dir else 0,
                            "type": get_file_type_description(name, is_dir),
                            "icon": get_file_icon(name, is_dir),
                            "modified": mtime
                        })
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass

    # Sort: Folders first (alphabetical), then Files (alphabetical)
    items.sort(key=lambda x: (0 if x["is_dir"] else 1, x["name"].lower()))

    return {
        "current_path": current_path,
        "parent_path": parent_path,
        "items": items,
        "drives": get_windows_drives()
    }


# ==============================================================================
# REMOTE SSH / SFTP DIRECTORY EXPLORER
# ==============================================================================
def browse_remote_directory(ssh_cfg: Dict[str, Any], remote_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Navigates a remote Linux/Unix directory over SFTP.
    Returns current_path, parent_path, and list of folders and dump files.
    """
    target_path = remote_path or ssh_cfg.get("remote_backup_dir", "/var/backups")
    if not target_path or not target_path.strip():
        target_path = "/var/backups"

    # Normalize remote POSIX path
    target_path = "/" + target_path.strip().lstrip("/")
    if target_path != "/" and target_path.endswith("/"):
        target_path = target_path.rstrip("/")

    # Parent path
    if target_path == "/":
        parent_path = None
    else:
        parent_path = os.path.dirname(target_path)
        if not parent_path:
            parent_path = "/"

    items = []
    error_msg = None
    client = None

    try:
        client = get_ssh_client(ssh_cfg)
        sftp = client.open_sftp()
        try:
            entries = sftp.listdir_attr(target_path)
            for entry in entries:
                name = entry.filename
                if name in (".", ".."):
                    continue
                is_dir = stat.S_ISDIR(entry.st_mode)
                ext = os.path.splitext(name.lower())[1]
                is_dump = ext in VALID_EXTENSIONS or name.lower().endswith(".sql.gz")

                if is_dir or is_dump:
                    size_mb = 0.0 if is_dir else (entry.st_size / (1024 * 1024))
                    mtime = datetime.fromtimestamp(entry.st_mtime).strftime("%Y-%m-%d %H:%M")
                    full_remote_path = f"{target_path.rstrip('/')}/{name}"
                    items.append({
                        "name": name,
                        "path": full_remote_path,
                        "is_dir": is_dir,
                        "size_mb": round(size_mb, 2),
                        "raw_size": entry.st_size if not is_dir else 0,
                        "type": get_file_type_description(name, is_dir),
                        "icon": get_file_icon(name, is_dir),
                        "modified": mtime
                    })
        except IOError as e:
            error_msg = f"Cannot access remote folder '{target_path}': {str(e)}"
        finally:
            sftp.close()
    except Exception as e:
        error_msg = f"SSH/SFTP connection failed: {str(e)}"
    finally:
        if client:
            client.close()

    # Sort: Folders first, then Files
    items.sort(key=lambda x: (0 if x["is_dir"] else 1, x["name"].lower()))

    return {
        "current_path": target_path,
        "parent_path": parent_path,
        "items": items,
        "error": error_msg
    }
