# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.8.2] - 2025-11-29

### Added
- **CLAUDE.md documentation improvements**: Comprehensive AI-assisted development guide
  - Data robustness principles (7 core principles)
  - Migration system guide with full code examples
  - Version release process with checklist
- **Version documentation**: Created docs/versions/1.8.0.md and 1.8.1.md

### Documentation
- Enhanced CLAUDE.md for better AI context understanding
- Added data source hierarchy diagram
- Added implementation checklist for new features

## [1.8.1] - 2025-11-29

### Fixed
- **--force flag behavior**: Now triggers full export + conflict skip as intended
  - Previously only skipped conflict check, keeping incremental export
  - Fixed: `force=export_all or force` ensures full data export

### Technical Details
- Modified `src/commands/gist_cmd.py`: push() function parameter mapping fixed
- Tested: 107,168 records exported successfully on HOME-WT

## [1.8.0] - 2025-11-29

### Added
- **daily_snapshots sync support**: Full historical data now included in Gist sync
  - `export_to_json()`: Exports daily_snapshots table data
  - `import_from_json()`: Imports daily_snapshots with UPSERT (INSERT OR REPLACE)
  - Chunked export: daily_snapshots included in first chunk only

### Fixed
- **Data loss on device format**: Previously only `usage_records` (recent data) was synced
  - Now `daily_snapshots` (full historical aggregates) are also synced
  - Device format → gist pull now restores complete history

### Technical Details
- Modified `src/sync/json_export.py`: Added daily_snapshots to export
- Modified `src/sync/json_import.py`: Added daily_snapshots import with UPSERT
- Import stats now include `new_snapshots` and `updated_snapshots` counts

## [1.2.4] - 2025-10-17

### Changed
- **README.md Title**: Updated from "Multi devices" to "Multi-Device Aggregation"
  - Clarifies that the tool aggregates (combines/sums) usage data from multiple devices
  - Better communicates the value proposition: total usage across all PCs
- **Devices Mode GIF**: Regenerated with improved height (1100px)
  - Better visibility of device statistics table
  - Complete display without scrolling
- **Heatmap Mode GIF**: Regenerated with updated settings
- **GIF Cache Management**: Added cache busting parameter (`?1`) to devices mode GIF URL

### Technical Details
- Modified `docs/gifs/08-devices-mode.tape`: Height increased from 830px to 1100px
- Regenerated `docs/images/07-heatmap-mode.gif` with current VHS settings
- Regenerated `docs/images/08-devices-mode.gif` with new height setting
- Added comprehensive version documentation in `docs/versions/1.2.4.md`

### Documentation
- Improved clarity of multi-device aggregation feature in README title
- Enhanced visual demonstrations with optimized GIF dimensions
- Better first impression for new users

## [1.2.3] - 2025-10-17

### Added
- **Interactive GIF Demonstrations**: Complete visual documentation for all view modes
  - 00-setup.gif: First-time configuration wizard with database location selection
  - 01-usage-mode.gif: Real-time limits, session tracking, and cost estimation
  - 02-weekly-mode.gif: Daily breakdown with drill-down navigation (1-7 keys)
  - 05-monthly-mode.gif: Monthly project statistics with time navigation (<, >)
  - 06-yearly-mode.gif: Annual overview with monthly/weekly breakdowns
  - 07-heatmap-mode.gif: GitHub-style activity visualization with year navigation
  - 08-devices-mode.gif: Multi-PC usage breakdown with period cycling
  - 09-settings-mode.gif: Configuration management and customization
- **Heatmap Year Navigation**: Navigate through historical years with `<` and `>` keys
  - Consistent navigation pattern with Weekly/Monthly/Yearly views
  - Footer displays current year being viewed
  - Year offset tracking with view_mode_ref dictionary
- Comprehensive version documentation in `docs/versions/1.2.3.md`

### Changed
- **README.md Screenshots Section**: Replaced all PNG screenshots with VHS-generated GIF files
  - Added interactive demonstrations showing real-time keyboard navigation
  - Updated descriptions with keyboard shortcut details
  - Reorganized with proper numbering (0-9)
  - Removed unused sections (03-weekly-daily-detail, 04-weekly-message-detail)
- **Heatmap View Footer**: Enhanced with year navigation instructions
  - Shows current year being viewed
  - Displays navigation key hints (<, >)

### Fixed
- VHS tape script syntax errors preventing GIF generation
  - Corrected lowercase `type` to uppercase `Type`
  - Fixed standalone number commands (e.g., `1` → `Type "1"`)

### Technical Details
- Modified `src/visualization/dashboard.py`:
  - Lines 175-184: Added year offset support for Heatmap view
  - Lines 2901-2923: Enhanced footer with year navigation instructions
- Updated VHS tape scripts in `docs/gifs/*.tape`:
  - 00-setup.tape (renamed from 00-vscode-integration.tape)
  - 09-settings-mode.tape (renamed from 09-settings-menu.tape)
  - Fixed syntax in 01-usage-mode.tape, 02-weekly-mode.tape
- Generated new GIF files in `docs/images/`

## [1.2.2] - 2025-10-17

### Added
- **Data Synchronization Status Check**: New feature in settings menu to verify if local Claude Code source data (JSONL files) matches database records
  - Interactive sync check with `[i]` key in settings
  - Automatic status display with color-coded indicators (green for synced, yellow for issues)
  - Multi-scenario detection:
    - Synced: Timestamps within 1 second, record counts match
    - DB Outdated: Source has newer data with missing record count
    - Multi-PC Sync: Database has more records (another PC may have synced)
    - Integrity Issue: Source is newer but has fewer records
  - Manual sync option with user confirmation
  - Progress indicators with spinners during analysis and sync operations
- Comprehensive version documentation in `docs/versions/1.2.2.md`

### Technical Details
- Added `check_data_sync_status()` function in `src/storage/snapshot_db.py` (lines 3728-3833)
- Modified `src/commands/settings.py` to integrate sync status display and interactive check
- Smart timestamp comparison with 1-second tolerance
- Safe sync process requiring user confirmation

## [1.2.1] - 2025-10-17

### Added
- **Yearly Heatmap Visualization**: Complete year-at-a-glance view in heatmap command
  - Shows 12 months horizontally with weeks vertically
  - 5-level opacity gradient for better visual distinction
  - Table-based layout for square cells and better alignment
  - Month headers and weekday labels
  - Yearly totals at the bottom
- Comprehensive version documentation in `docs/versions/1.2.1.md`

### Changed
- **Heatmap Cell Display**: Improved from Unicode blocks to table-based layout
  - Square-shaped cells (2x2 character spaces)
  - More consistent spacing and alignment
  - Better visual balance across different terminal sizes

### Fixed
- Monthly heatmap alignment issues
- Cell size inconsistencies between monthly and yearly views
- Yearly totals alignment in heatmap display

### Technical Details
- Modified `src/commands/heatmap.py` for yearly heatmap implementation
- Replaced Unicode block characters with Rich Table-based layout
- Added `_generate_yearly_heatmap()` function
- Enhanced color gradient calculation for 5 opacity levels

## [1.2.0] - 2025-10-17

### Added
- **OneDrive Synchronization Improvements**: Enhanced multi-device support
- **Device Statistics**: New command to view usage statistics per device
  - Shows total tokens, prompts, requests, and cost per device
  - Sortable by any column
  - Time range filtering support
- Version tagging and documentation workflow

### Changed
- Improved data synchronization across multiple PCs
- Enhanced database snapshot mechanism

### Technical Details
- Added `src/commands/device_stats.py` for device statistics
- Enhanced `src/storage/snapshot_db.py` for better sync handling

## [1.0.4] - Previous Release

### Initial Fork Features
- Multi-PC support via OneDrive synchronization
- SQLite-based local database for performance
- Rich TUI with interactive dashboard
- Usage analytics and visualization
- Cost tracking and reporting

---

For detailed release notes, see [docs/versions/](docs/versions/) directory.
