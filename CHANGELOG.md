# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Improved OneDrive path detection with user confirmation in setup wizard
- Migration script (`scripts/migrate_to_onedrive.sh`) for moving databases to correct OneDrive location
- Setup wizard now prioritizes external drives (D:, E:, F:) over C: drive for OneDrive detection
- FAQ section in main README.md with 20+ common questions and answers
- Documentation for migration scripts in `scripts/README.md`
- OneDrive path confirmation dialog - wizard now asks users to confirm auto-detected paths
- Custom OneDrive path input option when auto-detection is incorrect

### Changed
- OneDrive detection priority changed to check `/mnt/d`, `/mnt/e`, `/mnt/f` before `/mnt/c` paths
- Scripts README examples now use generic usernames instead of personal information

### Fixed
- OneDrive detection now correctly identifies external drive mounts in WSL2
- Setup wizard allows users to confirm or override auto-detected OneDrive paths
- Removed personal information (usernames, machine names) from documentation examples

## [0.1.5] - 2025-10-13

### Added
- Added `--fast` flag to `stats` command for faster rendering (skips all updates, reads from database)

### Fixed
- Fixed missing limits updates in `stats` command - now automatically saves limits to database like other commands

## [0.1.4] - 2025-10-12

### Added
- Added `--anon` flag to `usage` command to anonymize project names (displays as project-001, project-002, etc., ranked by token usage)
- Added `PreCompact` hook support for audio notifications (plays sound before conversation compaction)
- Added multi-hook selection for `audio-tts` setup (choose between Notification, Stop, PreCompact, or combinations)
- Audio hook now supports three sounds: completion, permission requests, and conversation compaction

### Changed
- `audio-tts` hook now supports configurable hook types (Notification only by default, with 7 selection options)
- Audio hook setup now prompts for three sounds instead of two (added compaction sound)
- TTS hook script intelligently handles different hook types with appropriate messages
- Enhanced hook removal to properly clean up PreCompact hooks

### Fixed
- Fixed `AttributeError` in `--anon` flag where `total_tokens` was accessed incorrectly on UsageRecord objects

## [0.1.3] - 2025-10-12

### Fixed
- Fixed audio `Notification` hook format to properly trigger on permission requests (removed incorrect `matcher` field)
- Fixed missing limits data in heatmap exports - `usage` command now automatically saves limits to database
- Fixed double `claude` command execution - dashboard now uses cached limits from database instead of fetching live

### Changed
- Improved status messages to show three distinct steps: "Updating usage data", "Updating usage limits", "Preparing dashboard"
- Dashboard now displays limits from database after initial fetch, eliminating redundant API calls

### Added
- Added `get_latest_limits()` function to retrieve most recent limits from database
- Added `--fast` flag to `usage` command for faster dashboard rendering (skips all updates, reads directly from database)
- Added `--fast` flag to `export` command for faster exports (skips all updates, reads directly from database)
- Added database existence check for `--fast` mode with helpful error message
- Added timestamp warning when using `--fast` mode showing last database update date

## [0.1.2] - 2025-10-11

### Added
- Enhanced audio hook to support both `Stop` and `Notification` hooks
  - Completion sound: Plays when Claude finishes responding (`Stop` hook)
  - Permission sound: Plays when Claude requests permission (`Notification` hook)
- User now selects two different sounds during `setup-hooks audio` for better distinction
- Expanded macOS sound library from 5 to 10 sounds

### Changed
- Updated `claude-goblin setup-hooks audio` to prompt for two sounds instead of one
- Audio hook removal now cleans up both `Stop` and `Notification` hooks
- Updated documentation to reflect dual audio notification capability

### Fixed
- Fixed `NameError: name 'fast' is not defined` in usage command when `--fast` flag was used

## [0.1.1] - 2025-10-11

### Fixed
- **CRITICAL**: Fixed data loss bug in "full" storage mode where `daily_snapshots` were being recalculated from scratch, causing historical data to be lost when JSONL files aged out (30-day window)
- Now only updates `daily_snapshots` for dates that currently have records, preserving all historical data forever

### Changed
- Migrated CLI from manual `sys.argv` parsing to `typer` for better UX and automatic help generation
- Updated command syntax: `claude-goblin <command>` instead of `claude-goblin --<command>`
  - Old: `claude-goblin --usage` → New: `claude-goblin usage`
  - Old: `claude-goblin --stats` → New: `claude-goblin stats`
  - Old: `claude-goblin --export` → New: `claude-goblin export`
  - All other commands follow the same pattern
- Updated hooks to use new command syntax (`claude-goblin update-usage` instead of `claude-goblin --update-usage`)
- Improved help messages with examples and better descriptions

### Added
- Added `typer>=0.9.0` as a dependency for CLI framework
- Added backward compatibility in hooks to recognize both old and new command syntax

## [0.1.0] - 2025-10-10

### Added
- Initial release
- Usage tracking and analytics for Claude Code
- GitHub-style activity heatmap visualization
- TUI dashboard with real-time stats
- Cost analysis and API pricing comparison
- Export functionality (PNG/SVG)
- Hook integration for automatic tracking
- macOS menu bar app for usage monitoring
- Support for both "aggregate" and "full" storage modes
- Historical database preservation (SQLite)
- Text analysis (politeness markers, phrase counting)
- Model and project breakdown statistics
