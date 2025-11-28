"""
Migration v1.7.6: Manifest data_files format support.

This migration ensures compatibility with the new chunked export format
where manifests can have multiple data_files per machine.

Changes:
- Gist manifests now support data_files array field
- Backward compatible: existing current_file field still works
- No local database changes required
"""

from src.migrations.base import Migration, MigrationResult


class ManifestDataFilesMigration(Migration):
    """
    Migration for manifest data_files format.

    This is primarily a documentation/tracking migration.
    The actual manifest format update happens automatically during push.
    """

    version = "1.7.6"
    name = "Manifest data_files format"
    description = "Support chunked export with multiple data files per machine in Gist manifest"

    def check_required(self) -> bool:
        """
        Check if migration is needed.

        Always returns True on first run - the migration is mostly for tracking.
        """
        return True

    def up(self) -> MigrationResult:
        """
        Apply the migration.

        For this migration, no actual changes are needed.
        The manifest format update is handled automatically during gist push.
        We just record that we're now aware of the new format.
        """
        try:
            # No actual changes needed - manifest format is backward compatible
            # and updates happen automatically during push
            return MigrationResult(
                success=True,
                message="Manifest format now supports chunked data files"
            )
        except Exception as e:
            return MigrationResult(
                success=False,
                message="Migration failed",
                error=str(e)
            )

    def down(self) -> MigrationResult:
        """
        Rollback the migration.

        No rollback needed - the format is backward compatible.
        """
        return MigrationResult(
            success=True,
            message="Rollback not needed - format is backward compatible"
        )
