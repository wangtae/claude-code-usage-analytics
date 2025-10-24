#region Imports
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.progress import Progress, BarColumn, TextColumn
from rich.spinner import Spinner

from src.aggregation.daily_stats import AggregatedStats
from src.aggregation.summary import DailyTotal, UsageSummary
from src.models.usage_record import UsageRecord
from src.storage.snapshot_db import get_limits_data
#endregion


#region Constants
# Claude-inspired color scheme
ORANGE = "#ff8800"
YELLOW = "bright_yellow"
CYAN = "cyan"  # For percentages
BLUE = "dodger_blue1"  # More distinct blue for Input/Output tokens (distinct from cyan)
DIM = "grey50"
BAR_WIDTH = 20
#endregion


#region Functions


def _format_number(num: int) -> str:
    """
    Format number with thousands separator and appropriate suffix.

    Args:
        num: Number to format

    Returns:
        Formatted string (e.g., "1.4bn", "523.7M", "45.2K", "1.234")
    """
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}bn".replace(".", ".")
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M".replace(".", ".")
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K".replace(".", ".")
    else:
        # Add thousands separator for numbers < 1000
        return f"{num:,}".replace(",", ".")


def _create_bar(value: int, max_value: int, width: int = BAR_WIDTH, color: str = ORANGE) -> Text:
    """
    Create a simple text bar for visualization.

    Args:
        value: Current value
        max_value: Maximum value for scaling
        width: Width of bar in characters
        color: Color for the filled portion of the bar

    Returns:
        Rich Text object with colored bar
    """
    if max_value == 0:
        return Text("▬" * width, style=DIM)

    filled = int((value / max_value) * width)
    bar = Text()
    bar.append("▬" * filled, style=color)
    bar.append("▬" * (width - filled), style=DIM)
    return bar


def _calculate_session_recommended_pct(session_reset_str: str) -> float:
    """
    Calculate recommended usage percentage for current session based on elapsed time.

    Session limit is 5 hours (300 minutes). This calculates what percentage should be used
    based on how many minutes have elapsed in the current 5-hour window.

    Args:
        session_reset_str: Session reset time string (e.g., "2pm (Asia/Seoul)")

    Returns:
        Recommended usage percentage (0-100)
    """
    from datetime import datetime, timezone as dt_timezone, timedelta
    from zoneinfo import ZoneInfo
    import re

    try:
        # Extract timezone
        tz_match = re.search(r'\((.*?)\)', session_reset_str)
        tz_name = tz_match.group(1) if tz_match else 'UTC'
        reset_no_tz = session_reset_str.split(' (')[0].strip()

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = dt_timezone.utc

        # Parse reset time (e.g., "2pm" or "10:30am")
        time_match = re.search(r'(\d+):?(\d*)(am|pm)', reset_no_tz)
        if not time_match:
            return 0

        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else 0
        meridiem = time_match.group(3)

        # Convert to 24-hour format
        if meridiem == 'pm' and hour != 12:
            hour += 12
        elif meridiem == 'am' and hour == 12:
            hour = 0

        # Get current time in the same timezone
        now = datetime.now(tz)

        # Session resets at this time, so session started 5 hours before reset
        # We need to find when the current session started
        reset_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If reset time is in the future, it's the next reset
        # Session started 5 hours before the reset
        if reset_time > now:
            session_start = reset_time - timedelta(hours=5)
        else:
            # Reset time is in the past - find next reset
            next_reset = reset_time + timedelta(days=1)
            session_start = next_reset - timedelta(hours=5)

            # If session start is still in the future, use today's reset
            if session_start > now:
                session_start = reset_time - timedelta(hours=5)

        # Calculate elapsed time in the 5-hour window
        elapsed = now - session_start
        elapsed_minutes = elapsed.total_seconds() / 60

        # Cap at 5 hours (300 minutes)
        elapsed_minutes = min(elapsed_minutes, 300)

        # Calculate recommended percentage (linear based on elapsed time in minutes)
        recommended_pct = (elapsed_minutes / 300) * 100
        return min(100, recommended_pct)

    except Exception:
        return 0


def _calculate_weekly_recommended_pct(week_reset_str: str, weekly_days: int) -> float:
    """
    Calculate recommended usage percentage based on 24-hour window from week start.

    Goal: Encourage consistent daily usage by recommending to use all quota within
    the first 24 hours of the week. This prevents uneven distribution and ensures
    users have access to their full quota when they need it.

    Uses minute-based calculation for maximum accuracy, since week resets can occur
    at any time (e.g., Friday 9:59am).

    Args:
        week_reset_str: Week reset time string (e.g., "Oct 31, 9:59am (Asia/Seoul)")
        weekly_days: Unused parameter (kept for compatibility with settings)
                     Calculation always uses 24-hour window regardless of this value

    Returns:
        Recommended usage percentage (0-100)
        - 0-100% during first 24 hours based on elapsed time
        - 100% after 24 hours have passed

    Example:
        Week start: Oct 24, 9:59am
        Current: Oct 24, 10:41am (42 minutes elapsed)
        Recommended: (42 / 1440) * 100 = 2.9%

        After 24 hours (Oct 25, 10:00am+):
        Recommended: 100% (all quota should be used)
    """
    from datetime import datetime, timezone as dt_timezone, timedelta
    from zoneinfo import ZoneInfo
    import re

    try:
        # Extract timezone
        tz_match = re.search(r'\((.*?)\)', week_reset_str)
        tz_name = tz_match.group(1) if tz_match else 'UTC'
        reset_no_tz = week_reset_str.split(' (')[0].strip()

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = dt_timezone.utc

        # Try to parse "Oct 17, 10am" format (with date)
        date_match = re.search(r'([A-Za-z]+)\s+(\d+),\s+(\d+):?(\d*)(am|pm)', reset_no_tz)

        # If no date found, try time-only format like "9:59am"
        if not date_match:
            time_match = re.search(r'(\d+):?(\d*)(am|pm)', reset_no_tz)
            if not time_match:
                return 0

            # Use today's date with the given time
            hour = int(time_match.group(1))
            minute = int(time_match.group(2)) if time_match.group(2) else 0
            meridiem = time_match.group(3)

            # Convert to 24-hour format
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0

            # Get current time and create reset datetime for today
            now = datetime.now(tz)
            reset_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # If reset is in the past today, find the next occurrence
            # Weekly resets happen within the next 7 days (shown without date in Claude)
            while reset_dt < now:
                reset_dt = reset_dt + timedelta(days=1)
                # Stop if we've gone more than 7 days (safety check)
                if (reset_dt - now).days > 7:
                    break
        else:
            # Parse full date format
            month_name = date_match.group(1)
            day = int(date_match.group(2))
            hour = int(date_match.group(3))
            minute = int(date_match.group(4)) if date_match.group(4) else 0
            meridiem = date_match.group(5)

            # Convert to 24-hour format
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0

            # Parse month
            year = datetime.now(tz).year
            month_num = datetime.strptime(month_name, '%b').month

            # Create reset datetime
            reset_dt = datetime(year, month_num, day, hour, minute, 0, tzinfo=tz)

            # Get current time
            now = datetime.now(tz)

            # If reset is in the past, it might be next year
            if reset_dt < now:
                # Check if adding 7 days puts us in the future
                next_reset = reset_dt + timedelta(days=7)
                if next_reset > now:
                    reset_dt = next_reset
                else:
                    # Must be next year
                    reset_dt = reset_dt.replace(year=year + 1)

        # Week started 7 days before reset
        week_start = reset_dt - timedelta(days=7)

        # If we're before the week start, we're looking at next week
        if now < week_start:
            return 0

        # If we're after the reset, we're in a new week
        if now >= reset_dt:
            return 0

        # Calculate recommended usage based on 24-hour window
        # Goal: Use all quota within next 24 hours from week start
        # This ensures consistent daily usage instead of uneven distribution
        total_minutes_24h = 24 * 60  # 1440 minutes (24 hours)

        # Calculate elapsed minutes since week start
        elapsed = now - week_start
        elapsed_minutes = elapsed.total_seconds() / 60  # seconds to minutes

        # Calculate recommended percentage based on 24-hour window
        # After 24 hours, recommended stays at 100%
        recommended_pct = (elapsed_minutes / total_minutes_24h) * 100
        return min(100, recommended_pct)

    except Exception:
        return 0


def _calculate_distributed_recommended_pct(reset_str: str) -> float:
    """
    Calculate recommended usage by distributing 100% evenly across remaining time until reset.

    This function distributes the quota uniformly over the remaining time period,
    updating every minute. As time passes, the recommended percentage increases,
    causing the exceeded (red) portion to gradually decrease.

    Strategy:
    - Total period: 7 days (from week_start to reset)
    - Distribute 100% evenly across all remaining minutes
    - Each minute increases recommended usage proportionally
    - Exceeded usage (red bar) shrinks as time progresses

    Args:
        reset_str: Reset time string (e.g., "Oct 27, 9:59am (Asia/Seoul)")

    Returns:
        Recommended usage percentage (0-100) for current time

    Example:
        Reset: Oct 27, 9:59am
        Week start: Oct 24, 9:59am (reset - 7 days)
        Now: Oct 24, 10:48am (49 minutes elapsed)

        Total period: 7 days = 10,080 minutes
        Elapsed: 49 minutes
        Recommended: (49 / 10,080) × 100 = 0.49%

        If actual usage is 100%, then:
        - Blue bar: 0.49% (recommended portion used)
        - Red bar: 99.51% (exceeded portion)

        After 1 minute (10:49am):
        - Recommended: (50 / 10,080) × 100 = 0.50%
        - Red bar shrinks to 99.50%
    """
    from datetime import datetime, timezone as dt_timezone, timedelta
    from zoneinfo import ZoneInfo
    import re

    try:
        # Extract timezone
        tz_match = re.search(r'\((.*?)\)', reset_str)
        tz_name = tz_match.group(1) if tz_match else 'UTC'
        reset_no_tz = reset_str.split(' (')[0].strip()

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = dt_timezone.utc

        # Try to parse "Oct 17, 10am" format (with date)
        date_match = re.search(r'([A-Za-z]+)\s+(\d+),\s+(\d+):?(\d*)(am|pm)', reset_no_tz)

        # If no date found, try time-only format like "9:59am"
        if not date_match:
            time_match = re.search(r'(\d+):?(\d*)(am|pm)', reset_no_tz)
            if not time_match:
                return 0

            # Use today's date with the given time
            hour = int(time_match.group(1))
            minute = int(time_match.group(2)) if time_match.group(2) else 0
            meridiem = time_match.group(3)

            # Convert to 24-hour format
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0

            # Get current time and create reset datetime for today
            now = datetime.now(tz)
            reset_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # If reset is in the past today, find the next occurrence
            # Weekly resets happen within the next 7 days (shown without date in Claude)
            while reset_dt < now:
                reset_dt = reset_dt + timedelta(days=1)
                # Stop if we've gone more than 7 days (safety check)
                if (reset_dt - now).days > 7:
                    break
        else:
            # Parse full date format
            month_name = date_match.group(1)
            day = int(date_match.group(2))
            hour = int(date_match.group(3))
            minute = int(date_match.group(4)) if date_match.group(4) else 0
            meridiem = date_match.group(5)

            # Convert to 24-hour format
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0

            # Parse month
            year = datetime.now(tz).year
            month_num = datetime.strptime(month_name, '%b').month

            # Create reset datetime
            reset_dt = datetime(year, month_num, day, hour, minute, 0, tzinfo=tz)

            # Get current time
            now = datetime.now(tz)

            # If reset is in the past, it might be next year
            if reset_dt < now:
                # Check if adding 7 days puts us in the future
                next_reset = reset_dt + timedelta(days=7)
                if next_reset > now:
                    reset_dt = next_reset
                else:
                    # Must be next year
                    reset_dt = reset_dt.replace(year=year + 1)

        # Week started 7 days before reset
        week_start = reset_dt - timedelta(days=7)

        # If we're before the week start, we're looking at next week
        if now < week_start:
            return 0

        # If we're after the reset, we're in a new week
        if now >= reset_dt:
            return 0

        # Calculate total period in minutes (always 7 days)
        total_period_minutes = 7 * 24 * 60  # 10,080 minutes

        # Calculate elapsed minutes since week start
        elapsed = now - week_start
        elapsed_minutes = elapsed.total_seconds() / 60

        # Calculate recommended percentage based on elapsed time
        # This distributes 100% evenly across the entire 7-day period
        recommended_pct = (elapsed_minutes / total_period_minutes) * 100

        return min(100, recommended_pct)

    except Exception:
        return 0


def _get_bar_color(percentage: int, color_mode: str, colors: dict) -> str:
    """
    Get color based on color mode and usage percentage.

    Args:
        percentage: Usage percentage (0-100)
        color_mode: Color mode ("solid" or "gradient")
        colors: Dictionary with color values:
            - solid: Color for solid mode (hex or Rich color name)
            - gradient_low: Color for 0-X% (hex or Rich color name)
            - gradient_mid: Color for X-Y% (hex or Rich color name)
            - gradient_high: Color for Y-100% (hex or Rich color name)
            - unfilled: Color for unfilled portion (hex or Rich color name)
            - color_range_low: Low range threshold (default: 60)
            - color_range_high: High range threshold (default: 85)

    Returns:
        Color string (hex or Rich color name) for Rich library
    """
    from src.config.defaults import DEFAULT_COLORS

    if color_mode == "solid":
        return colors.get("color_solid", DEFAULT_COLORS['color_solid'])
    elif color_mode == "gradient":
        # Get user-defined color range thresholds
        color_range_low = int(colors.get("color_range_low", DEFAULT_COLORS.get('color_range_low', '60')))
        color_range_high = int(colors.get("color_range_high", DEFAULT_COLORS.get('color_range_high', '85')))

        # Gradation mode: percentage-based colors with user-defined thresholds
        if percentage < color_range_low:
            return colors.get("color_gradient_low", DEFAULT_COLORS['color_gradient_low'])
        elif percentage < color_range_high:
            return colors.get("color_gradient_mid", DEFAULT_COLORS['color_gradient_mid'])
        else:
            return colors.get("color_gradient_high", DEFAULT_COLORS['color_gradient_high'])
    else:
        return colors.get("color_solid", DEFAULT_COLORS['color_solid'])


def _create_usage_bar_with_percent(percentage: int, width: int = 50, color_mode: str = "solid", colors: dict = None) -> Text:
    """
    Create a usage bar for usage page with percentage at the end.
    Format: ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 8%

    Args:
        percentage: Percentage value (0-100)
        width: Total width of bar (excluding percentage text)
        color_mode: Color mode ("solid" or "gradient")
        colors: Dictionary with color values (solid, gradient_low/mid/high, unfilled)

    Returns:
        Rich Text object with bar and percentage
    """
    from src.config.defaults import DEFAULT_COLORS

    if colors is None:
        colors = DEFAULT_COLORS

    filled = int((percentage / 100) * width)
    bar_color = _get_bar_color(percentage, color_mode, colors)
    unfilled_color = colors.get("color_unfilled", DEFAULT_COLORS['color_unfilled'])

    bar_text = Text()
    bar_text.append("█" * filled, style=bar_color)
    bar_text.append("█" * (width - filled), style=unfilled_color)
    bar_text.append(f" {percentage}%", style="bold white")
    return bar_text


def _create_usage_bar_with_recommended(
    current_pct: int,
    recommended_pct: float,
    width: int = 50,
    color_mode: str = "solid",
    colors: dict = None
) -> Text:
    """
    Create a usage bar with recommended usage indicator (combined with percentage).
    Format: ████████████▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░ 14%
            [=current=][recommended][    unfilled     ]

    Args:
        current_pct: Current usage percentage (0-100)
        recommended_pct: Recommended usage percentage (0-100)
        width: Total width of bar (excluding percentage text)
        color_mode: Color mode ("solid" or "gradient")
        colors: Dictionary with color values

    Returns:
        Rich Text object with bar showing current, recommended, and unfilled portions
    """
    from src.config.defaults import DEFAULT_COLORS

    if colors is None:
        colors = DEFAULT_COLORS

    # Calculate filled blocks
    filled = int((current_pct / 100) * width)
    recommended = int((recommended_pct / 100) * width)

    # Get colors
    bar_color = _get_bar_color(current_pct, color_mode, colors)
    recommended_color = colors.get("color_recommended", DEFAULT_COLORS.get("color_recommended", "grey70"))
    exceeded_color = colors.get("color_exceeded", DEFAULT_COLORS.get("color_exceeded", "#ff4444"))
    unfilled_color = colors.get("color_unfilled", DEFAULT_COLORS['color_unfilled'])

    bar_text = Text()

    if current_pct <= recommended_pct:
        # Normal: current usage <= recommended
        bar_text.append("█" * filled, style=bar_color)
        bar_text.append("█" * (recommended - filled), style=recommended_color)
        bar_text.append("█" * (width - recommended), style=unfilled_color)
    else:
        # Exceeded: current usage > recommended
        bar_text.append("█" * recommended, style=bar_color)
        bar_text.append("█" * (filled - recommended), style=exceeded_color)
        bar_text.append("█" * (width - filled), style=unfilled_color)

    bar_text.append(f" {current_pct}%", style="bold white")
    return bar_text


def _create_usage_bar_with_recommended_separate(
    current_pct: int,
    recommended_pct: float,
    width: int = 50,
    color_mode: str = "solid",
    colors: dict = None
) -> Text:
    """
    Create a usage bar with recommended usage indicator (percentage displayed separately).
    Format: ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬░░░░░░░░░░░░░░░░░░░░░░
            [=current=][recommended][  unfilled  ]
    (Percentage is added separately by caller)

    Args:
        current_pct: Current usage percentage (0-100)
        recommended_pct: Recommended usage percentage (0-100)
        width: Total width of bar
        color_mode: Color mode ("solid" or "gradient")
        colors: Dictionary with color values

    Returns:
        Rich Text object with bar (no percentage text)
    """
    from src.config.defaults import DEFAULT_COLORS

    if colors is None:
        colors = DEFAULT_COLORS

    # Calculate filled blocks
    filled = int((current_pct / 100) * width)
    recommended = int((recommended_pct / 100) * width)

    # Get colors
    bar_color = _get_bar_color(current_pct, color_mode, colors)
    recommended_color = colors.get("color_recommended", DEFAULT_COLORS.get("color_recommended", "grey70"))
    exceeded_color = colors.get("color_exceeded", DEFAULT_COLORS.get("color_exceeded", "#ff4444"))
    unfilled_color = colors.get("color_unfilled", DEFAULT_COLORS['color_unfilled'])

    bar_text = Text()

    if current_pct <= recommended_pct:
        # Normal: current usage <= recommended
        bar_text.append("▬" * filled, style=bar_color)
        bar_text.append("▬" * (recommended - filled), style=recommended_color)
        bar_text.append("▬" * (width - recommended), style=unfilled_color)
    else:
        # Exceeded: current usage > recommended
        bar_text.append("▬" * recommended, style=bar_color)
        bar_text.append("▬" * (filled - recommended), style=exceeded_color)
        bar_text.append("▬" * (width - filled), style=unfilled_color)

    return bar_text


def render_dashboard(summary: UsageSummary, stats: AggregatedStats, records: list[UsageRecord], console: Console, skip_limits: bool = False, clear_screen: bool = True, date_range: str = None, limits_from_db: dict | None = None, fast_mode: bool = False, view_mode: str = "usage", is_updating: bool = False, view_mode_ref: dict | None = None) -> None:
    """
    Render a concise, modern dashboard with KPI cards and breakdowns.

    Args:
        summary: Aggregated usage summary (precomputed statistics)
        stats: Aggregated statistics derived from summary
        records: Raw usage records for detailed breakdowns
        console: Rich console for rendering
        skip_limits: If True, skip fetching current limits for faster display
        clear_screen: If True, clear the screen before rendering (default True)
        date_range: Optional date range string to display in footer
        limits_from_db: Pre-fetched limits from database (avoids live fetch)
        fast_mode: If True, show warning that data is from last update
        view_mode: Display mode - "usage", "weekly", "monthly", "yearly", "heatmap", or "devices" (default: "usage")
        is_updating: If True, show updating spinner in footer
        view_mode_ref: Reference dict for view mode state (includes usage_display_mode)
    """
    # Clear screen and move cursor to home in one atomic operation
    # This minimizes flicker by doing both operations together
    if clear_screen:
        import sys
        # Use alternate method: clear from cursor to end of screen, then move to home
        # This is faster than full screen clear
        sys.stdout.write('\033[H\033[J')  # Move to home + clear from cursor to end
        sys.stdout.flush()

    # For heatmap mode, show heatmap instead of dashboard
    if view_mode == "heatmap":
        from src.commands.heatmap import _display_heatmap, _load_limits_data
        from datetime import datetime

        # Get year offset from view_mode_ref
        time_offset = view_mode_ref.get('offset', 0) if view_mode_ref else 0
        target_year = datetime.now().year + time_offset

        limits_data = _load_limits_data()
        _display_heatmap(console, stats, limits_data, year=target_year)
        # Show footer with keyboard shortcuts
        footer = _create_footer(date_range, fast_mode=fast_mode, view_mode=view_mode, in_live_mode=True, is_updating=is_updating, view_mode_ref=view_mode_ref)
        console.print()
        console.print(footer, end="")
        return

    # For devices mode, show device statistics
    if view_mode == "devices":
        from src.visualization.device_stats import render_device_statistics
        from src.storage.snapshot_db import get_device_statistics_for_period
        from datetime import datetime, timedelta

        # Get device week offset and display period from view_mode_ref
        device_week_offset = view_mode_ref.get('device_week_offset', 0) if view_mode_ref else 0
        device_display_period = view_mode_ref.get('device_display_period', 'all') if view_mode_ref else 'all'

        # Calculate date range based on display period and week offset
        today = datetime.now().date()

        if device_display_period == 'all':
            # All time - get actual data range from device statistics
            try:
                device_stats = get_device_statistics_for_period(period='all')
            except sqlite3.OperationalError as e:
                # Handle database errors gracefully
                error_text = Text()
                error_text.append("⚠️  Database Error\n\n", style="bold yellow")
                error_text.append(f"Could not read device statistics: {e}\n\n", style="red")
                error_text.append("This may be due to:\n", style="white")
                error_text.append("  • OneDrive/iCloud sync issues\n", style="dim")
                error_text.append("  • Corrupted database files\n", style="dim")
                error_text.append("  • File permission problems\n\n", style="dim")
                error_text.append("Try: ", style="white")
                error_text.append("ccu reset-db --force", style="bold cyan")
                console.print(Panel(error_text, border_style="red"))
                device_stats = []

            if device_stats:
                # Find the oldest and newest dates across all devices
                all_oldest_dates = [d['oldest_date'] for d in device_stats if d.get('oldest_date')]
                all_newest_dates = [d['newest_date'] for d in device_stats if d.get('newest_date')]

                if all_oldest_dates and all_newest_dates:
                    oldest_date = min(all_oldest_dates)
                    newest_date = max(all_newest_dates)
                    # Convert to datetime for formatting
                    oldest_dt = datetime.strptime(oldest_date, '%Y-%m-%d')
                    newest_dt = datetime.strptime(newest_date, '%Y-%m-%d')
                    devices_date_range = f"{oldest_dt.strftime('%y/%m/%d')} ~ {newest_dt.strftime('%y/%m/%d')}"
                else:
                    devices_date_range = None
            else:
                devices_date_range = None
        elif device_display_period == 'monthly':
            # Current month
            month_start = today.replace(day=1)
            # Calculate last day of month
            if today.month == 12:
                month_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                month_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
            devices_date_range = f"{month_start.strftime('%y/%m/%d')} ~ {month_end.strftime('%y/%m/%d')}"
        elif device_display_period == 'weekly':
            # Calculate week range with offset
            days_since_monday = today.weekday()
            current_week_monday = today - timedelta(days=days_since_monday)
            target_week_monday = current_week_monday + timedelta(weeks=device_week_offset)
            target_week_sunday = target_week_monday + timedelta(days=6)
            devices_date_range = f"{target_week_monday.strftime('%y/%m/%d')} ~ {target_week_sunday.strftime('%y/%m/%d')}"
        else:
            devices_date_range = date_range

        render_device_statistics(console, week_offset=device_week_offset, display_period=device_display_period)
        # Show footer with keyboard shortcuts and calculated date range
        footer = _create_footer(devices_date_range, fast_mode=fast_mode, view_mode=view_mode, in_live_mode=True, is_updating=is_updating, view_mode_ref=view_mode_ref)
        console.print()
        console.print(footer, end="")
        return

    # For usage mode, show only Usage Limits
    if view_mode == "usage":
        from src.models.pricing import format_cost
        from rich.console import Group as RichGroup

        # Get usage display mode from view_mode_ref
        usage_display_mode = view_mode_ref.get('usage_display_mode', 0) if view_mode_ref else 0
        # 0 = M1 (no border, bar+%), 1 = M2 (no border, separate %), 2 = M3 (border, bar+%), 3 = M4 (border, separate %)

        # Get color mode and colors from view_mode_ref
        from src.config.defaults import DEFAULT_COLORS
        color_mode = view_mode_ref.get('color_mode', 'solid') if view_mode_ref else 'solid'
        colors = view_mode_ref.get('colors', DEFAULT_COLORS) if view_mode_ref else DEFAULT_COLORS

        # Determine bar width and style based on mode
        is_m1_mode = usage_display_mode == 0
        is_m2_mode = usage_display_mode == 1
        is_m3_mode = usage_display_mode == 2
        is_m4_mode = usage_display_mode == 3

        # Both modes use terminal width auto-sizing
        terminal_width = console.width
        bar_width = max(20, terminal_width - 14)

        # Use limits from DB if available, otherwise fetch live
        limits = limits_from_db
        if limits is None and not skip_limits:
            from src.commands.limits import capture_limits
            if console:
                with console.status(f"[bold {ORANGE}]Loading usage limits...", spinner="dots", spinner_style=ORANGE):
                    limits = capture_limits()
            else:
                limits = capture_limits()

        # Default content when limits are unavailable (e.g., first launch or skip_limits=True)
        usage_content = Panel(
            Text("Usage limits are unavailable until Claude permissions are granted.\n"
                 "Run 'claude' once in your terminal to authorize, then press 'r' to refresh or wait for the next auto-update.",
                 justify="center",
                 style=DIM),
            title="[bold]Usage Limits",
            border_style="white",
            expand=True,
        )

        if limits and limits.get("error") == "trust_prompt":
            usage_content = Panel(
                Text("Claude needs to trust this folder before usage limits are available.\n"
                     "Run 'claude' once inside this directory to approve access, then launch 'ccu' from the same project path so both commands share the trusted workspace.\n"
                     "After granting permissions, press 'r' to refresh or wait for the next auto-update.",
                     justify="center",
                     style=DIM),
                title="[bold]Usage Limits",
                border_style="white",
                expand=True,
            )
        # Show Usage Limits if available
        elif limits and "error" not in limits:
            # Format reset dates from "Oct 17, 10am" to "10/17"
            def format_reset_date(reset_str: str) -> str:
                """Convert 'Oct 17, 10am (Asia/Seoul)' to '10/17'"""
                import re
                # Remove timezone part
                reset_no_tz = reset_str.split(' (')[0] if '(' in reset_str else reset_str
                # Extract month and day from "Oct 17, 10am" format
                match = re.search(r'([A-Za-z]+)\s+(\d+)', reset_no_tz)
                if match:
                    month_name = match.group(1)
                    day = match.group(2)
                    # Convert month name to number
                    from datetime import datetime
                    month_num = datetime.strptime(month_name, '%b').month
                    return f"{month_num}/{day}"
                return reset_no_tz

            session_reset = format_reset_date(limits['session_reset'])
            week_reset = format_reset_date(limits['week_reset'])
            opus_reset = format_reset_date(limits['opus_reset'])

            # Calculate costs for each limit period
            session_cost = _calculate_session_cost(records)  # Last 5 hours, all models
            weekly_sonnet_cost = _calculate_weekly_sonnet_cost(records)  # Weekly, sonnet only
            weekly_opus_cost = _calculate_weekly_opus_cost(records)  # Weekly, opus only

            # Calculate recommended usage percentages
            from datetime import datetime, timezone as dt_timezone
            from src.storage.snapshot_db import load_user_preferences
            from src.config.defaults import DEFAULT_PREFERENCES

            prefs = load_user_preferences()
            weekly_days = int(prefs.get('weekly_recommended_days',
                                         DEFAULT_PREFERENCES['weekly_recommended_days']))

            # Parse session reset time to calculate elapsed time
            session_recommended_pct = 0
            if limits.get('session_reset'):
                session_recommended_pct = _calculate_session_recommended_pct(limits['session_reset'])

            # Parse week reset time to calculate elapsed days
            weekly_recommended_pct = 0
            if limits.get('week_reset'):
                weekly_recommended_pct = _calculate_weekly_recommended_pct(limits['week_reset'], weekly_days)

            # Parse opus reset time with distributed calculation (separate from week reset)
            # Opus uses even distribution across 7-day period for gradual red bar reduction
            opus_recommended_pct = 0
            if limits.get('opus_reset'):
                opus_recommended_pct = _calculate_distributed_recommended_pct(limits['opus_reset'])

            # Create table structure with 3 rows per limit
            # M1/M2 modes use no padding, M3/M4 modes use reduced padding for compact display
            table_padding = (0, 1) if (is_m3_mode or is_m4_mode) else (0, 0)
            limits_table = Table(show_header=False, box=None, padding=table_padding)
            limits_table.add_column("Content", justify="left")

            # M1 mode: compact style with bar+percentage combined, no border
            if is_m1_mode:
                # Session limit (3 rows)
                limits_table.add_row("Current session")

                # Check if recommended usage indicator should be shown for session
                if session_recommended_pct > 0:
                    session_bar = _create_usage_bar_with_recommended(
                        limits["session_pct"],
                        session_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    session_bar = _create_usage_bar_with_percent(limits["session_pct"], width=bar_width, color_mode=color_mode, colors=colors)

                limits_table.add_row(session_bar)
                limits_table.add_row(f"Resets {session_reset} ({format_cost(session_cost)})", style=DIM)
                limits_table.add_row("")  # Blank line

                # Week limit (3 rows)
                limits_table.add_row("Current week (all models)")

                # Check if recommended usage indicator should be shown for week
                if weekly_recommended_pct > 0:
                    # Show bar with recommended usage indicator
                    week_bar = _create_usage_bar_with_recommended(
                        limits["week_pct"],
                        weekly_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    # No recommendation available - use standard bar
                    week_bar = _create_usage_bar_with_percent(limits["week_pct"], width=bar_width, color_mode=color_mode, colors=colors)

                limits_table.add_row(week_bar)
                limits_table.add_row(f"Resets {week_reset} ({format_cost(weekly_sonnet_cost)})", style=DIM)
                limits_table.add_row("")  # Blank line

                # Opus limit (2-3 rows: hide reset info if 0%, matching claude /usage behavior)
                limits_table.add_row("Current week (Opus)")

                # Opus uses its own reset time (opus_reset) which may differ from week_reset
                if opus_recommended_pct > 0:
                    opus_bar = _create_usage_bar_with_recommended(
                        limits["opus_pct"],
                        opus_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    opus_bar = _create_usage_bar_with_percent(limits["opus_pct"], width=bar_width, color_mode=color_mode, colors=colors)

                limits_table.add_row(opus_bar)
                # Only show reset info if usage > 0%
                if limits["opus_pct"] > 0:
                    limits_table.add_row(f"Resets {opus_reset} ({format_cost(weekly_opus_cost)})", style=DIM)

                # Store table for later grouped output
                usage_content = limits_table

            elif is_m2_mode:
                # M2 mode: M1 style (no border) with M4 bars (percentage separated)
                # Session limit (3 rows)
                limits_table.add_row("Current session")

                if session_recommended_pct > 0:
                    session_bar = _create_usage_bar_with_recommended_separate(
                        limits["session_pct"],
                        session_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    session_bar = _create_bar(limits["session_pct"], 100, width=bar_width, color=_get_bar_color(limits["session_pct"], color_mode, colors))

                bar_text = Text()
                bar_text.append(session_bar)
                bar_text.append(f"  {limits['session_pct']}%", style="bold white")
                limits_table.add_row(bar_text)
                limits_table.add_row(f"Resets {session_reset} ({format_cost(session_cost)})", style=DIM)
                limits_table.add_row("")  # Blank line

                # Week limit (3 rows)
                limits_table.add_row("Current week (all models)")

                # Check if recommended usage indicator should be shown for week
                if weekly_recommended_pct > 0:
                    # Show bar with recommended usage indicator
                    week_bar = _create_usage_bar_with_recommended_separate(
                        limits["week_pct"],
                        weekly_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    # No recommendation available - use standard bar
                    week_bar = _create_bar(limits["week_pct"], 100, width=bar_width, color=_get_bar_color(limits["week_pct"], color_mode, colors))

                bar_text = Text()
                bar_text.append(week_bar)
                bar_text.append(f"  {limits['week_pct']}%", style="bold white")
                limits_table.add_row(bar_text)
                limits_table.add_row(f"Resets {week_reset} ({format_cost(weekly_sonnet_cost)})", style=DIM)
                limits_table.add_row("")  # Blank line

                # Opus limit (2-3 rows: hide reset info if 0%, matching claude /usage behavior)
                limits_table.add_row("Current week (Opus)")

                # Opus uses its own reset time (opus_reset) which may differ from week_reset
                if opus_recommended_pct > 0:
                    opus_bar = _create_usage_bar_with_recommended_separate(
                        limits["opus_pct"],
                        opus_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    opus_bar = _create_bar(limits["opus_pct"], 100, width=bar_width, color=_get_bar_color(limits["opus_pct"], color_mode, colors))

                bar_text = Text()
                bar_text.append(opus_bar)
                bar_text.append(f"  {limits['opus_pct']}%", style="bold white")
                limits_table.add_row(bar_text)
                # Only show reset info if usage > 0%
                if limits["opus_pct"] > 0:
                    limits_table.add_row(f"Resets {opus_reset} ({format_cost(weekly_opus_cost)})", style=DIM)

                # Store table for later grouped output
                usage_content = limits_table

            elif is_m3_mode:
                # M3 mode: dashboard style with bar+percentage combined (like M1) and panel wrapper
                # Session limit (3 rows)
                limits_table.add_row("Current session")

                if session_recommended_pct > 0:
                    session_bar = _create_usage_bar_with_recommended(
                        limits["session_pct"],
                        session_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    session_bar = _create_usage_bar_with_percent(limits["session_pct"], width=bar_width, color_mode=color_mode, colors=colors)

                limits_table.add_row(session_bar)
                limits_table.add_row(f"Resets {session_reset} ({format_cost(session_cost)})", style=DIM)
                limits_table.add_row("")  # Blank line

                # Week limit (3 rows)
                limits_table.add_row("Current week (all models)")

                # Check if recommended usage indicator should be shown for week
                if weekly_recommended_pct > 0:
                    # Show bar with recommended usage indicator
                    week_bar = _create_usage_bar_with_recommended(
                        limits["week_pct"],
                        weekly_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    # No recommendation available - use standard bar
                    week_bar = _create_usage_bar_with_percent(limits["week_pct"], width=bar_width, color_mode=color_mode, colors=colors)

                limits_table.add_row(week_bar)
                limits_table.add_row(f"Resets {week_reset} ({format_cost(weekly_sonnet_cost)})", style=DIM)
                limits_table.add_row("")  # Blank line

                # Opus limit (2-3 rows: hide reset info if 0%, matching claude /usage behavior)
                limits_table.add_row("Current week (Opus)")

                # Opus uses its own reset time (opus_reset) which may differ from week_reset
                if opus_recommended_pct > 0:
                    opus_bar = _create_usage_bar_with_recommended(
                        limits["opus_pct"],
                        opus_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    opus_bar = _create_usage_bar_with_percent(limits["opus_pct"], width=bar_width, color_mode=color_mode, colors=colors)

                limits_table.add_row(opus_bar)
                # Only show reset info if usage > 0%
                if limits["opus_pct"] > 0:
                    limits_table.add_row(f"Resets {opus_reset} ({format_cost(weekly_opus_cost)})", style=DIM)

                # Wrap in outer "Usage Limits" panel
                usage_content = Panel(
                    limits_table,
                    title="[bold]Usage Limits",
                    border_style="white",
                    expand=True,
                )

            elif is_m4_mode:
                # M4 mode: dashboard style with percentage separated and panel wrapper
                # Session limit (3 rows)
                limits_table.add_row("Current session")

                if session_recommended_pct > 0:
                    session_bar = _create_usage_bar_with_recommended_separate(
                        limits["session_pct"],
                        session_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    session_bar = _create_bar(limits["session_pct"], 100, width=bar_width, color=_get_bar_color(limits["session_pct"], color_mode, colors))

                bar_text = Text()
                bar_text.append(session_bar)
                bar_text.append(f"  {limits['session_pct']}%", style="bold white")
                limits_table.add_row(bar_text)
                limits_table.add_row(f"Resets {session_reset} ({format_cost(session_cost)})", style=DIM)
                limits_table.add_row("")  # Blank line

                # Week limit (3 rows)
                limits_table.add_row("Current week (all models)")

                # Check if recommended usage indicator should be shown for week
                if weekly_recommended_pct > 0:
                    # Show bar with recommended usage indicator
                    week_bar = _create_usage_bar_with_recommended_separate(
                        limits["week_pct"],
                        weekly_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    # No recommendation available - use standard bar
                    week_bar = _create_bar(limits["week_pct"], 100, width=bar_width, color=_get_bar_color(limits["week_pct"], color_mode, colors))

                bar_text = Text()
                bar_text.append(week_bar)
                bar_text.append(f"  {limits['week_pct']}%", style="bold white")
                limits_table.add_row(bar_text)
                limits_table.add_row(f"Resets {week_reset} ({format_cost(weekly_sonnet_cost)})", style=DIM)
                limits_table.add_row("")  # Blank line

                # Opus limit (2-3 rows: hide reset info if 0%, matching claude /usage behavior)
                limits_table.add_row("Current week (Opus)")

                # Opus uses its own reset time (opus_reset) which may differ from week_reset
                if opus_recommended_pct > 0:
                    opus_bar = _create_usage_bar_with_recommended_separate(
                        limits["opus_pct"],
                        opus_recommended_pct,
                        width=bar_width,
                        color_mode=color_mode,
                        colors=colors
                    )
                else:
                    opus_bar = _create_bar(limits["opus_pct"], 100, width=bar_width, color=_get_bar_color(limits["opus_pct"], color_mode, colors))

                bar_text = Text()
                bar_text.append(opus_bar)
                bar_text.append(f"  {limits['opus_pct']}%", style="bold white")
                limits_table.add_row(bar_text)
                # Only show reset info if usage > 0%
                if limits["opus_pct"] > 0:
                    limits_table.add_row(f"Resets {opus_reset} ({format_cost(weekly_opus_cost)})", style=DIM)

                # Wrap in outer "Usage Limits" panel
                usage_content = Panel(
                    limits_table,
                    title="[bold]Usage Limits",
                    border_style="white",
                    expand=True,
                )

        # Create footer
        footer = _create_footer(date_range, fast_mode=fast_mode, view_mode=view_mode, in_live_mode=True, is_updating=is_updating, view_mode_ref=view_mode_ref)

        # Group everything together and print once
        final_output = RichGroup(
            Text(""),  # Top blank line
            usage_content,
            Text(""),  # Blank line before footer
            footer
        )
        console.print(final_output, end="")

        return

    # Check if we're in daily detail mode or message detail mode for weekly view
    daily_detail_date = None
    hourly_detail_hour = None
    if view_mode == "weekly" and view_mode_ref:
        daily_detail_date = view_mode_ref.get("daily_detail_date")
        hourly_detail_hour = view_mode_ref.get("hourly_detail_hour")

    # Create footer with export info, date range, and view mode
    footer = _create_footer(date_range, fast_mode=fast_mode, view_mode=view_mode, in_live_mode=True, is_updating=is_updating, view_mode_ref=view_mode_ref)

    # Create breakdowns for each view mode
    sections_to_render = []

    if daily_detail_date and hourly_detail_hour is not None:
        # Message detail mode - show messages for specific hour
        from src.storage.snapshot_db import load_all_devices_messages_by_hour
        hourly_messages = load_all_devices_messages_by_hour(daily_detail_date, hourly_detail_hour)
        # Get content mode: "hide" (default), "brief", or "detail"
        content_mode = view_mode_ref.get('message_content_mode', 'hide') if view_mode_ref else 'hide'
        message_detail = _create_message_detail_view(hourly_messages, daily_detail_date, hourly_detail_hour, content_mode, view_mode_ref)
        sections_to_render.append(("message_detail", message_detail))
    elif daily_detail_date:
        # Daily detail mode - show only the detail view without KPI section
        daily_detail = _create_daily_detail_view(records, daily_detail_date)
        sections_to_render.append(("daily_detail", daily_detail))

        # Calculate hourly hours for keyboard navigation
        # Filter records to only those from target_date (using local timezone)
        from collections import defaultdict
        from src.utils.timezone import format_local_time, get_user_timezone

        filtered_records = []
        for record in records:
            if record.timestamp:
                # Convert UTC timestamp to local timezone for date comparison
                try:
                    local_time = record.timestamp.astimezone()
                    record_date = local_time.strftime("%Y-%m-%d")
                    if record_date == daily_detail_date:
                        filtered_records.append(record)
                except Exception:
                    # Fallback to UTC if timezone conversion fails
                    if record.timestamp.strftime("%Y-%m-%d") == daily_detail_date:
                        filtered_records.append(record)

        if filtered_records:
            user_tz = get_user_timezone()
            hourly_data = defaultdict(int)

            for record in filtered_records:
                if record.token_usage:
                    # Convert UTC timestamp to local timezone hour
                    hour = format_local_time(record.timestamp, "%H:00", user_tz)
                    hourly_data[hour] += record.token_usage.total_tokens

            # Sort by hour in descending order (same as display order)
            sorted_hours = sorted(hourly_data.keys(), reverse=True)

            # Extract hour numbers (00-23) as integers
            hourly_hours_int = []
            for hour_str in sorted_hours:
                try:
                    hour_int = int(hour_str.split(":")[0])
                    hourly_hours_int.append(hour_int)
                except:
                    pass

            # Store in view_mode_ref for keyboard listener
            if view_mode_ref:
                view_mode_ref['hourly_hours'] = hourly_hours_int
    else:
        # Normal mode - show KPI section and breakdowns
        # Calculate totals from records (multi-device support)
        # Always use _calculate_totals_for_records() to ensure all devices are included
        # Previously used summary-based calculations, but summary only contains current device data
        scoped_totals = _calculate_totals_for_records(records)

        kpi_section = _create_kpi_section(summary, records, view_mode=view_mode, skip_limits=skip_limits, console=console, limits_from_db=limits_from_db, view_mode_ref=view_mode_ref, scoped_totals=scoped_totals)

        # Render Summary (and Usage Limits in weekly mode)
        console.print(kpi_section, end="")
        console.print()  # Blank line between sections

        # Model breakdown is always important
        model_breakdown = _create_model_breakdown(records)
        sections_to_render.append(("model", model_breakdown))

        # Add mode-specific breakdown
        if view_mode == "weekly":
            # Show normal weekly breakdown
            project_breakdown = _create_project_breakdown(records)
            sections_to_render.append(("project", project_breakdown))

            # Check weekly display mode (limits or calendar)
            weekly_display_mode = view_mode_ref.get('weekly_display_mode', 'limits') if view_mode_ref else 'limits'

            if weekly_display_mode == 'calendar':
                # Show calendar week (Mon-Sun, current ISO week)
                daily_breakdown_calendar = _create_daily_breakdown_calendar_week(records)
                sections_to_render.append(("daily_calendar", daily_breakdown_calendar))
            else:
                # Show Usage Limits week (default)
                # Get week range from view_mode_ref if available
                week_start = view_mode_ref.get('week_start_date') if view_mode_ref else None
                week_end = view_mode_ref.get('week_end_date') if view_mode_ref else None
                reset_time = view_mode_ref.get('week_reset_time') if view_mode_ref else None
                reset_day = view_mode_ref.get('week_reset_day') if view_mode_ref else None

                daily_breakdown_weekly = _create_daily_breakdown_weekly(records, week_start, week_end, reset_time, reset_day)
                sections_to_render.append(("daily_weekly", daily_breakdown_weekly))
        elif view_mode == "monthly":
            project_breakdown = _create_project_breakdown(records)
            sections_to_render.append(("project", project_breakdown))

            # Check monthly display mode (daily or weekly)
            monthly_display_mode = view_mode_ref.get('monthly_display_mode', 'daily') if view_mode_ref else 'daily'

            monthly_daily_summary = None
            if view_mode_ref:
                target_year = view_mode_ref.get('target_year')
                target_month = view_mode_ref.get('target_month')
                if isinstance(target_year, int) and isinstance(target_month, int):
                    prefix = f"{target_year:04d}-{target_month:02d}"
                    monthly_daily_summary = {
                        date: totals
                        for date, totals in summary.daily.items()
                        if date.startswith(prefix)
                    }

            if monthly_display_mode == 'weekly':
                # Show weekly breakdown (calendar weeks for this month)
                target_year = view_mode_ref.get('target_year') if view_mode_ref else None
                target_month = view_mode_ref.get('target_month') if view_mode_ref else None
                if not isinstance(target_year, int) or not isinstance(target_month, int):
                    from datetime import datetime
                    current_date = datetime.now()
                    target_year = current_date.year
                    target_month = current_date.month

                weekly_breakdown = _create_weekly_breakdown_for_month(records, target_year, target_month)
                sections_to_render.append(("weekly_month", weekly_breakdown))
            else:
                # Show daily breakdown (default)
                daily_breakdown = _create_daily_breakdown(records, monthly_daily_summary)
                sections_to_render.append(("daily", daily_breakdown))
        elif view_mode == "yearly":
            project_breakdown = _create_project_breakdown(records)
            sections_to_render.append(("project", project_breakdown))

            # Check yearly display mode (monthly or weekly)
            yearly_display_mode = view_mode_ref.get('yearly_display_mode', 'monthly') if view_mode_ref else 'monthly'

            if yearly_display_mode == 'weekly':
                # Show weekly breakdown (calendar weeks)
                target_year = view_mode_ref.get('target_year') if view_mode_ref else None
                if not isinstance(target_year, int):
                    from datetime import datetime
                    target_year = datetime.now().year

                weekly_breakdown = _create_weekly_breakdown_calendar(records, target_year)
                sections_to_render.append(("weekly_calendar", weekly_breakdown))
            else:
                # Show monthly breakdown (default)
                target_year = view_mode_ref.get('target_year') if view_mode_ref else None
                monthly_breakdown = _create_monthly_breakdown(records, summary=summary, target_year=target_year)
                sections_to_render.append(("monthly", monthly_breakdown))

    # Render sections
    for section_type, section in sections_to_render:
        console.print(section, end="")
        console.print()  # Blank line between sections

    # Always render footer
    console.print(footer, end="")


def _calculate_session_cost(records: list[UsageRecord]) -> float:
    """
    Calculate cost for session limit period (last 5 hours, all models).

    Args:
        records: List of usage records

    Returns:
        Total cost for session period
    """
    from src.models.pricing import calculate_cost
    from datetime import timedelta, timezone

    # Use timezone-aware datetime to match record.timestamp
    now = datetime.now(timezone.utc)
    five_hours_ago = now - timedelta(hours=5)

    session_cost = 0.0
    for record in records:
        if record.timestamp >= five_hours_ago and record.model and record.token_usage and record.model != "<synthetic>":
            cost = calculate_cost(
                record.token_usage.input_tokens,
                record.token_usage.output_tokens,
                record.model,
                record.token_usage.cache_creation_tokens,
                record.token_usage.cache_read_tokens,
            )
            session_cost += cost

    return session_cost


def _calculate_weekly_sonnet_cost(records: list[UsageRecord]) -> float:
    """
    Calculate cost for weekly sonnet usage (last 7 days, sonnet models only).

    Args:
        records: List of usage records

    Returns:
        Total cost for weekly sonnet usage
    """
    from src.models.pricing import calculate_cost
    from datetime import timedelta, timezone

    # Use timezone-aware datetime to match record.timestamp
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    weekly_cost = 0.0
    for record in records:
        if record.timestamp >= seven_days_ago and record.model and record.token_usage and record.model != "<synthetic>":
            # Check if it's a sonnet model
            if "sonnet" in record.model.lower():
                cost = calculate_cost(
                    record.token_usage.input_tokens,
                    record.token_usage.output_tokens,
                    record.model,
                    record.token_usage.cache_creation_tokens,
                    record.token_usage.cache_read_tokens,
                )
                weekly_cost += cost

    return weekly_cost


def _calculate_weekly_opus_cost(records: list[UsageRecord]) -> float:
    """
    Calculate cost for weekly opus usage (last 7 days, opus models only).

    Args:
        records: List of usage records

    Returns:
        Total cost for weekly opus usage
    """
    from src.models.pricing import calculate_cost
    from datetime import timedelta, timezone

    # Use timezone-aware datetime to match record.timestamp
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    weekly_cost = 0.0
    for record in records:
        if record.timestamp >= seven_days_ago and record.model and record.token_usage and record.model != "<synthetic>":
            # Check if it's an opus model
            if "opus" in record.model.lower():
                cost = calculate_cost(
                    record.token_usage.input_tokens,
                    record.token_usage.output_tokens,
                    record.model,
                    record.token_usage.cache_creation_tokens,
                    record.token_usage.cache_read_tokens,
                )
                weekly_cost += cost

    return weekly_cost


def _calculate_totals_for_month(summary: UsageSummary, year: int, month: int) -> DailyTotal | None:
    """Aggregate totals from UsageSummary for a specific month."""
    from datetime import datetime

    aggregated = DailyTotal(date=f"{year:04d}-{month:02d}")
    found = False

    for date_str, totals in summary.daily.items():
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if date_obj.year == year and date_obj.month == month:
            aggregated.total_prompts += totals.total_prompts
            aggregated.total_responses += totals.total_responses
            aggregated.total_sessions += totals.total_sessions
            aggregated.total_tokens += totals.total_tokens
            aggregated.input_tokens += totals.input_tokens
            aggregated.output_tokens += totals.output_tokens
            aggregated.cache_creation_tokens += totals.cache_creation_tokens
            aggregated.cache_read_tokens += totals.cache_read_tokens
            aggregated.total_cost += totals.total_cost
            found = True

    return aggregated if found else None


def _calculate_totals_for_year(summary: UsageSummary, year: int) -> DailyTotal | None:
    """Aggregate totals from UsageSummary for a specific year."""
    from datetime import datetime

    aggregated = DailyTotal(date=f"{year:04d}")
    found = False

    for date_str, totals in summary.daily.items():
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if date_obj.year == year:
            aggregated.total_prompts += totals.total_prompts
            aggregated.total_responses += totals.total_responses
            aggregated.total_sessions += totals.total_sessions
            aggregated.total_tokens += totals.total_tokens
            aggregated.input_tokens += totals.input_tokens
            aggregated.output_tokens += totals.output_tokens
            aggregated.cache_creation_tokens += totals.cache_creation_tokens
            aggregated.cache_read_tokens += totals.cache_read_tokens
            aggregated.total_cost += totals.total_cost
            found = True

    return aggregated if found else None


def _calculate_totals_for_records(records: list[UsageRecord]) -> DailyTotal:
    """
    Aggregate high-level totals for a scoped set of usage records.

    Args:
        records: Usage records to aggregate

    Returns:
        DailyTotal object containing aggregated metrics
    """
    from src.models.pricing import calculate_cost

    totals = DailyTotal(date="scoped")
    unique_sessions: set[str] = set()
    total_cost = 0.0

    for record in records:
        if record.session_id:
            unique_sessions.add(record.session_id)

        if record.is_user_prompt:
            totals.total_prompts += 1
        elif record.is_assistant_response:
            totals.total_responses += 1

        usage = record.token_usage
        if usage:
            totals.total_tokens += usage.total_tokens
            totals.input_tokens += usage.input_tokens
            totals.output_tokens += usage.output_tokens
            totals.cache_creation_tokens += usage.cache_creation_tokens
            totals.cache_read_tokens += usage.cache_read_tokens

            if record.model and record.model != "<synthetic>":
                total_cost += calculate_cost(
                    usage.input_tokens,
                    usage.output_tokens,
                    record.model,
                    usage.cache_creation_tokens,
                    usage.cache_read_tokens,
                )

    totals.total_sessions = len(unique_sessions)
    totals.total_cost = total_cost
    return totals


def _create_kpi_section(summary: UsageSummary, records: list[UsageRecord], view_mode: str = "monthly", skip_limits: bool = False, console: Console = None, limits_from_db: dict | None = None, view_mode_ref: dict | None = None, scoped_totals: DailyTotal | None = None) -> Group:
    """
    Create KPI cards with individual limit boxes beneath each (only for weekly mode).

    Args:
        summary: Aggregated usage summary for entire history
        records: List of usage records (for cost calculation)
        view_mode: Current view mode - "monthly", "weekly", or "yearly"
        skip_limits: If True, skip fetching current limits (faster)
        console: Console instance for showing spinner
        limits_from_db: Pre-fetched limits from database (avoids live fetch)
        view_mode_ref: Reference dict for view mode state (includes color settings)

    Returns:
        Group containing KPI cards and limit boxes (if weekly mode)
    """
    from src.models.pricing import format_cost

    totals_source = scoped_totals or summary.totals

    total_cost = totals_source.total_cost
    total_input_tokens = totals_source.input_tokens
    total_output_tokens = totals_source.output_tokens
    total_cache_creation = totals_source.cache_creation_tokens
    total_cache_read = totals_source.cache_read_tokens
    total_messages = totals_source.total_prompts + totals_source.total_responses

    # Create KPI cards in 2 rows of 3 cards each
    kpi_grid = Table.grid(padding=(0, 2), expand=False)
    kpi_grid.add_column(justify="center")
    kpi_grid.add_column(justify="center")
    kpi_grid.add_column(justify="center")

    # Row 1: Cost, Messages, Input Tokens
    cost_card = Panel(
        Text(_format_number(total_cost) if isinstance(total_cost, int) else format_cost(total_cost), style="bold green"),
        title="Cost",
        border_style="white",
        width=36,
    )

    messages_card = Panel(
        Text(_format_number(total_messages), style="bold white"),
        title="Messages",
        border_style="white",
        width=36,
    )

    input_tokens_card = Panel(
        Text(_format_number(total_input_tokens), style=f"bold {BLUE}"),
        title="Input Tokens",
        border_style="white",
        width=36,
    )

    kpi_grid.add_row(cost_card, messages_card, input_tokens_card)

    # Row 2: Output Tokens, Cache Creation, Cache Read
    output_tokens_card = Panel(
        Text(_format_number(total_output_tokens), style=f"bold {BLUE}"),
        title="Output Tokens",
        border_style="white",
        width=36,
    )

    cache_creation_card = Panel(
        Text(_format_number(total_cache_creation), style="bold magenta"),
        title="Cache Write",
        border_style="white",
        width=36,
    )

    cache_read_card = Panel(
        Text(_format_number(total_cache_read), style="bold magenta"),
        title="Cache Read",
        border_style="white",
        width=36,
    )

    kpi_grid.add_row(output_tokens_card, cache_creation_card, cache_read_card)

    # For weekly mode, add limit boxes below KPI cards
    if view_mode == "weekly":
        # Use limits from DB if provided, otherwise fetch live (unless skipped)
        limits = limits_from_db
        if limits is None and not skip_limits:
            from src.commands.limits import capture_limits
            if console:
                with console.status(f"[bold {ORANGE}]Loading usage limits...", spinner="dots", spinner_style=ORANGE):
                    limits = capture_limits()
            else:
                limits = capture_limits()

        # Create individual limit boxes if available
        if limits and "error" not in limits:
            # Use reset strings as-is (no formatting) for weekly mode
            session_reset = limits['session_reset']
            week_reset = limits['week_reset']
            opus_reset = limits['opus_reset']

            # Calculate costs for each limit period
            from datetime import timedelta
            session_cost = _calculate_session_cost(records)  # Last 5 hours, all models
            weekly_sonnet_cost = _calculate_weekly_sonnet_cost(records)  # Weekly, sonnet only
            weekly_opus_cost = _calculate_weekly_opus_cost(records)  # Weekly, opus only

            # Get color mode and colors from view_mode_ref
            from src.config.defaults import DEFAULT_COLORS
            color_mode = view_mode_ref.get('color_mode', 'solid') if view_mode_ref else 'solid'
            colors = view_mode_ref.get('colors', DEFAULT_COLORS) if view_mode_ref else DEFAULT_COLORS

            # Calculate recommended usage percentages
            from datetime import datetime, timezone as dt_timezone
            from src.storage.snapshot_db import load_user_preferences
            from src.config.defaults import DEFAULT_PREFERENCES

            prefs = load_user_preferences()
            weekly_days = int(prefs.get('weekly_recommended_days',
                                         DEFAULT_PREFERENCES['weekly_recommended_days']))

            # Parse session reset time to calculate elapsed time
            session_recommended_pct = 0
            if limits.get('session_reset'):
                session_recommended_pct = _calculate_session_recommended_pct(limits['session_reset'])

            # Parse week reset time to calculate elapsed days
            weekly_recommended_pct = 0
            if limits.get('week_reset'):
                weekly_recommended_pct = _calculate_weekly_recommended_pct(limits['week_reset'], weekly_days)

            # Parse opus reset time with distributed calculation (separate from week reset)
            # Opus uses even distribution across 7-day period for gradual red bar reduction
            opus_recommended_pct = 0
            if limits.get('opus_reset'):
                opus_recommended_pct = _calculate_distributed_recommended_pct(limits['opus_reset'])

            # Calculate bar width based on terminal width (same as usage mode)
            terminal_width = console.width if console else 120
            bar_width = max(20, terminal_width - 14)

            # Create table structure with 3 rows per limit (G3 style - bar+percentage combined)
            limits_table = Table(show_header=False, box=None, padding=(0, 2))
            limits_table.add_column("Content", justify="left")

            # Session limit (3 rows)
            limits_table.add_row("Current session")

            if session_recommended_pct > 0:
                session_bar = _create_usage_bar_with_recommended(
                    limits["session_pct"],
                    session_recommended_pct,
                    width=bar_width,
                    color_mode=color_mode,
                    colors=colors
                )
            else:
                session_bar = _create_usage_bar_with_percent(limits["session_pct"], width=bar_width, color_mode=color_mode, colors=colors)

            limits_table.add_row(session_bar)
            limits_table.add_row(f"Resets {session_reset} ({format_cost(session_cost)})", style=DIM)
            limits_table.add_row("")  # Blank line

            # Week limit (3 rows)
            limits_table.add_row("Current week (all models)")

            if weekly_recommended_pct > 0:
                week_bar = _create_usage_bar_with_recommended(
                    limits["week_pct"],
                    weekly_recommended_pct,
                    width=bar_width,
                    color_mode=color_mode,
                    colors=colors
                )
            else:
                week_bar = _create_usage_bar_with_percent(limits["week_pct"], width=bar_width, color_mode=color_mode, colors=colors)

            limits_table.add_row(week_bar)
            limits_table.add_row(f"Resets {week_reset} ({format_cost(weekly_sonnet_cost)})", style=DIM)
            limits_table.add_row("")  # Blank line

            # Opus limit (2-3 rows: hide reset info if 0%, matching claude /usage behavior)
            limits_table.add_row("Current week (Opus)")

            # Opus uses its own reset time (opus_reset) which may differ from week_reset
            if opus_recommended_pct > 0:
                opus_bar = _create_usage_bar_with_recommended(
                    limits["opus_pct"],
                    opus_recommended_pct,
                    width=bar_width,
                    color_mode=color_mode,
                    colors=colors
                )
            else:
                opus_bar = _create_usage_bar_with_percent(limits["opus_pct"], width=bar_width, color_mode=color_mode, colors=colors)

            limits_table.add_row(opus_bar)
            # Only show reset info if usage > 0%
            if limits["opus_pct"] > 0:
                limits_table.add_row(f"Resets {opus_reset} ({format_cost(weekly_opus_cost)})", style=DIM)

            # Wrap in outer "Usage Limits" panel (expand to fit terminal width)
            limits_outer_panel = Panel(
                limits_table,
                title="[bold]Usage Limits",
                border_style="white",
                expand=True,
            )

            # Add single line spacing between Summary and Usage Limits
            spacing = Text("")
            return Group(kpi_grid, spacing, limits_outer_panel)

    # Return only KPI cards for monthly/yearly modes
    return Group(kpi_grid)


def _create_model_breakdown(records: list[UsageRecord]) -> Panel:
    """
    Create table showing token usage and cost per model.

    Args:
        records: Usage records scoped to the current view

    Returns:
        Panel with model breakdown table including costs
    """
    from src.models.pricing import calculate_cost, format_cost

    model_totals: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"tokens": 0, "cost": 0.0})

    for record in records:
        usage = record.token_usage
        if not usage:
            continue

        model_name = record.model or "<unknown>"
        tokens = usage.total_tokens
        model_totals[model_name]["tokens"] += tokens

        if record.model and record.model != "<synthetic>":
            model_totals[model_name]["cost"] += calculate_cost(
                usage.input_tokens,
                usage.output_tokens,
                record.model,
                usage.cache_creation_tokens,
                usage.cache_read_tokens,
            )

    if not model_totals:
        return Panel(
            Text("No model data available", style=DIM),
            title="[bold]Tokens by Model",
            border_style="white",
        )

    total_tokens = sum(data["tokens"] for data in model_totals.values())
    total_cost = sum(data["cost"] for data in model_totals.values())
    max_tokens = max((data["tokens"] for data in model_totals.values()), default=0)

    sorted_models = sorted(model_totals.items(), key=lambda x: x[1]["tokens"], reverse=True)

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Model", style="white", justify="left", width=30, overflow="crop")
    table.add_column("", justify="left", width=20)
    table.add_column("Tokens", style=ORANGE, justify="right", width=12)
    table.add_column("%", style=CYAN, justify="right", width=8)
    table.add_column("Cost", style="green", justify="right", width=10)

    for model, data in sorted_models:
        display_name = model.split("/")[-1] if "/" in model else model
        if "claude" in display_name.lower():
            display_name = display_name.replace("claude-", "")

        tokens = data["tokens"]
        percentage = (tokens / total_tokens * 100) if total_tokens > 0 else 0
        bar = _create_bar(tokens, max_tokens, width=20)

        table.add_row(
            display_name,
            bar,
            _format_number(tokens),
            f"[cyan]{percentage:.1f}%[/cyan]",
            format_cost(data["cost"]),
        )

    table.add_row("", "", "", "", "")

    table.add_row(
        "[bold]Total",
        Text(""),
        f"[bold]{_format_number(total_tokens)}",
        "[bold cyan]100.0%" if total_tokens > 0 else "[bold cyan]0.0%",
        f"[bold green]{format_cost(total_cost)}",
    )

    return Panel(
        table,
        title="[bold]Tokens by Model",
        border_style="white",
        expand=True,
    )


def _create_project_breakdown(records: list[UsageRecord]) -> Panel:
    """
    Create table showing token usage and cost per project.

    Args:
        records: List of usage records

    Returns:
        Panel with project breakdown table
    """
    from src.models.pricing import calculate_cost, format_cost

    folder_totals: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"tokens": 0, "cost": 0.0})

    for record in records:
        usage = record.token_usage
        if not usage:
            continue

        folder = record.folder or "<unknown>"
        folder_totals[folder]["tokens"] += usage.total_tokens

        if record.model and record.model != "<synthetic>":
            folder_totals[folder]["cost"] += calculate_cost(
                usage.input_tokens,
                usage.output_tokens,
                record.model,
                usage.cache_creation_tokens,
                usage.cache_read_tokens,
            )

    if not folder_totals:
        return Panel(
            Text("No project data available", style=DIM),
            title="[bold]Tokens by Project",
            border_style="white",
        )

    total_tokens = sum(data["tokens"] for data in folder_totals.values())
    total_cost = sum(data["cost"] for data in folder_totals.values())
    sorted_folders = sorted(folder_totals.items(), key=lambda x: x[1]["tokens"], reverse=True)
    sorted_folders = sorted_folders[:10]
    max_tokens = max((data["tokens"] for _, data in sorted_folders), default=0)

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Project", style="white", justify="left", width=30, overflow="crop")
    table.add_column("", justify="left", width=20)
    table.add_column("Tokens", style=ORANGE, justify="right", width=12)
    table.add_column("%", style=CYAN, justify="right", width=8)
    table.add_column("Cost", style="green", justify="right", width=10)

    for folder, data in sorted_folders:
        parts = folder.split("/")
        if len(parts) > 2:
            display_name = "/".join(parts[-2:])
        else:
            display_name = folder

        if len(display_name) > 35:
            display_name = display_name[:35]

        tokens = data["tokens"]
        percentage = (tokens / total_tokens * 100) if total_tokens > 0 else 0
        bar = _create_bar(tokens, max_tokens, width=20)

        table.add_row(
            display_name,
            bar,
            _format_number(tokens),
            f"[cyan]{percentage:.1f}%[/cyan]",
            format_cost(data["cost"]),
        )

    table.add_row("", "", "", "", "")

    table.add_row(
        "[bold]Total",
        Text(""),
        f"[bold]{_format_number(total_tokens)}",
        "[bold cyan]100.0%" if total_tokens > 0 else "[bold cyan]0.0%",
        f"[bold green]{format_cost(total_cost)}",
    )

    return Panel(
        table,
        title="[bold]Tokens by Project",
        border_style="white",
        expand=True,
    )


def _create_daily_breakdown(records: list[UsageRecord], daily_summary: dict[str, DailyTotal] | None = None) -> Panel:
    """
    Create table showing daily usage breakdown for monthly mode.
    Shows all dates in the range, including days with no usage.

    Args:
        records: List of usage records

    Returns:
        Panel with daily breakdown table
    """
    from src.models.pricing import calculate_cost, format_cost
    from datetime import timedelta

    # Aggregate by date (format: "YYYY-MM-DD")
    daily_data: dict[str, dict] = defaultdict(lambda: {
        "cost": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "messages": 0
    })

    # Track min and max dates
    min_date = None
    max_date = None

    if records:
        for record in records:
            if record.token_usage and record.timestamp:
                timestamp = record.timestamp
                if timestamp.tzinfo:
                    local_ts = timestamp.astimezone()
                else:
                    local_ts = timestamp

                # Extract date from local timestamp
                date = local_ts.strftime("%Y-%m-%d")
                record_date = local_ts.date()

                # Update min/max dates
                if min_date is None or record_date < min_date:
                    min_date = record_date
                if max_date is None or record_date > max_date:
                    max_date = record_date

                daily_data[date]["input_tokens"] += record.token_usage.input_tokens
                daily_data[date]["output_tokens"] += record.token_usage.output_tokens
                daily_data[date]["cache_creation"] += record.token_usage.cache_creation_tokens
                daily_data[date]["cache_read"] += record.token_usage.cache_read_tokens
                daily_data[date]["messages"] += 1

                if record.model and record.model != "<synthetic>":
                    cost = calculate_cost(
                        record.token_usage.input_tokens,
                        record.token_usage.output_tokens,
                        record.model,
                        record.token_usage.cache_creation_tokens,
                        record.token_usage.cache_read_tokens,
                    )
                    daily_data[date]["cost"] += cost

    if not records and daily_summary:
        for date, totals in daily_summary.items():
            daily_data[date]["input_tokens"] += totals.input_tokens
            daily_data[date]["output_tokens"] += totals.output_tokens
            daily_data[date]["cache_creation"] += totals.cache_creation_tokens
            daily_data[date]["cache_read"] += totals.cache_read_tokens
            daily_data[date]["messages"] += totals.total_prompts + totals.total_responses
            daily_data[date]["cost"] += totals.total_cost

        if daily_summary:
            from datetime import datetime as dt
            dates = sorted(daily_summary.keys())
            if dates:
                min_date = dt.strptime(dates[0], "%Y-%m-%d").date()
                max_date = dt.strptime(dates[-1], "%Y-%m-%d").date()

    if not daily_data or min_date is None or max_date is None:
        return Panel(
            Text("No daily data available", style=DIM),
            title="[bold]Daily Usage",
            border_style="white",
        )

    # Generate all dates in range
    all_dates = []
    current_date = min_date
    while current_date <= max_date:
        date_str = current_date.strftime("%Y-%m-%d")
        # Add date with actual data or zeros
        if date_str in daily_data:
            all_dates.append((date_str, daily_data[date_str]))
        else:
            # Add date with zero values
            all_dates.append((date_str, {
                "cost": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation": 0,
                "cache_read": 0,
                "messages": 0
            }))
        current_date += timedelta(days=1)

    # Sort by date in ascending order (oldest first)
    sorted_dates = sorted(all_dates, reverse=False)

    # Calculate max total tokens for bar scaling
    max_total_tokens = max((data["input_tokens"] + data["output_tokens"] for _, data in sorted_dates), default=0)

    # Create table with bar graph
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Date", style="white", justify="left", width=12)
    table.add_column("", justify="left", width=10)  # Bar column (no header)
    table.add_column("Tokens(I/O)", style=ORANGE, justify="right", width=20)
    table.add_column("Cache(W/R)", style="magenta", justify="right", width=20)
    table.add_column("Messages", style="white", justify="right", width=10)
    table.add_column("Cost", style="green", justify="right", width=10)

    for date, data in sorted_dates:
        # Calculate total tokens for bar
        total_tokens = data["input_tokens"] + data["output_tokens"]

        # Create bar (use total_tokens for scaling)
        if total_tokens == 0:
            bar = Text("▬" * 10, style=DIM)
        else:
            bar = _create_bar(total_tokens, max_total_tokens, width=10)

        # Format Tokens(I/O) as "Input / Output"
        tokens_io = f"{_format_number(data['input_tokens'])} / {_format_number(data['output_tokens'])}"

        # Format Cache(W/R) as "Write / Read"
        cache_wr = f"{_format_number(data['cache_creation'])} / {_format_number(data['cache_read'])}"

        table.add_row(
            date,
            bar,
            tokens_io,
            cache_wr,
            str(data["messages"]),
            format_cost(data["cost"]),
        )

    return Panel(
        table,
        title="[bold]Daily Usage",
        border_style="white",
        expand=True,
    )


def _create_daily_breakdown_calendar_week(records: list[UsageRecord]) -> Panel:
    """
    Create daily usage breakdown for weekly mode using calendar week (Mon-Sun).
    Shows 7 days of current ISO calendar week.

    Args:
        records: List of usage records (should cover current calendar week)

    Returns:
        Panel with daily breakdown in graph format
    """
    from src.models.pricing import calculate_cost, format_cost
    from datetime import timedelta

    # Get current ISO calendar week
    now = datetime.now().astimezone()
    iso_cal = now.isocalendar()
    current_year = iso_cal[0]
    current_week = iso_cal[1]

    # Get Monday of current week
    monday = datetime.fromisocalendar(current_year, current_week, 1)
    week_start_date = monday.date()
    week_end_date = (monday + timedelta(days=6)).date()

    # Aggregate by date (format: "YYYY-MM-DD")
    daily_data: dict[str, dict] = defaultdict(lambda: {
        "total_tokens": 0,
        "cost": 0.0
    })

    for record in records:
        if record.token_usage and record.timestamp:
            timestamp = record.timestamp
            if timestamp.tzinfo:
                local_ts = timestamp.astimezone()
            else:
                local_ts = timestamp

            date = local_ts.strftime("%Y-%m-%d")
            record_date = local_ts.date()

            # Only include records within calendar week
            if week_start_date <= record_date <= week_end_date:
                daily_data[date]["total_tokens"] += (
                    record.token_usage.input_tokens + record.token_usage.output_tokens
                )

                if record.model and record.model != "<synthetic>":
                    cost = calculate_cost(
                        record.token_usage.input_tokens,
                        record.token_usage.output_tokens,
                        record.model,
                        record.token_usage.cache_creation_tokens,
                        record.token_usage.cache_read_tokens,
                    )
                    daily_data[date]["cost"] += cost

    # Generate all dates in the calendar week (Mon-Sun)
    all_dates = []
    current_date = week_start_date
    while current_date <= week_end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        if date_str in daily_data:
            all_dates.append((date_str, daily_data[date_str]))
        else:
            # Day with no data
            all_dates.append((date_str, {
                "total_tokens": 0,
                "cost": 0.0
            }))
        current_date += timedelta(days=1)

    # Calculate totals and max for scaling
    total_tokens = sum(data["total_tokens"] for _, data in all_dates)
    max_tokens = max((data["total_tokens"] for _, data in all_dates), default=0)

    # Sort by date in ascending order (Monday first)
    sorted_dates = sorted(all_dates, reverse=False)

    # Create table with bars
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Date", style="white", justify="left", width=30)
    table.add_column("", justify="left", width=20)  # Bar column
    table.add_column("Tokens", style=ORANGE, justify="right", width=12)
    table.add_column("%", style=CYAN, justify="right", width=8)
    table.add_column("Cost", style="green", justify="right", width=10)

    for idx, (date, data) in enumerate(sorted_dates, start=1):
        tokens = data["total_tokens"]
        percentage = (tokens / total_tokens * 100) if total_tokens > 0 else 0

        # Parse date and get day of week
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        day_name = date_obj.strftime("%a")  # Mon, Tue, Wed, etc.

        # Format: [1] 2025-10-15, Mon
        date_with_shortcut = f"[yellow][{idx}][/yellow] {date}, {day_name}"

        # If no data for this day, show "-" for tokens/cost and empty bar
        if tokens == 0:
            bar = Text("▬" * 20, style=DIM)
            tokens_display = "[dim]-[/dim]"
            percentage_display = "[dim]-[/dim]"
            cost_display = "[dim]-[/dim]"
        else:
            bar = _create_bar(tokens, max_tokens, width=20)
            tokens_display = _format_number(tokens)
            percentage_display = f"[cyan]{percentage:.1f}%[/cyan]"
            cost_display = format_cost(data["cost"])

        table.add_row(
            date_with_shortcut,
            bar,
            tokens_display,
            percentage_display,
            cost_display,
        )

    # Create dynamic subtitle based on actual number of dates
    num_dates = len(sorted_dates)
    if num_dates > 0:
        subtitle_text = f"[dim]Press number keys (1-{num_dates}) to view detailed hourly breakdown[/dim]"
    else:
        subtitle_text = None

    return Panel(
        table,
        title="[bold]Daily Usage (Calendar Week)",
        subtitle=subtitle_text,
        border_style="white",
        expand=True,
    )


def _create_daily_breakdown_weekly(records: list[UsageRecord], week_start_date=None, week_end_date=None, reset_time=None, reset_day=None) -> Panel:
    """Create weekly daily breakdown using scoped records."""

    from datetime import datetime, timedelta
    from src.models.pricing import calculate_cost, format_cost

    if week_start_date is None or week_end_date is None:
        return Panel(
            Text("No daily data available", style=DIM),
            title="[bold]Daily Usage",
            border_style="white",
        )

    if isinstance(week_start_date, datetime):
        week_start_date = week_start_date.date()
    if isinstance(week_end_date, datetime):
        week_end_date = week_end_date.date()

    if week_end_date < week_start_date:
        week_end_date = week_start_date

    daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: {
        "tokens": 0,
        "cost": 0.0,
    })

    for record in records:
        usage = record.token_usage
        if not usage or not record.timestamp:
            continue

        timestamp = record.timestamp
        if timestamp.tzinfo:
            local_ts = timestamp.astimezone()
        else:
            local_ts = timestamp

        record_date = local_ts.date()
        if week_start_date <= record_date <= week_end_date:
            date_key = record_date.strftime("%Y-%m-%d")
            daily_totals[date_key]["tokens"] += usage.total_tokens

            if record.model and record.model != "<synthetic>":
                daily_totals[date_key]["cost"] += calculate_cost(
                    usage.input_tokens,
                    usage.output_tokens,
                    record.model,
                    usage.cache_creation_tokens,
                    usage.cache_read_tokens,
                )

    all_dates: list[tuple[str, dict[str, float]]] = []
    current_date = week_start_date
    while current_date <= week_end_date:
        date_key = current_date.strftime("%Y-%m-%d")
        all_dates.append((date_key, daily_totals.get(date_key, {"tokens": 0, "cost": 0.0})))
        current_date += timedelta(days=1)

    if not all_dates:
        return Panel(
            Text("No daily data available", style=DIM),
            title="[bold]Daily Usage",
            border_style="white",
        )

    total_tokens = sum(data["tokens"] for _, data in all_dates)
    max_tokens = max((data["tokens"] for _, data in all_dates), default=0)

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Date", style="white", justify="left", width=30)
    table.add_column("", justify="left", width=20)
    table.add_column("Tokens", style=ORANGE, justify="right", width=12)
    table.add_column("%", style=CYAN, justify="right", width=8)
    table.add_column("Cost", style="green", justify="right", width=10)

    today = datetime.now().date()

    for idx, (date_str, data) in enumerate(all_dates, start=1):
        tokens = data["tokens"]
        percentage = (tokens / total_tokens * 100) if total_tokens > 0 else 0
        cost_display = format_cost(data["cost"]) if data["cost"] else "-"

        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = date_obj.strftime("%a")
        is_future = date_obj.date() > today
        is_today = date_obj.date() == today

        # Apply bright cyan background to day_name if today
        if is_today:
            day_name_styled = f"[black on bright_cyan]{day_name}[/black on bright_cyan]"
        else:
            day_name_styled = day_name

        if is_future:
            if reset_day and reset_time and day_name == reset_day:
                date_with_shortcut = f"[dim][{idx}][/dim] {date_str}, {day_name_styled} [purple][{reset_time}][/purple]"
            else:
                date_with_shortcut = f"[dim][{idx}][/dim] {date_str}, {day_name_styled}"
        else:
            if reset_day and reset_time and day_name == reset_day:
                date_with_shortcut = f"[yellow][{idx}][/yellow] {date_str}, {day_name_styled} [purple][{reset_time}][/purple]"
            else:
                date_with_shortcut = f"[yellow][{idx}][/yellow] {date_str}, {day_name_styled}"

        if tokens == 0:
            bar = Text("▬" * 20, style=DIM)
            tokens_display = "[dim]-[/dim]"
            percentage_display = "[dim]-[/dim]"
            cost_display = "[dim]-[/dim]" if cost_display == "-" else f"[dim]{cost_display}[/dim]"
        else:
            bar = _create_bar(tokens, max_tokens, width=20)
            tokens_display = _format_number(tokens)
            percentage_display = f"[cyan]{percentage:.1f}%[/cyan]"

        table.add_row(
            date_with_shortcut,
            bar,
            tokens_display,
            percentage_display,
            cost_display,
        )

    num_dates = len(all_dates)
    subtitle_text = f"[dim]Press number keys (1-{num_dates}) to view detailed hourly breakdown[/dim]" if num_dates else None

    return Panel(
        table,
        title="[bold]Daily Usage (Weekly Limit Period)",
        subtitle=subtitle_text,
        border_style="white",
        expand=True,
    )


def _create_hourly_breakdown(records: list[UsageRecord]) -> Panel:
    """
    Create table showing hourly usage breakdown for weekly mode.

    Args:
        records: List of usage records

    Returns:
        Panel with hourly breakdown table
    """
    from src.models.pricing import calculate_cost, format_cost

    # Aggregate by hour (format: "HH:00")
    hourly_data: dict[str, dict] = defaultdict(lambda: {
        "cost": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "messages": 0
    })

    for record in records:
        if record.token_usage and record.timestamp:
            timestamp = record.timestamp
            if timestamp.tzinfo:
                local_ts = timestamp.astimezone()
            else:
                local_ts = timestamp

            hour = local_ts.strftime("%H:00")

            hourly_data[hour]["input_tokens"] += record.token_usage.input_tokens
            hourly_data[hour]["output_tokens"] += record.token_usage.output_tokens
            hourly_data[hour]["cache_creation"] += record.token_usage.cache_creation_tokens
            hourly_data[hour]["cache_read"] += record.token_usage.cache_read_tokens
            hourly_data[hour]["messages"] += 1

            if record.model and record.model != "<synthetic>":
                cost = calculate_cost(
                    record.token_usage.input_tokens,
                    record.token_usage.output_tokens,
                    record.model,
                    record.token_usage.cache_creation_tokens,
                    record.token_usage.cache_read_tokens,
                )
                hourly_data[hour]["cost"] += cost

    if not hourly_data:
        return Panel(
            Text("No hourly data available", style=DIM),
            title="[bold]Hourly Usage",
            border_style="white",
        )

    # Sort by hour in descending order (most recent first)
    sorted_hours = sorted(hourly_data.items(), reverse=True)

    # Create table with English column names
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Time", style="purple", justify="left", width=14)
    table.add_column("Cost", style="green", justify="right", width=10)
    table.add_column("Input", style=BLUE, justify="right", width=12)
    table.add_column("Output", style=BLUE, justify="right", width=12)
    table.add_column("Cache Write", style="magenta", justify="right", width=14)
    table.add_column("Cache Read", style="magenta", justify="right", width=14)
    table.add_column("Messages", style="white", justify="right", width=10)

    for idx, (hour, data) in enumerate(sorted_hours, start=1):
        # Generate shortcut key: 1-9 for first 9 hours, a-o for hours 10-24
        if idx <= 9:
            shortcut = str(idx)
        else:
            # idx=10 -> 'a', idx=11 -> 'b', ..., idx=24 -> 'o'
            shortcut = chr(ord('a') + idx - 10)

        hour_with_shortcut = f"[yellow][{shortcut}][/yellow] {hour}"

        table.add_row(
            hour_with_shortcut,
            format_cost(data["cost"]),
            _format_number(data["input_tokens"]),
            _format_number(data["output_tokens"]),
            _format_number(data["cache_creation"]),
            _format_number(data["cache_read"]),
            str(data["messages"]),
        )

    return Panel(
        table,
        title="[bold]Hourly Usage",
        border_style="white",
        expand=True,
    )


def _create_monthly_breakdown(
    records: list[UsageRecord],
    summary: UsageSummary | None = None,
    target_year: int | None = None,
) -> Panel:
    """
    Create table showing monthly usage breakdown for yearly mode.

    Args:
        records: List of usage records
        summary: Aggregated usage summary (used when records are scoped)
        target_year: Year to display when using summary data

    Returns:
        Panel with monthly breakdown table
    """
    from src.models.pricing import format_cost

    # Aggregate by month (format: "YYYY-MM")
    monthly_data: dict[str, dict] = defaultdict(lambda: {
        "cost": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "messages": 0
    })

    # Always calculate from records to ensure multi-device support
    # Previously used summary.daily, but summary only contains current device data
    from src.models.pricing import calculate_cost

    for record in records:
        if record.token_usage and record.timestamp:
            timestamp = record.timestamp
            if timestamp.tzinfo:
                local_ts = timestamp.astimezone()
            else:
                local_ts = timestamp

            # Extract year-month from local timestamp
            month = local_ts.strftime("%Y-%m")

            monthly_data[month]["input_tokens"] += record.token_usage.input_tokens
            monthly_data[month]["output_tokens"] += record.token_usage.output_tokens
            monthly_data[month]["cache_creation"] += record.token_usage.cache_creation_tokens
            monthly_data[month]["cache_read"] += record.token_usage.cache_read_tokens
            monthly_data[month]["messages"] += 1

            if record.model and record.model != "<synthetic>":
                cost = calculate_cost(
                    record.token_usage.input_tokens,
                    record.token_usage.output_tokens,
                    record.model,
                    record.token_usage.cache_creation_tokens,
                    record.token_usage.cache_read_tokens,
                )
                monthly_data[month]["cost"] += cost

    if not monthly_data:
        return Panel(
            Text("No monthly data available", style=DIM),
            title="[bold]Monthly Usage",
            border_style="white",
        )

    # Sort by month in ascending order (oldest first)
    sorted_months = sorted(monthly_data.items(), reverse=False)

    # Calculate max total tokens for bar scaling
    max_total_tokens = max((data["input_tokens"] + data["output_tokens"] for _, data in sorted_months), default=0)

    # Create table with bar graph (same structure as Daily Usage in monthly mode)
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Month", style="white", justify="left", width=10)
    table.add_column("", justify="left", width=10)  # Bar column (no header)
    table.add_column("Tokens(I/O)", style=ORANGE, justify="right", width=20)
    table.add_column("Cache(W/R)", style="magenta", justify="right", width=20)
    table.add_column("Messages", style="white", justify="right", width=10)
    table.add_column("Cost", style="green", justify="right", width=10)

    for month, data in sorted_months:
        # Calculate total tokens for bar
        total_tokens = data["input_tokens"] + data["output_tokens"]

        # Create bar (use total_tokens for scaling)
        if total_tokens == 0:
            bar = Text("▬" * 10, style=DIM)
        else:
            bar = _create_bar(total_tokens, max_total_tokens, width=10)

        # Format Tokens(I/O) as "Input / Output"
        tokens_io = f"{_format_number(data['input_tokens'])} / {_format_number(data['output_tokens'])}"

        # Format Cache(W/R) as "Write / Read"
        cache_wr = f"{_format_number(data['cache_creation'])} / {_format_number(data['cache_read'])}"

        table.add_row(
            month,
            bar,
            tokens_io,
            cache_wr,
            str(data["messages"]),
            format_cost(data["cost"]),
        )

    return Panel(
        table,
        title="[bold]Monthly Usage",
        border_style="white",
        expand=True,
    )


def _create_weekly_breakdown_for_month(records: list[UsageRecord], year: int, month: int) -> Panel:
    """
    Create weekly usage breakdown for monthly mode (calendar weeks in a specific month).
    Uses ISO 8601 week definition: Monday start, week 1 contains Jan 4th.

    Args:
        records: List of usage records
        year: Year to display (e.g., 2025)
        month: Month to display (1-12)

    Returns:
        Panel with weekly breakdown table
    """
    from src.models.pricing import calculate_cost, format_cost
    from datetime import datetime, timedelta

    # Aggregate by ISO week (keyed by Monday start date)
    weekly_data: dict[str, dict] = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "messages": 0,
        "cost": 0.0,
        "iso_week": None,
        "iso_year": None,
        "week_start": None,
    })

    for record in records:
        if record.token_usage and record.timestamp:
            timestamp = record.timestamp
            if timestamp.tzinfo:
                local_ts = timestamp.astimezone()
            else:
                local_ts = timestamp

            iso_year, iso_week, _ = local_ts.isocalendar()

            # Only include weeks from the target year and month (local time)
            if local_ts.year == year and local_ts.month == month:
                week_start = (local_ts - timedelta(days=local_ts.weekday())).date()
                key = week_start.isoformat()
                bucket = weekly_data[key]
                bucket["input_tokens"] += record.token_usage.input_tokens
                bucket["output_tokens"] += record.token_usage.output_tokens
                bucket["cache_creation"] += record.token_usage.cache_creation_tokens
                bucket["cache_read"] += record.token_usage.cache_read_tokens
                bucket["messages"] += 1
                if bucket["iso_week"] is None:
                    bucket["iso_week"] = iso_week
                    bucket["iso_year"] = iso_year
                    bucket["week_start"] = week_start

                if record.model and record.model != "<synthetic>":
                    cost = calculate_cost(
                        record.token_usage.input_tokens,
                        record.token_usage.output_tokens,
                        record.model,
                        record.token_usage.cache_creation_tokens,
                        record.token_usage.cache_read_tokens,
                    )
                    bucket["cost"] += cost

    if not weekly_data:
        return Panel(
            Text("No weekly data available", style=DIM),
            title="[bold]Weekly Usage (Calendar)",
            border_style="white",
        )

    # Sort by week number (ascending - oldest first)
    sorted_weeks = sorted(weekly_data.items(), key=lambda item: item[1]["week_start"])

    # Calculate max for bar scaling
    max_total_tokens = max((data["input_tokens"] + data["output_tokens"] for _, data in sorted_weeks), default=0)

    # Create table (same structure as Daily Usage)
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Week", style="white", justify="left", width=15)
    table.add_column("", justify="left", width=10)  # Bar
    table.add_column("Tokens(I/O)", style=ORANGE, justify="right", width=20)
    table.add_column("Cache(W/R)", style="magenta", justify="right", width=20)
    table.add_column("Messages", style="white", justify="right", width=10)
    table.add_column("Cost", style="green", justify="right", width=10)

    for _, data in sorted_weeks:
        week_start = data.get("week_start")
        if week_start is None:
            continue
        iso_week = data.get("iso_week") or week_start.isocalendar()[1]
        week_label = f"{week_start.strftime('%Y-%m-%d')} (W{iso_week:02d})"

        total_tokens = data["input_tokens"] + data["output_tokens"]

        # Create bar
        if total_tokens == 0:
            bar = Text("▬" * 10, style=DIM)
        else:
            bar = _create_bar(total_tokens, max_total_tokens, width=10)

        # Format data
        tokens_io = f"{_format_number(data['input_tokens'])} / {_format_number(data['output_tokens'])}"
        cache_wr = f"{_format_number(data['cache_creation'])} / {_format_number(data['cache_read'])}"

        table.add_row(
            week_label,
            bar,
            tokens_io,
            cache_wr,
            str(data["messages"]),
            format_cost(data["cost"]),
        )

    return Panel(
        table,
        title="[bold]Weekly Usage (Calendar)",
        border_style="white",
        expand=True,
    )


def _create_weekly_breakdown_calendar(records: list[UsageRecord], year: int) -> Panel:
    """
    Create weekly usage breakdown for yearly mode (calendar weeks).
    Uses ISO 8601 week definition: Monday start, week 1 contains Jan 4th.

    Args:
        records: List of usage records
        year: Year to display (e.g., 2025)

    Returns:
        Panel with weekly breakdown table
    """
    from src.models.pricing import calculate_cost, format_cost

    # Aggregate by ISO week (keyed by Monday start date)
    weekly_data: dict[str, dict] = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "messages": 0,
        "cost": 0.0,
        "iso_week": None,
        "iso_year": None,
        "week_start": None,
    })

    for record in records:
        if record.token_usage and record.timestamp:
            timestamp = record.timestamp
            if timestamp.tzinfo:
                local_ts = timestamp.astimezone()
            else:
                local_ts = timestamp

            iso_year, iso_week, _ = local_ts.isocalendar()

            # Only include weeks from the target year (local time)
            if iso_year == year:
                week_start = (local_ts - timedelta(days=local_ts.weekday())).date()
                key = week_start.isoformat()
                bucket = weekly_data[key]
                bucket["input_tokens"] += record.token_usage.input_tokens
                bucket["output_tokens"] += record.token_usage.output_tokens
                bucket["cache_creation"] += record.token_usage.cache_creation_tokens
                bucket["cache_read"] += record.token_usage.cache_read_tokens
                bucket["messages"] += 1
                if bucket["iso_week"] is None:
                    bucket["iso_week"] = iso_week
                    bucket["iso_year"] = iso_year
                    bucket["week_start"] = week_start

                if record.model and record.model != "<synthetic>":
                    cost = calculate_cost(
                        record.token_usage.input_tokens,
                        record.token_usage.output_tokens,
                        record.model,
                        record.token_usage.cache_creation_tokens,
                        record.token_usage.cache_read_tokens,
                    )
                    bucket["cost"] += cost

    if not weekly_data:
        return Panel(
            Text("No weekly data available", style=DIM),
            title="[bold]Weekly Usage (Calendar)",
            border_style="white",
        )

    # Sort by week start date (ascending - oldest first)
    sorted_weeks = sorted(weekly_data.items(), key=lambda item: item[1]["week_start"])

    # Calculate max for bar scaling
    max_total_tokens = max((data["input_tokens"] + data["output_tokens"] for _, data in sorted_weeks), default=0)

    # Create table (same structure as Monthly Usage)
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Week", style="white", justify="left", width=15)
    table.add_column("", justify="left", width=10)  # Bar
    table.add_column("Tokens(I/O)", style=ORANGE, justify="right", width=20)
    table.add_column("Cache(W/R)", style="magenta", justify="right", width=20)
    table.add_column("Messages", style="white", justify="right", width=10)
    table.add_column("Cost", style="green", justify="right", width=10)

    for _, data in sorted_weeks:
        week_start = data.get("week_start")
        if week_start is None:
            continue
        iso_week = data.get("iso_week") or week_start.isocalendar()[1]
        week_label = f"{week_start.strftime('%Y-%m-%d')} (W{iso_week:02d})"

        # Calculate total tokens for bar
        total_tokens = data["input_tokens"] + data["output_tokens"]

        # Create bar
        if total_tokens == 0:
            bar = Text("▬" * 10, style=DIM)
        else:
            bar = _create_bar(total_tokens, max_total_tokens, width=10)

        # Format data
        tokens_io = f"{_format_number(data['input_tokens'])} / {_format_number(data['output_tokens'])}"
        cache_wr = f"{_format_number(data['cache_creation'])} / {_format_number(data['cache_read'])}"

        table.add_row(
            week_label,
            bar,
            tokens_io,
            cache_wr,
            str(data["messages"]),
            format_cost(data["cost"]),
        )

    return Panel(
        table,
        title="[bold]Weekly Usage (Calendar)",
        border_style="white",
        expand=True,
    )


def _create_daily_detail_view(records: list[UsageRecord], target_date: str) -> Group:
    """
    Create detailed view for a specific day showing hourly usage, models, and projects.

    Args:
        records: List of usage records
        target_date: Target date in YYYY-MM-DD format (e.g., "2025-10-15")

    Returns:
        Group containing hourly, model, and project breakdowns for the target date
    """
    from src.models.pricing import calculate_cost, format_cost

    # Parse target date to get day of week
    date_obj = datetime.strptime(target_date, "%Y-%m-%d")
    day_name = date_obj.strftime("%A")  # Full day name (Monday, Tuesday, etc.)
    title_date = f"{target_date} ({day_name})"

    # Filter records to only those from target_date (using local timezone)
    filtered_records = []
    for record in records:
        if record.timestamp:
            # Convert UTC timestamp to local timezone for date comparison
            try:
                local_time = record.timestamp.astimezone()
                record_date = local_time.strftime("%Y-%m-%d")
                if record_date == target_date:
                    filtered_records.append(record)
            except Exception:
                # Fallback to UTC if timezone conversion fails
                if record.timestamp.strftime("%Y-%m-%d") == target_date:
                    filtered_records.append(record)

    if not filtered_records:
        return Group(
            Panel(
                Text(f"No data available for {target_date}", style=DIM),
                title=f"[bold]Daily Detail - {title_date}",
                border_style="white",
            )
        )

    # Create hourly breakdown
    hourly_data: dict[str, dict] = defaultdict(lambda: {
        "cost": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "messages": 0
    })

    # Import timezone utilities and get timezone once (performance optimization)
    from src.utils.timezone import format_local_time, get_user_timezone
    user_tz = get_user_timezone()  # Load timezone once instead of per-record

    for record in filtered_records:
        if record.token_usage:
            # Convert UTC timestamp to local timezone for display
            hour = format_local_time(record.timestamp, "%H:00", user_tz)
            hourly_data[hour]["input_tokens"] += record.token_usage.input_tokens
            hourly_data[hour]["output_tokens"] += record.token_usage.output_tokens
            hourly_data[hour]["cache_creation"] += record.token_usage.cache_creation_tokens
            hourly_data[hour]["cache_read"] += record.token_usage.cache_read_tokens
            hourly_data[hour]["messages"] += 1

            if record.model and record.model != "<synthetic>":
                cost = calculate_cost(
                    record.token_usage.input_tokens,
                    record.token_usage.output_tokens,
                    record.model,
                    record.token_usage.cache_creation_tokens,
                    record.token_usage.cache_read_tokens,
                )
                hourly_data[hour]["cost"] += cost

    # Create hourly table
    hourly_table = Table(show_header=True, box=None, padding=(0, 2))
    hourly_table.add_column("Time", style="purple", justify="left", width=14)
    hourly_table.add_column("Cost", style="green", justify="right", width=10)
    hourly_table.add_column("Input", style=BLUE, justify="right", width=12)
    hourly_table.add_column("Output", style=BLUE, justify="right", width=12)
    hourly_table.add_column("Cache Write", style="magenta", justify="right", width=14)
    hourly_table.add_column("Cache Read", style="magenta", justify="right", width=14)
    hourly_table.add_column("Messages", style="white", justify="right", width=10)

    # Sort by hour in descending order (most recent first) to show current work at top
    sorted_hours = sorted(hourly_data.items(), reverse=True)

    for idx, (hour, data) in enumerate(sorted_hours, start=1):
        # Use numbers 1-9, then letters a-o for shortcuts (supports up to 24 hours)
        if idx <= 9:
            shortcut = str(idx)
        else:
            shortcut = chr(ord('a') + idx - 10)  # 10->a, 11->b, ..., 24->o

        # Include shortcut in Time column like Weekly page
        shortcut_label = Text()
        shortcut_label.append(f"[{shortcut}]", style="yellow")
        shortcut_label.append(f" {hour}")

        hourly_table.add_row(
            shortcut_label,
            format_cost(data["cost"]),
            _format_number(data["input_tokens"]),
            _format_number(data["output_tokens"]),
            _format_number(data["cache_creation"]),
            _format_number(data["cache_read"]),
            str(data["messages"]),
        )

    hourly_panel = Panel(
        hourly_table,
        title=f"[bold]Hourly Usage - {title_date}",
        subtitle="[dim]Press keys [1]-[9], [a]-[o] to view message details. In message view, press [bold yellow]tab[/bold yellow] to expand/collapse content[/dim]",
        border_style="white",
        expand=True,
    )

    # Create model breakdown
    model_data: dict[str, dict] = defaultdict(lambda: {
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "messages": 0,
        "cost": 0.0
    })

    for record in filtered_records:
        if record.model and record.token_usage and record.model != "<synthetic>":
            model_data[record.model]["total_tokens"] += record.token_usage.total_tokens
            model_data[record.model]["input_tokens"] += record.token_usage.input_tokens
            model_data[record.model]["output_tokens"] += record.token_usage.output_tokens
            model_data[record.model]["cache_creation"] += record.token_usage.cache_creation_tokens
            model_data[record.model]["cache_read"] += record.token_usage.cache_read_tokens
            model_data[record.model]["messages"] += 1

            cost = calculate_cost(
                record.token_usage.input_tokens,
                record.token_usage.output_tokens,
                record.model,
                record.token_usage.cache_creation_tokens,
                record.token_usage.cache_read_tokens,
            )
            model_data[record.model]["cost"] += cost

    total_tokens = sum(data["total_tokens"] for data in model_data.values())
    total_cost = sum(data["cost"] for data in model_data.values())
    max_tokens = max(data["total_tokens"] for data in model_data.values()) if model_data else 0
    sorted_models = sorted(model_data.items(), key=lambda x: x[1]["total_tokens"], reverse=True)

    model_table = Table(show_header=True, box=None, padding=(0, 2))
    model_table.add_column("Model", style="white", justify="left", width=20)
    model_table.add_column("", justify="left", width=10)  # Bar
    model_table.add_column("Tokens(I/O)", style=ORANGE, justify="right", width=20)
    model_table.add_column("Cache(W/R)", style="magenta", justify="right", width=20)
    model_table.add_column("Messages", style="white", justify="right", width=10)
    model_table.add_column("Cost", style="green", justify="right", width=10)

    for model, data in sorted_models:
        display_name = model.split("/")[-1] if "/" in model else model
        if "claude" in display_name.lower():
            display_name = display_name.replace("claude-", "")

        tokens = data["total_tokens"]
        bar = _create_bar(tokens, max_tokens, width=10)

        # Format tokens as "Input / Output"
        tokens_io = f"{_format_number(data['input_tokens'])} / {_format_number(data['output_tokens'])}"

        # Format cache as "Write / Read"
        cache_wr = f"{_format_number(data['cache_creation'])} / {_format_number(data['cache_read'])}"

        model_table.add_row(
            display_name,
            bar,
            tokens_io,
            cache_wr,
            str(data["messages"]),
            format_cost(data["cost"]),
        )

    # Add total row
    total_input = sum(data["input_tokens"] for data in model_data.values())
    total_output = sum(data["output_tokens"] for data in model_data.values())
    total_cache_w = sum(data["cache_creation"] for data in model_data.values())
    total_cache_r = sum(data["cache_read"] for data in model_data.values())
    total_messages = sum(data["messages"] for data in model_data.values())

    model_table.add_row("", "", "", "", "", "")
    model_table.add_row(
        "[bold]Total",
        Text(""),
        f"[bold]{_format_number(total_input)} / {_format_number(total_output)}",
        f"[bold]{_format_number(total_cache_w)} / {_format_number(total_cache_r)}",
        f"[bold]{total_messages}",
        f"[bold green]{format_cost(total_cost)}",
    )

    model_panel = Panel(
        model_table,
        title=f"[bold]Tokens by Model - {title_date}",
        border_style="white",
        expand=True,
    )

    # Create project breakdown
    folder_data: dict[str, dict] = defaultdict(lambda: {
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "messages": 0,
        "cost": 0.0
    })

    for record in filtered_records:
        if record.token_usage:
            folder_data[record.folder]["total_tokens"] += record.token_usage.total_tokens
            folder_data[record.folder]["input_tokens"] += record.token_usage.input_tokens
            folder_data[record.folder]["output_tokens"] += record.token_usage.output_tokens
            folder_data[record.folder]["cache_creation"] += record.token_usage.cache_creation_tokens
            folder_data[record.folder]["cache_read"] += record.token_usage.cache_read_tokens
            folder_data[record.folder]["messages"] += 1

            if record.model and record.model != "<synthetic>":
                cost = calculate_cost(
                    record.token_usage.input_tokens,
                    record.token_usage.output_tokens,
                    record.model,
                    record.token_usage.cache_creation_tokens,
                    record.token_usage.cache_read_tokens,
                )
                folder_data[record.folder]["cost"] += cost

    total_tokens_proj = sum(data["total_tokens"] for data in folder_data.values())
    sorted_folders = sorted(folder_data.items(), key=lambda x: x[1]["total_tokens"], reverse=True)
    sorted_folders = sorted_folders[:10]  # Limit to top 10
    max_tokens_proj = max(data["total_tokens"] for _, data in sorted_folders) if sorted_folders else 0

    project_table = Table(show_header=True, box=None, padding=(0, 2))
    project_table.add_column("Project", style="white", justify="left", width=30, overflow="crop")
    project_table.add_column("", justify="left", width=10)  # Bar
    project_table.add_column("Tokens(I/O)", style=ORANGE, justify="right", width=20)
    project_table.add_column("Cache(W/R)", style="magenta", justify="right", width=20)
    project_table.add_column("Messages", style="white", justify="right", width=10)
    project_table.add_column("Cost", style="green", justify="right", width=10)

    for folder, data in sorted_folders:
        parts = folder.split("/")
        if len(parts) > 2:
            display_name = "/".join(parts[-2:])
        else:
            display_name = folder

        if len(display_name) > 35:
            display_name = display_name[:35]

        tokens = data["total_tokens"]
        bar = _create_bar(tokens, max_tokens_proj, width=10)

        # Format tokens as "Input / Output"
        tokens_io = f"{_format_number(data['input_tokens'])} / {_format_number(data['output_tokens'])}"

        # Format cache as "Write / Read"
        cache_wr = f"{_format_number(data['cache_creation'])} / {_format_number(data['cache_read'])}"

        project_table.add_row(
            display_name,
            bar,
            tokens_io,
            cache_wr,
            str(data["messages"]),
            format_cost(data["cost"]),
        )

    project_panel = Panel(
        project_table,
        title=f"[bold]Tokens by Project - {title_date}",
        border_style="white",
        expand=True,
    )

    # Add spacing between panels
    spacing = Text("")
    return Group(hourly_panel, spacing, model_panel, spacing, project_panel)


def _create_session_aggregated_view(sessions: dict, target_date: str, target_hour: int, user_tz: str) -> Group:
    """
    Create session+model aggregated view for a specific hour.

    Shows each session with per-model breakdown of tokens and cost.

    Args:
        sessions: Dict mapping session_id to list of UsageRecord objects
        target_date: Target date in YYYY-MM-DD format
        target_hour: Target hour in 24-hour format (0-23)
        user_tz: User's timezone for time formatting

    Returns:
        Group containing session+model aggregated tables
    """
    from src.models.pricing import calculate_cost, format_cost
    from src.utils.timezone import format_local_time

    # Parse target date to get day of week
    date_obj = datetime.strptime(target_date, "%Y-%m-%d")
    day_name = date_obj.strftime("%A")
    hour_str = f"{target_hour:02d}:00"
    title_text = f"{target_date} ({day_name}) - {hour_str}"

    all_items = []

    # Process each session
    for session_idx, (session_id, session_records) in enumerate(sessions.items()):
        # Get first and last timestamp for this session
        first_timestamp = min(r.timestamp for r in session_records)
        last_timestamp = max(r.timestamp for r in session_records)
        start_time_str = format_local_time(first_timestamp, "%H:%M:%S", user_tz)
        end_time_str = format_local_time(last_timestamp, "%H:%M:%S", user_tz)
        time_range_str = f"{start_time_str} - {end_time_str}"

        # Show short session ID
        short_session_id = session_id[:8] if len(session_id) >= 8 else session_id

        # Aggregate by model within this session
        from collections import defaultdict
        model_data = defaultdict(lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation": 0,
            "cache_read": 0,
            "cost": 0.0,
            "messages": 0,
        })

        for record in session_records:
            if record.model and record.token_usage:
                bucket = model_data[record.model]
                bucket["input_tokens"] += record.token_usage.input_tokens
                bucket["output_tokens"] += record.token_usage.output_tokens
                bucket["cache_creation"] += record.token_usage.cache_creation_tokens
                bucket["cache_read"] += record.token_usage.cache_read_tokens
                bucket["messages"] += 1

                if record.model != "<synthetic>":
                    cost = calculate_cost(
                        record.token_usage.input_tokens,
                        record.token_usage.output_tokens,
                        record.model,
                        record.token_usage.cache_creation_tokens,
                        record.token_usage.cache_read_tokens,
                    )
                    bucket["cost"] += cost

        # Create table for this session
        session_table = Table(show_header=True, box=None, padding=(0, 2))
        session_table.add_column("Model", style="white", justify="left", width=18)
        session_table.add_column("Msgs", style="white", justify="right", width=6)
        session_table.add_column("Input", style=BLUE, justify="right", width=10)
        session_table.add_column("Output", style=BLUE, justify="right", width=10)
        session_table.add_column("Cache W", style="magenta", justify="right", width=10)
        session_table.add_column("Cache R", style="magenta", justify="right", width=10)
        session_table.add_column("Cost", style="green", justify="right", width=10)

        # Sort models by cost (descending)
        sorted_models = sorted(model_data.items(), key=lambda x: x[1]["cost"], reverse=True)

        # Add rows
        for model, data in sorted_models:
            # Model name (shortened)
            model_name = model.split("/")[-1] if "/" in model else model
            if "claude" in model_name.lower():
                model_name = model_name.replace("claude-", "")

            session_table.add_row(
                model_name,
                str(data["messages"]),
                _format_number(data["input_tokens"]),
                _format_number(data["output_tokens"]),
                _format_number(data["cache_creation"]),
                _format_number(data["cache_read"]),
                format_cost(data["cost"], precision=4),
            )

        # Add totals row
        total_msgs = sum(d["messages"] for d in model_data.values())
        total_input = sum(d["input_tokens"] for d in model_data.values())
        total_output = sum(d["output_tokens"] for d in model_data.values())
        total_cache_w = sum(d["cache_creation"] for d in model_data.values())
        total_cache_r = sum(d["cache_read"] for d in model_data.values())
        total_cost = sum(d["cost"] for d in model_data.values())

        session_table.add_row(
            "[bold]Total[/bold]",
            f"[bold]{total_msgs}[/bold]",
            f"[bold]{_format_number(total_input)}[/bold]",
            f"[bold]{_format_number(total_output)}[/bold]",
            f"[bold]{_format_number(total_cache_w)}[/bold]",
            f"[bold]{_format_number(total_cache_r)}[/bold]",
            f"[bold]{format_cost(total_cost, precision=4)}[/bold]",
        )

        # Create panel for this session
        session_panel = Panel(
            session_table,
            title=f"[bold]Session [{short_session_id}] - {time_range_str}[/bold]",
            border_style="cyan",
            expand=True,
        )

        all_items.append(session_panel)

        # Add spacing between sessions (except after last one)
        if session_idx < len(sessions) - 1:
            all_items.append(Text(""))

    # Create outer panel
    subtitle_text = "[dim]Press [bold yellow]tab[/bold yellow] to switch mode([bright_green]Session[/bright_green]), [bold]esc[/bold] to return to daily view[/dim]"

    outer_panel = Panel(
        Group(*all_items),
        title=f"[bold]Session+Model Detail - {title_text}",
        subtitle=subtitle_text,
        border_style="white",
        expand=True,
    )

    return Group(outer_panel)


def _create_message_detail_view(records: list[UsageRecord], target_date: str, target_hour: int, content_mode: str = "hide", view_mode_ref: dict | None = None) -> Group:
    """
    Create detailed view for messages in a specific hour.

    Args:
        records: List of usage records for the target hour
        target_date: Target date in YYYY-MM-DD format (e.g., "2025-10-15")
        target_hour: Target hour in 24-hour format (0-23)
        content_mode: Content display mode - "hide" (no content, default), "brief" (63 chars), "detail" (full content), or "session" (session+model aggregated)
        view_mode_ref: Reference dict to track last viewed message ID

    Returns:
        Group containing message detail table and content previews
    """
    from src.models.pricing import calculate_cost, format_cost
    from src.utils.timezone import format_local_time, get_user_timezone

    # Parse target date to get day of week
    date_obj = datetime.strptime(target_date, "%Y-%m-%d")
    day_name = date_obj.strftime("%A")
    hour_str = f"{target_hour:02d}:00"
    title_text = f"{target_date} ({day_name}) - {hour_str}"

    if not records:
        return Group(
            Panel(
                Text(f"No messages found for {target_date} at {hour_str}", style=DIM),
                title=f"[bold]Message Detail - {title_text}",
                border_style="white",
            )
        )

    # Get timezone once for performance
    user_tz = get_user_timezone()

    # Group messages by session for visual separation
    # Note: Haiku filtering is already done in load_all_devices_messages_by_hour()
    # Skip User messages as they have no token usage data (only show Assistant messages)
    from collections import defaultdict
    sessions: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in records:
        # Only include Assistant messages (User messages have no token/cost data)
        if not record.is_user_prompt:
            sessions[record.session_id].append(record)

    # If content_mode is "session", show session+model aggregated view
    if content_mode == "session":
        return _create_session_aggregated_view(sessions, target_date, target_hour, user_tz)

    # Build list of message items (each is a small table with optional content)
    message_items = []

    # Track last viewed message ID for auto-refresh separator
    # Get the stored last_viewed_message_id from view_mode_ref
    last_viewed_message_id = None
    if view_mode_ref:
        # Create a unique key for this specific hour view (date + hour)
        hour_key = f"{target_date}_{target_hour}"
        last_viewed_key = f"last_viewed_message_id_{hour_key}"
        last_viewed_message_id = view_mode_ref.get(last_viewed_key)

    # Find if we need to add separator: check if last_viewed_message_id exists in current records
    found_last_viewed = False
    add_separator_before_next = False
    if last_viewed_message_id:
        # Check if the last viewed message exists in current records
        all_message_uuids = []
        for session_id, session_records in sessions.items():
            for record in session_records:
                all_message_uuids.append(record.message_uuid)

        if last_viewed_message_id in all_message_uuids:
            found_last_viewed = True

    # Process each session
    for session_idx, (session_id, session_records) in enumerate(sessions.items()):
        # Add session separator (except before first session)
        if session_idx > 0:
            message_items.append(Text(""))

        # Add each message in the session
        for record in session_records:
            # Check if we should add separator before this message
            if found_last_viewed and add_separator_before_next:
                # Add separator line before this new message
                separator_line = Text("─" * 100, style="dim")
                message_items.append(Text(""))  # Empty line before separator
                message_items.append(separator_line)
                message_items.append(Text(""))  # Empty line after separator
                add_separator_before_next = False  # Only add once

            # Check if this is the last viewed message
            if found_last_viewed and record.message_uuid == last_viewed_message_id:
                # Next message should have separator before it
                add_separator_before_next = True

            # Format time (HH:MM:SS in local timezone)
            time_str = format_local_time(record.timestamp, "%H:%M:%S", user_tz)

            # Message type
            msg_type = "User" if record.is_user_prompt else "Asst"

            # Model name (shortened)
            if record.model:
                model_name = record.model.split("/")[-1] if "/" in record.model else record.model
                if "claude" in model_name.lower():
                    model_name = model_name.replace("claude-", "")
            else:
                model_name = "-"

            # Token values (only for assistant messages)
            if record.token_usage:
                input_tok = _format_number(record.token_usage.input_tokens)
                output_tok = _format_number(record.token_usage.output_tokens)
                cache_write = _format_number(record.token_usage.cache_creation_tokens)
                cache_read = _format_number(record.token_usage.cache_read_tokens)

                # Calculate cost
                if record.model and record.model != "<synthetic>":
                    cost = calculate_cost(
                        record.token_usage.input_tokens,
                        record.token_usage.output_tokens,
                        record.model,
                        record.token_usage.cache_creation_tokens,
                        record.token_usage.cache_read_tokens,
                    )
                    cost_str = format_cost(cost, precision=4)
                else:
                    cost_str = "-"
            else:
                input_tok = "-"
                output_tok = "-"
                cache_write = "-"
                cache_read = "-"
                cost_str = "-"

            # Create a small table for this message
            msg_table = Table(show_header=False, box=None, padding=(0, 2), show_edge=False)
            msg_table.add_column("Time", style="purple", justify="left", width=10)
            msg_table.add_column("Type", style="white", justify="left", width=6)
            msg_table.add_column("Model", style="white", justify="left", width=18)
            msg_table.add_column("Input", style=BLUE, justify="right", width=10)
            msg_table.add_column("Output", style=BLUE, justify="right", width=10)
            msg_table.add_column("Cache W", style="magenta", justify="right", width=10)
            msg_table.add_column("Cache R", style="magenta", justify="right", width=10)
            msg_table.add_column("Cost", style="green", justify="right", width=10)

            msg_table.add_row(
                time_str,
                msg_type,
                model_name,
                input_tok,
                output_tok,
                cache_write,
                cache_read,
                cost_str,
            )

            message_items.append(msg_table)

            # Add content preview immediately below all messages (both User and Asst)
            # Modes: "brief" (63 chars), "detail" (full content), "hide" (no content)
            if record.content and content_mode != "hide":
                if content_mode == "detail":
                    # Show full content with line breaks preserved
                    content_lines = record.content.strip().split("\n")
                    for line_idx, line in enumerate(content_lines):
                        content_text = Text()
                        if line_idx == 0:
                            # First line with branch indicator
                            content_text.append("                ", style=DIM)  # Indent
                            content_text.append("ㄴ ", style=DIM)
                        else:
                            # Subsequent lines with extra indent
                            content_text.append("                  ", style=DIM)
                        content_text.append(line, style=DIM)
                        message_items.append(content_text)
                elif content_mode == "brief":
                    # Show truncated preview
                    preview = record.content.strip().replace("\n", " ")
                    # Truncate to 63 chars (42 * 1.5, increased by 50%)
                    if len(preview) > 63:
                        preview = preview[:63] + "..."

                    # Create content text with indent to align with "Asst" column + 2 spaces
                    # Time column (10) + padding (4) + 2 extra spaces = 16
                    content_text = Text()
                    content_text.append("                ", style=DIM)  # Indent to Asst position + 2
                    content_text.append("ㄴ ", style=DIM)
                    content_text.append(preview, style=DIM)
                    message_items.append(content_text)

    # Create header table
    header_table = Table(show_header=True, box=None, padding=(0, 2))
    header_table.add_column("Time", style="purple", justify="left", width=10)
    header_table.add_column("Type", style="white", justify="left", width=6)
    header_table.add_column("Model", style="white", justify="left", width=18)
    header_table.add_column("Input", style=BLUE, justify="right", width=10)
    header_table.add_column("Output", style=BLUE, justify="right", width=10)
    header_table.add_column("Cache W", style="magenta", justify="right", width=10)
    header_table.add_column("Cache R", style="magenta", justify="right", width=10)
    header_table.add_column("Cost", style="green", justify="right", width=10)

    # Combine header and messages
    all_items = [header_table] + message_items

    # Create panel with dynamic subtitle based on content mode
    # Mode display: capitalize first letter for display
    mode_display = content_mode.capitalize()  # "brief" -> "Brief", "detail" -> "Detail", "hide" -> "Hide"
    subtitle_text = f"[dim]Press [bold yellow]tab[/bold yellow] to switch mode([bright_green]{mode_display}[/bright_green]), [bold]esc[/bold] to return to daily view[/dim]"

    panel = Panel(
        Group(*all_items),
        title=f"[bold]Message Detail - {title_text}",
        subtitle=subtitle_text,
        border_style="white",
        expand=True,
    )

    # Update last_viewed_message_id to the last message in the current view
    if view_mode_ref and records:
        # Get the last message UUID (records are already sorted by timestamp)
        last_message = records[-1]
        hour_key = f"{target_date}_{target_hour}"
        last_viewed_key = f"last_viewed_message_id_{hour_key}"
        view_mode_ref[last_viewed_key] = last_message.message_uuid

    return Group(panel)


def _create_footer(date_range: str = None, fast_mode: bool = False, view_mode: str = "monthly", in_live_mode: bool = False, is_updating: bool = False, view_mode_ref: dict | None = None) -> Text:
    """
    Create footer with export command info, date range, and view mode.

    Args:
        date_range: Optional date range string to display
        fast_mode: If True, show warning about fast mode
        view_mode: Current view mode - "monthly", "weekly", or "yearly"
        in_live_mode: If True, show keyboard shortcuts for mode switching
        is_updating: If True, show updating spinner instead of last update time
        view_mode_ref: Reference dict for view mode state (includes usage_display_mode)

    Returns:
        Text with export instructions, date range, and view mode info
    """
    footer = Text()
    db_stats: dict | None = None

    def _ensure_db_stats() -> dict | None:
        nonlocal db_stats
        if db_stats is None:
            from src.storage.snapshot_db import get_database_stats as _get_database_stats
            db_stats = _get_database_stats()
        return db_stats

    # Get last update time from database (only if not currently updating)
    last_update_time = None
    if not is_updating:
        try:
            from src.utils.timezone import format_local_time, get_user_timezone
            # Get timezone once for performance
            user_tz = get_user_timezone()
            stats = _ensure_db_stats()
            if stats and stats.get("newest_timestamp"):
                timestamp_str = stats["newest_timestamp"]
                try:
                    dt = datetime.fromisoformat(timestamp_str)
                    last_update_time = format_local_time(dt, "%H:%M:%S", user_tz)
                except (ValueError, AttributeError):
                    pass
        except Exception:
            pass

    # Add fast mode warning if enabled
    if fast_mode:
        try:
            stats = _ensure_db_stats()
        except Exception:
            stats = None
        if stats and stats.get("newest_timestamp"):
            # Format ISO timestamp to be more readable
            timestamp_str = stats["newest_timestamp"]
            try:
                dt = datetime.fromisoformat(timestamp_str)
                formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                footer.append("⚠ Fast mode: Reading from last update (", style="bold red")
                footer.append(f"{formatted_time}", style="bold red")
                footer.append(")\n\n", style="bold red")
            except (ValueError, AttributeError):
                footer.append(f"⚠ Fast mode: Reading from last update ({timestamp_str})\n\n", style="bold red")
        else:
            footer.append("⚠ Fast mode: Reading from database (no timestamp available)\n\n", style="bold red")

    # Add current view mode if in live mode
    if in_live_mode:
        # Check if in daily detail mode or message detail mode
        in_daily_detail = view_mode == "weekly" and view_mode_ref and view_mode_ref.get("daily_detail_date")
        in_message_detail = view_mode == "weekly" and view_mode_ref and view_mode_ref.get("hourly_detail_hour") is not None

        # Show "Shortcut:" for usage mode, "View:" for others
        if view_mode == "usage":
            footer.append("Shortcut: ", style=DIM)
        else:
            footer.append("View: ", style=DIM)

        # Show simplified format for usage mode, full names for others
        if view_mode == "usage":
            # Simplified format for usage mode (only show usage and weekly)
            footer.append("[u]sage", style=f"black on {YELLOW}")
            footer.append(" ", style="")
            footer.append("[", style=DIM)
            footer.append("w", style="white")
            footer.append("]eekly", style=DIM)
        elif in_message_detail:
            # Message detail mode - show date and hour
            daily_date = view_mode_ref.get("daily_detail_date")
            hourly_hour = view_mode_ref.get("hourly_detail_hour")
            # Parse date to get day of week
            try:
                date_obj = datetime.strptime(daily_date, "%Y-%m-%d")
                day_name = date_obj.strftime("%a")
                hour_str = f"{hourly_hour:02d}:00"
                footer.append(f"Message Detail - {daily_date} ({day_name}) {hour_str}", style=f"black on {YELLOW}")
            except:
                footer.append(f"Message Detail - {daily_date} {hourly_hour:02d}:00", style=f"black on {YELLOW}")
        elif in_daily_detail:
            # Daily detail mode - show date
            daily_date = view_mode_ref.get("daily_detail_date")
            # Parse date to get day of week
            try:
                date_obj = datetime.strptime(daily_date, "%Y-%m-%d")
                day_name = date_obj.strftime("%a")
                footer.append(f"Daily Detail - {daily_date} ({day_name})", style=f"black on {YELLOW}")
            except:
                footer.append(f"Daily Detail - {daily_date}", style=f"black on {YELLOW}")
        else:
            # Full names for other modes
            # Usage
            if view_mode == "usage":
                footer.append("[u]sage", style=f"black on {YELLOW}")
            else:
                footer.append("[", style=DIM)
                footer.append("u", style="white")
                footer.append("]sage", style=DIM)
            footer.append(" ", style="")

            # Weekly
            if view_mode == "weekly":
                # Check which weekly mode is active
                weekly_display_mode = view_mode_ref.get('weekly_display_mode', 'limits') if view_mode_ref else 'limits'

                if weekly_display_mode == 'calendar':
                    # Calendar mode - use yellow (standard selection color)
                    footer.append("[w]eekly", style=f"black on {YELLOW}")
                else:
                    # Limit Period mode - use bright red (same as Usage Limits bar color)
                    footer.append("[w]eekly", style="black on bright_red")
            else:
                footer.append("[", style=DIM)
                footer.append("w", style="white")
                footer.append("]eekly", style=DIM)
            footer.append(" ", style="")

            # Monthly
            if view_mode == "monthly":
                footer.append("[m]onthly", style=f"black on {YELLOW}")
            else:
                footer.append("[", style=DIM)
                footer.append("m", style="white")
                footer.append("]onthly", style=DIM)
            footer.append(" ", style="")

            # Yearly
            if view_mode == "yearly":
                footer.append("[y]early", style=f"black on {YELLOW}")
            else:
                footer.append("[", style=DIM)
                footer.append("y", style="white")
                footer.append("]early", style=DIM)
            footer.append(" ", style="")

            # Heatmap
            if view_mode == "heatmap":
                footer.append("[h]eatmap", style=f"black on {YELLOW}")
            else:
                footer.append("[", style=DIM)
                footer.append("h", style="white")
                footer.append("]eatmap", style=DIM)
            footer.append(" ", style="")

            # Devices
            if view_mode == "devices":
                footer.append("[d]evices", style=f"black on {YELLOW}")
            else:
                footer.append("[", style=DIM)
                footer.append("d", style="white")
                footer.append("]evices", style=DIM)
            footer.append(" ", style="")

            # Settings
            footer.append("[", style=DIM)
            footer.append("s", style="white")
            footer.append("]ettings", style=DIM)

        # Add date range if provided (on same line), but not for usage mode, daily detail, or message detail mode
        if date_range and view_mode != "usage" and not in_daily_detail and not in_message_detail:
            footer.append("  ", style=DIM)
            footer.append(f"{date_range}", style="bold cyan")

        # Add newline at end
        footer.append("\n")

        # Add navigation hint for usage mode (second line) - change display mode and color
        if view_mode == "usage":
            # Get current display mode
            usage_display_mode = view_mode_ref.get('usage_display_mode', 0) if view_mode_ref else 0

            # Get current color mode
            color_mode = view_mode_ref.get('color_mode', 'solid') if view_mode_ref else 'solid'

            # Build mode name: S1-S4 (gradient modes removed)
            mode_number = (usage_display_mode % 4) + 1  # Convert 0-3 to 1-4
            current_mode_name = f"S{mode_number}"

            footer.append("Use ", style=DIM)
            footer.append("tab", style=f"bold {YELLOW}")
            footer.append(" to change mode(", style=DIM)
            footer.append(current_mode_name, style="bright_green")
            footer.append(")", style=DIM)
            footer.append("\n")

        # Add navigation hint for non-usage modes (second line)
        if view_mode in ["weekly", "monthly", "yearly"]:
            # Check if in message detail mode or daily detail mode
            in_message_detail = view_mode == "weekly" and view_mode_ref and view_mode_ref.get("hourly_detail_hour") is not None
            in_daily_detail = view_mode == "weekly" and view_mode_ref and view_mode_ref.get("daily_detail_date")

            if in_message_detail:
                # Message detail mode - show tab to switch mode and esc to return
                # Get current content mode ("hide", "brief", "detail", or "session")
                content_mode = view_mode_ref.get('message_content_mode', 'hide')
                current_mode = content_mode.capitalize()  # "Hide", "Brief", "Detail", or "Session"

                footer.append("Press ", style=DIM)
                footer.append("tab", style=f"bold {YELLOW}")
                footer.append(" to switch mode(", style=DIM)
                footer.append(current_mode, style="bright_green")
                footer.append("), ", style=DIM)
                footer.append("esc", style=f"bold {YELLOW}")
                footer.append(" to return to ", style=DIM)
                footer.append("daily view", style="white")
                footer.append(".", style=DIM)
                footer.append("\n")
            elif in_daily_detail:
                # Daily detail mode - show return instruction
                footer.append("Press ", style=DIM)
                footer.append("esc", style=f"bold {YELLOW}")
                footer.append(" to return to ", style=DIM)
                footer.append("weekly view", style="white")
                footer.append(".", style=DIM)
                footer.append("\n")
            else:
                # Navigation for weekly/monthly/yearly modes
                footer.append("Use ", style=DIM)
                footer.append("<", style=f"bold {YELLOW}")
                footer.append(" ", style=DIM)
                footer.append(">", style=f"bold {YELLOW}")
                if view_mode == "weekly":
                    period_label = "week"
                elif view_mode == "monthly":
                    period_label = "month"
                else:
                    period_label = "year"
                footer.append(f" to navigate {period_label}s, ", style=DIM)

                # Add tab to switch mode hint for weekly, monthly, and yearly modes
                if view_mode in ["weekly", "monthly", "yearly"]:
                    footer.append("tab", style=f"bold {YELLOW}")
                    footer.append(" to switch mode(", style=DIM)

                    # Show current mode name
                    if view_mode == "weekly":
                        current_mode = view_mode_ref.get('weekly_display_mode', 'limits')
                        mode_name = "Weekly Limit Period" if current_mode == "limits" else "Calendar Week"
                    elif view_mode == "monthly":
                        current_mode = view_mode_ref.get('monthly_display_mode', 'daily')
                        mode_name = "Daily" if current_mode == "daily" else "Weekly"
                    else:  # yearly
                        current_mode = view_mode_ref.get('yearly_display_mode', 'monthly')
                        mode_name = "Monthly" if current_mode == "monthly" else "Weekly"

                    footer.append(mode_name, style="bright_green")
                    footer.append("), ", style=DIM)

                footer.append("esc", style=f"bold {YELLOW}")
                footer.append(" key to quit.", style=DIM)
                footer.append("\n")
        elif view_mode == "devices":
            # Navigation for devices mode (week offset + period switching)
            footer.append("Use ", style=DIM)
            footer.append("<", style=f"bold {YELLOW}")
            footer.append(" ", style=DIM)
            footer.append(">", style=f"bold {YELLOW}")
            footer.append(" to navigate weeks, ", style=DIM)
            footer.append("tab", style=f"bold {YELLOW}")
            footer.append(" to switch period(", style=DIM)

            # Show current period
            current_period = view_mode_ref.get('device_display_period', 'all') if view_mode_ref else 'all'
            if current_period == 'all':
                period_name = "All Time"
            elif current_period == 'monthly':
                period_name = "Monthly"
            else:  # weekly
                period_name = "Weekly"

            footer.append(period_name, style="bright_green")
            footer.append("), ", style=DIM)
            footer.append("esc", style=f"bold {YELLOW}")
            footer.append(" key to quit.", style=DIM)
            footer.append("\n")
        elif view_mode == "heatmap":
            # Navigation and quit instructions for heatmap mode
            time_offset = view_mode_ref.get('offset', 0) if view_mode_ref else 0
            current_year = datetime.now().year + time_offset

            # Year navigation
            footer.append("Year: ", style=DIM)
            footer.append(f"{current_year}", style="bold bright_green")
            footer.append("  |  ", style=DIM)

            # Navigation keys
            footer.append("Navigate: ", style=DIM)
            footer.append("<", style=f"bold {YELLOW}")
            footer.append(" Previous year, ", style=DIM)
            footer.append(">", style=f"bold {YELLOW}")
            footer.append(" Next year  |  ", style=DIM)

            # Quit instruction
            footer.append("Press ", style=DIM)
            footer.append("esc", style=f"bold {YELLOW}")
            footer.append(" to quit.", style=DIM)
            footer.append("\n")

        # Add auto update time or updating status (last line, so cursor appears on right)
        if is_updating:
            footer.append("Auto [", style=DIM)
            footer.append("r", style="white")
            footer.append("]efresh: ", style=DIM)
            footer.append("Updating... ", style="bold yellow")
            footer.append("◼", style="bold yellow blink")
        elif last_update_time:
            footer.append("Auto [", style=DIM)
            footer.append("r", style="white")
            footer.append("]efresh: ", style=DIM)
            footer.append(f"{last_update_time} ", style="bold cyan")

        # Add Gist sync status (if available)
        if view_mode_ref and view_mode_ref.get('sync_status'):
            sync_status = view_mode_ref['sync_status']
            footer.append(" | ", style=DIM)
            footer.append("Gist: ", style=DIM)

            if sync_status.get('is_syncing'):
                footer.append("Syncing... ", style="bold yellow")
                footer.append("◼", style="bold yellow blink")
            elif sync_status.get('error'):
                footer.append("✗ Error ", style="bold red")
            elif sync_status.get('last_sync'):
                last_sync = sync_status['last_sync']
                now = datetime.now()
                diff = now - last_sync

                # Format time ago
                if diff.total_seconds() < 60:
                    time_ago = "Just now"
                elif diff.total_seconds() < 3600:
                    minutes = int(diff.total_seconds() / 60)
                    time_ago = f"{minutes}분 전"
                elif diff.total_seconds() < 86400:
                    hours = int(diff.total_seconds() / 3600)
                    time_ago = f"{hours}시간 전"
                else:
                    days = int(diff.total_seconds() / 86400)
                    time_ago = f"{days}일 전"

                footer.append(f"✓ {time_ago}", style="bold green")
            else:
                footer.append("Not synced", style=DIM)

    else:
        # No live mode, just date range if provided
        if date_range:
            footer.append("Data range: ", style=DIM)
            footer.append(f"{date_range}", style="bold cyan")
            footer.append("\n", style=DIM)

    return footer


#endregion
