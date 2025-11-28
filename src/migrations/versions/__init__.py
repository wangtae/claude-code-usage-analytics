"""
Migration versions registry.

All migrations should be imported and added to ALL_MIGRATIONS list.
Migrations are run in version order (sorted semantically).
"""

from typing import Type

from src.migrations.base import Migration

# Import all migrations here
from src.migrations.versions.v1_7_6_manifest_data_files import ManifestDataFilesMigration

# List of all migration classes (order doesn't matter, sorted by version automatically)
ALL_MIGRATIONS: list[Type[Migration]] = [
    ManifestDataFilesMigration,
]
