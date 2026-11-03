# 🗄️ DB Restore Studio (SSH & Local)

A modern browser-based UI tool built with **Streamlit** to manage, create, and restore **PostgreSQL** and **MariaDB / MySQL** databases from dump files (`.sql`, `.dump`, `.tar`, `.sql.gz`) with **full SSH remote server execution**, SFTP file browsing, and persistent restore history.

---

## 🚀 Features

- **🌐 Remote Server via SSH Mode:**
  - Connect to remote Linux/Unix servers over SSH (using password or `.pem` / `.id_rsa` private key).
  - Browse and select dump files directly from remote directories (e.g. `/var/backups`) via SFTP.
  - Execute `psql`, `pg_restore`, `mariadb`, and `mysql` **directly on the remote server over SSH**, eliminating massive file transfers across the network.
  - Live console streaming of remote execution output directly to your browser.
  - Upload dump files from your client PC directly to the remote server via SFTP.
- **🔍 Database Restore Proof & Live Data Inspector:**
  - **Automated Verification:** Automatically verifies and proves database restoration upon completion.
  - **Summary Proof Metrics:** Displays total tables created, total records across all tables, and total database size in MB.
  - **Restored Tables Table:** Lists every table created along with its exact row count and size.
  - **Live Table Records Previewer:** Select any table from a dropdown to inspect the latest records and column values in real-time.
  - **On-Demand Inspection:** Click `🔍 Inspect Tables & Data` at any time to verify any existing database or any past restore job from the History log!
- **🗂️ Interactive File Explorer (Browse Like Windows):**
  - **In-App Explorer View:** Navigate directories, click `📂 Open` to enter subfolders, `⬆️ Up` to go to parent directories, and `💾 Drive shortcuts` (`C:\`, `D:\`).
  - **Native Windows Explorer Dialog:** Click `🖥️ Open Windows File Explorer` in local mode to pop the real Windows file picker dialog directly on your screen.
  - **Remote SSH / SFTP Explorer:** Navigate remote Linux directories over SFTP, open remote folders, and pick dump files with one click.
  - **Live Search & Filter:** Filter files and folders by name within the current directory.
  - **Detailed File Info:** Displays file size, type (`SQL Script`, `PG Custom Dump`, etc.), and modification date before restoring.
- **💻 Direct / Local Mode:**
  - Connect to local or direct network databases.
  - Select files via File Explorer, native OS dialog, or upload via browser.
- **Multi-Database Support:** Full support for both **PostgreSQL** and **MariaDB / MySQL**.
- **Auto DB Creation:** Automatically create target databases (or drop & cleanly recreate) before restoring.
- **Streaming Restoration Engine:** Executes chunked statement batches with live progress updates, preventing RAM overflow or packet limit errors.
- **Restore History Log:** Persists every restore attempt into an embedded SQLite database (`history.db`) with timestamps, connection mode (SSH vs Local), target DB, file details, execution duration, and full console logs.
- **Saved Configurations:** Save SSH credentials, database connections, and backup directories into `config.json` with one-click connection tests.

---

## 🏃 Quick Start

### 1. Launch the App
Simply double-click:
```bat
run.bat
```
Or run via terminal:
```powershell
.\venv\Scripts\streamlit run app.py
```
The application will automatically open in your browser at:
`http://localhost:8501`

---

## ⚙️ Configuration

Open the **⚙️ Database Configuration** tab in the app to configure your database servers:

| Database | Default Port | Default User | Default Maintenance DB |
| :--- | :--- | :--- | :--- |
| **PostgreSQL** | `5432` | `postgres` | `postgres` |
| **MariaDB / MySQL** | `3306` | `root` | *N/A* |

Click **Test PG Connection** or **Test MariaDB Connection** to verify your credentials, then click **Save All Configurations**.

---

## 📁 Project Structure

```
d:\Narak\restore-database\
├── app.py                 # Streamlit UI & interactive dashboard
├── config_manager.py      # Configuration persistence & connection testing
├── history_manager.py     # SQLite history logger & queries
├── restore_engine.py      # Core restore & database creation orchestration
├── config.json            # Saved connection settings (auto-created)
├── history.db             # Local SQLite database storing restore logs
├── requirements.txt       # Python dependencies
├── run.bat                # 1-click Windows launcher
└── uploads/               # Temporary storage for uploaded dump files
```
