"""
Safe, read-only JSON export of usage data from SQLite databases.

This module NEVER modifies source databases in ~/.claude/.
Exports are incremental and include statistics.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.config.user_config import get_machine_name
from src.storage.snapshot_db import get_current_machine_db_path


def export_to_json(
    db_path: Optional[Path] = None,
    since_date: Optional[str] = None,
    include_stats: bool = True,
) -> dict[str, Any]:
    """
    Export usage data to JSON format (READ-ONLY operation).

    Args:
        db_path: Path to database file (default: current machine DB)
        since_date: Export only records after this date (YYYY-MM-DD)
        include_stats: Include summary statistics

    Returns:
        Dictionary with exported data

    Security:
        - Opens database in READ-ONLY mode
        - Never modifies source database
        - Validates all paths before access
    """
    if db_path is None:
        db_path = get_current_machine_db_path()

    machine_name = get_machine_name() or "Unknown"

    # If database doesn't exist yet, return empty export
    if not db_path.exists():
        return {
            "machine_name": machine_name,
            "export_date": datetime.now(timezone.utc).isoformat(),
            "data_range": {
                "oldest": None,
                "newest": None,
            },
            "records": [],
            "stats": {
                "total_records": 0,
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
        }

    # Open database in READ-ONLY mode (URI syntax)
    # This prevents any accidental writes
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Build query with optional date filter
        query = """
            SELECT
                session_id,
                message_uuid,
                timestamp,
                model,
                total_tokens,
                input_tokens,
                output_tokens,
                cache_creation_tokens,
                cache_read_tokens,
                folder,
                git_branch,
                version,
                date
            FROM usage_records
        """

        params: tuple = ()
        if since_date:
            query += " WHERE date >= ?"
            params = (since_date,)

        query += " ORDER BY timestamp ASC"

        cursor.execute(query, params)

        # Export records
        records = []
        for row in cursor:
            records.append({
                "session_id": row[0],
                "message_uuid": row[1],
                "timestamp": row[2],
                "model": row[3],
                "total_tokens": row[4],
                "input_tokens": row[5],
                "output_tokens": row[6],
                "cache_creation_tokens": row[7],
                "cache_read_tokens": row[8],
                "folder": row[9],
                "git_branch": row[10],
                "version": row[11],
                "date": row[12],
            })

        # Get date range
        oldest_date = records[0]["date"] if records else None
        newest_date = records[-1]["date"] if records else None

        result = {
            "machine_name": machine_name,
            "export_date": datetime.now(timezone.utc).isoformat(),
            "data_range": {
                "oldest": oldest_date,
                "newest": newest_date,
            },
            "records": records,
        }

        # Add statistics if requested
        if include_stats:
            stats_query = """
                SELECT
                    COUNT(*) as total_records,
                    COUNT(DISTINCT session_id) as total_sessions,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens,
                    COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                    COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens
                FROM usage_records
            """

            if since_date:
                stats_query += " WHERE date >= ?"

            cursor.execute(stats_query, params)
            stats_row = cursor.fetchone()

            # Calculate total cost (join with model_pricing)
            cost_query = """
                SELECT COALESCE(SUM(
                    (ur.input_tokens / 1000000.0) * COALESCE(mp.input_price_per_mtok, 0) +
                    (ur.output_tokens / 1000000.0) * COALESCE(mp.output_price_per_mtok, 0) +
                    (ur.cache_creation_tokens / 1000000.0) * COALESCE(mp.cache_write_price_per_mtok, 0) +
                    (ur.cache_read_tokens / 1000000.0) * COALESCE(mp.cache_read_price_per_mtok, 0)
                ), 0.0) as total_cost
                FROM usage_records ur
                LEFT JOIN model_pricing mp ON ur.model = mp.model_name
            """

            if since_date:
                cost_query += " WHERE ur.date >= ?"

            cursor.execute(cost_query, params)
            cost_row = cursor.fetchone()

            result["statistics"] = {
                "total_records": stats_row[0],
                "total_sessions": stats_row[1],
                "total_tokens": stats_row[2],
                "input_tokens": stats_row[3],
                "output_tokens": stats_row[4],
                "cache_creation_tokens": stats_row[5],
                "cache_read_tokens": stats_row[6],
                "total_cost": round(cost_row[0], 2),
            }

        return result

    finally:
        conn.close()


def save_json_export(
    output_path: Path,
    db_path: Optional[Path] = None,
    since_date: Optional[str] = None,
    pretty: bool = True,
) -> None:
    """
    Export usage data to JSON file.

    Args:
        output_path: Path to output JSON file
        db_path: Path to database file (default: current machine DB)
        since_date: Export only records after this date
        pretty: Pretty-print JSON (default: True)
    """
    data = export_to_json(db_path, since_date)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, indent=2, ensure_ascii=False)
        else:
            json.dump(data, f, ensure_ascii=False)


def get_last_export_date(db_path: Optional[Path] = None) -> Optional[str]:
    """
    Get the date of the last export from sync metadata.

    Args:
        db_path: Path to database file (default: current machine DB)

    Returns:
        Last export date (YYYY-MM-DD) or None if never exported
    """
    if db_path is None:
        db_path = get_current_machine_db_path()

    if not db_path.exists():
        return None

    # Open database in READ-ONLY mode
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Check if sync_metadata table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='sync_metadata'
        """)

        if not cursor.fetchone():
            return None

        # Get last export date
        cursor.execute("""
            SELECT value FROM sync_metadata
            WHERE key = 'last_gist_export_date'
        """)

        row = cursor.fetchone()
        if not row:
            return None

        # Normalize to YYYY-MM-DD format for comparison with date column
        # Handle both ISO format ("2024-11-28T10:30:00+00:00") and date-only format
        date_value = row[0]
        if date_value and len(date_value) > 10 and 'T' in date_value:
            # ISO format - extract just the date part
            return date_value[:10]
        return date_value

    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return None
    finally:
        conn.close()
