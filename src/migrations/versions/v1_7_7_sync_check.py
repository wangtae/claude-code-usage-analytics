"""
Migration v1.7.7: Automatic sync check on version upgrade.

When upgrading to a new version, check if local data is out of sync
with Gist and offer to pull missing data.
"""

import sqlite3
from pathlib import Path
from typing import Optional

from rich.console import Console

from src.migrations.base import Migration, MigrationResult


class SyncCheckMigration(Migration):
    """
    Migration to check and fix data synchronization issues.

    On version upgrade:
    1. Check if Gist sync is configured
    2. Compare local record counts with Gist manifest
    3. If local is missing data, automatically pull
    """

    version = "1.7.7"
    name = "Auto sync check"
    description = "Check and fix data synchronization with Gist on upgrade"

    def check_required(self) -> bool:
        """Always run this check on upgrade."""
        return True

    def up(self) -> MigrationResult:
        """
        Check sync status and pull missing data if needed.
        """
        try:
            from src.sync.token_manager import TokenManager

            # Check if Gist sync is configured
            token_manager = TokenManager()
            if not token_manager.has_token():
                return MigrationResult(
                    success=True,
                    message="Gist sync not configured, skipping sync check"
                )

            # Try to check and sync
            sync_result = self._check_and_sync()
            return sync_result

        except Exception as e:
            # Don't fail the migration, just report
            return MigrationResult(
                success=True,
                message=f"Sync check skipped: {e}"
            )

    def _check_and_sync(self) -> MigrationResult:
        """
        Check Gist manifest and sync missing data.
        """
        from src.sync.sync_manager import SyncManager
        from src.config.user_config import get_machine_name
        from src.storage.snapshot_db import get_storage_dir

        console = Console()
        sync_manager = SyncManager()
        current_machine = get_machine_name() or "Unknown"
        storage_dir = get_storage_dir()

        try:
            # Find or get Gist
            if sync_manager.gist_id is None:
                gist = sync_manager.client.find_gist_by_description(sync_manager.GIST_DESCRIPTION)
                if not gist:
                    return MigrationResult(
                        success=True,
                        message="No Gist found, skipping sync check"
                    )
                sync_manager.gist_id = gist["id"]

            # Download manifest
            manifest = sync_manager._download_manifest()
            machines = manifest.list_machines()

            if not machines:
                return MigrationResult(
                    success=True,
                    message="No machines in Gist manifest"
                )

            # Check each machine's local data vs manifest
            machines_to_sync = []

            for machine_name in machines:
                machine_info = manifest.get_machine(machine_name)
                if not machine_info:
                    continue

                gist_records = machine_info.get("total_records", 0)
                if gist_records == 0:
                    continue

                # Check local database for this machine
                local_db_path = storage_dir / f"usage_history_{machine_name}.db"

                if not local_db_path.exists():
                    # No local data for this machine
                    machines_to_sync.append({
                        "name": machine_name,
                        "local": 0,
                        "gist": gist_records,
                        "missing": gist_records,
                    })
                else:
                    # Count local records
                    try:
                        conn = sqlite3.connect(f"file:{local_db_path}?mode=ro", uri=True, timeout=5.0)
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM usage_records")
                        local_records = cursor.fetchone()[0]
                        conn.close()

                        # If local has significantly fewer records, needs sync
                        if local_records < gist_records * 0.9:  # 10% tolerance
                            machines_to_sync.append({
                                "name": machine_name,
                                "local": local_records,
                                "gist": gist_records,
                                "missing": gist_records - local_records,
                            })
                    except Exception:
                        pass

            if not machines_to_sync:
                return MigrationResult(
                    success=True,
                    message="All machines in sync"
                )

            # Show what needs syncing
            console.print("\n[yellow]⚠ Data sync needed:[/yellow]")
            for m in machines_to_sync:
                console.print(f"  {m['name']}: local {m['local']:,} / gist {m['gist']:,} ({m['missing']:,} missing)")

            # Auto-pull missing data
            console.print("\n[cyan]Pulling missing data from Gist...[/cyan]")

            stats = sync_manager.pull()

            if stats.get("new_records", 0) > 0:
                return MigrationResult(
                    success=True,
                    message=f"Synced {stats['new_records']:,} records from {stats['machines_pulled']} machine(s)"
                )
            else:
                return MigrationResult(
                    success=True,
                    message="Sync check completed, no new records"
                )

        except Exception as e:
            return MigrationResult(
                success=True,
                message=f"Sync check error: {e}"
            )

    def down(self) -> MigrationResult:
        """No rollback needed."""
        return MigrationResult(
            success=True,
            message="No rollback needed for sync check"
        )
