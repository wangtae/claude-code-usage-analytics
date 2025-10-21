# GitHub Gist Sync Guide

## Overview

GitHub Gist synchronization provides a secure, cloud-based backup of your Claude Code usage data with the following benefits:

- **Universal Access**: Works on any platform with GitHub account
- **Version Control**: Git-based history of all changes
- **Multi-Device Sync**: Automatic synchronization across all your machines
- **Automatic Backups**: Daily snapshots with 30-day retention
- **Safe Operations**: Never modifies original `~/.claude/` files

## Quick Start

### 1. Install Dependencies

```bash
pip install -e .
```

This installs `requests` and `keyring` (for secure token storage).

### 2. Create GitHub Personal Access Token

1. Go to https://github.com/settings/tokens
2. Click "Generate new token" → "Generate new token (classic)"
3. Set name: `claude-code-usage-analytics`
4. Select scope: ✓ **gist** (only this scope is needed)
5. Click "Generate token"
6. **Copy the token** (you won't see it again!)

### 3. Run Setup Wizard

```bash
ccu gist setup
```

The wizard will:
- Validate your GitHub token
- Securely store it (system keyring or encrypted file)
- Create initial Gist backup
- Test synchronization

## Commands

### Setup & Configuration

```bash
# Interactive setup wizard (recommended)
ccu gist setup

# Manual token configuration
ccu gist set-token <your-github-token>

# Check sync status
ccu gist status

# View detailed Gist information
ccu gist info
```

### Synchronization

```bash
# Push local data to Gist (incremental)
ccu gist push

# Push all data (not incremental)
ccu gist push --force

# Push without creating backup
ccu gist push --no-backup

# Pull data from Gist to local
ccu gist pull

# Pull specific machines only
ccu gist pull HOME-WT Laptop-WT
```

## How It Works

### Data Flow

```
┌─────────────────┐
│ ~/.claude/      │ (Original files - READ ONLY)
│ projects/*.jsonl│
└────────┬────────┘
         │ Export (read-only)
         ▼
┌─────────────────┐
│ Local SQLite DB │
│ usage_history_  │
│ {machine}.db    │
└────────┬────────┘
         │ Incremental Export (JSON)
         ▼
┌─────────────────┐
│ GitHub Gist     │
│ ├─ manifest.json│
│ ├─ usage_data_  │
│ │  HOME-WT.json │
│ └─ backups/     │
└─────────────────┘
         │ Pull & Import
         ▼
┌─────────────────┐
│ Other Machines  │
│ Merge & Dedupe  │
└─────────────────┘
```

### Incremental Sync

- **First push**: Exports all data
- **Subsequent pushes**: Only new records since last sync
- **Deduplication**: UNIQUE constraint on (session_id, message_uuid)
- **Safe**: Original files never modified

### Automatic Backups

- **Daily snapshots**: Created before first push each day
- **Retention**: 30 days (configurable in manifest.json)
- **Automatic cleanup**: Old backups deleted during push
- **Format**: `usage_data_{machine}_backup_YYYYMMDD.json`

## File Structure in Gist

```
manifest.json                          # Metadata for all machines
usage_data_HOME-WT.json                # Current data for HOME-WT
usage_data_Laptop-WT.json              # Current data for Laptop-WT
usage_data_HOME-WT_backup_20251021.json    # Backup from Oct 21
usage_data_HOME-WT_backup_20251020.json    # Backup from Oct 20
...
```

### manifest.json

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

## Security

### Token Storage

Priority (most secure → least secure):

1. **System Keyring** (recommended)
   - macOS: Keychain
   - Windows: Credential Manager
   - Linux: Secret Service (gnome-keyring, kwallet)

2. **Config File** (fallback)
   - Location: `~/.claude/gist_token.txt`
   - Permissions: `rw-------` (600)
   - ⚠️ Less secure, install `keyring` for better protection

3. **Environment Variable**
   - Set `GITHUB_GIST_TOKEN` in your shell
   - Highest priority, overrides stored token

### Check Token Location

```bash
ccu gist status
```

Output shows where token is stored:
```
GitHub Token
✓ Configured
Location: System keyring (SecretServiceKeyring)
```

### Private Gist

All Gists are **private by default**. Only you can access them.

## Use Cases

### Scenario 1: New Machine Setup

You formatted your machine or got a new computer:

```bash
# 1. Install ccu on new machine
pip install -e .

# 2. Configure GitHub token
ccu gist setup

# 3. Pull all data from Gist
ccu gist pull

# 4. Verify data
ccu
```

### Scenario 2: Daily Workflow

Use Claude Code normally. Sync happens automatically if you set up cron/scheduler:

```bash
# Add to crontab (sync every hour)
0 * * * * /path/to/ccu gist push --no-backup

# Or manually sync when needed
ccu gist push
```

### Scenario 3: Data Recovery

Your local database was corrupted:

```bash
# 1. Check available backups
ccu gist info

# 2. Pull all data (will merge with existing)
ccu gist pull

# 3. If needed, reset local DB first
ccu reset-db --force
ccu gist pull
```

### Scenario 4: Multiple Machines

You use Claude Code on 3 machines (Home Desktop, Laptop, Work PC):

```bash
# On each machine, run setup once
ccu gist setup

# Then sync whenever you want
ccu gist push   # Upload local data
ccu gist pull   # Download data from other machines

# View combined statistics in dashboard
ccu   # Press 'd' for devices view
```

## Troubleshooting

### "GitHub token not configured"

```bash
# Run setup wizard
ccu gist setup

# Or manually set token
ccu gist set-token <your-token>
```

### "Invalid token"

- Token may have expired or been revoked
- Check scopes: Must have **gist** scope
- Generate new token at https://github.com/settings/tokens

### "Rate limit exceeded"

GitHub API limits: 5000 requests/hour

```bash
# Check rate limit status
ccu gist status

# Wait or use --force less frequently
```

### "Duplicate records"

This is normal! Deduplication handles it automatically.

- Pull imports all data but skips duplicates
- Check statistics: `duplicate_records: 1234`

### Sync conflicts

**Last-write-wins** strategy:
- Gist always has the source of truth
- `pull` overwrites local data
- `push` overwrites Gist data

To avoid conflicts:
- Pull before push: `ccu gist pull && ccu gist push`

## Advanced

### Custom Backup Retention

Edit `manifest.json` in Gist:

```json
{
  "backup_retention_days": 60  // Keep for 60 days
}
```

Next push will apply new retention period.

### Export to JSON (offline)

```bash
# Export current machine's data
python3 -c "
from src.sync.json_export import save_json_export
from pathlib import Path
save_json_export(Path('export.json'))
"

# View export
cat export.json | jq .
```

### Import from JSON

```bash
# Import JSON file
python3 -c "
from src.sync.json_import import import_from_json_file
from pathlib import Path
stats = import_from_json_file(Path('export.json'))
print(f'Imported: {stats[\"new_records\"]} records')
"
```

### Manual Gist Operations

```python
from src.sync.gist_client import GistClient
from src.sync.token_manager import get_github_token

token = get_github_token()
client = GistClient(token)

# List all Gists
gists = client.list_gists()
for g in gists:
    print(f"{g['id']}: {g['description']}")

# Get file content
content = client.get_file_content("gist_id", "filename.json")
print(content)
```

## FAQ

**Q: Is my data safe?**
A: Yes. Gists are private by default, use HTTPS, and support 2FA.

**Q: What happens if I delete the Gist?**
A: Local data remains safe. Next push creates a new Gist.

**Q: Can I use public Gist?**
A: Not recommended. Your usage data would be public.

**Q: Does this replace OneDrive sync?**
A: You can use both! Gist is more reliable for SQLite files.

**Q: How much space does it use?**
A: ~1-5 MB per 100K records (JSON is compressed).

**Q: Can I sync to multiple Gists?**
A: No. One Gist per account. Use different GitHub accounts for separation.

## See Also

- [Design Document](./gist-sync-design.md) - Technical architecture
- [GitHub Gist API](https://docs.github.com/en/rest/gists)
- [Keyring Library](https://github.com/jaraco/keyring)
