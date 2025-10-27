#region Imports
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
import json
from pathlib import Path
from src.config.user_config import get_app_data_dir
#endregion


#region Constants
RESET_TIMES_FILE = "reset_times.json"
#endregion


#region Data Structure
def get_default_reset_times() -> dict:
    """
    Get default reset times structure.

    Returns:
        Dictionary with reset time fields (all None)
    """
    return {
        "session_reset": {
            "date": None,  # YYYY-MM-DD
            "time": None,  # HH:MM (24-hour)
            "timezone": None,  # e.g., "Asia/Seoul"
            "full_string": None,  # Original string from claude /usage
        },
        "week_reset": {
            "date": None,
            "time": None,
            "timezone": None,
            "full_string": None,
        },
        "opus_reset": {
            "date": None,
            "time": None,
            "timezone": None,
            "full_string": None,
        },
        "last_updated": None,  # ISO format timestamp
    }
#endregion


#region File Operations

def _get_reset_times_path() -> Path:
    """Get the path to the reset times JSON file."""
    return get_app_data_dir() / RESET_TIMES_FILE


def load_reset_times() -> dict:
    """
    Load stored reset times from disk.

    Returns:
        Dictionary with reset time information
    """
    path = _get_reset_times_path()

    if not path.exists():
        return get_default_reset_times()

    try:
        with open(path, "r") as f:
            data = json.load(f)
            # Ensure all required fields exist
            default = get_default_reset_times()
            for key in default:
                if key not in data:
                    data[key] = default[key]
            return data
    except (json.JSONDecodeError, IOError):
        return get_default_reset_times()


def save_reset_times(reset_times: dict) -> None:
    """
    Save reset times to disk.

    Args:
        reset_times: Dictionary with reset time information
    """
    path = _get_reset_times_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Update last_updated timestamp
    reset_times["last_updated"] = datetime.now().isoformat()

    with open(path, "w") as f:
        json.dump(reset_times, f, indent=2)


#endregion


#region Parsing Functions

def parse_reset_string(reset_str: str, current_tz: str = "UTC") -> dict:
    """
    Parse a reset time string from claude /usage output.

    Handles various formats:
    - Full: "Oct 27, 9:59am (Asia/Seoul)"
    - Date only: "Oct 27 (Asia/Seoul)"
    - Time only: "9:59am (Asia/Seoul)"
    - Numeric date: "10/27 9:59am (Asia/Seoul)"

    Args:
        reset_str: Reset time string from claude /usage
        current_tz: Current timezone to use if not specified

    Returns:
        Dictionary with parsed date, time, timezone fields
    """
    import re

    result = {
        "date": None,
        "time": None,
        "timezone": current_tz,
        "full_string": reset_str,
    }

    # Extract timezone from parentheses
    tz_match = re.search(r'\((.*?)\)', reset_str)
    if tz_match:
        result["timezone"] = tz_match.group(1)

    # Remove timezone part for easier parsing
    reset_no_tz = reset_str.split(' (')[0].strip()

    # Try to parse full format: "Oct 27, 9:59am" or "Oct 27 9:59am"
    date_match = re.search(r'([A-Za-z]+)\s+(\d+),?\s+(\d+):?(\d*)(am|pm)', reset_no_tz)
    if date_match:
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

        # Parse month name to number
        try:
            month_num = datetime.strptime(month_name, '%b').month
            year = datetime.now().year
            result["date"] = f"{year}-{month_num:02d}-{day:02d}"
            result["time"] = f"{hour:02d}:{minute:02d}"
            return result
        except ValueError:
            pass

    # Try numeric date format: "10/27 9:59am"
    date_match = re.search(r'(\d+)/(\d+)\s+(\d+):?(\d*)(am|pm)', reset_no_tz)
    if date_match:
        month_num = int(date_match.group(1))
        day = int(date_match.group(2))
        hour = int(date_match.group(3))
        minute = int(date_match.group(4)) if date_match.group(4) else 0
        meridiem = date_match.group(5)

        # Convert to 24-hour format
        if meridiem == 'pm' and hour != 12:
            hour += 12
        elif meridiem == 'am' and hour == 12:
            hour = 0

        year = datetime.now().year
        result["date"] = f"{year}-{month_num:02d}-{day:02d}"
        result["time"] = f"{hour:02d}:{minute:02d}"
        return result

    # Try date only: "Oct 27" or "10/27"
    date_match = re.search(r'([A-Za-z]+)\s+(\d+)', reset_no_tz)
    if date_match:
        month_name = date_match.group(1)
        day = int(date_match.group(2))
        try:
            month_num = datetime.strptime(month_name, '%b').month
            year = datetime.now().year
            result["date"] = f"{year}-{month_num:02d}-{day:02d}"
            return result
        except ValueError:
            pass

    date_match = re.search(r'(\d+)/(\d+)', reset_no_tz)
    if date_match:
        month_num = int(date_match.group(1))
        day = int(date_match.group(2))
        year = datetime.now().year
        result["date"] = f"{year}-{month_num:02d}-{day:02d}"
        return result

    # Try time only: "9:59am" or "12pm"
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

        result["time"] = f"{hour:02d}:{minute:02d}"
        return result

    return result


def update_reset_time(reset_type: str, new_reset_str: str, current_tz: str = "UTC") -> None:
    """
    Update a specific reset time (session, week, or opus) with new data.

    Only updates fields that are present in the new string (incremental update).

    Args:
        reset_type: One of "session_reset", "week_reset", "opus_reset"
        new_reset_str: New reset time string from claude /usage
        current_tz: Current timezone to use if not specified in string
    """
    if reset_type not in ["session_reset", "week_reset", "opus_reset"]:
        raise ValueError(f"Invalid reset_type: {reset_type}")

    # Load existing reset times
    reset_times = load_reset_times()

    # Parse new reset string
    parsed = parse_reset_string(new_reset_str, current_tz)

    # Get existing data for this reset type
    existing = reset_times[reset_type]

    # Incremental update: only update fields that are present in new data
    if parsed["date"]:
        existing["date"] = parsed["date"]
    if parsed["time"]:
        existing["time"] = parsed["time"]
    if parsed["timezone"]:
        existing["timezone"] = parsed["timezone"]

    # Always update full_string
    existing["full_string"] = parsed["full_string"]

    # Save back
    save_reset_times(reset_times)


#endregion


#region Query Functions

def get_reset_datetime(reset_type: str) -> Optional[datetime]:
    """
    Get the reset datetime as a timezone-aware datetime object.

    Args:
        reset_type: One of "session_reset", "week_reset", "opus_reset"

    Returns:
        Timezone-aware datetime object, or None if not enough info
    """
    reset_times = load_reset_times()
    reset_info = reset_times.get(reset_type)

    if not reset_info or not reset_info.get("date") or not reset_info.get("time"):
        return None

    try:
        # Parse date and time
        date_str = reset_info["date"]
        time_str = reset_info["time"]
        tz_str = reset_info.get("timezone", "UTC")

        # Create datetime
        dt_str = f"{date_str} {time_str}"
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")

        # Add timezone
        tz = ZoneInfo(tz_str)
        dt = dt.replace(tzinfo=tz)

        return dt
    except (ValueError, Exception):
        return None


def get_week_start_datetime(reset_type: str) -> Optional[datetime]:
    """
    Get the start of the current week period (reset_datetime - 7 days).

    Args:
        reset_type: One of "week_reset", "opus_reset"

    Returns:
        Timezone-aware datetime object for week start, or None if not available
    """
    from datetime import timedelta

    reset_dt = get_reset_datetime(reset_type)
    if not reset_dt:
        return None

    # Check if reset is in the past
    now = datetime.now(reset_dt.tzinfo)
    if reset_dt < now:
        # Reset already happened, week started 7 days before reset
        return reset_dt - timedelta(days=7)
    else:
        # Reset is in the future, we're still in the current week
        # Week started 7 days before reset
        return reset_dt - timedelta(days=7)


def format_reset_for_display(reset_type: str) -> str:
    """
    Format reset time for display in dashboard.

    Args:
        reset_type: One of "session_reset", "week_reset", "opus_reset"

    Returns:
        Formatted string like "Oct 27, 9:59am (Asia/Seoul)" or "Not available"
    """
    reset_times = load_reset_times()
    reset_info = reset_times.get(reset_type)

    if not reset_info:
        return "Not available"

    # Use full_string if available
    if reset_info.get("full_string"):
        return reset_info["full_string"]

    # Otherwise reconstruct from parts
    parts = []

    if reset_info.get("date"):
        # Convert YYYY-MM-DD to "Oct 27"
        try:
            dt = datetime.strptime(reset_info["date"], "%Y-%m-%d")
            parts.append(dt.strftime("%b %d"))
        except ValueError:
            pass

    if reset_info.get("time"):
        # Convert HH:MM to "9:59am"
        try:
            hour, minute = map(int, reset_info["time"].split(":"))
            meridiem = "am" if hour < 12 else "pm"
            display_hour = hour if hour <= 12 else hour - 12
            if display_hour == 0:
                display_hour = 12
            time_str = f"{display_hour}:{minute:02d}{meridiem}"
            parts.append(time_str)
        except ValueError:
            pass

    result = ", ".join(parts) if parts else "Not available"

    if reset_info.get("timezone"):
        result += f" ({reset_info['timezone']})"

    return result


#endregion
