"""
Migration runner with version tracking.

Automatically detects and runs pending migrations on startup.
"""

import sqlite3
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from src.migrations.base import Migration, MigrationResult, compare_versions
from src.storage.snapshot_db import get_storage_dir


# Version metadata table name
VERSION_TABLE = "ccu_version_info"


def get_version_db_path() -> Path:
    """Get path to version tracking database."""
    storage_dir = get_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir / "version_info.db"


def init_version_db(db_path: Optional[Path] = None) -> None:
    """
    Initialize version tracking database.

    Args:
        db_path: Path to database (default: version_info.db in storage dir)
    """
    if db_path is None:
        db_path = get_version_db_path()

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        cursor = conn.cursor()

        # Create version info table
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Create migrations history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS migration_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                name TEXT NOT NULL,
                applied_at TEXT DEFAULT (datetime('now')),
                success INTEGER NOT NULL DEFAULT 1,
                message TEXT
            )
        """)

        conn.commit()
    finally:
        conn.close()


def get_current_app_version() -> str:
    """Get current application version from pyproject.toml."""
    try:
        from importlib.metadata import version
        return version("claude-code-usage-analytics")
    except Exception:
        # Fallback: read from pyproject.toml
        try:
            import tomllib
            pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
            if pyproject_path.exists():
                with open(pyproject_path, "rb") as f:
                    data = tomllib.load(f)
                    return data.get("project", {}).get("version", "0.0.0")
        except Exception:
            pass
        return "0.0.0"


def get_stored_version(db_path: Optional[Path] = None) -> Optional[str]:
    """
    Get previously stored application version.

    Args:
        db_path: Path to version database

    Returns:
        Version string or None if never stored
    """
    if db_path is None:
        db_path = get_version_db_path()

    if not db_path.exists():
        return None

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT value FROM {VERSION_TABLE} WHERE key = 'app_version'")
        row = cursor.fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return None
    finally:
        conn.close()


def set_stored_version(version: str, db_path: Optional[Path] = None) -> None:
    """
    Store current application version.

    Args:
        version: Version string to store
        db_path: Path to version database
    """
    if db_path is None:
        db_path = get_version_db_path()

    init_version_db(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT OR REPLACE INTO {VERSION_TABLE} (key, value, updated_at)
            VALUES ('app_version', ?, datetime('now'))
        """, (version,))
        conn.commit()
    finally:
        conn.close()


def record_migration(
    version: str,
    name: str,
    success: bool,
    message: str,
    db_path: Optional[Path] = None
) -> None:
    """
    Record migration execution in history.

    Args:
        version: Migration version
        name: Migration name
        success: Whether migration succeeded
        message: Result message
        db_path: Path to version database
    """
    if db_path is None:
        db_path = get_version_db_path()

    init_version_db(db_path)

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO migration_history (version, name, success, message)
            VALUES (?, ?, ?, ?)
        """, (version, name, 1 if success else 0, message))
        conn.commit()
    finally:
        conn.close()


def is_migration_applied(version: str, db_path: Optional[Path] = None) -> bool:
    """
    Check if a specific migration has been applied.

    Args:
        version: Migration version to check
        db_path: Path to version database

    Returns:
        True if migration was successfully applied
    """
    if db_path is None:
        db_path = get_version_db_path()

    if not db_path.exists():
        return False

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT success FROM migration_history
            WHERE version = ? AND success = 1
            ORDER BY applied_at DESC
            LIMIT 1
        """, (version,))
        row = cursor.fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def get_pending_migrations() -> list[Migration]:
    """
    Get list of migrations that need to be run.

    Returns:
        List of Migration instances sorted by version
    """
    # Import all migrations here
    from src.migrations.versions import ALL_MIGRATIONS

    pending = []
    for migration_class in ALL_MIGRATIONS:
        migration = migration_class()
        if not is_migration_applied(migration.version):
            if migration.check_required():
                pending.append(migration)

    # Sort by version
    pending.sort(key=lambda m: tuple(map(int, m.version.split("."))))
    return pending


def run_migrations(console: Optional[Console] = None, silent: bool = False) -> dict[str, Any]:
    """
    Run all pending migrations.

    Args:
        console: Rich console for output (optional)
        silent: If True, suppress all output

    Returns:
        Dictionary with migration results:
        - ran: Number of migrations run
        - success: Number of successful migrations
        - failed: Number of failed migrations
        - messages: List of status messages
    """
    result = {
        "ran": 0,
        "success": 0,
        "failed": 0,
        "messages": [],
        "version_updated": False,
    }

    # Check for version change
    current_version = get_current_app_version()
    stored_version = get_stored_version()

    # Initialize version DB
    init_version_db()

    # Get pending migrations
    pending = get_pending_migrations()

    if not pending:
        # No migrations to run, just update version if changed
        if stored_version != current_version:
            set_stored_version(current_version)
            result["version_updated"] = True
            result["messages"].append(f"Version updated: {stored_version or 'initial'} -> {current_version}")
        return result

    # Show migration header
    if not silent and console:
        console.print()
        console.print("[bold cyan]CCU Migration[/bold cyan]")
        if stored_version:
            console.print(f"[dim]Upgrading from v{stored_version} to v{current_version}[/dim]")
        else:
            console.print(f"[dim]First run of v{current_version}[/dim]")
        console.print()

    # Run each migration
    for migration in pending:
        result["ran"] += 1

        if not silent and console:
            console.print(f"  [cyan]→[/cyan] {migration.name}...", end="")

        try:
            migration_result = migration.up()

            if migration_result.success:
                result["success"] += 1
                record_migration(
                    migration.version,
                    migration.name,
                    True,
                    migration_result.message
                )
                if not silent and console:
                    console.print(" [green]✓[/green]")
                result["messages"].append(f"✓ {migration.name}: {migration_result.message}")
            else:
                result["failed"] += 1
                record_migration(
                    migration.version,
                    migration.name,
                    False,
                    migration_result.error or migration_result.message
                )
                if not silent and console:
                    console.print(f" [red]✗[/red] {migration_result.error}")
                result["messages"].append(f"✗ {migration.name}: {migration_result.error}")

        except Exception as e:
            result["failed"] += 1
            error_msg = str(e)
            record_migration(migration.version, migration.name, False, error_msg)
            if not silent and console:
                console.print(f" [red]✗[/red] {error_msg}")
            result["messages"].append(f"✗ {migration.name}: {error_msg}")

    # Update stored version
    set_stored_version(current_version)
    result["version_updated"] = True

    # Show summary
    if not silent and console:
        console.print()
        if result["failed"] == 0:
            console.print(f"[green]✓ {result['success']} migration(s) applied successfully[/green]")
        else:
            console.print(f"[yellow]⚠ {result['success']} succeeded, {result['failed']} failed[/yellow]")
        console.print()

    return result


def get_migration_status() -> dict[str, Any]:
    """
    Get current migration status.

    Returns:
        Dictionary with:
        - current_version: Current app version
        - stored_version: Previously stored version
        - pending_count: Number of pending migrations
        - pending_migrations: List of pending migration info
        - history: List of applied migrations
    """
    current_version = get_current_app_version()
    stored_version = get_stored_version()
    pending = get_pending_migrations()

    status = {
        "current_version": current_version,
        "stored_version": stored_version,
        "pending_count": len(pending),
        "pending_migrations": [
            {"version": m.version, "name": m.name, "description": m.description}
            for m in pending
        ],
        "history": [],
    }

    # Get migration history
    db_path = get_version_db_path()
    if db_path.exists():
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT version, name, applied_at, success, message
                FROM migration_history
                ORDER BY applied_at DESC
                LIMIT 20
            """)
            status["history"] = [
                {
                    "version": row[0],
                    "name": row[1],
                    "applied_at": row[2],
                    "success": bool(row[3]),
                    "message": row[4],
                }
                for row in cursor.fetchall()
            ]
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    return status
