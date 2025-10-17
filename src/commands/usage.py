#region Imports
import sys
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
import re
import sqlite3

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src.commands.limits import capture_limits
from src.config.settings import (
    DEFAULT_REFRESH_INTERVAL,
    get_claude_jsonl_files,
)
from src.config.user_config import get_tracking_mode
from src.data.jsonl_parser import parse_all_jsonl_files
from src.storage.snapshot_db import (
    load_usage_summary,
    load_recent_usage_records,
    load_all_devices_historical_records_cached,
    save_limits_snapshot,
    save_snapshot,
)
from src.visualization.dashboard import render_dashboard
#endregion


#region Constants
# View modes for dashboard
VIEW_MODE_WEEKLY = "weekly"
VIEW_MODE_MONTHLY = "monthly"
VIEW_MODE_YEARLY = "yearly"
VIEW_MODE_HEATMAP = "heatmap"
VIEW_MODE_DEVICES = "devices"

#endregion


#region Functions


def _parse_week_reset_date(week_reset_str: str) -> datetime | None:
    """
    Parse week reset string to datetime object.

    Supports two formats:
    1. "Oct 17, 10am (Asia/Seoul)" - with date
    2. "10am (Asia/Seoul)" - time only (calculates next occurrence)

    Args:
        week_reset_str: Reset string from limits data

    Returns:
        datetime object or None if parsing fails
    """
    try:
        from datetime import datetime, timezone as dt_timezone
        from zoneinfo import ZoneInfo

        # Remove timezone part and extract timezone name
        tz_match = re.search(r'\((.*?)\)', week_reset_str)
        tz_name = tz_match.group(1) if tz_match else 'UTC'
        reset_no_tz = week_reset_str.split(' (')[0].strip()

        # Get timezone
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = dt_timezone.utc

        # Try to parse "Oct 17, 10am" format (with date)
        date_match = re.search(r'([A-Za-z]+)\s+(\d+),\s+(\d+)(am|pm)', reset_no_tz)
        if date_match:
            month_name = date_match.group(1)
            day = int(date_match.group(2))
            hour = int(date_match.group(3))
            meridiem = date_match.group(4)

            # Convert to 24-hour format
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0

            # Use current year
            year = datetime.now(tz).year

            # Parse month
            month_num = datetime.strptime(month_name, '%b').month

            # Create timezone-aware datetime and convert to UTC
            local_dt = datetime(year, month_num, day, hour, 0, 0, tzinfo=tz)
            utc_dt = local_dt.astimezone(dt_timezone.utc)
            return utc_dt.replace(tzinfo=None)

        # Try to parse "10am" or "10:59am" format (time only)
        time_match = re.search(r'(\d+):?(\d*)(am|pm)', reset_no_tz)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2)) if time_match.group(2) else 0
            meridiem = time_match.group(3)

            # Convert to 24-hour format
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0

            # Calculate next occurrence of this time in the specified timezone
            now = datetime.now(tz)
            reset_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # If reset time is in the past today, it's tomorrow
            if reset_time <= now:
                reset_time += timedelta(days=1)

            # Convert to UTC and remove timezone info
            utc_dt = reset_time.astimezone(dt_timezone.utc)
            return utc_dt.replace(tzinfo=None)

        return None
    except Exception:
        return None


def _filter_records_by_week(records: list, week_reset_date: datetime) -> list:
    """
    Filter records to a specific week limit period with exact time boundaries.

    Claude's weekly limits reset at a specific time (e.g., 9:59am on Friday).
    The week period is exactly 7 days with precise time boundaries.

    For example, if week_reset_date is 2025-10-17 09:59am (Friday):
    - Week starts: 2025-10-10 09:59am (last Friday reset)
    - Week ends: 2025-10-17 09:58:59am (just before next Friday reset)
    - Reset day (Friday) appears in BOTH weeks:
      - Previous week: 00:00 ~ 09:58
      - Current week: 09:59 ~ 23:59

    Args:
        records: List of UsageRecord objects
        week_reset_date: Next week reset datetime with time (the END boundary)

    Returns:
        Filtered list of records within the week period
    """
    from datetime import timezone as dt_timezone

    # Calculate the START of the week period (last reset time)
    last_reset_datetime = week_reset_date - timedelta(days=7)

    # Week period: from last_reset (inclusive) to week_reset (exclusive)
    week_start = last_reset_datetime
    week_end = week_reset_date

    filtered = []
    for record in records:
        try:
            # Get record timestamp
            if hasattr(record, 'timestamp') and record.timestamp:
                record_dt = record.timestamp

                # Ensure timezone-aware comparison (convert to UTC if needed)
                if record_dt.tzinfo is None:
                    record_dt = record_dt.replace(tzinfo=dt_timezone.utc)

                # Make sure week boundaries are also timezone-aware
                start_dt = week_start if week_start.tzinfo else week_start.replace(tzinfo=dt_timezone.utc)
                end_dt = week_end if week_end.tzinfo else week_end.replace(tzinfo=dt_timezone.utc)

                # Filter: last_reset <= record < next_reset
                if start_dt <= record_dt < end_dt:
                    filtered.append(record)
            else:
                continue
        except Exception:
            continue

    return filtered


def _limits_updater_thread(stop_event: threading.Event, interval: int = 60) -> None:
    """
    Background thread that updates usage limits at regular intervals.

    Updates limits data from Claude CLI and saves to database every N seconds.
    This prevents blocking the main dashboard updates while keeping limits data fresh.

    Args:
        stop_event: Threading event to signal when to stop updating
        interval: Seconds between updates (default: 60)
    """
    # Wait 5 seconds before first update (let initial dashboard render first)
    if not stop_event.wait(5):
        # Do initial update
        tracking_mode = get_tracking_mode()
        if tracking_mode in ["both", "limits"]:
            try:
                limits = capture_limits()
                if limits and "error" not in limits:
                    save_limits_snapshot(
                        session_pct=limits["session_pct"],
                        week_pct=limits["week_pct"],
                        opus_pct=limits["opus_pct"],
                        session_reset=limits["session_reset"],
                        week_reset=limits["week_reset"],
                        opus_reset=limits["opus_reset"],
                    )
            except Exception:
                pass  # Silently ignore errors in background thread

    # Continue updating at regular intervals
    while not stop_event.is_set():
        if stop_event.wait(interval):
            break  # Stop event was set during wait

        tracking_mode = get_tracking_mode()
        if tracking_mode in ["both", "limits"]:
            try:
                limits = capture_limits()
                if limits and "error" not in limits:
                    save_limits_snapshot(
                        session_pct=limits["session_pct"],
                        week_pct=limits["week_pct"],
                        opus_pct=limits["opus_pct"],
                        session_reset=limits["session_reset"],
                        week_reset=limits["week_reset"],
                        opus_reset=limits["opus_reset"],
                    )
            except Exception:
                pass  # Silently ignore errors in background thread


def _keyboard_listener(view_mode_ref: dict, stop_event: threading.Event) -> None:
    """
    Listen for keyboard input to switch view modes.

    Runs in a separate thread to handle keyboard input without blocking dashboard.

    Args:
        view_mode_ref: Dict with 'mode' key to track current view mode
        stop_event: Threading event to signal when to stop listening
    """
    import sys
    import tty
    import termios

    # Save terminal settings
    old_settings = termios.tcgetattr(sys.stdin)

    try:
        tty.setcbreak(sys.stdin.fileno())

        while not stop_event.is_set():
            # Check if input is available (non-blocking with timeout)
            import select
            if select.select([sys.stdin], [], [], 0.1)[0]:
                key = sys.stdin.read(1)

                # Check for arrow keys (escape sequences)
                if key == '\x1b':  # ESC
                    # Read the rest of the escape sequence
                    # Arrow keys send: ESC [ A/B/C/D
                    # Wait a bit longer for the rest of the sequence to arrive
                    remaining = []
                    for _ in range(2):  # Read next 2 characters
                        if select.select([sys.stdin], [], [], 0.5)[0]:  # Increased timeout to 0.5s
                            remaining.append(sys.stdin.read(1))
                        else:
                            # Timeout - might be a plain ESC press
                            break

                    # Check if it's an arrow key
                    if len(remaining) == 2 and remaining[0] == '[':
                        arrow = remaining[1]

                        if arrow == 'D':  # Left arrow
                            # Go to previous period
                            if view_mode_ref['mode'] in [VIEW_MODE_WEEKLY, VIEW_MODE_MONTHLY, VIEW_MODE_YEARLY]:
                                old_offset = view_mode_ref.get('offset', 0)
                                view_mode_ref['offset'] = old_offset - 1
                                view_mode_ref['changed'] = True
                            continue  # Skip the rest of the key processing
                        elif arrow == 'C':  # Right arrow
                            # Go to next period (don't go beyond current)
                            if view_mode_ref['mode'] in [VIEW_MODE_WEEKLY, VIEW_MODE_MONTHLY, VIEW_MODE_YEARLY]:
                                current_offset = view_mode_ref.get('offset', 0)
                                if current_offset < 0:  # Only allow if we're in the past
                                    view_mode_ref['offset'] = current_offset + 1
                                    view_mode_ref['changed'] = True
                            continue  # Skip the rest of the key processing

                    # If we got here, it's a plain ESC press (not arrow key)
                    # If in message detail mode (hourly detail), exit to daily detail view
                    if view_mode_ref.get('hourly_detail_hour') is not None:
                        view_mode_ref['hourly_detail_hour'] = None
                        view_mode_ref['changed'] = True
                        continue
                    # If in daily detail mode, exit to normal weekly view
                    if view_mode_ref.get('daily_detail_date'):
                        view_mode_ref['daily_detail_date'] = None
                        view_mode_ref['changed'] = True
                        continue
                    # Otherwise, treat ESC as quit command
                    stop_event.set()
                    continue

                key = key.lower()

                # Map Korean characters to English keys (for Korean keyboard mode)
                hangul_to_english = {
                    'ㅕ': 'u',  # u key in Korean mode
                    'ㅈ': 'w',  # w key in Korean mode
                    'ㅡ': 'm',  # m key in Korean mode
                    'ㅛ': 'y',  # y key in Korean mode
                    'ㅗ': 'h',  # h key in Korean mode
                    'ㅇ': 'd',  # d key in Korean mode
                    'ㅂ': 'q',  # q key in Korean mode
                    'ㄱ': 'r',  # r key in Korean mode
                    'ㄴ': 's',  # s key in Korean mode
                    'ㅏ': '<',  # < key in Korean mode (shift+,)
                    'ㅐ': '>',  # > key in Korean mode (shift+.)
                    # Numbers with shift (Korean keyboard)
                    '!': '1',  # shift+1 in Korean mode
                    '@': '2',  # shift+2 in Korean mode
                    '#': '3',  # shift+3 in Korean mode
                    '$': '4',  # shift+4 in Korean mode
                    '%': '5',  # shift+5 in Korean mode
                    '^': '6',  # shift+6 in Korean mode
                    '&': '7',  # shift+7 in Korean mode
                    '*': '8',  # shift+8 in Korean mode
                    '(': '9',  # shift+9 in Korean mode
                }
                if key in hangul_to_english:
                    key = hangul_to_english[key]

                # Flush any remaining characters in the input buffer
                # to prevent stale input from affecting future reads
                while select.select([sys.stdin], [], [], 0)[0]:
                    sys.stdin.read(1)

                if key == 'u':
                    view_mode_ref['mode'] = "usage"
                    view_mode_ref['offset'] = 0  # Reset offset when changing mode
                    view_mode_ref['changed'] = True
                elif key == 'w':
                    view_mode_ref['mode'] = VIEW_MODE_WEEKLY
                    view_mode_ref['offset'] = 0  # Reset offset when changing mode
                    view_mode_ref['changed'] = True
                elif key == 'm':
                    view_mode_ref['mode'] = VIEW_MODE_MONTHLY
                    view_mode_ref['offset'] = 0  # Reset offset when changing mode
                    view_mode_ref['changed'] = True
                elif key == 'y':
                    view_mode_ref['mode'] = VIEW_MODE_YEARLY
                    view_mode_ref['offset'] = 0  # Reset offset when changing mode
                    view_mode_ref['changed'] = True
                elif key == 'h':
                    view_mode_ref['mode'] = VIEW_MODE_HEATMAP
                    view_mode_ref['offset'] = 0  # Reset offset when changing mode
                    view_mode_ref['changed'] = True
                elif key == 'd':
                    view_mode_ref['mode'] = VIEW_MODE_DEVICES
                    view_mode_ref['offset'] = 0  # Reset offset when changing mode
                    view_mode_ref.pop('device_week_offset', None)  # Reset device week offset
                    # Don't reset device_display_period - keep user's last selection
                    # view_mode_ref.setdefault('device_display_period', 'all')  # Already defaults to 'all'
                    view_mode_ref['changed'] = True
                elif key == '<':  # Previous week (devices mode)
                    if view_mode_ref['mode'] == VIEW_MODE_DEVICES:
                        # Only allow week navigation in weekly mode
                        if view_mode_ref.get('device_display_period', 'all') == 'weekly':
                            current_offset = view_mode_ref.get('device_week_offset', 0)
                            view_mode_ref['device_week_offset'] = current_offset - 1
                            view_mode_ref['changed'] = True
                elif key == '>':  # Next week (devices mode)
                    if view_mode_ref['mode'] == VIEW_MODE_DEVICES:
                        # Only allow week navigation in weekly mode
                        if view_mode_ref.get('device_display_period', 'all') == 'weekly':
                            current_offset = view_mode_ref.get('device_week_offset', 0)
                            # Don't go beyond current week
                            if current_offset < 0:
                                view_mode_ref['device_week_offset'] = current_offset + 1
                                view_mode_ref['changed'] = True
                elif key == '\t':  # Tab key
                    # Check if in message detail mode (content mode rotation: hide -> brief -> detail -> hide)
                    if view_mode_ref.get('hourly_detail_hour') is not None:
                        # Get current mode (default: "hide")
                        current_mode = view_mode_ref.get('message_content_mode', 'hide')

                        # Rotate: hide -> brief -> detail -> hide
                        if current_mode == 'hide':
                            view_mode_ref['message_content_mode'] = 'brief'
                        elif current_mode == 'brief':
                            view_mode_ref['message_content_mode'] = 'detail'
                        else:  # detail
                            view_mode_ref['message_content_mode'] = 'hide'

                        view_mode_ref['changed'] = True
                    elif view_mode_ref['mode'] == "usage":
                        # Cycle through 8 modes: S1 -> S2 -> S3 -> S4 -> G1 -> G2 -> G3 -> G4 -> S1
                        # Current state: usage_display_mode (0-3) and color_mode ('solid' or 'gradient')
                        current_display = view_mode_ref.get('usage_display_mode', 0)
                        current_color = view_mode_ref.get('color_mode', 'gradient')

                        # Convert to 0-7 index (0-3: Solid modes, 4-7: Gradient modes)
                        if current_color == 'solid':
                            current_index = current_display  # 0-3
                        else:
                            current_index = current_display + 4  # 4-7

                        # Increment and wrap around
                        new_index = (current_index + 1) % 8

                        # Convert back to display mode and color mode
                        if new_index < 4:
                            new_display = new_index
                            new_color = 'solid'
                        else:
                            new_display = new_index - 4
                            new_color = 'gradient'

                        # Update state
                        view_mode_ref['usage_display_mode'] = new_display
                        view_mode_ref['color_mode'] = new_color
                        view_mode_ref['changed'] = True

                        # Save to DB
                        from src.storage.snapshot_db import save_user_preference
                        save_user_preference('usage_display_mode', str(new_display))
                        save_user_preference('color_mode', new_color)
                    elif view_mode_ref['mode'] == VIEW_MODE_WEEKLY:
                        # Toggle between limits and calendar week
                        current = view_mode_ref.get('weekly_display_mode', 'limits')
                        view_mode_ref['weekly_display_mode'] = 'calendar' if current == 'limits' else 'limits'
                        view_mode_ref['changed'] = True
                    elif view_mode_ref['mode'] == VIEW_MODE_MONTHLY:
                        # Toggle between daily and weekly breakdown
                        current = view_mode_ref.get('monthly_display_mode', 'daily')
                        view_mode_ref['monthly_display_mode'] = 'weekly' if current == 'daily' else 'daily'
                        view_mode_ref['changed'] = True
                    elif view_mode_ref['mode'] == VIEW_MODE_YEARLY:
                        # Toggle between monthly and weekly breakdown
                        current = view_mode_ref.get('yearly_display_mode', 'monthly')
                        view_mode_ref['yearly_display_mode'] = 'weekly' if current == 'monthly' else 'monthly'
                        view_mode_ref['changed'] = True
                    elif view_mode_ref['mode'] == VIEW_MODE_DEVICES:
                        # Cycle through: all -> monthly -> weekly -> all
                        current = view_mode_ref.get('device_display_period', 'all')
                        if current == 'all':
                            view_mode_ref['device_display_period'] = 'monthly'
                        elif current == 'monthly':
                            view_mode_ref['device_display_period'] = 'weekly'
                        else:  # weekly
                            view_mode_ref['device_display_period'] = 'all'
                        view_mode_ref['changed'] = True
                elif key == 's':  # Settings menu
                    # Save current view mode to restore later
                    previous_mode = view_mode_ref['mode']

                    # Set mode to 'settings' to pause auto-refresh
                    view_mode_ref['mode'] = 'settings'

                    # Restore terminal to ORIGINAL normal mode (from program start)
                    import termios
                    original_settings = view_mode_ref.get('original_terminal_settings')
                    if original_settings:
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, original_settings)

                    # Open settings menu (blocks until ESC is pressed)
                    from src.commands.settings import run as settings_run
                    from rich.console import Console
                    settings_console = Console()
                    settings_run(settings_console)

                    # Restore raw mode for keyboard listener AFTER settings exits
                    import tty
                    tty.setcbreak(sys.stdin.fileno())

                    # Reload preferences after settings close
                    from src.storage.snapshot_db import load_user_preferences
                    from src.config.defaults import DEFAULT_COLORS
                    prefs = load_user_preferences()
                    view_mode_ref['usage_display_mode'] = int(prefs.get('usage_display_mode', '0'))
                    view_mode_ref['color_mode'] = prefs.get('color_mode', 'gradient')
                    view_mode_ref['colors'] = {
                        'color_solid': prefs.get('color_solid', DEFAULT_COLORS['color_solid']),
                        'color_gradient_low': prefs.get('color_gradient_low', DEFAULT_COLORS['color_gradient_low']),
                        'color_gradient_mid': prefs.get('color_gradient_mid', DEFAULT_COLORS['color_gradient_mid']),
                        'color_gradient_high': prefs.get('color_gradient_high', DEFAULT_COLORS['color_gradient_high']),
                        'color_unfilled': prefs.get('color_unfilled', DEFAULT_COLORS['color_unfilled']),
                    }
                    # Restore previous mode and trigger refresh
                    view_mode_ref['mode'] = previous_mode
                    view_mode_ref['changed'] = True
                elif key == '<':
                    # Only for weekly/monthly/yearly modes - go to previous period
                    if view_mode_ref['mode'] in [VIEW_MODE_WEEKLY, VIEW_MODE_MONTHLY, VIEW_MODE_YEARLY]:
                        view_mode_ref['offset'] = view_mode_ref.get('offset', 0) - 1
                        view_mode_ref['changed'] = True
                elif key == '>':
                    # Only for weekly/monthly/yearly modes - go to next period
                    if view_mode_ref['mode'] in [VIEW_MODE_WEEKLY, VIEW_MODE_MONTHLY, VIEW_MODE_YEARLY]:
                        current_offset = view_mode_ref.get('offset', 0)
                        if current_offset < 0:  # Only allow if we're in the past
                            view_mode_ref['offset'] = current_offset + 1
                            view_mode_ref['changed'] = True
                elif key == 'r':
                    # Manual refresh - update data
                    view_mode_ref['manual_refresh'] = True
                    view_mode_ref['changed'] = True
                elif key in ['1', '2', '3', '4', '5', '6', '7', '8', '9'] or key in ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'n', 'o']:
                    # Number keys (1-9) and letter keys (a-o) for navigation
                    # 1-9 map to indices 0-8, a-o map to indices 9-23 (supports up to 24 hours)
                    if key.isdigit():
                        day_index = int(key) - 1  # 1->0, 2->1, ..., 9->8
                    else:
                        day_index = ord(key) - ord('a') + 9  # a->9, b->10, ..., o->23

                    # If in daily detail mode, navigate to hourly detail (message view)
                    if view_mode_ref.get('daily_detail_date'):
                        # Get hourly data from daily detail view
                        # Number keys (1-9) and letters (a-o) map to hours (1 = first hour, 2 = second, ..., a = 10th, etc.)
                        # We need to get the sorted hours from the daily detail view
                        # For now, we'll store hourly_hours in view_mode_ref similar to weekly_dates
                        hourly_hours = view_mode_ref.get('hourly_hours', [])

                        if day_index < len(hourly_hours):
                            # Set the hourly detail hour (store as integer 0-23)
                            view_mode_ref['hourly_detail_hour'] = hourly_hours[day_index]
                            view_mode_ref['changed'] = True
                    # If in weekly mode (not in daily detail), navigate to daily detail
                    elif view_mode_ref['mode'] == VIEW_MODE_WEEKLY:
                        # Check if we have weekly dates available
                        weekly_dates = view_mode_ref.get('weekly_dates', [])

                        if day_index < len(weekly_dates):
                            # Check if the date is not in the future
                            from datetime import datetime
                            target_date = weekly_dates[day_index]
                            target_date_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
                            today = datetime.now().date()

                            # Only navigate if date is not in the future
                            if target_date_obj <= today:
                                # Set the daily detail date
                                view_mode_ref['daily_detail_date'] = target_date
                                view_mode_ref['changed'] = True
                elif key == '\x1b':  # ESC key pressed again (not arrow key)
                    # If in daily detail mode, exit to normal weekly view
                    if view_mode_ref.get('daily_detail_date'):
                        view_mode_ref['daily_detail_date'] = None
                        view_mode_ref['changed'] = True
                    # Note: ESC to quit is already handled above in the escape sequence section

    finally:
        # Restore terminal settings
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def run(console: Console, refresh: int | None = None, anon: bool = False, watch_interval: int = 60, limits_interval: int = 60) -> None:
    """
    Handle the usage command.

    Loads Claude Code usage data and displays an interactive dashboard with:
    - Real-time file watching (updates when Claude Code creates new logs) - default
    - Or periodic refresh at specified interval (if --refresh is provided)
    - Keyboard shortcuts to switch between different views
    - GitHub-style activity visualization

    Args:
        console: Rich console for output
        refresh: Refresh interval in seconds (None = file watching mode)
        anon: Anonymize project names to project-001, project-002, etc (default: False)
        watch_interval: File watch check interval in seconds (default: 60)
        limits_interval: Usage limits update interval in seconds (default: 60)

    Exit:
        Exits with status 0 on success, 1 on error
    """
    import termios

    # Save terminal settings at the very start
    original_terminal_settings = None
    try:
        original_terminal_settings = termios.tcgetattr(sys.stdin)
    except:
        pass  # Not a TTY or stdin not available

    # Hide cursor for cleaner look
    try:
        console.show_cursor(False)
    except:
        pass  # Not a TTY

    # Clear screen to create clean starting point
    # Use console.clear() for better VSCode compatibility (avoids triggering sticky scroll)
    try:
        from rich.console import Console
        temp_console = Console()
        temp_console.clear()
    except:
        pass

    # Check for --anon flag: CLI flag > DB setting
    # Load anonymize setting from DB
    from src.storage.snapshot_db import load_user_preferences
    prefs = load_user_preferences()
    anonymize_from_db = prefs.get('anonymize_projects', '0') == '1'
    # CLI flag takes priority over DB setting
    anonymize = anon or "--anon" in sys.argv or anonymize_from_db

    # Check for --refresh in sys.argv if not passed as parameter
    if refresh is None:
        for arg in sys.argv:
            if arg.startswith("--refresh="):
                try:
                    refresh = int(arg.split("=")[1])
                except:
                    pass

    try:
        with console.status("[bold #ff8800]Loading Claude Code usage data...", spinner="dots", spinner_style="#ff8800"):
            jsonl_files = get_claude_jsonl_files()

        if not jsonl_files:
            console.print(
                "[yellow]No Claude Code data found. "
                "Make sure you've used Claude Code at least once.[/yellow]"
            )
            return

        console.print(f"[dim]Found {len(jsonl_files)} session files[/dim]")
        console.print("[dim]Tip: Run 'ccu --help' to see all available options[/dim]\n", end="")

        # Choose between refresh mode (polling) or watch mode (file events)
        if refresh is not None:
            _run_refresh_dashboard(jsonl_files, console, original_terminal_settings, refresh_interval=refresh, anonymize=anonymize, limits_interval=limits_interval)
        else:
            _run_watch_dashboard(jsonl_files, console, original_terminal_settings, skip_limits=False, anonymize=anonymize, watch_interval=watch_interval, limits_interval=limits_interval)

    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        # Ctrl+C pressed - exit immediately without message
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Restore cursor
        try:
            console.show_cursor(True)
        except:
            pass

        # Always restore terminal settings before exiting
        if original_terminal_settings is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, original_terminal_settings)
            except:
                pass


def _run_refresh_dashboard(jsonl_files: list[Path], console: Console, original_terminal_settings, refresh_interval: int = 30, anonymize: bool = False, limits_interval: int = 60) -> None:
    """
    Run dashboard with periodic refresh - updates at fixed intervals.

    Simpler than watch mode, just polls every N seconds. Good for environments where
    file watching might not work reliably.

    Keyboard shortcuts:
        w - Switch to weekly mode (default, current week limit period)
        m - Switch to monthly mode
        y - Switch to yearly mode (monthly statistics)
        h - Switch to heatmap view
        d - Switch to devices view
        q - Quit

    Args:
        jsonl_files: List of JSONL files to parse
        console: Rich console for output
        refresh_interval: Seconds between updates (default: 30)
        anonymize: Anonymize project names
        limits_interval: Usage limits update interval in seconds (default: 60)
    """
    console.print(
        f"\n[white]Refreshing every {refresh_interval} seconds... "
        f"Dashboard will update automatically.[/white]"
    )

    # Load user preferences from DB
    from src.storage.snapshot_db import load_user_preferences
    from src.config.defaults import DEFAULT_COLORS
    prefs = load_user_preferences()

    # Track current view mode, time offset, usage display mode, and color mode
    # Initialize from DB settings
    usage_display_mode = int(prefs.get('usage_display_mode', '0'))
    color_mode = prefs.get('color_mode', 'gradient')
    colors = {
        'color_solid': prefs.get('color_solid', DEFAULT_COLORS['color_solid']),
        'color_gradient_low': prefs.get('color_gradient_low', DEFAULT_COLORS['color_gradient_low']),
        'color_gradient_mid': prefs.get('color_gradient_mid', DEFAULT_COLORS['color_gradient_mid']),
        'color_gradient_high': prefs.get('color_gradient_high', DEFAULT_COLORS['color_gradient_high']),
        'color_unfilled': prefs.get('color_unfilled', DEFAULT_COLORS['color_unfilled']),
    }

    view_mode_ref = {
        'mode': "usage",
        'changed': False,
        'offset': 0,
        'usage_display_mode': usage_display_mode,
        'color_mode': color_mode,
        'colors': colors,
        'weekly_display_mode': 'limits',  # 'limits' or 'calendar'
        'monthly_display_mode': 'daily',  # 'daily' or 'weekly'
        'yearly_display_mode': 'monthly',  # 'monthly' or 'weekly'
        'original_terminal_settings': original_terminal_settings,  # Store for settings page
    }
    stop_event = threading.Event()

    # Start keyboard listener thread (daemon=True for instant Ctrl+C exit)
    keyboard_thread = threading.Thread(target=_keyboard_listener, args=(view_mode_ref, stop_event), daemon=True)
    keyboard_thread.start()

    # Start background limits updater thread
    limits_thread = threading.Thread(target=_limits_updater_thread, args=(stop_event, limits_interval), daemon=True)
    limits_thread.start()

    # Display initial dashboard (skip limits update - will be done by background thread)
    _display_dashboard(jsonl_files, console, skip_limits=False, skip_limits_update=True, anonymize=anonymize, view_mode=view_mode_ref['mode'], view_mode_ref=view_mode_ref)

    try:
        # Keep refreshing at intervals
        last_refresh = time.time()
        while not stop_event.is_set():
            # Skip refresh if in settings mode
            if view_mode_ref.get('mode') == 'settings':
                time.sleep(0.1)
                continue

            # Check for view mode changes frequently
            if view_mode_ref.get('changed', False):
                view_mode_ref['changed'] = False
                updated_files = get_claude_jsonl_files()
                # Check if it's a manual refresh (r key) or just mode change
                if view_mode_ref.get('manual_refresh', False):
                    view_mode_ref['manual_refresh'] = False
                    # Manual refresh: update data
                    _display_dashboard(updated_files, console, skip_limits=False, skip_limits_update=True, anonymize=anonymize, view_mode=view_mode_ref['mode'], view_mode_ref=view_mode_ref, show_status=False)
                    last_refresh = time.time()  # Reset timer after manual refresh
                else:
                    # Mode change only: use cached data for instant switching
                    _display_dashboard(updated_files, console, skip_limits=True, skip_limits_update=True, anonymize=anonymize, view_mode=view_mode_ref['mode'], view_mode_ref=view_mode_ref, show_status=False)

            # Periodic refresh
            elif time.time() - last_refresh >= refresh_interval:
                updated_files = get_claude_jsonl_files()
                # Skip limits update - background thread handles it, no status messages
                _display_dashboard(updated_files, console, skip_limits=False, skip_limits_update=True, anonymize=anonymize, view_mode=view_mode_ref['mode'], view_mode_ref=view_mode_ref, show_status=False)
                last_refresh = time.time()

            time.sleep(0.05)  # Check frequently for keyboard input

    except KeyboardInterrupt:
        stop_event.set()
        raise
    finally:
        # Signal threads to stop (daemon threads will exit automatically)
        stop_event.set()


def _run_watch_dashboard(jsonl_files: list[Path], console: Console, original_terminal_settings, skip_limits: bool = False, anonymize: bool = False, watch_interval: int = 60, limits_interval: int = 60) -> None:
    """
    Run dashboard with file watching - updates only when JSONL files change.

    More efficient than polling mode as it only updates when files actually change.
    Uses the watchdog library to monitor file system events.

    Keyboard shortcuts:
        w - Switch to weekly mode (default, current week limit period)
        m - Switch to monthly mode
        y - Switch to yearly mode (monthly statistics)
        h - Switch to heatmap view
        q - Quit

    Args:
        jsonl_files: List of JSONL files to parse
        console: Rich console for output
        skip_limits: Skip limits fetching for faster rendering
        anonymize: Anonymize project names
        watch_interval: File watch check interval in seconds (default: 60)
        limits_interval: Usage limits update interval in seconds (default: 60)
    """
    from src.utils.file_watcher import watch_claude_files

    console.print(
        "\n[white]Watching for file changes... "
        "Dashboard will update when Claude Code creates or modifies log files.[/white]"
    )

    # Load user preferences from DB
    from src.storage.snapshot_db import load_user_preferences
    from src.config.defaults import DEFAULT_COLORS
    prefs = load_user_preferences()

    # Track current view mode, time offset, usage display mode, and color mode
    # Initialize from DB settings
    usage_display_mode = int(prefs.get('usage_display_mode', '0'))
    color_mode = prefs.get('color_mode', 'gradient')
    colors = {
        'color_solid': prefs.get('color_solid', DEFAULT_COLORS['color_solid']),
        'color_gradient_low': prefs.get('color_gradient_low', DEFAULT_COLORS['color_gradient_low']),
        'color_gradient_mid': prefs.get('color_gradient_mid', DEFAULT_COLORS['color_gradient_mid']),
        'color_gradient_high': prefs.get('color_gradient_high', DEFAULT_COLORS['color_gradient_high']),
        'color_unfilled': prefs.get('color_unfilled', DEFAULT_COLORS['color_unfilled']),
    }

    view_mode_ref = {
        'mode': "usage",
        'changed': False,
        'offset': 0,
        'usage_display_mode': usage_display_mode,
        'color_mode': color_mode,
        'colors': colors,
        'weekly_display_mode': 'limits',  # 'limits' or 'calendar'
        'monthly_display_mode': 'daily',  # 'daily' or 'weekly'
        'yearly_display_mode': 'monthly',  # 'monthly' or 'weekly'
        'original_terminal_settings': original_terminal_settings,  # Store for settings page
    }
    stop_event = threading.Event()

    # Start background limits updater thread
    limits_thread = threading.Thread(target=_limits_updater_thread, args=(stop_event, limits_interval), daemon=True)
    limits_thread.start()

    # Display initial dashboard (skip limits update - will be done by background thread)
    _display_dashboard(jsonl_files, console, skip_limits, skip_limits_update=True, anonymize=anonymize, view_mode=view_mode_ref['mode'], view_mode_ref=view_mode_ref)

    # Start file watcher (monitors changes without immediate callback)
    watcher = watch_claude_files()
    watcher.start()

    # Start keyboard listener thread (daemon=True for instant Ctrl+C exit)
    keyboard_thread = threading.Thread(target=_keyboard_listener, args=(view_mode_ref, stop_event), daemon=True)
    keyboard_thread.start()

    try:
        # Track last file check time
        last_file_check = time.time()

        # Keep the main thread alive and check for view mode changes and file updates
        while watcher.is_alive() and not stop_event.is_set():
            current_time = time.time()

            # Skip refresh if in settings mode
            if view_mode_ref.get('mode') == 'settings':
                time.sleep(0.1)
                continue

            # Check for view mode changes (instant response)
            if view_mode_ref.get('changed', False):
                # Refresh dashboard with new view mode
                view_mode_ref['changed'] = False
                updated_files = get_claude_jsonl_files()
                # Check if it's a manual refresh (r key) or just mode change
                if view_mode_ref.get('manual_refresh', False):
                    view_mode_ref['manual_refresh'] = False
                    # Manual refresh: update data
                    _display_dashboard(updated_files, console, skip_limits=False, skip_limits_update=True, anonymize=anonymize, view_mode=view_mode_ref['mode'], view_mode_ref=view_mode_ref, show_status=False)
                    last_file_check = current_time  # Reset timer after manual refresh
                else:
                    # Mode change only: use cached data for instant switching
                    _display_dashboard(updated_files, console, skip_limits=True, skip_limits_update=True, anonymize=anonymize, view_mode=view_mode_ref['mode'], view_mode_ref=view_mode_ref, show_status=False)

            # Check for file changes at specified interval
            elif current_time - last_file_check >= watch_interval:
                if watcher.get_and_reset_changes():
                    # Files changed - refresh dashboard without status messages
                    updated_files = get_claude_jsonl_files()
                    _display_dashboard(updated_files, console, skip_limits, skip_limits_update=True, anonymize=anonymize, view_mode=view_mode_ref['mode'], view_mode_ref=view_mode_ref, show_status=False)
                last_file_check = current_time

            time.sleep(0.05)  # Check more frequently for keyboard input

        # Stop watcher if quit was pressed
        if stop_event.is_set():
            watcher.stop()

    except KeyboardInterrupt:
        stop_event.set()
        watcher.stop()
        raise
    finally:
        # Signal threads to stop (daemon threads will exit automatically)
        stop_event.set()


def _display_dashboard(jsonl_files: list[Path], console: Console, skip_limits: bool = False, skip_limits_update: bool = False, anonymize: bool = False, view_mode: str = "usage", view_mode_ref: dict | None = None, show_status: bool = True) -> None:
    """
    Ingest JSONL data and display dashboard.

    This performs two steps:
    1. Ingestion: Read JSONL files and save to DB (with deduplication)
    2. Display: Read from DB and render dashboard

    Args:
        jsonl_files: List of JSONL files to parse
        console: Rich console for output
        skip_limits: Skip ALL updates, read directly from DB (fast mode)
        skip_limits_update: Skip only limits update (use DB cache), but still update usage data
        anonymize: Anonymize project names to project-001, project-002, etc
        view_mode: Display mode - usage (default), weekly, monthly, yearly, or heatmap
        view_mode_ref: Reference dict to check for view mode changes and time offset (for interruption)
        show_status: Show status messages (default: True, set to False for instant mode switching)
    """
    from src.storage.snapshot_db import get_latest_limits, DEFAULT_DB_PATH

    first_time_setup = not DEFAULT_DB_PATH.exists()
    db_path_str = str(DEFAULT_DB_PATH).lower()
    is_onedrive_path = "onedrive" in db_path_str

    def _handle_database_exception(context: str, exc: Exception) -> None:
        console.clear()

        if is_onedrive_path:
            panel_text = Text(
                "OneDrive may still be downloading the shared database.\n"
                "Wait until the `.claude-goblin/usage_history_*.db` file finishes syncing, then press 'r' to refresh or relaunch 'ccu'.\n"
                "Initial downloads can take a few minutes.",
                justify="center",
                style="yellow",
            )
            console.print(
                Panel(
                    panel_text,
                    title="[bold]OneDrive Sync[/bold]",
                    border_style="yellow",
                    expand=True,
                )
            )
        else:
            error_text = Text(
                f"{context}\n{exc}",
                justify="center",
                style="red",
            )
            console.print(
                Panel(
                    error_text,
                    title="[bold]Database Error[/bold]",
                    border_style="red",
                    expand=True,
                )
            )

    # Check if database exists when using --fast
    if skip_limits and not DEFAULT_DB_PATH.exists():
        console.clear()
        console.print("[red]Error: Cannot use --fast flag without existing database.[/red]")
        console.print("[yellow]Run 'ccu usage' (without --fast) first to create the database.[/yellow]")
        return

    # Update data unless in fast mode
    if not skip_limits:
        # Step 1: Update usage data
        try:
            if show_status:
                with console.status("[bold #ff8800]Updating usage data...", spinner="dots", spinner_style="#ff8800"):
                    current_records = parse_all_jsonl_files(jsonl_files)

                    # Save to database (with automatic deduplication via UNIQUE constraint)
                    if current_records:
                        save_snapshot(current_records)
            else:
                # Fast mode: no status messages
                current_records = parse_all_jsonl_files(jsonl_files)
                if current_records:
                    save_snapshot(current_records)
        except (sqlite3.Error, OSError) as exc:
            _handle_database_exception("Failed to update usage data.", exc)
            return

        # Step 2: Update limits data (if enabled and not skipped)
        if not skip_limits_update:
            tracking_mode = get_tracking_mode()
            if tracking_mode in ["both", "limits"]:
                # Run limits capture in a thread so we can interrupt it if mode changes
                limits_result = {'data': None, 'completed': False}

                def capture_limits_thread():
                    try:
                        limits_result['data'] = capture_limits()
                        limits_result['completed'] = True
                    except Exception:
                        limits_result['completed'] = True

                limits_thread = threading.Thread(target=capture_limits_thread, daemon=True)
                limits_thread.start()

                # Show spinner while waiting, but check for interruption
                if show_status:
                    with console.status("[bold #ff8800]Updating usage limits...", spinner="dots", spinner_style="#ff8800") as status:
                        while limits_thread.is_alive():
                            # Check if mode changed - if so, abandon limits update
                            if view_mode_ref and view_mode_ref.get('changed', False):
                                # Mode changed - stop waiting and proceed without limits update
                                break
                            time.sleep(0.1)  # Poll every 100ms
                else:
                    # Fast mode: wait without spinner
                    while limits_thread.is_alive():
                        if view_mode_ref and view_mode_ref.get('changed', False):
                            break
                        time.sleep(0.1)

                # Save limits if capture completed successfully
                if limits_result['completed']:
                    limits = limits_result['data']
                    if limits and "error" not in limits:
                        try:
                            save_limits_snapshot(
                                session_pct=limits["session_pct"],
                                week_pct=limits["week_pct"],
                                opus_pct=limits["opus_pct"],
                                session_reset=limits["session_reset"],
                                week_reset=limits["week_reset"],
                                opus_reset=limits["opus_reset"],
                            )
                        except (sqlite3.Error, OSError) as exc:
                            _handle_database_exception("Failed to cache usage limits.", exc)
                            return

    usage_summary = None
    all_records = []
    limits_from_db = None

    def _load_records_for_view() -> list:
        if view_mode in {VIEW_MODE_WEEKLY, VIEW_MODE_MONTHLY, VIEW_MODE_YEARLY, VIEW_MODE_HEATMAP}:
            return load_all_devices_historical_records_cached()
        return load_recent_usage_records()

    # Step 3: Prepare dashboard from database (using cached version for performance)
    try:
        if show_status:
            status_text = "[bold #ff8800]Initial database setup may take a while. Please wait..." if first_time_setup else "[bold #ff8800]Preparing dashboard..."
            with console.status(status_text, spinner="dots", spinner_style="#ff8800"):
                usage_summary = load_usage_summary()
                all_records = _load_records_for_view()
                limits_from_db = get_latest_limits()
        else:
            usage_summary = load_usage_summary()
            all_records = _load_records_for_view()
            limits_from_db = get_latest_limits()
    except (sqlite3.Error, OSError) as exc:
        _handle_database_exception("Failed to load usage data.", exc)
        return

    if not all_records and not getattr(usage_summary, "daily", None):
        console.clear()
        console.print(
            "[yellow]No Claude Code usage data found.[/yellow]\n"
            "[dim]This could mean:[/dim]\n"
            "[dim]  • Claude Code has not been used yet on this machine[/dim]\n"
            "[dim]  • No JSONL log files exist in ~/.claude/projects/[/dim]\n"
        )
        return

    # Get time offset from view_mode_ref
    time_offset = view_mode_ref.get('offset', 0) if view_mode_ref else 0

    # Apply view mode filter
    display_records = list(all_records)
    if view_mode == VIEW_MODE_WEEKLY:
        if limits_from_db and limits_from_db.get("week_reset"):
            # Parse week reset date and filter records for weekly mode only
            week_reset_date = _parse_week_reset_date(limits_from_db["week_reset"])
            if week_reset_date:
                # Apply offset for week navigation
                adjusted_week_reset_date = week_reset_date - timedelta(weeks=-time_offset)
                display_records = _filter_records_by_week(all_records, adjusted_week_reset_date)

                # Calculate week range for display (with time boundaries)
                week_start_datetime = adjusted_week_reset_date - timedelta(days=7)
                week_end_datetime = adjusted_week_reset_date

                # Extract reset time (HH:MM format)
                reset_time_str = adjusted_week_reset_date.strftime("%H:%M")

                # Get reset day of week (e.g., "Fri" for Friday)
                from zoneinfo import ZoneInfo
                from datetime import timezone
                # Convert to local timezone for display
                tz_name = limits_from_db.get("week_reset", "").split("(")[-1].rstrip(")") if "(" in limits_from_db.get("week_reset", "") else "UTC"
                try:
                    tz = ZoneInfo(tz_name)
                    local_reset_time = adjusted_week_reset_date.replace(tzinfo=timezone.utc).astimezone(tz)
                    reset_day_name = local_reset_time.strftime("%a")  # Mon, Tue, Wed, etc.
                    reset_time_str = local_reset_time.strftime("%H:%M")
                except Exception:
                    reset_day_name = adjusted_week_reset_date.strftime("%a")

                week_start_date = week_start_datetime.date()
                week_end_date = (week_end_datetime - timedelta(seconds=1)).date()
                total_days = (week_end_date - week_start_date).days
                if total_days < 0:
                    total_days = 6
                    week_end_date = week_start_date + timedelta(days=total_days)

                week_dates = [
                    (week_start_date + timedelta(days=offset)).strftime("%Y-%m-%d")
                    for offset in range(total_days + 1)
                ]

                if view_mode_ref:
                    view_mode_ref['weekly_dates'] = week_dates
                    view_mode_ref['week_start_date'] = week_start_date
                    view_mode_ref['week_end_date'] = week_end_date
                    view_mode_ref['week_reset_time'] = reset_time_str
                    view_mode_ref['week_reset_day'] = reset_day_name
        else:
            from datetime import timezone

            # Determine base end date using latest record (local date) or current day
            latest_record = None
            if all_records:
                latest_record = max(
                    (record for record in all_records if getattr(record, "timestamp", None)),
                    key=lambda r: r.timestamp,
                    default=None,
                )

            if latest_record and latest_record.timestamp:
                base_end_date = latest_record.timestamp.astimezone().date()
            else:
                base_end_date = datetime.now(timezone.utc).astimezone().date()

            # Apply offset (negative offsets move backwards)
            target_end_date = base_end_date + timedelta(weeks=time_offset)
            target_start_date = target_end_date - timedelta(days=6)

            filtered_records = []
            for record in all_records:
                try:
                    record_date = record.timestamp.astimezone().date()
                except Exception:
                    continue

                if target_start_date <= record_date <= target_end_date:
                    filtered_records.append(record)

            display_records = filtered_records

            if view_mode_ref:
                week_dates = [
                    (target_start_date + timedelta(days=offset)).strftime("%Y-%m-%d")
                    for offset in range(7)
                ]
                view_mode_ref['weekly_dates'] = week_dates
                view_mode_ref['week_start_date'] = target_start_date
                view_mode_ref['week_end_date'] = target_end_date
                view_mode_ref['week_reset_time'] = None
                view_mode_ref['week_reset_day'] = None
    elif view_mode == VIEW_MODE_MONTHLY:
        # Filter by month with offset
        now = datetime.now()
        target_month = now.month + time_offset
        target_year = now.year

        # Adjust year if month goes out of bounds
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        while target_month > 12:
            target_month -= 12
            target_year += 1

        if view_mode_ref is not None:
            view_mode_ref['target_year'] = target_year
            view_mode_ref['target_month'] = target_month

        # Filter records for target month
        filtered = []
        for record in all_records:
            try:
                record_date = datetime.strptime(record.date_key, "%Y-%m-%d")
                if record_date.year == target_year and record_date.month == target_month:
                    filtered.append(record)
            except Exception:
                continue

        display_records = filtered
    elif view_mode == VIEW_MODE_YEARLY:
        # Filter by year with offset
        target_year = datetime.now().year + time_offset

        if view_mode_ref is not None:
            view_mode_ref['target_year'] = target_year

        # Filter records for target year
        filtered = []
        for record in all_records:
            try:
                record_date = datetime.strptime(record.date_key, "%Y-%m-%d")
                if record_date.year == target_year:
                    filtered.append(record)
            except Exception:
                continue

        display_records = filtered

    # Get date range for footer
    dates = sorted(set(r.date_key for r in display_records))
    date_range = None
    if dates:
        # Convert from "YYYY-MM-DD" to "YY/MM/DD"
        def format_short_date(date_str: str) -> str:
            """Convert YYYY-MM-DD to YY/MM/DD"""
            parts = date_str.split("-")
            if len(parts) == 3:
                year = parts[0][-2:]  # Last 2 digits of year
                month = parts[1]
                day = parts[2]
                return f"{year}/{month}/{day}"
            return date_str

        start_date = format_short_date(dates[0])
        end_date = format_short_date(dates[-1])
        date_range = f"{start_date} ~ {end_date}"

    # Anonymize project names if requested
    if anonymize:
        display_records = _anonymize_projects(display_records)

    # Aggregate statistics with caching
    stats = usage_summary.to_aggregated_stats()

    # Render dashboard with limits from DB (no live fetch needed)
    # Note: fast_mode is always False to avoid showing warning message
    render_dashboard(usage_summary, stats, display_records, console, skip_limits=True, clear_screen=True, date_range=date_range, limits_from_db=limits_from_db, fast_mode=False, view_mode=view_mode, view_mode_ref=view_mode_ref)


def _anonymize_projects(records: list) -> list:
    """
    Anonymize project folder names by ranking them by total tokens and replacing
    with project-001, project-002, etc (where project-001 is the highest usage).

    Args:
        records: List of UsageRecord objects

    Returns:
        List of UsageRecord objects with anonymized folder names
    """
    from collections import defaultdict
    from dataclasses import replace

    # Calculate total tokens per project
    project_totals = defaultdict(int)
    for record in records:
        if record.token_usage:
            project_totals[record.folder] += record.token_usage.total_tokens

    # Sort projects by total tokens (descending) and create mapping
    sorted_projects = sorted(project_totals.items(), key=lambda x: x[1], reverse=True)
    project_mapping = {
        folder: f"project-{str(i+1).zfill(3)}"
        for i, (folder, _) in enumerate(sorted_projects)
    }

    # Replace folder names in records
    anonymized_records = []
    for record in records:
        anonymized_records.append(
            replace(record, folder=project_mapping.get(record.folder, record.folder))
        )

    return anonymized_records


#endregion
