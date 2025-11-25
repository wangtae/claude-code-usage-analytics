#region Imports
from datetime import datetime, timedelta
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.aggregation.daily_stats import aggregate_all
from src.config.settings import get_claude_jsonl_files
from src.data.jsonl_parser import parse_all_jsonl_files
from src.storage.snapshot_db import load_historical_records, save_snapshot
#endregion


#region Functions


def run(console: Console, year: Optional[int] = None, fast: bool = False) -> None:
    """
    Display GitHub-style activity heatmap in the terminal.

    Shows 3 heatmaps: token usage, week limit %, and sonnet limit %
    with the same visual design as PNG export but rendered directly
    in the terminal using Unicode block characters and colors.

    Args:
        console: Rich console for output
        year: Year to display (defaults to current year)
        fast: Skip data collection, read from database only

    Exit:
        Exits with status 0 on success, 1 on error
    """
    import sys

    try:
        # Load and update data (unless fast mode)
        if not fast:
            with console.status("[bold #ffffff]Loading Claude Code usage data...", spinner="dots", spinner_style="#ffffff"):
                jsonl_files = get_claude_jsonl_files()

            if not jsonl_files:
                console.print(
                    "[yellow]No Claude Code data found. "
                    "Make sure you've used Claude Code at least once.[/yellow]"
                )
                return

            # Update data
            with console.status("[bold #ffffff]Updating usage data...", spinner="dots", spinner_style="#ffffff"):
                current_records = parse_all_jsonl_files(jsonl_files)
                if current_records:
                    save_snapshot(current_records)

        # Load from database
        with console.status("[bold #ffffff]Preparing heatmap...", spinner="dots", spinner_style="#ffffff"):
            all_records = load_historical_records()

        if not all_records:
            console.print(
                "[yellow]No Claude Code usage data found.[/yellow]\n"
                "[dim]This could mean:[/dim]\n"
                "[dim]  • Claude Code has not been used yet on this machine[/dim]\n"
                "[dim]  • No JSONL log files exist in ~/.claude/projects/[/dim]\n"
            )
            return

        # Aggregate statistics
        stats = aggregate_all(all_records)

        # Load limits data
        limits_data = _load_limits_data()

        # Display heatmaps
        _display_heatmap(console, stats, limits_data, year)

    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[cyan]Exiting...[/cyan]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _display_heatmap(console: Console, stats, limits_data: dict, year: Optional[int] = None) -> None:
    """
    Display 3 GitHub-style heatmaps in terminal: tokens, week %, opus %.

    Uses the same visual design as PNG export with Claude theme colors.

    Args:
        console: Rich console for output
        stats: Aggregated statistics
        limits_data: Dictionary mapping dates to week_pct and sonnet_pct
        year: Year to display (defaults to current year)
    """
    # Determine year
    today = datetime.now().date()
    display_year = year if year is not None else today.year
    start_date = datetime(display_year, 1, 1).date()
    end_date = datetime(display_year, 12, 31).date()

    # Build weeks structure (same as PNG export)
    jan1_day = (start_date.weekday() + 1) % 7
    weeks = []
    current_week = []

    # Pad first week
    for _ in range(jan1_day):
        current_week.append((None, None))

    # Add all days
    current_date = start_date
    while current_date <= end_date:
        date_key = current_date.strftime("%Y-%m-%d")
        day_stats = stats.daily_stats.get(date_key)
        current_week.append((day_stats, current_date))

        if len(current_week) == 7:
            weeks.append(current_week)
            current_week = []

        current_date += timedelta(days=1)

    # Pad final week
    if current_week:
        while len(current_week) < 7:
            current_week.append((None, None))
        weeks.append(current_week)

    # Calculate max tokens for color scaling
    max_tokens = max(
        (s.total_tokens for s in stats.daily_stats.values()), default=1
    ) if stats.daily_stats else 1

    # Clear screen and display
    console.clear()

    # Title with Claude guy icon
    title = Text()
    title.append("🤖 ", style="bold")
    title.append(f"Your Claude Code activity in {display_year}", style="bold white")
    console.print(title)
    console.print()

    # Day names
    day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    # Helper function to create one heatmap panel
    def create_single_heatmap_panel(heatmap_title: str, color_func, legend_colors, legend_left: str, legend_right: str) -> Panel:
        # Create table with wider cells to make them square-like
        table = Table(
            show_header=True,
            box=None,
            padding=(0, 0),
            collapse_padding=True,
        )

        # Add day column
        table.add_column("", style="dim", width=3, justify="right")

        # Build month header and add week columns
        last_month = None
        for week_idx, week in enumerate(weeks):
            # Find first valid date in this week for month label
            month_label = ""
            for day_stats, date in week:
                if date is not None:
                    month = date.month
                    if month != last_month:
                        month_label = str(month)
                        last_month = month
                    break

            # Add column for this week - width=2 to make cells more square
            table.add_column(month_label, style="dim", width=2, justify="center")

        # Display 7 rows (one per day of week)
        for day_idx in range(7):
            row_cells = [day_names[day_idx]]

            for week_idx, week in enumerate(weeks):
                day_stats, date = week[day_idx]

                if date is None:
                    # Empty cell for padding
                    row_cells.append(Text("  ", style=""))
                else:
                    # Get color and create colored cell (2 spaces for square shape)
                    color_style = color_func(day_stats, date)
                    row_cells.append(Text("  ", style=f"on {color_style}"))

            table.add_row(*row_cells)

        # Create legend
        legend = Text()
        legend.append(legend_left + " ", style="dim")
        for color in legend_colors:
            legend.append("■", style=color)
        legend.append(" " + legend_right, style="dim")

        # Combine table and legend
        from rich.console import Group
        panel_content = Group(table, Text(""), legend)

        return Panel(
            panel_content,
            title=f"[bold]{heatmap_title}",
            border_style="white",
            expand=True,
        )

    # 1. Token Usage heatmap
    def tokens_color_func(day_stats, date):
        return _get_tokens_style(day_stats, max_tokens, date, today)

    # Legend colors matching PNG export: dark grey + 4 orange shades
    token_legend_colors = [
        "#3C3C3A",  # Dark grey (no activity)
        "#66554D",  # 20% orange
        "#8A6E61",  # 40% orange
        "#AE8775",  # 60% orange
        "#D2A089",  # 80% orange
        "#CB7B5D",  # Full orange
    ]
    token_panel = create_single_heatmap_panel("Token Usage", tokens_color_func, token_legend_colors, "Less", "More")

    # 2. Week Limit % heatmap (blue → red gradient)
    def week_color_func(day_stats, date):
        date_key = date.strftime("%Y-%m-%d")
        week_pct = limits_data.get(date_key, {}).get("week_pct", None)  # None for no data
        return _get_limits_style(week_pct, (93, 150, 203), date, today)

    # Legend: blue → red → dark red
    week_legend_colors = [
        "#5D96CB",  # Blue (0%)
        "#A36BA8",  # 33%
        "#CB5D5D",  # Red (100%)
        "#782850",  # Dark red (>100%)
    ]
    week_panel = create_single_heatmap_panel("Week Limit %", week_color_func, week_legend_colors, "0%", "100%+")

    # 3. Sonnet Limit % heatmap (green → red gradient)
    def sonnet_color_func(day_stats, date):
        date_key = date.strftime("%Y-%m-%d")
        sonnet_pct = limits_data.get(date_key, {}).get("sonnet_pct", None)  # None for no data
        return _get_limits_style(sonnet_pct, (93, 203, 123), date, today)

    # Legend: green → red → dark red
    sonnet_legend_colors = [
        "#5DCB7B",  # Green (0%)
        "#A36B99",  # 33%
        "#CB5D5D",  # Red (100%)
        "#782850",  # Dark red (>100%)
    ]
    sonnet_panel = create_single_heatmap_panel("Sonnet Limit %", sonnet_color_func, sonnet_legend_colors, "0%", "100%+")

    # Display all 3 panels
    console.print(token_panel, end="")
    console.print()
    console.print(week_panel, end="")
    console.print()
    console.print(sonnet_panel, end="")
    console.print()

    # Summary stats
    total_days = sum(1 for s in stats.daily_stats.values() if s.total_tokens > 0)
    total_tokens = sum(s.total_tokens for s in stats.daily_stats.values())
    console.print(f"[dim]Total: {total_tokens:,} tokens across {total_days} active days[/dim]")
    console.print()
    console.print("[dim]Tip: Use [bold]ccu export --open[/bold] for high-resolution PNG[/dim]")


def _load_limits_data() -> dict:
    """
    Load limits data from database.

    Returns:
        Dictionary mapping dates to {"week_pct": int, "sonnet_pct": int}
    """
    from src.storage.snapshot_db import DEFAULT_DB_PATH
    import sqlite3

    limits_data = {}

    try:
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        cursor = conn.cursor()

        # Load limits snapshots (percentages are already calculated)
        cursor.execute("""
            SELECT date, week_pct, sonnet_pct
            FROM limits_snapshots
            ORDER BY timestamp DESC
        """)

        for row in cursor.fetchall():
            date_str, week_pct, sonnet_pct = row

            # Only keep the most recent snapshot for each date
            if date_str not in limits_data:
                limits_data[date_str] = {
                    "week_pct": week_pct if week_pct is not None else 0,
                    "sonnet_pct": sonnet_pct if sonnet_pct is not None else 0
                }

        conn.close()
    except Exception as e:
        print(f"Warning: Could not load limits data: {e}")

    return limits_data


def _get_tokens_style(day_stats, max_tokens: int, date, today) -> str:
    """
    Get Rich color style for token usage (same as PNG export).

    Args:
        day_stats: Statistics for the day
        max_tokens: Maximum tokens for scaling
        date: The date of this cell
        today: Today's date

    Returns:
        Rich color style string
    """
    # Future days: light grey
    if date > today:
        return "#6B6B68"

    # Past days with no activity: dark grey
    if not day_stats or day_stats.total_tokens == 0:
        return "#3C3C3A"

    # Calculate intensity ratio (0.0 to 1.0)
    ratio = day_stats.total_tokens / max_tokens if max_tokens > 0 else 0

    # Apply non-linear scaling (same as PNG export)
    ratio = ratio ** 0.5

    # Interpolate from dark grey (#3C3C3A) to orange (#CB7B5D)
    dark_grey = (60, 60, 58)
    orange = (203, 123, 93)

    r = int(dark_grey[0] + (orange[0] - dark_grey[0]) * ratio)
    g = int(dark_grey[1] + (orange[1] - dark_grey[1]) * ratio)
    b = int(dark_grey[2] + (orange[2] - dark_grey[2]) * ratio)

    return f"#{r:02x}{g:02x}{b:02x}"


def _get_limits_style(pct: Optional[int], base_color_rgb: tuple[int, int, int], date, today) -> str:
    """
    Get Rich color style for limits percentage (same as PNG export).

    None = no data (dark grey), 0% = base_color, 100% = red, >100% = dark red/purple

    Args:
        pct: Usage percentage (0-100+) or None if no data
        base_color_rgb: Base color (blue for week, green for opus)
        date: The date of this cell
        today: Today's date

    Returns:
        Rich color style string
    """
    # Future days: light grey
    if date > today:
        return "#6B6B68"

    # Past days with no data: dark grey
    if pct is None:
        return "#3C3C3A"

    # 0%: show base color (blue/green)
    if pct == 0:
        return f"#{base_color_rgb[0]:02x}{base_color_rgb[1]:02x}{base_color_rgb[2]:02x}"

    # >100%: dark red/purple
    if pct > 100:
        return "#782850"

    # 1-100%: interpolate from base_color to red
    ratio = pct / 100.0
    red = (203, 93, 93)

    r = int(base_color_rgb[0] + (red[0] - base_color_rgb[0]) * ratio)
    g = int(base_color_rgb[1] + (red[1] - base_color_rgb[1]) * ratio)
    b = int(base_color_rgb[2] + (red[2] - base_color_rgb[2]) * ratio)

    return f"#{r:02x}{g:02x}{b:02x}"


#endregion
