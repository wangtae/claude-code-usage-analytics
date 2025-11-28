"""
Manifest management for Gist synchronization.

The manifest tracks all machines, their sync status, and backup files.
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Optional


class Manifest:
    """
    Gist synchronization manifest.

    Tracks all synced machines and their metadata.
    """

    VERSION = "1.0"
    FILENAME = "manifest.json"

    def __init__(self, data: Optional[dict[str, Any]] = None):
        """
        Initialize manifest.

        Args:
            data: Existing manifest data (creates new if None)
        """
        if data is None:
            self.data = self._create_empty()
        else:
            self.data = data
            self._validate()

    def _create_empty(self) -> dict[str, Any]:
        """Create empty manifest structure."""
        return {
            "version": self.VERSION,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "machines": [],
            "backup_retention_days": 0,  # Backup disabled by default (v1.7.9)
        }

    def _validate(self) -> None:
        """
        Validate manifest structure.

        Raises:
            ValueError: If manifest is invalid
        """
        required_fields = ["version", "last_updated", "machines"]
        for field in required_fields:
            if field not in self.data:
                raise ValueError(f"Invalid manifest: missing field '{field}'")

    def add_machine(
        self,
        machine_name: str,
        current_file: str,
        total_records: int = 0,
        last_record_date: Optional[str] = None,
        data_files: Optional[list[str]] = None,
    ) -> None:
        """
        Add or update machine entry.

        Args:
            machine_name: Machine identifier
            current_file: Current data filename (for backwards compatibility)
            total_records: Total number of records
            last_record_date: Date of last record (YYYY-MM-DD)
            data_files: List of data filenames (for chunked export)
        """
        # Get existing machine entry to preserve backups
        existing = self.get_machine(machine_name)
        existing_backups = existing.get("backups", []) if existing else []

        # Remove existing entry
        self.data["machines"] = [
            m for m in self.data["machines"]
            if m["machine_name"] != machine_name
        ]

        # Build new entry
        entry = {
            "machine_name": machine_name,
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "last_record_date": last_record_date,
            "total_records": total_records,
            "current_file": current_file,
            "backups": existing_backups,
        }

        # Add data_files if provided (for chunked export)
        if data_files:
            entry["data_files"] = data_files

        self.data["machines"].append(entry)
        self.data["last_updated"] = datetime.now(timezone.utc).isoformat()

    def get_data_files(self, machine_name: str) -> list[str]:
        """
        Get list of data files for a machine.

        Returns data_files if available, otherwise [current_file].

        Args:
            machine_name: Machine identifier

        Returns:
            List of data filenames
        """
        machine = self.get_machine(machine_name)
        if machine is None:
            return []

        # Use data_files if available, otherwise fall back to current_file
        if "data_files" in machine and machine["data_files"]:
            return machine["data_files"]
        elif "current_file" in machine:
            return [machine["current_file"]]
        return []

    def get_machine(self, machine_name: str) -> Optional[dict[str, Any]]:
        """
        Get machine entry by name.

        Args:
            machine_name: Machine identifier

        Returns:
            Machine data or None if not found
        """
        for machine in self.data["machines"]:
            if machine["machine_name"] == machine_name:
                return machine
        return None

    def add_backup(self, machine_name: str, backup_filename: str) -> None:
        """
        Add backup file to machine entry.

        Args:
            machine_name: Machine identifier
            backup_filename: Backup filename
        """
        machine = self.get_machine(machine_name)
        if machine is None:
            raise ValueError(f"Machine '{machine_name}' not found in manifest")

        if "backups" not in machine:
            machine["backups"] = []

        # Add backup if not already present
        if backup_filename not in machine["backups"]:
            machine["backups"].insert(0, backup_filename)  # Most recent first

        self.data["last_updated"] = datetime.now(timezone.utc).isoformat()

    def get_old_backups(
        self,
        machine_name: str,
        retention_days: Optional[int] = None,
    ) -> list[str]:
        """
        Get list of backup files older than retention period.

        Args:
            machine_name: Machine identifier
            retention_days: Retention period (uses manifest default if None)

        Returns:
            List of backup filenames to delete
        """
        if retention_days is None:
            retention_days = self.data.get("backup_retention_days", 30)

        machine = self.get_machine(machine_name)
        if machine is None or "backups" not in machine:
            return []

        # Parse backup dates from filenames (format: usage_data_MACHINE_backup_YYYYMMDD.json)
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
        old_backups = []

        for backup in machine["backups"]:
            try:
                # Extract date from filename
                date_str = backup.split("_backup_")[1].replace(".json", "")
                backup_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)

                if backup_date < cutoff_date:
                    old_backups.append(backup)
            except (IndexError, ValueError):
                # Invalid filename format, skip
                continue

        return old_backups

    def remove_backup(self, machine_name: str, backup_filename: str) -> None:
        """
        Remove backup from machine entry.

        Args:
            machine_name: Machine identifier
            backup_filename: Backup filename to remove
        """
        machine = self.get_machine(machine_name)
        if machine is None or "backups" not in machine:
            return

        machine["backups"] = [b for b in machine["backups"] if b != backup_filename]
        self.data["last_updated"] = datetime.now(timezone.utc).isoformat()

    def get_last_sync_date(self, machine_name: str) -> Optional[str]:
        """
        Get last sync date for machine.

        Args:
            machine_name: Machine identifier

        Returns:
            ISO timestamp of last sync or None
        """
        machine = self.get_machine(machine_name)
        return machine["last_sync"] if machine else None

    def get_last_record_date(self, machine_name: str) -> Optional[str]:
        """
        Get date of last record for machine.

        Args:
            machine_name: Machine identifier

        Returns:
            Date string (YYYY-MM-DD) or None
        """
        machine = self.get_machine(machine_name)
        return machine.get("last_record_date") if machine else None

    def list_machines(self) -> list[str]:
        """
        Get list of all machine names.

        Returns:
            List of machine names
        """
        return [m["machine_name"] for m in self.data["machines"]]

    def to_json(self, pretty: bool = True) -> str:
        """
        Convert manifest to JSON string.

        Args:
            pretty: Pretty-print JSON

        Returns:
            JSON string
        """
        if pretty:
            return json.dumps(self.data, indent=2, ensure_ascii=False)
        else:
            return json.dumps(self.data, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "Manifest":
        """
        Create manifest from JSON string.

        Args:
            json_str: JSON string

        Returns:
            Manifest instance

        Raises:
            ValueError: If JSON is invalid
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

        return cls(data)

    def get_statistics(self) -> dict[str, Any]:
        """
        Get summary statistics from manifest.

        Returns:
            Dictionary with total machines, records, etc.
        """
        total_machines = len(self.data["machines"])
        total_records = sum(m.get("total_records", 0) for m in self.data["machines"])
        total_backups = sum(len(m.get("backups", [])) for m in self.data["machines"])

        # Find oldest and newest sync dates
        sync_dates = [
            datetime.fromisoformat(m["last_sync"].replace("Z", "+00:00"))
            for m in self.data["machines"]
            if "last_sync" in m
        ]
        oldest_sync = min(sync_dates).isoformat() if sync_dates else None
        newest_sync = max(sync_dates).isoformat() if sync_dates else None

        return {
            "total_machines": total_machines,
            "total_records": total_records,
            "total_backups": total_backups,
            "oldest_sync": oldest_sync,
            "newest_sync": newest_sync,
            "retention_days": self.data.get("backup_retention_days", 30),
        }

    def get_last_updated(self) -> str:
        """
        Get the last_updated timestamp of this manifest.

        Returns:
            ISO timestamp string
        """
        return self.data.get("last_updated", datetime.now(timezone.utc).isoformat())

    def is_newer_than(self, timestamp: str) -> bool:
        """
        Check if this manifest is newer than the given timestamp.

        Args:
            timestamp: ISO timestamp string to compare against

        Returns:
            True if this manifest is newer
        """
        try:
            manifest_time = datetime.fromisoformat(self.data["last_updated"].replace("Z", "+00:00"))
            compare_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            return manifest_time > compare_time
        except (ValueError, KeyError):
            # If timestamps are invalid, assume not newer
            return False

    def merge_with(self, other: "Manifest") -> "Manifest":
        """
        Merge this manifest with another, preferring newer data.

        Strategy:
        - For each machine, keep the entry with the most recent last_sync
        - Combine backup lists (deduplicate)
        - Use the newer last_updated timestamp

        Args:
            other: Another Manifest instance to merge with

        Returns:
            New Manifest instance with merged data
        """
        merged_data = self._create_empty()

        # Merge machines from both manifests
        all_machine_names = set(self.list_machines()) | set(other.list_machines())

        for machine_name in all_machine_names:
            self_machine = self.get_machine(machine_name)
            other_machine = other.get_machine(machine_name)

            if self_machine and other_machine:
                # Both have this machine - keep the newer one
                self_sync = datetime.fromisoformat(self_machine["last_sync"].replace("Z", "+00:00"))
                other_sync = datetime.fromisoformat(other_machine["last_sync"].replace("Z", "+00:00"))

                if self_sync >= other_sync:
                    merged_machine = self_machine.copy()
                else:
                    merged_machine = other_machine.copy()

                # Combine backups from both (deduplicate)
                self_backups = set(self_machine.get("backups", []))
                other_backups = set(other_machine.get("backups", []))
                merged_machine["backups"] = list(self_backups | other_backups)

            elif self_machine:
                merged_machine = self_machine.copy()
            else:
                merged_machine = other_machine.copy()

            merged_data["machines"].append(merged_machine)

        # Use the newer last_updated timestamp
        self_updated = datetime.fromisoformat(self.data["last_updated"].replace("Z", "+00:00"))
        other_updated = datetime.fromisoformat(other.data["last_updated"].replace("Z", "+00:00"))
        merged_data["last_updated"] = max(self_updated, other_updated).isoformat()

        # Preserve backup retention setting
        merged_data["backup_retention_days"] = self.data.get("backup_retention_days", 30)

        return Manifest(merged_data)
