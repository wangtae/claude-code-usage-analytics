#region Imports
import os
import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.models.usage_record import UsageRecord
#endregion


#region Constants and Path Detection

# Cache for device statistics (expires after 60 seconds)
_device_stats_cache: dict | None = None
_device_stats_cache_time: float = 0
_CACHE_EXPIRY_SECONDS = 60

# Multi-device records cache
# Structure: {device_name: {"records": [...], "last_timestamp": datetime}}
_device_records_cache: dict[str, dict] = {}
_merged_records_cache: list[UsageRecord] | None = None

def _try_create_folder(path: Path) -> bool:
    """
    Try to create folder. Return True if successful or already exists.

    Args:
        path: Path to folder to create

    Returns:
        True if folder exists or was created successfully, False otherwise
    """
    try:
        # Check if parent exists (e.g., OneDrive root)
        if path.parent.parent.exists():
            path.mkdir(parents=True, exist_ok=True)
            return True
        elif path.exists():
            return True
    except (PermissionError, OSError):
        pass
    return False


def get_storage_dir() -> Path:
    """
    Get the base storage directory for databases.

    Returns the directory without the filename, allowing us to construct
    multiple DB filenames (machines.db, usage_history_{machine}.db, etc.)

    Priority:
    1. Config file: user_config.get_db_path()
    2. Environment variable: CLAUDE_GOBLIN_DB_PATH
    3. OneDrive (WSL2): /mnt/c/Users/{username}/OneDrive/.claude-goblin/
    4. iCloud (macOS): ~/Library/Mobile Documents/com~apple~CloudDocs/.claude-goblin/
    5. Local fallback: ~/.claude/usage/

    Returns:
        Path to the storage directory (not including filename)
    """
    # Try to get configured path first
    try:
        from src.config.user_config import get_db_path
        config_path = get_db_path()
        if config_path:
            return Path(config_path).parent
    except ImportError:
        pass

    # Environment variable override
    custom_path = os.getenv("CLAUDE_GOBLIN_DB_PATH")
    if custom_path:
        return Path(custom_path).parent

    # WSL2 OneDrive detection
    if platform.system() == "Linux" and "microsoft" in platform.release().lower():
        username = os.getenv("USER")
        onedrive_candidates = []

        # Check external drives first (OneDrive is often on D:/E:/F: in WSL)
        for drive in ["d", "e", "f"]:
            onedrive_candidates.append(Path(f"/mnt/{drive}/OneDrive"))

        # Check C: drive OneDrive folders last
        onedrive_candidates.append(Path("/mnt/c/OneDrive"))
        if username:
            onedrive_candidates.append(Path(f"/mnt/c/Users/{username}/OneDrive"))

        for onedrive_base in onedrive_candidates:
            if onedrive_base.exists():
                storage_dir = onedrive_base / ".claude-goblin"
                if _try_create_folder(storage_dir):
                    return storage_dir

    # macOS iCloud Drive detection
    elif platform.system() == "Darwin":
        icloud_base = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if icloud_base.exists():
            storage_dir = icloud_base / ".claude-goblin"
            if _try_create_folder(storage_dir):
                return storage_dir

    # Local fallback
    return Path.home() / ".claude" / "usage"


def get_current_machine_db_path() -> Path:
    """
    Get the database path for the current machine.

    Returns usage_history_{machine_name}.db in the storage directory.
    Machine registration is deferred to avoid blocking startup.

    Returns:
        Path to the current machine's database file
    """
    from src.config.user_config import get_machine_name

    storage_dir = get_storage_dir()
    machine_name = get_machine_name() or "Unknown"

    # Defer machine registration - will be done asynchronously later
    # This speeds up startup significantly (no DB write on import)

    return storage_dir / f"usage_history_{machine_name}.db"


def get_all_machine_db_paths() -> list[tuple[str, Path]]:
    """
    Get database paths for all registered machines.

    Returns:
        List of tuples: (machine_name, db_path)
    """
    from src.storage.machines_db import get_all_machines

    storage_dir = get_storage_dir()

    try:
        machines = get_all_machines()
        return [(m['machine_name'], storage_dir / f"usage_history_{m['machine_name']}.db")
                for m in machines]
    except Exception:
        # If machines.db doesn't exist or fails, return only current machine
        from src.config.user_config import get_machine_name
        machine_name = get_machine_name() or "Unknown"
        return [(machine_name, storage_dir / f"usage_history_{machine_name}.db")]


def get_default_db_path() -> Path:
    """
    Get the database path for the current machine.

    This is the main function used throughout the codebase.
    Now returns PC-specific DB path: usage_history_{machine_name}.db

    Returns:
        Path to the current machine's database file
    """
    return get_current_machine_db_path()


# Initialize default path
DEFAULT_DB_PATH = get_default_db_path()

#endregion


#region Functions


def init_database(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Initialize the SQLite database for historical snapshots.

    Creates tables if they don't exist:
    - daily_snapshots: Daily aggregated usage data
    - usage_records: Individual usage records for detailed analysis

    Enables WAL mode for better concurrent access (safe for multi-PC OneDrive sync).

    Args:
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database initialization fails
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=30.0)  # 30 second timeout for OneDrive sync
    try:
        cursor = conn.cursor()

        # Enable WAL mode for better concurrent access
        # WAL allows multiple readers and one writer simultaneously
        # Safe for OneDrive sync as WAL files sync properly
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")  # Faster while still safe
        cursor.execute("PRAGMA busy_timeout=30000")  # 30 second busy timeout

        # Table for daily aggregated snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_snapshots (
                date TEXT PRIMARY KEY,
                total_prompts INTEGER NOT NULL,
                total_responses INTEGER NOT NULL,
                total_sessions INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_creation_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL,
                snapshot_timestamp TEXT NOT NULL
            )
        """)

        # Table for detailed usage records
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                session_id TEXT NOT NULL,
                message_uuid TEXT NOT NULL,
                message_type TEXT NOT NULL,
                model TEXT,
                folder TEXT NOT NULL,
                git_branch TEXT,
                version TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_creation_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                machine_name TEXT,
                UNIQUE(session_id, message_uuid)
            )
        """)

        # Migration: Add machine_name column if it doesn't exist
        cursor.execute("PRAGMA table_info(usage_records)")
        columns = [row[1] for row in cursor.fetchall()]
        if "machine_name" not in columns:
            cursor.execute("ALTER TABLE usage_records ADD COLUMN machine_name TEXT")

        # Migration: Add content column if it doesn't exist
        cursor.execute("PRAGMA table_info(usage_records)")
        columns = [row[1] for row in cursor.fetchall()]
        if "content" not in columns:
            cursor.execute("ALTER TABLE usage_records ADD COLUMN content TEXT")

        # Migration: Add cost column if it doesn't exist
        cursor.execute("PRAGMA table_info(usage_records)")
        columns = [row[1] for row in cursor.fetchall()]
        if "cost" not in columns:
            cursor.execute("ALTER TABLE usage_records ADD COLUMN cost REAL")

        # Index for faster date-based queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_records_date
            ON usage_records(date)
        """)

        # Index for model-based queries (used in cost calculations)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_records_model
            ON usage_records(model)
        """)

        # Composite index for date + model (optimizes monthly cost calculations)
        # This speeds up queries like: WHERE substr(date, 1, 7) = '2025-10' AND model = 'X'
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_records_date_model
            ON usage_records(date, model)
        """)

        # Table for usage limits snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS limits_snapshots (
                timestamp TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                session_pct INTEGER,
                week_pct INTEGER,
                opus_pct INTEGER,
                session_reset TEXT,
                week_reset TEXT,
                opus_reset TEXT
            )
        """)

        # Index for faster date-based queries on limits
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_limits_snapshots_date
            ON limits_snapshots(date)
        """)

        # Table for model pricing
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                model_name TEXT PRIMARY KEY,
                input_price_per_mtok REAL NOT NULL,
                output_price_per_mtok REAL NOT NULL,
                cache_write_price_per_mtok REAL NOT NULL,
                cache_read_price_per_mtok REAL NOT NULL,
                last_updated TEXT NOT NULL,
                notes TEXT
            )
        """)

        # Table for user preferences
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Insert default preferences from defaults.py
        from src.config.defaults import get_all_defaults

        default_prefs_dict = get_all_defaults()
        for key, value in default_prefs_dict.items():
            cursor.execute("""
                INSERT OR IGNORE INTO user_preferences (key, value)
                VALUES (?, ?)
            """, (key, value))

        # Table for per-device aggregates when storage mode is aggregate
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_usage_aggregates (
                machine_name TEXT NOT NULL,
                date TEXT NOT NULL,
                total_prompts INTEGER NOT NULL,
                total_responses INTEGER NOT NULL,
                total_sessions INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_creation_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL,
                last_updated TEXT NOT NULL,
                PRIMARY KEY (machine_name, date)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_model_aggregates (
                machine_name TEXT NOT NULL,
                date TEXT NOT NULL,
                model TEXT NOT NULL,
                total_responses INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_creation_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                last_updated TEXT NOT NULL,
                PRIMARY KEY (machine_name, date, model)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_device_model_machine
            ON device_model_aggregates(machine_name)
        """)

        # Table for monthly device statistics (pre-aggregated for performance)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_monthly_stats (
                machine_name TEXT NOT NULL,
                year_month TEXT NOT NULL,
                total_records INTEGER NOT NULL,
                total_sessions INTEGER NOT NULL,
                total_messages INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_creation_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL,
                total_cost REAL NOT NULL,
                oldest_date TEXT NOT NULL,
                newest_date TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                PRIMARY KEY (machine_name, year_month)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_device_monthly_stats_machine
            ON device_monthly_stats(machine_name)
        """)

        # Populate pricing data from defaults.py
        from src.config.defaults import DEFAULT_MODEL_PRICING

        timestamp = datetime.now(timezone.utc).isoformat()
        for model_name, pricing_info in DEFAULT_MODEL_PRICING.items():
            cursor.execute("""
                INSERT OR REPLACE INTO model_pricing (
                    model_name, input_price_per_mtok, output_price_per_mtok,
                    cache_write_price_per_mtok, cache_read_price_per_mtok,
                    last_updated, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                model_name,
                pricing_info['input_price'],
                pricing_info['output_price'],
                pricing_info['cache_write_price'],
                pricing_info['cache_read_price'],
                timestamp,
                pricing_info.get('notes', '')
            ))

        conn.commit()
    finally:
        conn.close()


def save_snapshot(records: list[UsageRecord], db_path: Path = DEFAULT_DB_PATH) -> int:
    """
    Save usage records to the database as a snapshot.

    Only saves records that don't already exist (based on session_id + message_uuid).
    Also updates daily_snapshots table with aggregated data.

    Storage mode is fixed to "full" for data safety and integrity.
    Full mode prevents duplicate aggregation issues across multiple devices.

    Invalidates cache for current device to ensure fresh data on next load.

    Args:
        records: List of usage records to save
        db_path: Path to the SQLite database file

    Returns:
        Number of new records saved

    Raises:
        sqlite3.Error: If database operation fails
    """
    global _device_stats_cache, _device_stats_cache_time, _device_records_cache, _merged_records_cache

    if not records:
        return 0

    # Register machine on first data save (deferred from get_current_machine_db_path)
    # This is much faster than registering on every import/startup
    try:
        import socket
        from src.config.user_config import get_machine_name
        from src.storage.machines_db import register_machine

        machine_name = get_machine_name() or "Unknown"
        hostname = socket.gethostname()
        register_machine(machine_name, hostname)
    except Exception:
        pass  # Non-fatal if registration fails

    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)
    saved_count = 0

    try:
        cursor = conn.cursor()
        from src.config.user_config import get_machine_name
        machine_name = get_machine_name() or "Unknown"

        # Save individual records (full mode only)
        for record in records:
            # Get token values (0 for user messages without token_usage)
            input_tokens = record.token_usage.input_tokens if record.token_usage else 0
            output_tokens = record.token_usage.output_tokens if record.token_usage else 0
            cache_creation_tokens = record.token_usage.cache_creation_tokens if record.token_usage else 0
            cache_read_tokens = record.token_usage.cache_read_tokens if record.token_usage else 0
            total_tokens = record.token_usage.total_tokens if record.token_usage else 0

            # Calculate cost from model pricing table
            cost = None
            if record.model and record.token_usage:
                cursor.execute("""
                    SELECT input_price_per_mtok, output_price_per_mtok,
                           cache_write_price_per_mtok, cache_read_price_per_mtok
                    FROM model_pricing
                    WHERE model_name = ?
                """, (record.model,))
                pricing_row = cursor.fetchone()

                if pricing_row:
                    input_price, output_price, cache_write_price, cache_read_price = pricing_row
                    cost = (
                        (input_tokens / 1_000_000) * input_price +
                        (output_tokens / 1_000_000) * output_price +
                        (cache_creation_tokens / 1_000_000) * cache_write_price +
                        (cache_read_tokens / 1_000_000) * cache_read_price
                    )

            try:
                cursor.execute("""
                    INSERT INTO usage_records (
                        date, timestamp, session_id, message_uuid, message_type,
                        model, folder, git_branch, version,
                        input_tokens, output_tokens,
                        cache_creation_tokens, cache_read_tokens, total_tokens,
                        machine_name, content, cost
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.date_key,
                    record.timestamp.isoformat(),
                    record.session_id,
                    record.message_uuid,
                    record.message_type,
                    record.model,
                    record.folder,
                    record.git_branch,
                    record.version,
                    input_tokens,
                    output_tokens,
                    cache_creation_tokens,
                    cache_read_tokens,
                    total_tokens,
                    machine_name,
                    record.content,  # Save content field
                    cost,  # Save calculated cost
                ))
                saved_count += 1
            except sqlite3.IntegrityError:
                # Record already exists, skip it
                pass

        # Update daily snapshots (aggregate by date)
        # In full mode, only update dates that have records in usage_records
        # IMPORTANT: Never use REPLACE - it would delete old data when JSONL files age out
        # Instead, recalculate only for dates that currently have records
        timestamp = datetime.now(timezone.utc).isoformat()

        # Get all dates that currently have usage_records
        cursor.execute("SELECT DISTINCT date FROM usage_records")
        dates_with_records = [row[0] for row in cursor.fetchall()]

        for date in dates_with_records:
            # Calculate totals for this date from usage_records
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN message_type = 'user' THEN 1 ELSE 0 END) as total_prompts,
                    SUM(CASE WHEN message_type = 'assistant' THEN 1 ELSE 0 END) as total_responses,
                    COUNT(DISTINCT session_id) as total_sessions,
                    SUM(total_tokens) as total_tokens,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(cache_creation_tokens) as cache_creation_tokens,
                    SUM(cache_read_tokens) as cache_read_tokens
                FROM usage_records
                WHERE date = ?
            """, (date,))

            row = cursor.fetchone()

            # Use INSERT OR REPLACE only for dates that currently have data
            # This preserves historical daily_snapshots for dates no longer in usage_records
            cursor.execute("""
                INSERT OR REPLACE INTO daily_snapshots (
                    date, total_prompts, total_responses, total_sessions, total_tokens,
                    input_tokens, output_tokens, cache_creation_tokens,
                    cache_read_tokens, snapshot_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date,
                row[0] or 0,
                row[1] or 0,
                row[2] or 0,
                row[3] or 0,
                row[4] or 0,
                row[5] or 0,
                row[6] or 0,
                row[7] or 0,
                timestamp,
            ))

        conn.commit()

        # Invalidate caches if new records were saved
        if saved_count > 0:
            # Invalidate device statistics cache
            _device_stats_cache = None
            _device_stats_cache_time = 0

            # Invalidate current device's records cache for incremental updates
            current_device = _get_current_device_name()
            if current_device in _device_records_cache:
                del _device_records_cache[current_device]
            # Also clear merged cache since it needs to be rebuilt
            _merged_records_cache = None

        # Update monthly aggregates (only if we saved new records)
        # This runs in the background and won't slow down the save operation significantly
        if saved_count > 0:
            try:
                update_monthly_device_stats(db_path)
            except Exception:
                # Non-fatal if monthly update fails
                pass

    finally:
        conn.close()

    return saved_count


def save_limits_snapshot(
    session_pct: int,
    week_pct: int,
    opus_pct: int,
    session_reset: str,
    week_reset: str,
    opus_reset: str,
    db_path: Path = DEFAULT_DB_PATH
) -> None:
    """
    Save usage limits snapshot to the database.

    Args:
        session_pct: Session usage percentage
        week_pct: Week (all models) usage percentage
        opus_pct: Opus usage percentage
        session_reset: Session reset time
        week_reset: Week reset time
        opus_reset: Opus reset time
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database operation fails
    """
    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        timestamp = datetime.now(timezone.utc).isoformat()
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        cursor.execute("""
            INSERT OR REPLACE INTO limits_snapshots (
                timestamp, date, session_pct, week_pct, opus_pct,
                session_reset, week_reset, opus_reset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            date,
            session_pct,
            week_pct,
            opus_pct,
            session_reset,
            week_reset,
            opus_reset,
        ))

        conn.commit()
    finally:
        conn.close()


def _get_current_device_name() -> str:
    """
    Get the current device/machine name.

    Returns:
        Machine name string
    """
    from src.config.user_config import get_machine_name
    return get_machine_name() or "Unknown"


def load_records_after_timestamp(
    last_timestamp: datetime,
    db_path: Path
) -> list[UsageRecord]:
    """
    Load records that are newer than the given timestamp.

    This is used for incremental updates - only fetching new records
    since the last load, rather than reloading everything.

    Args:
        last_timestamp: Only load records with timestamp > this value
        db_path: Path to the SQLite database file

    Returns:
        List of UsageRecord objects created after last_timestamp

    Raises:
        sqlite3.Error: If database query fails
    """
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Query only records newer than last_timestamp
        query = "SELECT * FROM usage_records WHERE timestamp > ? ORDER BY timestamp ASC"
        cursor.execute(query, (last_timestamp.isoformat(),))
        rows = cursor.fetchall()

        if not rows:
            return []

        # Convert rows to UsageRecord objects
        records = []
        from src.models.usage_record import TokenUsage

        for row in rows:
            # Row columns: id, date, timestamp, session_id, message_uuid, message_type,
            #              model, folder, git_branch, version,
            #              input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, total_tokens,
            #              machine_name, content, cost

            # Only create TokenUsage if tokens exist (assistant messages)
            token_usage = None
            if row[10] > 0 or row[11] > 0:  # if input_tokens or output_tokens exist
                token_usage = TokenUsage(
                    input_tokens=row[10],
                    output_tokens=row[11],
                    cache_creation_tokens=row[12],
                    cache_read_tokens=row[13],
                )

            record = UsageRecord(
                timestamp=datetime.fromisoformat(row[2]),
                session_id=row[3],
                message_uuid=row[4],
                message_type=row[5],
                model=row[6],
                folder=row[7],
                git_branch=row[8],
                version=row[9],
                token_usage=token_usage,
                content=row[16] if len(row) > 16 else None,
            )
            records.append(record)

        return records
    finally:
        conn.close()


def load_all_devices_historical_records_cached(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[UsageRecord]:
    """
    Load historical usage records from ALL registered devices with caching.

    Implements incremental update strategy:
    1. On first call: Load all devices completely
    2. On subsequent calls:
       - Other devices: Use cached data (no reload)
       - Current device: Load only new records since last timestamp

    This significantly improves performance for multi-device setups:
    - Initial load: Same as full load
    - Page transitions: 30-50x faster (300ms → <10ms)

    Cache is invalidated only when:
    - New records are saved to current device (save_snapshot)
    - Program restarts

    Args:
        start_date: Optional start date in YYYY-MM-DD format (inclusive)
        end_date: Optional end date in YYYY-MM-DD format (inclusive)

    Returns:
        List of UsageRecord objects from all devices combined

    Raises:
        sqlite3.Error: If database query fails
    """
    global _device_records_cache, _merged_records_cache

    # Get current device name
    current_device = _get_current_device_name()

    # Get all machine DB paths
    machine_db_paths = get_all_machine_db_paths()

    if not machine_db_paths:
        return []

    # Check if this is the first load (cache is empty)
    is_first_load = len(_device_records_cache) == 0

    if is_first_load:
        # First load: Load all devices completely
        for machine_name, machine_db in machine_db_paths:
            if machine_db.exists():
                records = load_historical_records(start_date, end_date, machine_db)

                # Find latest timestamp for this device
                latest_timestamp = None
                if records:
                    latest_timestamp = max(r.timestamp for r in records)

                # Cache this device's records
                _device_records_cache[machine_name] = {
                    "records": records,
                    "last_timestamp": latest_timestamp
                }
    else:
        # Subsequent loads: Only update current device
        for machine_name, machine_db in machine_db_paths:
            if not machine_db.exists():
                continue

            if machine_name == current_device:
                # Current device: Incremental update
                cached_data = _device_records_cache.get(machine_name)

                if cached_data and cached_data["last_timestamp"]:
                    # Load only new records since last timestamp
                    new_records = load_records_after_timestamp(
                        cached_data["last_timestamp"],
                        machine_db
                    )

                    if new_records:
                        # Merge new records with cached records
                        all_device_records = cached_data["records"] + new_records

                        # Update cache with merged records and new latest timestamp
                        latest_timestamp = max(r.timestamp for r in all_device_records)
                        _device_records_cache[machine_name] = {
                            "records": all_device_records,
                            "last_timestamp": latest_timestamp
                        }
                else:
                    # No cache for current device, do full load
                    records = load_historical_records(start_date, end_date, machine_db)
                    latest_timestamp = None
                    if records:
                        latest_timestamp = max(r.timestamp for r in records)

                    _device_records_cache[machine_name] = {
                        "records": records,
                        "last_timestamp": latest_timestamp
                    }
            else:
                # Other devices: Use cached data (no reload)
                # If not in cache yet, load it once
                if machine_name not in _device_records_cache:
                    records = load_historical_records(start_date, end_date, machine_db)
                    latest_timestamp = None
                    if records:
                        latest_timestamp = max(r.timestamp for r in records)

                    _device_records_cache[machine_name] = {
                        "records": records,
                        "last_timestamp": latest_timestamp
                    }

    # Merge all cached records
    all_records = []
    for device_data in _device_records_cache.values():
        all_records.extend(device_data["records"])

    # Sort by timestamp to maintain chronological order
    all_records.sort(key=lambda r: r.timestamp)

    # Cache merged result
    _merged_records_cache = all_records

    return all_records


def load_all_devices_historical_records(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[UsageRecord]:
    """
    Load historical usage records from ALL registered devices.

    Combines data from all machine-specific databases.

    NOTE: This is the original non-cached version, kept for compatibility.
    Consider using load_all_devices_historical_records_cached() instead
    for better performance in multi-device scenarios.

    Args:
        start_date: Optional start date in YYYY-MM-DD format (inclusive)
        end_date: Optional end date in YYYY-MM-DD format (inclusive)

    Returns:
        List of UsageRecord objects from all devices combined

    Raises:
        sqlite3.Error: If database query fails
    """
    all_records = []

    # Get all machine DB paths
    machine_db_paths = get_all_machine_db_paths()

    if not machine_db_paths:
        return []

    # Load records from each machine DB
    for machine_name, machine_db in machine_db_paths:
        if machine_db.exists():
            records = load_historical_records(start_date, end_date, machine_db)
            all_records.extend(records)

    # Sort by timestamp to maintain chronological order
    all_records.sort(key=lambda r: r.timestamp)

    return all_records


def load_historical_records(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH
) -> list[UsageRecord]:
    """
    Load historical usage records from the database.

    Reads from usage_records table if available (full mode).
    Falls back to daily_snapshots if usage_records is empty (aggregate mode from other PC).

    Args:
        start_date: Optional start date in YYYY-MM-DD format (inclusive)
        end_date: Optional end date in YYYY-MM-DD format (inclusive)
        db_path: Path to the SQLite database file

    Returns:
        List of UsageRecord objects

    Raises:
        sqlite3.Error: If database query fails
    """
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Try usage_records first (full mode)
        query = "SELECT * FROM usage_records WHERE 1=1"
        params = []

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)

        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date, timestamp"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        # If usage_records has data, use it
        if rows:
            records = []
            from src.models.usage_record import TokenUsage

            for row in rows:
                # Parse the row into a UsageRecord
                # Row columns: id, date, timestamp, session_id, message_uuid, message_type,
                #              model, folder, git_branch, version,
                #              input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, total_tokens,
                #              machine_name, content, cost

                # Only create TokenUsage if tokens exist (assistant messages)
                token_usage = None
                if row[10] > 0 or row[11] > 0:  # if input_tokens or output_tokens exist
                    token_usage = TokenUsage(
                        input_tokens=row[10],
                        output_tokens=row[11],
                        cache_creation_tokens=row[12],
                        cache_read_tokens=row[13],
                    )

                record = UsageRecord(
                    timestamp=datetime.fromisoformat(row[2]),
                    session_id=row[3],
                    message_uuid=row[4],
                    message_type=row[5],
                    model=row[6],
                    folder=row[7],
                    git_branch=row[8],
                    version=row[9],
                    token_usage=token_usage,
                    content=row[16] if len(row) > 16 else None,  # Load content field
                    # Note: cost (row[17]) is stored in DB but not used by UsageRecord class
                    # Cost is calculated dynamically when needed for display
                )
                records.append(record)

            return records

        # Fall back to daily_snapshots (aggregate mode from other PC)
        query = "SELECT * FROM daily_snapshots WHERE 1=1"
        params = []

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)

        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date"

        cursor.execute(query, params)
        snapshot_rows = cursor.fetchall()

        if not snapshot_rows:
            return []

        # Convert daily_snapshots to synthetic UsageRecord objects
        # Row columns: date, total_prompts, total_responses, total_sessions, total_tokens,
        #              input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, snapshot_timestamp
        records = []
        from src.models.usage_record import TokenUsage

        for row in snapshot_rows:
            date = row[0]
            total_responses = row[2]
            input_tokens = row[5]
            output_tokens = row[6]
            cache_creation_tokens = row[7]
            cache_read_tokens = row[8]

            # Create one synthetic record per day representing aggregated assistant responses
            if total_responses > 0:
                token_usage = TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                    cache_read_tokens=cache_read_tokens,
                )

                # Use date as both timestamp and session_id (aggregated data)
                timestamp = datetime.fromisoformat(f"{date}T12:00:00+00:00")

                record = UsageRecord(
                    timestamp=timestamp,
                    session_id=f"aggregated-{date}",
                    message_uuid=f"aggregated-{date}",
                    message_type="assistant",
                    model=None,  # Model info not available in aggregate mode
                    folder="unknown",
                    git_branch=None,
                    version="unknown",
                    token_usage=token_usage,
                )
                records.append(record)

        return records
    finally:
        conn.close()



def get_text_analysis_stats(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """
    Analyze message content from JSONL files for text statistics.

    Returns:
        Dictionary with text analysis statistics
    """
    from src.config.settings import get_claude_jsonl_files
    from src.data.jsonl_parser import parse_all_jsonl_files
    from src.utils.text_analysis import (
        count_swears,
        count_perfect_phrases,
        count_absolutely_right_phrases,
        count_thank_phrases,
        count_please_phrases,
    )

    try:
        # Get current JSONL files
        jsonl_files = get_claude_jsonl_files()
        if not jsonl_files:
            return {
                "user_swears": 0,
                "assistant_swears": 0,
                "perfect_count": 0,
                "absolutely_right_count": 0,
                "user_thanks": 0,
                "user_please": 0,
                "avg_user_prompt_chars": 0,
                "total_user_chars": 0,
            }

        # Parse all records
        records = parse_all_jsonl_files(jsonl_files)

        user_swears = 0
        assistant_swears = 0
        perfect_count = 0
        absolutely_right_count = 0
        user_thanks = 0
        user_please = 0
        total_user_chars = 0
        user_prompt_count = 0

        for record in records:
            if not record.content:
                continue

            if record.is_user_prompt:
                user_swears += count_swears(record.content)
                user_thanks += count_thank_phrases(record.content)
                user_please += count_please_phrases(record.content)
                total_user_chars += record.char_count
                user_prompt_count += 1
            elif record.is_assistant_response:
                assistant_swears += count_swears(record.content)
                perfect_count += count_perfect_phrases(record.content)
                absolutely_right_count += count_absolutely_right_phrases(record.content)

        avg_user_prompt_chars = total_user_chars / user_prompt_count if user_prompt_count > 0 else 0

        return {
            "user_swears": user_swears,
            "assistant_swears": assistant_swears,
            "perfect_count": perfect_count,
            "absolutely_right_count": absolutely_right_count,
            "user_thanks": user_thanks,
            "user_please": user_please,
            "avg_user_prompt_chars": round(avg_user_prompt_chars),
            "total_user_chars": total_user_chars,
        }
    except Exception:
        # Return zeros if analysis fails
        return {
            "user_swears": 0,
            "assistant_swears": 0,
            "perfect_count": 0,
            "absolutely_right_count": 0,
            "user_thanks": 0,
            "user_please": 0,
            "avg_user_prompt_chars": 0,
            "total_user_chars": 0,
        }
def get_limits_data(db_path: Path = DEFAULT_DB_PATH) -> dict[str, dict[str, int]]:
    """
    Get daily maximum limits percentages from the database.

    Returns a dictionary mapping dates to their max limits:
    {
        "2025-10-11": {"week_pct": 14, "opus_pct": 8},
        ...
    }

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Dictionary mapping dates to max week_pct and opus_pct for that day
    """
    if not db_path.exists():
        return {}

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Get max week_pct and opus_pct per day
        cursor.execute("""
            SELECT
                date,
                MAX(week_pct) as max_week,
                MAX(opus_pct) as max_opus
            FROM limits_snapshots
            GROUP BY date
            ORDER BY date
        """)

        return {
            row[0]: {
                "week_pct": row[1] or 0,
                "opus_pct": row[2] or 0
            }
            for row in cursor.fetchall()
        }
    finally:
        conn.close()


def get_latest_limits(db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """
    Get the most recent limits snapshot from the database.

    Returns a dictionary with the latest limits data:
    {
        "session_pct": 14,
        "week_pct": 18,
        "opus_pct": 8,
        "session_reset": "Oct 16, 10:59am (Europe/Brussels)",
        "week_reset": "Oct 18, 3pm (Europe/Brussels)",
        "opus_reset": "Oct 18, 3pm (Europe/Brussels)",
    }

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Dictionary with latest limits, or None if no data exists
    """
    if not db_path.exists():
        return None

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Get most recent limits snapshot
        cursor.execute("""
            SELECT session_pct, week_pct, opus_pct,
                   session_reset, week_reset, opus_reset
            FROM limits_snapshots
            ORDER BY timestamp DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
        if not row:
            return None

        return {
            "session_pct": row[0] or 0,
            "week_pct": row[1] or 0,
            "opus_pct": row[2] or 0,
            "session_reset": row[3] or "",
            "week_reset": row[4] or "",
            "opus_reset": row[5] or "",
        }
    finally:
        conn.close()


def update_monthly_device_stats(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Update monthly device statistics for completed months.

    This function aggregates usage_records for each completed month
    and stores them in device_monthly_stats for faster queries.

    Only processes months that are fully complete (not current month).

    Args:
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database operation fails
    """
    if not db_path.exists():
        return

    # Ensure database schema is up to date (creates device_monthly_stats table if needed)
    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        from datetime import datetime as dt
        from src.config.user_config import get_machine_name

        machine_name = get_machine_name() or "Unknown"
        current_month = dt.now(timezone.utc).strftime("%Y-%m")

        # Get all distinct year-months from usage_records (excluding current month)
        cursor.execute("""
            SELECT DISTINCT substr(date, 1, 7) as year_month
            FROM usage_records
            WHERE substr(date, 1, 7) < ?
            ORDER BY year_month
        """, (current_month,))

        months = [row[0] for row in cursor.fetchall()]

        for year_month in months:
            # Check if this month is already aggregated
            cursor.execute("""
                SELECT last_updated FROM device_monthly_stats
                WHERE machine_name = ? AND year_month = ?
            """, (machine_name, year_month))

            existing = cursor.fetchone()

            # Skip if already aggregated and records haven't changed
            # (We could add more sophisticated change detection here)
            if existing:
                continue

            # Aggregate statistics for this month
            cursor.execute("""
                SELECT
                    COUNT(*) as total_records,
                    COUNT(DISTINCT session_id) as total_sessions,
                    COUNT(CASE WHEN message_type = 'assistant' THEN 1 END) as total_messages,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens,
                    COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                    COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                    MIN(date) as oldest_date,
                    MAX(date) as newest_date
                FROM usage_records
                WHERE substr(date, 1, 7) = ?
            """, (year_month,))

            row = cursor.fetchone()
            if not row or (row[0] or 0) == 0:
                continue

            # Calculate cost for this month
            cursor.execute("""
                SELECT
                    COALESCE(SUM(
                        (ur.input_tokens / 1000000.0) * mp.input_price_per_mtok +
                        (ur.output_tokens / 1000000.0) * mp.output_price_per_mtok +
                        (ur.cache_creation_tokens / 1000000.0) * mp.cache_write_price_per_mtok +
                        (ur.cache_read_tokens / 1000000.0) * mp.cache_read_price_per_mtok
                    ), 0.0) as total_cost
                FROM usage_records ur
                LEFT JOIN model_pricing mp ON ur.model = mp.model_name
                WHERE substr(ur.date, 1, 7) = ? AND ur.model IS NOT NULL
            """, (year_month,))

            cost_row = cursor.fetchone()
            total_cost = round(cost_row[0], 2) if cost_row else 0.0

            # Insert or replace monthly stats
            timestamp = dt.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT OR REPLACE INTO device_monthly_stats (
                    machine_name, year_month,
                    total_records, total_sessions, total_messages, total_tokens,
                    input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
                    total_cost, oldest_date, newest_date, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                machine_name, year_month,
                row[0], row[1], row[2], row[3],
                row[4], row[5], row[6], row[7],
                total_cost, row[8], row[9], timestamp
            ))

        conn.commit()
    finally:
        conn.close()


def get_device_statistics(db_path: Path = DEFAULT_DB_PATH, force_refresh: bool = False) -> list[dict]:
    """
    Get usage statistics grouped by device/machine.

    Reads from all PC-specific DB files (usage_history_{machine}.db)
    to aggregate statistics across all registered machines.

    Optimized version using monthly pre-aggregates + current month:
    - Completed months: Read from device_monthly_stats (fast)
    - Current month: Query usage_records (only ~30 days of data)

    Results are cached for 60 seconds.

    Args:
        db_path: Path to the database file (not used, kept for compatibility)
        force_refresh: Force refresh cache even if not expired

    Returns:
        List of dictionaries with device statistics:
        [
            {
                "machine_name": "PC-A",
                "total_records": 5000,
                "total_sessions": 10,
                "total_messages": 1000,
                "total_tokens": 50000000,
                "input_tokens": 10000,
                "output_tokens": 40000,
                "cache_creation_tokens": 5000000,
                "cache_read_tokens": 45000000,
                "total_cost": 123.45,
                "oldest_date": "2025-09-14",
                "newest_date": "2025-10-14",
            },
            ...
        ]
    """
    global _device_stats_cache, _device_stats_cache_time
    import time
    from datetime import datetime as dt

    # Check cache validity
    current_time = time.time()
    cache_age = current_time - _device_stats_cache_time

    if not force_refresh and _device_stats_cache is not None and cache_age < _CACHE_EXPIRY_SECONDS:
        # Return cached result
        return _device_stats_cache

    # Cache miss or expired - fetch fresh data
    # Get all machine DB paths
    machine_db_paths = get_all_machine_db_paths()

    if not machine_db_paths:
        _device_stats_cache = []
        _device_stats_cache_time = current_time
        return []

    # Current month (YYYY-MM)
    current_month = dt.now(timezone.utc).strftime("%Y-%m")

    device_stats: dict[str, dict] = {}

    # Process each machine DB independently (no ATTACH DATABASE)
    # This is MUCH faster than ATTACH DATABASE approach on OneDrive
    for machine_name, machine_db in machine_db_paths:
        if not machine_db.exists():
            continue

        # Open separate connection for each DB with optimized settings
        conn = sqlite3.connect(machine_db, timeout=30.0)

        try:
            cursor = conn.cursor()

            # Optimize read-only queries (no need for journaling/sync overhead)
            cursor.execute("PRAGMA query_only = ON")

            # Single combined query - get ALL data in one shot
            # This eliminates multiple round-trips to the database
            cursor.execute("""
                WITH monthly_agg AS (
                    SELECT
                        COALESCE(SUM(total_records), 0) as total_records,
                        COALESCE(SUM(total_sessions), 0) as total_sessions,
                        COALESCE(SUM(total_messages), 0) as total_messages,
                        COALESCE(SUM(total_tokens), 0) as total_tokens,
                        COALESCE(SUM(input_tokens), 0) as input_tokens,
                        COALESCE(SUM(output_tokens), 0) as output_tokens,
                        COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                        COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                        COALESCE(SUM(total_cost), 0.0) as total_cost,
                        MIN(oldest_date) as oldest_date,
                        MAX(newest_date) as newest_date
                    FROM device_monthly_stats
                    WHERE machine_name = ?
                ),
                current_month_agg AS (
                    SELECT
                        COUNT(*) as total_records,
                        COUNT(DISTINCT session_id) as total_sessions,
                        COUNT(CASE WHEN message_type = 'assistant' THEN 1 END) as total_messages,
                        COALESCE(SUM(ur.total_tokens), 0) as total_tokens,
                        COALESCE(SUM(ur.input_tokens), 0) as input_tokens,
                        COALESCE(SUM(ur.output_tokens), 0) as output_tokens,
                        COALESCE(SUM(ur.cache_creation_tokens), 0) as cache_creation_tokens,
                        COALESCE(SUM(ur.cache_read_tokens), 0) as cache_read_tokens,
                        MIN(ur.date) as oldest_date,
                        MAX(ur.date) as newest_date,
                        COALESCE(SUM(
                            (ur.input_tokens / 1000000.0) * COALESCE(mp.input_price_per_mtok, 0) +
                            (ur.output_tokens / 1000000.0) * COALESCE(mp.output_price_per_mtok, 0) +
                            (ur.cache_creation_tokens / 1000000.0) * COALESCE(mp.cache_write_price_per_mtok, 0) +
                            (ur.cache_read_tokens / 1000000.0) * COALESCE(mp.cache_read_price_per_mtok, 0)
                        ), 0.0) as total_cost
                    FROM usage_records ur
                    LEFT JOIN model_pricing mp ON ur.model = mp.model_name
                    WHERE substr(ur.date, 1, 7) = ?
                )
                SELECT
                    m.total_records + c.total_records as total_records,
                    m.total_sessions + c.total_sessions as total_sessions,
                    m.total_messages + c.total_messages as total_messages,
                    m.total_tokens + c.total_tokens as total_tokens,
                    m.input_tokens + c.input_tokens as input_tokens,
                    m.output_tokens + c.output_tokens as output_tokens,
                    m.cache_creation_tokens + c.cache_creation_tokens as cache_creation_tokens,
                    m.cache_read_tokens + c.cache_read_tokens as cache_read_tokens,
                    m.total_cost + c.total_cost as total_cost,
                    MIN(m.oldest_date, c.oldest_date) as oldest_date,
                    MAX(m.newest_date, c.newest_date) as newest_date
                FROM monthly_agg m, current_month_agg c
            """, (machine_name, current_month))

            row = cursor.fetchone()

            # Check if device has any data
            if row and (row[0] or 0) > 0:
                device_stats[machine_name] = {
                    "machine_name": machine_name,
                    "total_records": row[0] or 0,
                    "total_sessions": row[1] or 0,
                    "total_messages": row[2] or 0,
                    "total_tokens": row[3] or 0,
                    "input_tokens": row[4] or 0,
                    "output_tokens": row[5] or 0,
                    "cache_creation_tokens": row[6] or 0,
                    "cache_read_tokens": row[7] or 0,
                    "total_cost": round(row[8] or 0.0, 2),
                    "oldest_date": row[9],
                    "newest_date": row[10],
                }

        finally:
            conn.close()

    if not device_stats:
        _device_stats_cache = []
        _device_stats_cache_time = current_time
        return []

    # Sort by tokens and return
    results = list(device_stats.values())
    results.sort(key=lambda x: x["total_tokens"], reverse=True)

    # Update cache
    _device_stats_cache = results
    _device_stats_cache_time = current_time

    return results


def save_user_preference(key: str, value: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Save a single user preference to database.

    Args:
        key: Preference key (e.g., 'color_mode', 'usage_display_mode')
        value: Preference value (stored as string)
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database operation fails
    """
    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        timestamp = datetime.now(timezone.utc).isoformat()

        cursor.execute("""
            INSERT OR REPLACE INTO user_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, timestamp))

        conn.commit()
    finally:
        conn.close()


def load_user_preferences(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """
    Load all user preferences from database with defaults.

    Returns:
        Dictionary mapping preference keys to their values.
        Provides default values for any missing keys.

    Raises:
        sqlite3.Error: If database query fails
    """
    # Default preferences (fallback if DB doesn't exist or is empty)
    from src.config.defaults import get_all_defaults

    defaults = get_all_defaults()

    if not db_path.exists():
        return defaults

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Ensure user_preferences table exists (migration for existing DBs)
        try:
            cursor.execute("SELECT key, value FROM user_preferences")
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            # Table doesn't exist, initialize database to create it
            conn.close()
            init_database(db_path)
            conn = sqlite3.connect(db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM user_preferences")
            rows = cursor.fetchall()

        # Merge DB values with defaults
        prefs = defaults.copy()
        for key, value in rows:
            prefs[key] = value

        return prefs
    finally:
        conn.close()


def save_all_preferences(prefs: dict, db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Save multiple preferences at once (batch update).

    Args:
        prefs: Dictionary mapping preference keys to values
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database operation fails
    """
    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        timestamp = datetime.now(timezone.utc).isoformat()

        for key, value in prefs.items():
            cursor.execute("""
                INSERT OR REPLACE INTO user_preferences (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, timestamp))

        conn.commit()
    finally:
        conn.close()


def delete_user_preference(key: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Delete a single user preference from database.

    After deletion, defaults.py value will be used as fallback.

    Args:
        key: Preference key to delete (e.g., 'color_solid')
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database operation fails
    """
    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_preferences WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def delete_user_preferences(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Delete all user preferences from database.

    After deletion, defaults.py values will be used as fallback for all settings.
    This is the proper way to "reset to defaults" - by removing custom values
    rather than copying default values into the database.

    Args:
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database operation fails
    """
    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_preferences")
        conn.commit()
    finally:
        conn.close()


def load_all_devices_messages_by_hour(
    target_date: str,
    target_hour: int,
) -> list[UsageRecord]:
    """
    Load messages for a specific date and hour from ALL registered devices.

    Combines messages from all machine-specific databases.

    Args:
        target_date: Date in YYYY-MM-DD format (e.g., "2025-10-15")
        target_hour: Hour in 24-hour format (0-23)

    Returns:
        List of UsageRecord objects from all devices combined, sorted by timestamp

    Raises:
        sqlite3.Error: If database query fails
    """
    all_messages = []

    # Get all machine DB paths
    machine_db_paths = get_all_machine_db_paths()

    if not machine_db_paths:
        return []

    # Load messages from each machine DB
    for machine_name, machine_db in machine_db_paths:
        if machine_db.exists():
            messages = load_messages_by_hour(target_date, target_hour, machine_db)
            all_messages.extend(messages)

    # Sort by timestamp to maintain chronological order
    all_messages.sort(key=lambda r: r.timestamp)

    return all_messages


def load_messages_by_hour(
    target_date: str,
    target_hour: int,
    db_path: Path = DEFAULT_DB_PATH
) -> list[UsageRecord]:
    """
    Load messages for a specific date and hour.

    Args:
        target_date: Date in YYYY-MM-DD format (e.g., "2025-10-15")
        target_hour: Hour in 24-hour format (0-23)
        db_path: Path to the SQLite database file

    Returns:
        List of UsageRecord objects for the specified hour, sorted by timestamp

    Raises:
        sqlite3.Error: If database query fails
    """
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Query messages from the target hour
        # Note: timestamps are stored in UTC, need to filter correctly
        from src.utils.timezone import get_user_timezone
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo

        user_tz = get_user_timezone()

        # Create hour range in user's timezone, then convert to UTC for query
        # Target: 2025-10-15 23:00 to 23:59:59 in user's local time
        if user_tz == "UTC":
            tz_obj = ZoneInfo("UTC")
        elif user_tz == "auto":
            # Get system timezone
            import time
            tz_obj = dt.now().astimezone().tzinfo
        else:
            tz_obj = ZoneInfo(user_tz)

        # Create start and end times in user's timezone
        start_local = dt(
            int(target_date[:4]),
            int(target_date[5:7]),
            int(target_date[8:10]),
            target_hour,
            0,
            0,
            tzinfo=tz_obj
        )
        end_local = dt(
            int(target_date[:4]),
            int(target_date[5:7]),
            int(target_date[8:10]),
            target_hour,
            59,
            59,
            tzinfo=tz_obj
        )

        # Convert to UTC for database query
        start_utc = start_local.astimezone(ZoneInfo("UTC"))
        end_utc = end_local.astimezone(ZoneInfo("UTC"))

        # Query with UTC timestamps
        cursor.execute("""
            SELECT * FROM usage_records
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (start_utc.isoformat(), end_utc.isoformat()))

        rows = cursor.fetchall()

        if not rows:
            return []

        # Convert rows to UsageRecord objects
        records = []
        from src.models.usage_record import TokenUsage

        for row in rows:
            # Row columns: id, date, timestamp, session_id, message_uuid, message_type,
            #              model, folder, git_branch, version,
            #              input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, total_tokens,
            #              machine_name, content, cost

            # Only create TokenUsage if tokens exist (assistant messages)
            token_usage = None
            if row[10] > 0 or row[11] > 0:  # if input_tokens or output_tokens exist
                token_usage = TokenUsage(
                    input_tokens=row[10],
                    output_tokens=row[11],
                    cache_creation_tokens=row[12],
                    cache_read_tokens=row[13],
                )

            record = UsageRecord(
                timestamp=dt.fromisoformat(row[2]),
                session_id=row[3],
                message_uuid=row[4],
                message_type=row[5],
                model=row[6],
                folder=row[7],
                git_branch=row[8],
                version=row[9],
                token_usage=token_usage,
                content=row[16] if len(row) > 16 else None,  # Load content field
                # Note: cost (row[17]) is stored in DB but not used by UsageRecord class
            )
            records.append(record)

        return records
    finally:
        conn.close()


def get_database_stats(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """
    Get statistics about the historical database.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Dictionary with statistics including:
        - total_records, total_days, oldest_date, newest_date, newest_timestamp
        - total_tokens, total_prompts, total_sessions
        - tokens_by_model: dict of model -> token count
        - avg_tokens_per_session, avg_tokens_per_prompt
    """
    if not db_path.exists():
        return {
            "total_records": 0,
            "total_days": 0,
            "oldest_date": None,
            "newest_date": None,
            "newest_timestamp": None,
            "total_tokens": 0,
            "total_prompts": 0,
            "total_responses": 0,
            "total_sessions": 0,
            "tokens_by_model": {},
            "cost_by_model": {},
            "total_cost": 0.0,
            "avg_tokens_per_session": 0,
            "avg_tokens_per_response": 0,
            "avg_cost_per_session": 0.0,
            "avg_cost_per_response": 0.0,
        }

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Basic counts
        cursor.execute("SELECT COUNT(*) FROM usage_records")
        total_records = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT date) FROM usage_records")
        total_days = cursor.fetchone()[0]

        cursor.execute("SELECT MIN(date), MAX(date) FROM usage_records")
        oldest_date, newest_date = cursor.fetchone()

        # Get newest snapshot timestamp
        cursor.execute("SELECT MAX(snapshot_timestamp) FROM daily_snapshots")
        newest_timestamp = cursor.fetchone()[0]

        # Aggregate statistics from daily_snapshots
        cursor.execute("""
            SELECT
                SUM(total_tokens) as total_tokens,
                SUM(total_prompts) as total_prompts,
                SUM(total_responses) as total_responses,
                SUM(total_sessions) as total_sessions
            FROM daily_snapshots
        """)
        row = cursor.fetchone()
        total_tokens = row[0] or 0
        total_prompts = row[1] or 0
        total_responses = row[2] or 0
        total_sessions = row[3] or 0

        # Tokens by model (only available if usage_records exist)
        tokens_by_model = {}
        if total_records > 0:
            cursor.execute("""
                SELECT model, SUM(total_tokens) as tokens
                FROM usage_records
                GROUP BY model
                ORDER BY tokens DESC
            """)
            tokens_by_model = {row[0]: row[1] for row in cursor.fetchall() if row[0]}

        # Calculate costs by joining with pricing table
        total_cost = 0.0
        cost_by_model = {}

        if total_records > 0:
            cursor.execute("""
                SELECT
                    ur.model,
                    SUM(ur.input_tokens) as total_input,
                    SUM(ur.output_tokens) as total_output,
                    SUM(ur.cache_creation_tokens) as total_cache_write,
                    SUM(ur.cache_read_tokens) as total_cache_read,
                    mp.input_price_per_mtok,
                    mp.output_price_per_mtok,
                    mp.cache_write_price_per_mtok,
                    mp.cache_read_price_per_mtok
                FROM usage_records ur
                LEFT JOIN model_pricing mp ON ur.model = mp.model_name
                WHERE ur.model IS NOT NULL
                GROUP BY ur.model
            """)

            for row in cursor.fetchall():
                model = row[0]
                input_tokens = row[1] or 0
                output_tokens = row[2] or 0
                cache_write_tokens = row[3] or 0
                cache_read_tokens = row[4] or 0

                # Pricing per million tokens
                input_price = row[5] or 0.0
                output_price = row[6] or 0.0
                cache_write_price = row[7] or 0.0
                cache_read_price = row[8] or 0.0

                # Calculate cost in dollars
                model_cost = (
                    (input_tokens / 1_000_000) * input_price +
                    (output_tokens / 1_000_000) * output_price +
                    (cache_write_tokens / 1_000_000) * cache_write_price +
                    (cache_read_tokens / 1_000_000) * cache_read_price
                )

                cost_by_model[model] = model_cost
                total_cost += model_cost

        # Calculate averages
        avg_tokens_per_session = total_tokens / total_sessions if total_sessions > 0 else 0
        avg_tokens_per_response = total_tokens / total_responses if total_responses > 0 else 0
        avg_cost_per_session = total_cost / total_sessions if total_sessions > 0 else 0
        avg_cost_per_response = total_cost / total_responses if total_responses > 0 else 0

        return {
            "total_records": total_records,
            "total_days": total_days,
            "oldest_date": oldest_date,
            "newest_date": newest_date,
            "newest_timestamp": newest_timestamp,
            "total_tokens": total_tokens,
            "total_prompts": total_prompts,
            "total_responses": total_responses,
            "total_sessions": total_sessions,
            "tokens_by_model": tokens_by_model,
            "cost_by_model": cost_by_model,
            "total_cost": total_cost,
            "avg_tokens_per_session": round(avg_tokens_per_session),
            "avg_tokens_per_response": round(avg_tokens_per_response),
            "avg_cost_per_session": round(avg_cost_per_session, 2),
            "avg_cost_per_response": round(avg_cost_per_response, 4),
        }
    finally:
        conn.close()


def reset_pricing_to_defaults(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Reset all model pricing to default values from defaults.py.

    This updates all models in the model_pricing table with prices from defaults.py.
    Use this when user presses 'r' in Settings to reset all settings.

    Args:
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database operation fails
    """
    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        from src.config.defaults import DEFAULT_MODEL_PRICING

        timestamp = datetime.now(timezone.utc).isoformat()

        for model_name, pricing_info in DEFAULT_MODEL_PRICING.items():
            cursor.execute("""
                INSERT OR REPLACE INTO model_pricing (
                    model_name, input_price_per_mtok, output_price_per_mtok,
                    cache_write_price_per_mtok, cache_read_price_per_mtok,
                    last_updated, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                model_name,
                pricing_info['input_price'],
                pricing_info['output_price'],
                pricing_info['cache_write_price'],
                pricing_info['cache_read_price'],
                timestamp,
                pricing_info.get('notes', '')
            ))

        conn.commit()
    finally:
        conn.close()


def update_model_pricing_group(
    group_key: str,
    input_price: float,
    output_price: float,
    db_path: Path = DEFAULT_DB_PATH
) -> None:
    """
    Update pricing for a group of models (e.g., all Sonnet 4.x or Opus 4.x models).

    This updates all models in the specified group to have the same input/output prices.
    Cache prices are recalculated proportionally:
    - cache_write = input_price * 1.25
    - cache_read = input_price * 0.10

    Args:
        group_key: Group identifier from SETTINGS_MODEL_GROUPS (e.g., "sonnet-4.5", "opus-4")
        input_price: New input price per million tokens (USD)
        output_price: New output price per million tokens (USD)
        db_path: Path to the SQLite database file

    Raises:
        sqlite3.Error: If database operation fails
        ValueError: If group_key is not found in SETTINGS_MODEL_GROUPS
    """
    from src.config.defaults import SETTINGS_MODEL_GROUPS

    # Find the group
    group = None
    for g in SETTINGS_MODEL_GROUPS:
        if g['key'] == group_key:
            group = g
            break

    if not group:
        raise ValueError(f"Unknown model group: {group_key}")

    init_database(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        timestamp = datetime.now(timezone.utc).isoformat()

        # Calculate cache prices proportionally
        cache_write_price = input_price * 1.25
        cache_read_price = input_price * 0.10

        # Update all models in the group
        for model_id in group['model_ids']:
            cursor.execute("""
                UPDATE model_pricing
                SET input_price_per_mtok = ?,
                    output_price_per_mtok = ?,
                    cache_write_price_per_mtok = ?,
                    cache_read_price_per_mtok = ?,
                    last_updated = ?
                WHERE model_name = ?
            """, (input_price, output_price, cache_write_price, cache_read_price, timestamp, model_id))

        conn.commit()
    finally:
        conn.close()


def get_model_pricing_for_settings(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """
    Get model pricing information formatted for Settings UI.

    Returns pricing for model groups (e.g., Sonnet 4.5, Opus 4) by taking
    the first model in each group as representative.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Dictionary with group keys mapped to pricing info:
        {
            "sonnet-4.5": {
                "display_name": "Sonnet 4.5",
                "input_price": 3.0,
                "output_price": 15.0,
            },
            "opus-4": {
                "display_name": "Opus 4",
                "input_price": 15.0,
                "output_price": 75.0,
            },
        }
    """
    from src.config.defaults import SETTINGS_MODEL_GROUPS

    if not db_path.exists():
        # Return defaults if DB doesn't exist
        return {
            "sonnet-4.5": {"display_name": "Sonnet 4.5", "input_price": 3.0, "output_price": 15.0},
            "opus-4": {"display_name": "Opus 4", "input_price": 15.0, "output_price": 75.0},
        }

    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()
        result = {}

        for group in SETTINGS_MODEL_GROUPS:
            # Get pricing from the first model in the group (all models in group have same price)
            representative_model = group['model_ids'][0]

            cursor.execute("""
                SELECT input_price_per_mtok, output_price_per_mtok
                FROM model_pricing
                WHERE model_name = ?
            """, (representative_model,))

            row = cursor.fetchone()
            if row:
                result[group['key']] = {
                    "display_name": group['display_name'],
                    "input_price": row[0],
                    "output_price": row[1],
                }
            else:
                # Fallback to defaults if not found in DB
                result[group['key']] = {
                    "display_name": group['display_name'],
                    "input_price": 3.0 if 'sonnet' in group['key'] else 15.0,
                    "output_price": 15.0 if 'sonnet' in group['key'] else 75.0,
                }

        return result
    finally:
        conn.close()


#endregion
