"""
Main synchronization manager for GitHub Gist integration.

Orchestrates export, import, backup, and Gist operations.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.config.user_config import get_machine_name
from src.sync.exceptions import ConflictError
from src.sync.gist_client import GistClient
from src.sync.json_export import export_to_json, export_to_json_chunked, get_last_export_date
from src.sync.json_import import import_from_json, merge_multiple_exports
from src.sync.manifest import Manifest
from src.sync.token_manager import TokenManager


class SyncManager:
    """
    Manages synchronization between local database and GitHub Gist.
    """

    GIST_DESCRIPTION = "Claude Code Usage Analytics - Data Backup"

    def __init__(self, gist_id: Optional[str] = None):
        """
        Initialize sync manager.

        Args:
            gist_id: Existing Gist ID (creates new if None)
        """
        self.token_manager = TokenManager()
        self.gist_id = gist_id
        self.machine_name = get_machine_name() or "Unknown"
        self._client: Optional[GistClient] = None

    @property
    def client(self) -> GistClient:
        """Get or create Gist client."""
        if self._client is None:
            token = self.token_manager.get_token()
            if not token:
                raise RuntimeError(
                    "GitHub token not configured. Run: ccu gist set-token <token>"
                )
            self._client = GistClient(token)
        return self._client

    def push(
        self,
        force: bool = False,
        create_backup: bool = False,  # Backup disabled by default (v1.7.9)
        skip_conflict_check: bool = False,
    ) -> dict[str, Any]:
        """
        Push local data to Gist (incremental).

        Args:
            force: If True, export all data (not just incremental)
            create_backup: Create backup before overwriting
            skip_conflict_check: If True, skip conflict detection (for --force flag)

        Returns:
            Dictionary with push statistics

        Workflow:
            1. Export local data (incremental if not force)
            2. Download current manifest from Gist
            3. Detect and resolve conflicts (unless skip_conflict_check)
            4. Create backup of existing data (if requested)
            5. Upload new data
            6. Update manifest
            7. Clean up old backups
        """
        stats = {
            "exported_records": 0,
            "backup_created": False,
            "manifest_updated": False,
            "gist_id": self.gist_id,
            "conflicts_resolved": False,
        }

        # 1. Export local data (automatically chunks if too large)
        since_date = None if force else get_last_export_date()

        # Use chunked export which automatically splits by year if data is too large
        # This prevents GitHub Gist 422 errors for large datasets
        chunked_exports = export_to_json_chunked(
            db_path=None,  # Let export resolve the correct machine-specific path
            since_date=since_date,
        )

        # Calculate total records across all chunks
        total_records = sum(
            len(data.get("records", []))
            for data in chunked_exports.values()
        )
        stats["exported_records"] = total_records

        if total_records == 0 and not force:
            return {"status": "nothing_to_sync", **stats}

        # Check if data is chunked (multiple files)
        is_chunked = len(chunked_exports) > 1 or "" not in chunked_exports
        stats["chunked"] = is_chunked

        # 2. Ensure Gist exists
        if self.gist_id is None:
            self.gist_id = self._find_or_create_gist()
            stats["gist_id"] = self.gist_id

        # 3. Download current manifest
        manifest = self._download_manifest()

        # 4. Get old files to potentially delete (when switching from single to chunked)
        old_data_files = manifest.get_data_files(self.machine_name)

        # 5. Prepare file list and update manifest
        base_filename = f"usage_data_{self.machine_name}"
        data_files = []
        newest_date = None
        total_stats_records = 0

        for suffix, export_data in chunked_exports.items():
            filename = f"{base_filename}{suffix}.json"
            data_files.append(filename)

            # Track newest date
            data_newest = export_data.get("data_range", {}).get("newest")
            if data_newest and (newest_date is None or data_newest > newest_date):
                newest_date = data_newest

            # Track total records from statistics
            total_stats_records += export_data.get("statistics", {}).get("total_records", 0)

        # Update manifest with new data
        manifest.add_machine(
            machine_name=self.machine_name,
            current_file=data_files[0],  # Primary file for backwards compatibility
            total_records=total_stats_records,
            last_record_date=newest_date,
            data_files=data_files if is_chunked else None,
        )

        # 6. Conflict detection and auto-merge (unless skipped)
        if not skip_conflict_check:
            try:
                manifest = self._detect_and_resolve_conflict(manifest)
                if stats.get("conflicts_resolved"):
                    stats["conflicts_resolved"] = True
            except ConflictError:
                raise

        # 7. Create backup if requested (only for primary file)
        if create_backup and old_data_files:
            backup_created = self._create_backup(old_data_files[0], manifest)
            stats["backup_created"] = backup_created

        # 8. Upload data files (one at a time to avoid API size limits)
        # GitHub Gist API has request size limits, so we upload each chunk separately
        uploaded_count = 0
        for suffix, export_data in chunked_exports.items():
            filename = f"{base_filename}{suffix}.json"
            file_content = json.dumps(export_data, indent=2, ensure_ascii=False)
            self.client.update_gist(self.gist_id, {filename: file_content})
            uploaded_count += 1

        # 9. Delete old files that are no longer needed
        # (e.g., when switching from single file to chunked)
        new_file_set = set(data_files)
        old_file_set = set(old_data_files) if old_data_files else set()
        files_to_delete = old_file_set - new_file_set

        if files_to_delete:
            self.client.update_gist(
                self.gist_id,
                {old_file: None for old_file in files_to_delete}
            )

        # 10. Upload manifest separately
        self.client.update_gist(self.gist_id, {Manifest.FILENAME: manifest.to_json()})
        stats["manifest_updated"] = True
        stats["files_uploaded"] = uploaded_count

        if files_to_delete:
            stats["old_files_deleted"] = len(files_to_delete)

        # 11. Clean up old backups
        old_backups = manifest.get_old_backups(self.machine_name)
        if old_backups:
            self._delete_old_backups(old_backups, manifest)
            stats["backups_deleted"] = len(old_backups)

        # 12. Update local sync metadata
        first_export = next(iter(chunked_exports.values()))
        self._update_local_sync_metadata(first_export["export_date"])

        stats["status"] = "success"
        return stats

    def pull(self, machines: Optional[list[str]] = None) -> dict[str, Any]:
        """
        Pull data from Gist to local database.

        Args:
            machines: List of machine names to pull (all if None)

        Returns:
            Dictionary with pull statistics

        Workflow:
            1. Download manifest from Gist
            2. Download usage data for requested machines
            3. Import to local database (with deduplication)
        """
        stats = {
            "machines_pulled": 0,
            "new_records": 0,
            "duplicate_records": 0,
            "errors": 0,
        }

        if self.gist_id is None:
            self.gist_id = self._find_or_create_gist()

        # 1. Download manifest
        manifest = self._download_manifest()

        # 2. Determine which machines to pull
        if machines is None:
            machines = manifest.list_machines()

        # 3. Download and import each machine's data
        for machine_name in machines:
            machine = manifest.get_machine(machine_name)
            if machine is None:
                print(f"Warning: Machine '{machine_name}' not found in manifest")
                continue

            # Get all data files for this machine (supports chunked exports)
            data_files = manifest.get_data_files(machine_name)

            if not data_files:
                print(f"Warning: No data files found for '{machine_name}'")
                continue

            # Get database path for this specific machine
            from src.storage.snapshot_db import get_storage_dir
            storage_dir = get_storage_dir()
            machine_db_path = storage_dir / f"usage_history_{machine_name}.db"

            machine_new = 0
            machine_dup = 0
            machine_errors = 0

            # Download and import each data file
            for data_file in data_files:
                try:
                    # Download JSON data
                    json_str = self.client.get_file_content(self.gist_id, data_file)
                    json_data = json.loads(json_str)

                    # Import to machine-specific database
                    import_stats = import_from_json(json_data, db_path=machine_db_path)

                    machine_new += import_stats["new_records"]
                    machine_dup += import_stats["duplicate_records"]
                    machine_errors += import_stats["errors"]

                except Exception as e:
                    print(f"Error pulling {data_file} for {machine_name}: {e}")
                    machine_errors += 1

            stats["new_records"] += machine_new
            stats["duplicate_records"] += machine_dup
            stats["errors"] += machine_errors
            stats["machines_pulled"] += 1

        stats["status"] = "success"
        return stats

    def status(self) -> dict[str, Any]:
        """
        Get synchronization status.

        Returns:
            Dictionary with status information
        """
        status = {
            "token_configured": self.token_manager.has_token(),
            "token_location": self.token_manager.get_storage_location(),
            "machine_name": self.machine_name,
            "gist_id": self.gist_id,
            "gist_url": None,
            "last_local_export": get_last_export_date(),
        }

        if status["token_configured"]:
            # Try to get Gist info
            try:
                if self.gist_id is None:
                    gist = self.client.find_gist_by_description(self.GIST_DESCRIPTION)
                    if gist:
                        self.gist_id = gist["id"]
                        status["gist_id"] = self.gist_id

                if self.gist_id:
                    gist = self.client.get_gist(self.gist_id)
                    status["gist_url"] = gist["html_url"]

                    # Get manifest info
                    manifest = self._download_manifest()
                    status["manifest"] = manifest.get_statistics()

                    # Get machine-specific info
                    machine = manifest.get_machine(self.machine_name)
                    if machine:
                        status["last_gist_sync"] = machine.get("last_sync")
                        status["total_records_in_gist"] = machine.get("total_records", 0)

            except Exception as e:
                status["error"] = str(e)

        return status

    def _find_or_create_gist(self) -> str:
        """
        Find existing Gist or create new one.

        Returns:
            Gist ID
        """
        # Try to find existing Gist
        gist = self.client.find_gist_by_description(self.GIST_DESCRIPTION)

        if gist:
            return gist["id"]

        # Create new Gist
        manifest = Manifest()
        files = {
            Manifest.FILENAME: manifest.to_json()
        }

        gist = self.client.create_gist(
            files=files,
            description=self.GIST_DESCRIPTION,
            public=False,  # Private by default
        )

        return gist["id"]

    def _download_manifest(self) -> Manifest:
        """
        Download manifest from Gist.

        Returns:
            Manifest instance
        """
        try:
            json_str = self.client.get_file_content(self.gist_id, Manifest.FILENAME)
            return Manifest.from_json(json_str)
        except Exception:
            # Manifest doesn't exist, create new one
            return Manifest()

    def _create_backup(self, current_filename: str, manifest: Manifest) -> bool:
        """
        Create backup of current data file.

        Args:
            current_filename: Current data filename
            manifest: Manifest instance

        Returns:
            True if backup created
        """
        try:
            # Download current file
            current_data = self.client.get_file_content(self.gist_id, current_filename)

            # Generate backup filename
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            backup_filename = current_filename.replace(".json", f"_backup_{today}.json")

            # Check if backup already exists
            try:
                self.client.get_file_content(self.gist_id, backup_filename)
                # Backup exists, don't create duplicate
                return False
            except Exception:
                pass

            # Upload backup
            self.client.update_gist(
                self.gist_id,
                {backup_filename: current_data}
            )

            # Update manifest
            manifest.add_backup(self.machine_name, backup_filename)

            return True

        except Exception as e:
            print(f"Warning: Could not create backup: {e}")
            return False

    def _delete_old_backups(self, backup_filenames: list[str], manifest: Manifest) -> None:
        """
        Delete old backup files from Gist.

        Args:
            backup_filenames: List of backup filenames to delete
            manifest: Manifest instance
        """
        if not backup_filenames:
            return

        # Delete files from Gist (set content to None)
        files_to_delete = {filename: None for filename in backup_filenames}

        try:
            self.client.update_gist(self.gist_id, files_to_delete)

            # Update manifest
            for filename in backup_filenames:
                manifest.remove_backup(self.machine_name, filename)

        except Exception as e:
            print(f"Warning: Could not delete old backups: {e}")

    def _update_local_sync_metadata(self, export_date: str) -> None:
        """
        Update local database sync metadata.

        Args:
            export_date: Export timestamp
        """
        import sqlite3
        from src.sync.json_export import get_current_machine_db_path

        # Use machine-specific database path (consistent with get_last_export_date)
        db_path = get_current_machine_db_path()
        if not db_path.exists():
            return

        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            cursor = conn.cursor()

            # Ensure sync_metadata table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Update last export date (store as YYYY-MM-DD for comparison with date column)
            # export_date is ISO format like "2024-11-28T10:30:00+00:00"
            # We extract just the date part for incremental export comparison
            export_date_only = export_date[:10] if len(export_date) >= 10 else export_date
            cursor.execute("""
                INSERT OR REPLACE INTO sync_metadata (key, value)
                VALUES ('last_gist_export_date', ?)
            """, (export_date_only,))

            # Update Gist ID
            if self.gist_id:
                cursor.execute("""
                    INSERT OR REPLACE INTO sync_metadata (key, value)
                    VALUES ('gist_id', ?)
                """, (self.gist_id,))

            conn.commit()

        finally:
            conn.close()

    def _detect_and_resolve_conflict(
        self,
        local_manifest: Manifest,
        retry_count: int = 0
    ) -> Manifest:
        """
        Detect conflicts with remote Gist and auto-merge if needed.

        Args:
            local_manifest: Local manifest prepared for push
            retry_count: Current retry attempt (0-indexed)

        Returns:
            Merged manifest ready for push

        Raises:
            ConflictError: If conflict cannot be resolved after max retries

        Strategy:
            1. Download latest manifest from Gist
            2. Compare timestamps
            3. If remote is newer, merge and retry
            4. Max retries: 3
        """
        MAX_RETRIES = 3

        # Download latest manifest from Gist
        try:
            remote_manifest = self._download_manifest()
        except Exception:
            # If download fails, proceed with local manifest
            # (might be first push or network issue)
            return local_manifest

        # Get timestamps for comparison
        local_timestamp = local_manifest.get_last_updated()
        remote_timestamp = remote_manifest.get_last_updated()

        # Check if remote is newer (conflict detected)
        if remote_manifest.is_newer_than(local_timestamp):
            if retry_count >= MAX_RETRIES:
                raise ConflictError(
                    f"Cannot auto-resolve conflict after {MAX_RETRIES} retries. "
                    "Remote Gist has newer changes from another device. "
                    "Run 'ccu gist pull' to sync latest data, then push again. "
                    "Or use 'ccu gist push --force' to override (may lose data)."
                )

            # Log conflict detection
            print(f"⚠️  Conflict detected: Gist has newer changes")
            print(f"   Local:  {local_timestamp}")
            print(f"   Remote: {remote_timestamp}")
            print(f"🔄 Auto-merging... (attempt {retry_count + 1}/{MAX_RETRIES})")

            # Merge manifests
            merged_manifest = local_manifest.merge_with(remote_manifest)

            # Retry with merged manifest
            return self._detect_and_resolve_conflict(merged_manifest, retry_count + 1)

        # No conflict or conflict resolved
        return local_manifest
