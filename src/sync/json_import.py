"""
Safe JSON import with automatic deduplication.

Imports usage data from JSON format to local database.
NEVER modifies ~/.claude/ - only imports to designated storage directory.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from src.storage.snapshot_db import get_current_machine_db_path, init_database


def import_from_json(
    json_data: dict[str, Any],
    db_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Import usage data from JSON format to local database.

    Args:
        json_data: Exported JSON data
        db_path: Target database path (default: current machine DB)
        dry_run: If True, validate but don't write to database

    Returns:
        Dictionary with import statistics:
        - new_records: Number of new records inserted
        - duplicate_records: Number of duplicates skipped
        - errors: Number of records with errors

    Security:
        - Never modifies ~/.claude/ directories
        - Uses UNIQUE constraint for automatic deduplication
        - Validates JSON schema before import
        - Transaction-based (all-or-nothing)
    """
    if db_path is None:
        db_path = get_current_machine_db_path()

    # Validate JSON structure
    required_fields = ["machine_name", "export_date", "records"]
    for field in required_fields:
        if field not in json_data:
            raise ValueError(f"Invalid JSON: missing field '{field}'")

    # Ensure database is initialized
    init_database(db_path)

    stats = {
        "new_records": 0,
        "duplicate_records": 0,
        "errors": 0,
    }

    if dry_run:
        # Just validate records
        for record in json_data["records"]:
            required_record_fields = [
                "session_id", "message_uuid", "timestamp", "model",
                "total_tokens", "input_tokens", "output_tokens"
            ]
            if all(field in record for field in required_record_fields):
                stats["new_records"] += 1
            else:
                stats["errors"] += 1
        return stats

    # Open database for writing
    conn = sqlite3.connect(db_path, timeout=30.0)

    try:
        cursor = conn.cursor()

        # Use transaction for atomicity
        conn.execute("BEGIN")

        for record in json_data["records"]:
            try:
                # Insert record - UNIQUE constraint will prevent duplicates
                cursor.execute("""
                    INSERT INTO usage_records (
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
                        date,
                        message_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record["session_id"],
                    record["message_uuid"],
                    record["timestamp"],
                    record["model"],
                    record["total_tokens"],
                    record["input_tokens"],
                    record["output_tokens"],
                    record.get("cache_creation_tokens", 0),
                    record.get("cache_read_tokens", 0),
                    record.get("folder", ""),
                    record.get("git_branch", ""),
                    record.get("version", ""),
                    record.get("date", record["timestamp"][:10]),
                    "assistant",  # Default message type
                ))
                stats["new_records"] += 1

            except sqlite3.IntegrityError:
                # Duplicate record (UNIQUE constraint violation)
                stats["duplicate_records"] += 1

            except Exception as e:
                # Log error but continue with other records
                print(f"Warning: Failed to import record {record.get('message_uuid', 'unknown')}: {e}")
                stats["errors"] += 1

        # Commit transaction
        conn.commit()

        # Ensure sync_metadata table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Update sync metadata
        cursor.execute("""
            INSERT OR REPLACE INTO sync_metadata (key, value)
            VALUES ('last_gist_import_date', ?)
        """, (json_data["export_date"],))

        cursor.execute("""
            INSERT OR REPLACE INTO sync_metadata (key, value)
            VALUES ('last_gist_import_machine', ?)
        """, (json_data["machine_name"],))

        conn.commit()

        # Register machine in machines.db
        from src.storage.machines_db import register_machine
        machine_name = json_data["machine_name"]
        register_machine(machine_name, machine_name)  # Use machine_name as hostname for imported data

    except Exception as e:
        # Rollback on any error
        conn.rollback()
        raise RuntimeError(f"Import failed: {e}") from e

    finally:
        conn.close()

    return stats


def load_json_file(json_path: Path) -> dict[str, Any]:
    """
    Load and validate JSON file.

    Args:
        json_path: Path to JSON file

    Returns:
        Parsed JSON data

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If JSON is invalid
    """
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

    return data


def import_from_json_file(
    json_path: Path,
    db_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Import usage data from JSON file.

    Args:
        json_path: Path to JSON file
        db_path: Target database path (default: current machine DB)
        dry_run: If True, validate but don't write to database

    Returns:
        Import statistics
    """
    json_data = load_json_file(json_path)
    return import_from_json(json_data, db_path, dry_run)


def merge_multiple_exports(
    json_files: list[Path],
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Merge multiple JSON exports into database.

    Useful for restoring data from multiple machines or backups.

    Args:
        json_files: List of JSON file paths
        db_path: Target database path

    Returns:
        Combined import statistics with per-file breakdown
    """
    total_stats = {
        "new_records": 0,
        "duplicate_records": 0,
        "errors": 0,
        "files_processed": 0,
        "files_failed": 0,
        "per_file": {},
    }

    for json_file in json_files:
        try:
            stats = import_from_json_file(json_file, db_path)
            total_stats["new_records"] += stats["new_records"]
            total_stats["duplicate_records"] += stats["duplicate_records"]
            total_stats["errors"] += stats["errors"]
            total_stats["files_processed"] += 1
            total_stats["per_file"][str(json_file)] = stats

        except Exception as e:
            print(f"Error processing {json_file}: {e}")
            total_stats["files_failed"] += 1
            total_stats["per_file"][str(json_file)] = {"error": str(e)}

    return total_stats
