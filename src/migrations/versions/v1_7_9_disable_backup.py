"""
Migration v1.7.9: Disable backup functionality.

This migration cleans up existing backup files from Gist to free up space.
Going forward, backups are disabled by default to prevent hitting the 100MB Gist limit.

Changes:
- Delete all backup files from Gist
- Update manifest backup_retention_days to 0
- Clear backup lists in manifest
"""

from rich.console import Console

from src.migrations.base import Migration, MigrationResult


class DisableBackupMigration(Migration):
    """
    Migration to disable backup functionality and clean up existing backups.

    This is a one-time cleanup to free Gist space before implementing multi-Gist support.
    """

    version = "1.7.9"
    name = "Disable backup"
    description = "Clean up existing backup files from Gist and disable backup feature"

    def check_required(self) -> bool:
        """Always run this migration once."""
        return True

    def up(self) -> MigrationResult:
        """
        Clean up existing backup files from Gist.
        """
        try:
            from src.sync.token_manager import TokenManager

            # Check if Gist sync is configured
            token_manager = TokenManager()
            if not token_manager.has_token():
                return MigrationResult(
                    success=True,
                    message="Gist sync not configured, skipping backup cleanup"
                )

            # Clean up backups
            cleanup_result = self._cleanup_backups()
            return cleanup_result

        except Exception as e:
            # Don't fail the migration, just report
            return MigrationResult(
                success=True,
                message=f"Backup cleanup skipped: {e}"
            )

    def _cleanup_backups(self) -> MigrationResult:
        """
        Delete all backup files from Gist and update manifest.
        """
        from src.sync.sync_manager import SyncManager
        from src.sync.manifest import Manifest

        console = Console()
        sync_manager = SyncManager()
        deleted_count = 0

        try:
            # Find or get Gist
            if sync_manager.gist_id is None:
                gist = sync_manager.client.find_gist_by_description(sync_manager.GIST_DESCRIPTION)
                if not gist:
                    return MigrationResult(
                        success=True,
                        message="No Gist found, skipping backup cleanup"
                    )
                sync_manager.gist_id = gist["id"]

            # Get Gist files
            gist = sync_manager.client.get_gist(sync_manager.gist_id)
            files = gist.get("files", {})

            # Find backup files (format: usage_data_MACHINE_backup_YYYYMMDD.json)
            backup_files = [
                filename for filename in files.keys()
                if "_backup_" in filename and filename.endswith(".json")
            ]

            if not backup_files:
                return MigrationResult(
                    success=True,
                    message="No backup files found in Gist"
                )

            console.print(f"\n[yellow]⚠ Found {len(backup_files)} backup file(s) to delete[/yellow]")

            # Delete backup files
            files_to_delete = {filename: None for filename in backup_files}
            sync_manager.client.update_gist(sync_manager.gist_id, files_to_delete)
            deleted_count = len(backup_files)

            # Update manifest to clear backup lists and set retention to 0
            try:
                manifest = sync_manager._download_manifest()

                # Clear all backup lists
                for machine in manifest.data.get("machines", []):
                    machine["backups"] = []

                # Set backup retention to 0
                manifest.data["backup_retention_days"] = 0

                # Upload updated manifest
                sync_manager.client.update_gist(
                    sync_manager.gist_id,
                    {Manifest.FILENAME: manifest.to_json()}
                )
            except Exception as e:
                console.print(f"[yellow]Warning: Could not update manifest: {e}[/yellow]")

            return MigrationResult(
                success=True,
                message=f"Deleted {deleted_count} backup file(s) from Gist"
            )

        except Exception as e:
            return MigrationResult(
                success=True,  # Don't fail migration
                message=f"Backup cleanup error: {e}"
            )

    def down(self) -> MigrationResult:
        """No rollback - backups are gone."""
        return MigrationResult(
            success=True,
            message="No rollback needed - backup cleanup is one-way"
        )
