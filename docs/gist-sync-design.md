# Git Gist Sync Design

## Overview

Safe, incremental synchronization of Claude Code usage data to GitHub Gist with automatic backup versioning.

## Architecture

```
Local Storage (Read-Only)          Git Gist (Cloud Backup)
├── ~/.claude/projects/*.jsonl  →  ├── usage_data_{machine}.json (current)
└── usage_history_{machine}.db  →  ├── usage_data_{machine}_backup_YYYYMMDD.json
                                    └── manifest.json (metadata)
```

## Safety Principles

1. **Never Modify Source**: Local `~/.claude/` files are READ-ONLY
2. **Incremental Export**: Only export new records since last sync
3. **Automatic Backups**: Daily snapshots in Gist before overwriting
4. **Version Control**: Git history preserves all changes
5. **Restore Safety**: Restore only to designated directories, never overwrite `~/.claude/`

## JSON Schema

### 1. Manifest File (`manifest.json`)

```json
{
  "version": "1.0",
  "last_updated": "2025-10-21T20:45:00Z",
  "machines": [
    {
      "machine_name": "HOME-WT",
      "last_sync": "2025-10-21T20:45:00Z",
      "last_record_date": "2025-10-21",
      "total_records": 150000,
      "current_file": "usage_data_HOME-WT.json",
      "backups": [
        "usage_data_HOME-WT_backup_20251021.json",
        "usage_data_HOME-WT_backup_20251020.json"
      ]
    }
  ],
  "backup_retention_days": 30
}
```

### 2. Usage Data File (`usage_data_{machine}.json`)

```json
{
  "machine_name": "HOME-WT",
  "export_date": "2025-10-21T20:45:00Z",
  "data_range": {
    "oldest": "2024-01-01",
    "newest": "2025-10-21"
  },
  "records": [
    {
      "session_id": "abc123",
      "message_uuid": "def456",
      "timestamp": "2025-10-21T10:30:00Z",
      "model": "claude-sonnet-4",
      "total_tokens": 5000,
      "input_tokens": 3000,
      "output_tokens": 2000,
      "cache_creation_tokens": 0,
      "cache_read_tokens": 0,
      "folder": "project-name",
      "git_branch": "main",
      "version": "0.9.0"
    }
  ],
  "statistics": {
    "total_records": 150000,
    "total_sessions": 5000,
    "total_tokens": 1471795741,
    "total_cost": 1234.56
  }
}
```

## Sync Workflow

### Push (Local → Gist)

```
1. Read local DB (READ-ONLY, no locks)
2. Get last sync timestamp from manifest
3. Export only new records (incremental)
4. Create backup of current Gist file (if exists)
5. Update manifest.json
6. Push to Gist via API
7. Update local sync metadata
```

### Pull (Gist → Local)

```
1. Download manifest.json
2. Download usage_data_{machine}.json for all machines
3. Import to local database (~/.claude/usage/ NOT ~/.claude/)
4. Merge with existing data (deduplication by session_id + message_uuid)
5. Rebuild aggregated statistics
```

### Automatic Backup Rotation

```
Daily (on first sync of the day):
1. Copy current usage_data_{machine}.json → usage_data_{machine}_backup_YYYYMMDD.json
2. Update manifest.backups array
3. Delete backups older than retention_days (default: 30)
```

## Implementation Plan

### Phase 1: Export/Import (Non-destructive)

- [ ] JSON export from SQLite (read-only)
- [ ] JSON import to local DB (not ~/.claude/)
- [ ] Deduplication logic
- [ ] Statistics recalculation

### Phase 2: Gist Integration

- [ ] GitHub API client (PyGithub or requests)
- [ ] Token management (keyring library)
- [ ] Manifest CRUD operations
- [ ] File upload/download

### Phase 3: Sync Logic

- [ ] Incremental sync detection
- [ ] Backup rotation
- [ ] Conflict resolution (last-write-wins)
- [ ] Progress reporting

### Phase 4: CLI Commands

```bash
# Setup
ccu gist setup                    # Interactive setup wizard
ccu gist set-token <token>        # Set GitHub token

# Sync
ccu gist push                     # Upload local data to Gist
ccu gist pull                     # Download Gist data to local
ccu gist sync                     # Bidirectional sync
ccu gist auto-sync on|off         # Enable/disable auto-sync

# Backup
ccu gist backup create            # Manual backup
ccu gist backup list              # List all backups
ccu gist backup restore <date>    # Restore from backup

# Info
ccu gist status                   # Show sync status
ccu gist info                     # Show Gist info
```

## Security

1. **Token Storage**: Use `keyring` library (system credential manager)
2. **Private Gist**: Default to private visibility
3. **No Sensitive Data**: Exclude message content by default
4. **Rate Limiting**: Respect GitHub API limits (5000/hour)

## Error Handling

1. **Network Errors**: Retry with exponential backoff
2. **API Errors**: Clear error messages with recovery steps
3. **Conflict**: Prefer cloud data, warn user
4. **Corruption**: Validate JSON schema before import

## Testing Strategy

1. **Unit Tests**: Export/import logic
2. **Integration Tests**: Gist API mocking
3. **Manual Tests**: Multi-machine sync scenarios
4. **Safety Tests**: Verify ~/.claude/ never modified

## Concurrency Control

### Problem

Multiple devices pushing simultaneously can cause:
- **Last-write-wins**: Later push overwrites earlier push (data loss)
- **Manifest inconsistency**: Different devices see different manifest versions
- **Backup file collisions**: Same-day backups from different devices conflict

### Solution Strategy

#### 1. Per-Machine File Separation
- Each device has its own data file: `usage_data_{machine_name}.json`
- Reduces direct conflicts (machines don't overwrite each other's data)
- **But**: Shared `manifest.json` still has conflict potential

#### 2. Timestamp-Based Conflict Detection
- Every manifest has `last_updated` timestamp (ISO 8601)
- Before push: Compare local vs. remote manifest timestamps
- If remote is newer → Conflict detected

#### 3. Automatic Merge Strategy
When conflict detected:
1. Download latest manifest from Gist
2. Merge local and remote manifests:
   - Per machine: Keep entry with most recent `last_sync`
   - Backups: Combine lists (deduplicate)
   - Timestamp: Use the newer `last_updated`
3. Retry push with merged manifest
4. Maximum 3 retry attempts

#### 4. Force Override Option
- `ccu gist push --force` skips conflict detection
- Directly overwrites remote manifest
- **Warning**: May lose changes from other devices
- Use only when conflict resolution fails

### Workflow Examples

#### Normal Case (No Conflict)
```
PC-A: push → check timestamp → no conflict → upload → success
```

#### Conflict Detected & Auto-Resolved
```
PC-A: prepare manifest (timestamp T1)
PC-B: push at T2 (T2 > T1)
PC-A: push → detect conflict → download T2 → merge → retry → success
```

#### Max Retries Exceeded
```
PC-A: push → conflict → retry 1 → conflict → retry 2 → conflict → retry 3 → FAIL
User: run 'ccu gist pull' → 'ccu gist push'
```

### Code Implementation

**Manifest Class** (`src/sync/manifest.py`):
- `get_last_updated()` - Returns manifest timestamp
- `is_newer_than(timestamp)` - Compares timestamps
- `merge_with(other_manifest)` - Merges two manifests

**SyncManager** (`src/sync/sync_manager.py`):
- `_detect_and_resolve_conflict(manifest, retry_count)` - Main conflict resolution
- `push(skip_conflict_check=False)` - Integrated conflict detection

**Exceptions** (`src/sync/exceptions.py`):
- `ConflictError` - Raised when auto-merge fails after max retries

### User-Facing Messages

**During Push (Conflict Detected)**:
```
Pushing to Gist...
⚠️  Conflict detected: Gist has newer changes
   Local:  2025-10-22T10:30:00Z
   Remote: 2025-10-22T10:31:00Z
🔄 Auto-merging... (attempt 1/3)
✓ Done
✓ Conflicts auto-resolved via merge
```

**Conflict Resolution Failed**:
```
✗ Conflict Error

Cannot auto-resolve conflict after 3 retries.
Remote Gist has newer changes from another device.
Run 'ccu gist pull' to sync latest data, then push again.
Or use 'ccu gist push --force' to override (may lose data).

Suggestions:
  1. Run ccu gist pull to sync latest data, then push again
  2. Or use ccu gist push --force to override (may lose data)
```

### Limitations

1. **Not Atomic**: GitHub Gist API doesn't support atomic compare-and-swap
2. **Race Condition Window**: Small window between timestamp check and upload
3. **Manual Intervention**: Very rare cases may require manual `pull` + `push`

### Future Improvements

- Exponential backoff between retries
- Smarter merge strategies (per-record deduplication)
- Conflict resolution UI for manual decision-making
