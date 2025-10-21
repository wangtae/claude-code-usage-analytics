"""
Settings command - Interactive settings menu for Claude Goblin.

Allows users to configure display preferences, colors, and other options.
All settings are persisted to the database.
"""
import sys
import tty
import termios
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def run(console: Console) -> None:
    """
    Display settings menu and handle user input.

    Args:
        console: Rich console for rendering
    """
    from src.storage.snapshot_db import load_user_preferences, save_user_preference, DEFAULT_DB_PATH
    import socket

    try:
        while True:
            # Load current settings
            prefs = load_user_preferences()

            # Get machine name
            machine_name = prefs.get('machine_name', '') or socket.gethostname()

            # Get database path
            db_path = str(DEFAULT_DB_PATH)

            # Display settings menu
            _display_settings_menu(console, prefs, machine_name, db_path)

            # Wait for user input
            console.print("\n[dim]Enter setting key to edit ([#ff8800]1-2, 8-9, a-d, g-k[/#ff8800]), [#ff8800]\\[x][/#ff8800] reset to defaults, or [#ff8800]ESC[/#ff8800] to return...[/dim]", end="")

            key = _read_key()

            # Korean keyboard mapping
            hangul_to_english = {
                'ㅌ': 'x',  # x key (reset to defaults)
            }
            if key in hangul_to_english:
                key = hangul_to_english[key]

            if key == '\x1b':  # ESC
                break
            elif key in ['1', '2', '8', '9']:
                setting_num = int(key)
                _edit_setting(console, setting_num, prefs, save_user_preference)
            elif key in ['6', '7']:  # Model pricing (read-only)
                _show_pricing_readonly_message(console)
            elif key.lower() == 'a':  # Auto Backup
                setting_num = 10
                _edit_setting(console, setting_num, prefs, save_user_preference)
            elif key.lower() == 'b':  # Keep Monthly Backups
                setting_num = 11
                _edit_setting(console, setting_num, prefs, save_user_preference)
            elif key.lower() == 'c':  # Backup Retention
                setting_num = 12
                _edit_setting(console, setting_num, prefs, save_user_preference)
            elif key.lower() == 'd':  # Display Timezone
                setting_num = 13
                _edit_setting(console, setting_num, prefs, save_user_preference)
            elif key.lower() == 'g':  # Machine Name
                _edit_machine_name(console)
            elif key.lower() == 'h':  # Database Path
                _edit_database_path(console)
            elif key.lower() == 'i':  # Check Data Sync
                _check_and_sync_data(console)
            elif key.lower() == 'j':  # Exclude Haiku Messages
                setting_num = 16
                _edit_setting(console, setting_num, prefs, save_user_preference)
            elif key.lower() == 'k':  # Weekly Recommended Days
                setting_num = 17
                _edit_setting(console, setting_num, prefs, save_user_preference)
            elif key.lower() == 'x':  # Reset to defaults
                _reset_to_defaults(console, save_user_preference)
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully - just exit settings
        console.print("\n")
        return


def _read_key() -> str:
    """
    Read a single key from stdin.

    Returns:
        The key pressed as a string

    Raises:
        KeyboardInterrupt: If Ctrl+C is pressed
    """
    try:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            key = sys.stdin.read(1)

            # Handle Ctrl+C and Ctrl+D in raw mode
            if key == '\x03':  # Ctrl+C
                raise KeyboardInterrupt
            elif key == '\x04':  # Ctrl+D (EOF)
                raise EOFError

            return key
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception:
        # Fallback for non-Unix systems
        return input()


def _display_settings_menu(console: Console, prefs: dict, machine_name: str, db_path: str) -> None:
    """
    Display the settings menu showing all current settings.

    Args:
        console: Rich console for rendering
        prefs: Dictionary of user preferences
        machine_name: Current machine name
        db_path: Current database path
    """
    # Clear screen without affecting scroll buffer
    import sys
    sys.stdout.write("\033[3J")  # Clear scrollback buffer
    sys.stdout.write("\033[2J")  # Clear visible screen
    sys.stdout.write("\033[H")   # Move cursor to home
    sys.stdout.flush()
    console.print()

    # Get timezone info first (used by both panels)
    from src.utils.timezone import get_user_timezone, get_timezone_info
    tz_setting = prefs.get('timezone', 'auto')
    actual_tz = get_user_timezone()
    tz_info = get_timezone_info(actual_tz)

    # Status section (read-only)
    status_table = Table(show_header=True, box=None, padding=(0, 2))
    status_table.add_column("Status Item", style="white", justify="left", width=25)
    status_table.add_column("Value", style="cyan", justify="left")

    # Program version
    try:
        import tomllib
        from pathlib import Path
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject_data = tomllib.load(f)
            version = pyproject_data.get("project", {}).get("version", "Unknown")
    except Exception:
        version = "0.2.0"  # Fallback version

    status_table.add_row("Program Version", version)

    display_mode_names = ["M1 (simple, bar+%)", "M2 (simple, bar %)", "M3 (panel, bar+%)", "M4 (panel, bar %)"]
    display_mode = int(prefs.get('usage_display_mode', '0'))
    status_table.add_row("Display Mode", display_mode_names[display_mode] if 0 <= display_mode < 4 else "M1")

    color_mode = prefs.get('color_mode', 'solid')
    status_table.add_row("Color Mode", "Solid")

    # Timezone display
    if tz_setting == 'auto':
        tz_display = f"Auto ({tz_info['abbr']}, {tz_info['offset']})"
    else:
        tz_display = f"{tz_info['abbr']} ({tz_info['offset']})"
    status_table.add_row("Display Timezone", tz_display)

    # Machine name (editable with [g])
    import socket
    from src.config.user_config import get_machine_name as get_custom_machine_name
    custom_name = get_custom_machine_name()
    if custom_name == socket.gethostname():
        machine_display = f"{machine_name} [dim](auto)[/dim]   [#ff8800]\\[g][/#ff8800]"
    else:
        machine_display = f"{machine_name}   [#ff8800]\\[g][/#ff8800]"
    status_table.add_row("Machine Name", machine_display)

    # Database path (editable with [h])
    from src.config.user_config import get_db_path as get_custom_db_path
    custom_db = get_custom_db_path()
    if custom_db:
        if "OneDrive" in db_path or "CloudDocs" in db_path:
            db_display = f"{db_path}\n[green]✓ Cloud sync[/green]   [#ff8800]\\[h][/#ff8800]"
        else:
            db_display = f"{db_path}\n[yellow]⚠ Local only[/yellow]   [#ff8800]\\[h][/#ff8800]"
    else:
        if "OneDrive" in db_path or "CloudDocs" in db_path:
            db_display = f"{db_path}\n[green]✓ Cloud sync (auto)[/green]   [#ff8800]\\[h][/#ff8800]"
        else:
            db_display = f"{db_path}\n[dim](auto-detect)[/dim]   [#ff8800]\\[h][/#ff8800]"
    status_table.add_row("Database Path", db_display)

    # Storage Mode (new row)
    storage_mode = _detect_storage_mode(db_path)
    status_table.add_row("Storage Mode", storage_mode)

    # Data sync status
    from src.storage.snapshot_db import check_data_sync_status
    sync_status = check_data_sync_status()

    if sync_status['is_synced']:
        sync_display = f"[green]{sync_status['status_message']}[/green]   [#ff8800]\\[i][/#ff8800]"
    else:
        sync_display = f"[yellow]{sync_status['status_message']}[/yellow]   [#ff8800]\\[i][/#ff8800]"

    status_table.add_row("Data Sync Status", sync_display)

    # Database file size
    try:
        from pathlib import Path
        db_file = Path(db_path)
        if db_file.exists():
            size_bytes = db_file.stat().st_size
            # Format size in human-readable format (KB, MB, GB)
            if size_bytes < 1024:
                size_str = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.2f} KB"
            elif size_bytes < 1024 * 1024 * 1024:
                size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
            else:
                size_str = f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
            status_table.add_row("Database Size", size_str)
        else:
            status_table.add_row("Database Size", "[dim]Not found[/dim]")
    except Exception:
        status_table.add_row("Database Size", "[dim]Unknown[/dim]")

    # Local backup information
    from src.config.user_config import get_last_backup_date
    from src.utils.backup import list_backups, get_backup_directory
    from pathlib import Path

    last_backup = get_last_backup_date()
    try:
        backups = list_backups(Path(db_path))
        backup_count = len(backups)
        monthly_count = sum(1 for b in backups if b["is_monthly"])

        if last_backup:
            local_backup_display = f"{last_backup} ({backup_count} files)"
        else:
            local_backup_display = f"[dim]Never[/dim] ({backup_count} files)" if backup_count > 0 else "[dim]Never[/dim]"
    except:
        if last_backup:
            local_backup_display = last_backup
        else:
            local_backup_display = "[dim]Never[/dim]"

    status_table.add_row("Last Local Backup", local_backup_display)

    # Git Gist backup information
    gist_info = _get_gist_backup_info()
    if "error" not in gist_info:
        # Format timestamp: "2025-10-21T14:32:04Z" -> "2025-10-21 14:32"
        from datetime import datetime
        try:
            sync_time = datetime.fromisoformat(gist_info["last_sync"].replace("Z", "+00:00"))
            sync_display = sync_time.strftime("%Y-%m-%d %H:%M")
        except:
            # Fallback: YYYY-MM-DDTHH:MM
            sync_display = gist_info["last_sync"][:16].replace("T", " ")

        records_display = f"{gist_info['total_records']:,}" if gist_info['total_records'] > 0 else "0"
        gist_display = f"{sync_display} ({records_display} records)"
        status_table.add_row("Last Gist Sync", gist_display)
    elif gist_info.get("error") == "not_synced":
        # Git Gist configured but never synced
        status_table.add_row("Last Gist Sync", "[dim]Not synced yet[/dim]")
    # If not_configured or failed, don't show the row (keeps UI clean)

    status_panel = Panel(
        status_table,
        title="[bold]Status (Read-Only)",
        border_style="white",
        expand=True,
    )
    console.print(status_panel)
    console.print()

    # Settings section (editable)
    from src.config.defaults import DEFAULT_COLORS, DEFAULT_INTERVALS

    settings_table = Table(show_header=True, box=None, padding=(0, 2))
    settings_table.add_column("#", style="dim", justify="right", width=5)
    settings_table.add_column("Setting", style="white", justify="left", width=30)
    settings_table.add_column("Value", style="cyan", justify="left")

    # Color settings - display with actual color
    color_solid = prefs.get('color_solid', DEFAULT_COLORS['color_solid'])
    color_unfilled = prefs.get('color_unfilled', DEFAULT_COLORS['color_unfilled'])

    settings_table.add_row("[#ff8800][1][/#ff8800]", "Solid Color", f"[{color_solid}]{color_solid}[/{color_solid}]")
    settings_table.add_row("[#ff8800][2][/#ff8800]", "Unfilled Color", f"[{color_unfilled}]{color_unfilled}[/{color_unfilled}]")

    # Model pricing settings (read-only - edit src/config/defaults.py to change)
    from src.storage.snapshot_db import get_model_pricing_for_settings
    pricing_data = get_model_pricing_for_settings()

    sonnet_pricing = pricing_data.get('sonnet-4.5', {})
    sonnet_in = sonnet_pricing.get('input_price', 3.0)
    sonnet_out = sonnet_pricing.get('output_price', 15.0)
    settings_table.add_row("[dim][6][/dim]", "Sonnet 4.5 Pricing (In/Out)", f"[dim]${sonnet_in:.2f}/${sonnet_out:.2f}[/dim]")

    opus_pricing = pricing_data.get('opus-4', {})
    opus_in = opus_pricing.get('input_price', 15.0)
    opus_out = opus_pricing.get('output_price', 75.0)
    settings_table.add_row("[dim][7][/dim]", "Opus 4 Pricing (In/Out)", f"[dim]${opus_in:.2f}/${opus_out:.2f}[/dim]")

    # Auto refresh settings
    refresh_interval = prefs.get('refresh_interval', DEFAULT_INTERVALS['refresh_interval'])
    settings_table.add_row("[#ff8800][8][/#ff8800]", "Auto Refresh Interval (sec)", refresh_interval)

    watch_interval = prefs.get('watch_interval', DEFAULT_INTERVALS['watch_interval'])
    settings_table.add_row("[#ff8800][9][/#ff8800]", "File Watch Interval (sec)", watch_interval)

    # Backup settings
    from src.config.user_config import (
        get_backup_enabled,
        get_backup_keep_monthly,
        get_backup_retention_days,
    )

    backup_enabled = get_backup_enabled()
    settings_table.add_row("[#ff8800]\\[a][/#ff8800]", "Auto Backup", "Enabled" if backup_enabled else "Disabled")

    keep_monthly = get_backup_keep_monthly()
    settings_table.add_row("[#ff8800]\\[b][/#ff8800]", "Keep Monthly Backups", "Yes" if keep_monthly else "No")

    retention_days = get_backup_retention_days()
    settings_table.add_row("[#ff8800]\\[c][/#ff8800]", "Backup Retention (days)", str(retention_days))

    # Timezone setting
    if tz_setting == 'auto':
        tz_value = f"Auto ({tz_info['abbr']})"
    else:
        tz_value = f"{tz_setting} ({tz_info['abbr']})"
    settings_table.add_row("[#ff8800]\\[d][/#ff8800]", "Display Timezone", tz_value)

    # Exclude Haiku Messages
    from src.config.defaults import DEFAULT_PREFERENCES
    exclude_haiku = prefs.get('exclude_haiku_messages', DEFAULT_PREFERENCES['exclude_haiku_messages'])
    exclude_haiku_display = "Enabled" if exclude_haiku == "1" else "Disabled"
    settings_table.add_row("[#ff8800]\\[j][/#ff8800]", "Exclude Haiku Messages", exclude_haiku_display)

    # Weekly recommended days
    weekly_days = prefs.get('weekly_recommended_days', DEFAULT_PREFERENCES['weekly_recommended_days'])
    settings_table.add_row("[#ff8800]\\[k][/#ff8800]", "Weekly Recommended Days", weekly_days)

    # Empty row for spacing
    settings_table.add_row("", "", "")

    # Reset to defaults option
    settings_table.add_row("[#ff8800]\\[x][/#ff8800]", "Reset to Defaults", "[dim]Type 'yes' to confirm[/dim]")

    settings_panel = Panel(
        settings_table,
        title="[bold]Settings (Editable)",
        subtitle="[dim]Note: Edit src/config/defaults.py to change model pricing[/dim]",
        border_style="white",
        expand=True,
    )
    console.print(settings_panel)


def _edit_setting(console: Console, setting_num: int, prefs: dict, save_func) -> None:
    """
    Edit a single setting value.

    Args:
        console: Rich console for rendering
        setting_num: Number of the setting to edit (1-15)
        prefs: Current preferences dictionary
        save_func: Function to save preference
    """
    from src.config.defaults import DEFAULT_COLORS, DEFAULT_INTERVALS

    setting_map = {
        1: ('color_solid', 'Solid Color', DEFAULT_COLORS['color_solid']),
        2: ('color_unfilled', 'Unfilled Color', DEFAULT_COLORS['color_unfilled']),
        8: ('refresh_interval', 'Auto Refresh Interval (seconds)', DEFAULT_INTERVALS['refresh_interval']),
        9: ('watch_interval', 'File Watch Interval (seconds)', DEFAULT_INTERVALS['watch_interval']),
    }

    # Note: Model pricing (6, 7) is read-only - edit src/config/defaults.py to change

    # Handle backup settings separately (10, 11, 12)
    if setting_num in [10, 11, 12]:
        _edit_backup_setting(console, setting_num)
        return

    # Handle timezone setting separately (13)
    if setting_num == 13:
        _edit_timezone_setting(console, prefs, save_func)
        return

    # Handle exclude haiku messages setting separately (16)
    if setting_num == 16:
        _edit_exclude_haiku_setting(console, prefs, save_func)
        return

    # Handle weekly recommended days setting separately (17)
    if setting_num == 17:
        _edit_weekly_days_setting(console, prefs, save_func)
        return

    if setting_num not in setting_map:
        return

    key, name, default = setting_map[setting_num]
    current_value = prefs.get(key, default)

    console.print()
    console.print(f"[bold]Edit {name}[/bold]")
    console.print(f"[dim]Current value: {current_value}[/dim]")
    console.print(f"[dim]Default value: {default}[/dim]")

    # Read input in normal mode
    if setting_num in [1, 2]:
        # Color input
        console.print("[dim]Enter hex color (e.g., #00A7E1), 'd' for default, or press Enter to keep current:[/dim]")
        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            new_value = input().strip()

            if new_value:
                # Check for default reset
                if new_value.lower() in ['d', 'default']:
                    from src.storage.snapshot_db import delete_user_preference
                    delete_user_preference(key)
                    console.print(f"[green]✓ {name} reset to default: {default}[/green]")
                    console.print(f"[dim]  (Using value from src/config/defaults.py)[/dim]")
                # Validate hex color format
                elif new_value.startswith('#') and len(new_value) == 7:
                    try:
                        int(new_value[1:], 16)  # Check if valid hex
                        save_func(key, new_value)
                        console.print(f"[green]✓ {name} updated to {new_value}[/green]")
                    except ValueError:
                        console.print("[red]✗ Invalid hex color format. Must be #RRGGBB[/red]")
                else:
                    console.print("[red]✗ Invalid hex color format. Must be #RRGGBB or 'd' for default[/red]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Input cancelled[/yellow]")
    else:
        # Interval input (8, 9)
        console.print("[dim]Enter interval in seconds (minimum 10), 'd' for default, or press Enter to keep current:[/dim]")
        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            new_value = input().strip()

            if new_value:
                # Check for default reset
                if new_value.lower() in ['d', 'default']:
                    from src.storage.snapshot_db import delete_user_preference
                    delete_user_preference(key)
                    console.print(f"[green]✓ {name} reset to default: {default} seconds[/green]")
                    console.print(f"[dim]  (Using value from src/config/defaults.py)[/dim]")
                else:
                    try:
                        interval = int(new_value)
                        if interval >= 10:
                            save_func(key, str(interval))
                            console.print(f"[green]✓ {name} updated to {interval} seconds[/green]")
                        else:
                            console.print("[red]✗ Interval must be at least 10 seconds[/red]")
                    except ValueError:
                        console.print("[red]✗ Invalid number. Enter a number or 'd' for default[/red]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Input cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def _show_pricing_readonly_message(console: Console) -> None:
    """
    Show message explaining that model pricing is read-only.

    Args:
        console: Rich console for rendering
    """
    console.print()
    console.print("[bold yellow]Model Pricing (Read-Only)[/bold yellow]")
    console.print()
    console.print("[dim]Model pricing cannot be changed from the Settings menu.[/dim]")
    console.print()
    console.print("[cyan]To change model pricing:[/cyan]")
    console.print("  [yellow]1.[/yellow] Edit the file: [cyan]src/config/defaults.py[/cyan]")
    console.print("  [yellow]2.[/yellow] Find the [cyan]DEFAULT_MODEL_PRICING[/cyan] section")
    console.print("  [yellow]3.[/yellow] Update the pricing values (per million tokens in USD)")
    console.print("  [yellow]4.[/yellow] Restart the program to apply changes")
    console.print()
    console.print("[dim]Example:[/dim]")
    console.print('[dim]  "claude-sonnet-4-5-20250929": {[/dim]')
    console.print('[dim]      "input_price": 3.0,[/dim]')
    console.print('[dim]      "output_price": 15.0,[/dim]')
    console.print('[dim]      ...[/dim]')
    console.print('[dim]  }[/dim]')
    console.print()
    console.print("[dim]Press any key to continue...[/dim]")
    _read_key()


def _edit_backup_setting(console: Console, setting_num: int) -> None:
    """
    Edit backup-related settings (10, 11, 12).

    Args:
        console: Rich console for rendering
        setting_num: Setting number (10, 11, or 12)
    """
    from src.config.user_config import (
        get_backup_enabled,
        set_backup_enabled,
        get_backup_keep_monthly,
        set_backup_keep_monthly,
        get_backup_retention_days,
        set_backup_retention_days,
    )

    console.print()

    if setting_num == 10:
        # Auto Backup (True/False)
        current = get_backup_enabled()
        console.print("[bold]Edit Auto Backup[/bold]")
        console.print(f"[dim]Current value: {'Enabled' if current else 'Disabled'}[/dim]")
        console.print(f"[dim]Default value: Enabled[/dim]")
        console.print("[dim]Enter 'yes' to enable, 'no' to disable, 'd' for default, or press Enter to keep current:[/dim]")

        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            new_value = input().strip().lower()

            if new_value in ['d', 'default']:
                set_backup_enabled(True)
                console.print("[green]✓ Auto Backup reset to default: Enabled[/green]")
            elif new_value in ['yes', 'y', 'true', '1']:
                set_backup_enabled(True)
                console.print("[green]✓ Auto Backup enabled[/green]")
            elif new_value in ['no', 'n', 'false', '0']:
                set_backup_enabled(False)
                console.print("[green]✓ Auto Backup disabled[/green]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Input cancelled[/yellow]")

    elif setting_num == 11:
        # Keep Monthly Backups (True/False)
        current = get_backup_keep_monthly()
        console.print("[bold]Edit Keep Monthly Backups[/bold]")
        console.print(f"[dim]Current value: {'Yes' if current else 'No'}[/dim]")
        console.print(f"[dim]Default value: Yes[/dim]")
        console.print("[dim]Keep backups from the 1st of each month permanently?[/dim]")
        console.print("[dim]Enter 'yes', 'no', 'd' for default, or press Enter to keep current:[/dim]")

        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            new_value = input().strip().lower()

            if new_value in ['d', 'default']:
                set_backup_keep_monthly(True)
                console.print("[green]✓ Keep Monthly Backups reset to default: Yes[/green]")
            elif new_value in ['yes', 'y', 'true', '1']:
                set_backup_keep_monthly(True)
                console.print("[green]✓ Monthly backups will be kept permanently[/green]")
            elif new_value in ['no', 'n', 'false', '0']:
                set_backup_keep_monthly(False)
                console.print("[green]✓ Monthly backups will be deleted after retention period[/green]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Input cancelled[/yellow]")

    elif setting_num == 12:
        # Backup Retention Days (int)
        current = get_backup_retention_days()
        console.print("[bold]Edit Backup Retention (days)[/bold]")
        console.print(f"[dim]Current value: {current} days[/dim]")
        console.print(f"[dim]Default value: 30 days[/dim]")
        console.print("[dim]Enter number of days (minimum 1), 'd' for default, or press Enter to keep current:[/dim]")

        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            new_value = input().strip()

            if new_value:
                if new_value.lower() in ['d', 'default']:
                    set_backup_retention_days(30)
                    console.print("[green]✓ Backup retention reset to default: 30 days[/green]")
                else:
                    try:
                        days = int(new_value)
                        if days >= 1:
                            set_backup_retention_days(days)
                            console.print(f"[green]✓ Backup retention set to {days} days[/green]")
                        else:
                            console.print("[red]✗ Days must be at least 1[/red]")
                    except ValueError:
                        console.print("[red]✗ Invalid number. Enter a number or 'd' for default[/red]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Input cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def _edit_timezone_setting(console: Console, prefs: dict, save_func) -> None:
    """
    Edit timezone setting (13).

    Args:
        console: Rich console for rendering
        prefs: Current preferences dictionary
        save_func: Function to save preference
    """
    from src.utils.timezone import get_user_timezone, get_timezone_info, validate_timezone, list_common_timezones

    console.print()
    console.print("[bold]Edit Display Timezone[/bold]")

    # Show current setting
    current_tz = prefs.get('timezone', 'auto')
    actual_tz = get_user_timezone()
    tz_info = get_timezone_info(actual_tz)

    if current_tz == 'auto':
        console.print(f"[dim]Current: Auto ({tz_info['name']}, {tz_info['offset']})[/dim]")
    else:
        console.print(f"[dim]Current: {current_tz} ({tz_info['offset']})[/dim]")
    console.print(f"[dim]Default: Auto (system timezone detection)[/dim]")

    console.print()
    console.print("[dim]Select timezone option:[/dim]")
    console.print("  [yellow][1][/yellow] Auto (system timezone detection)")
    console.print("  [yellow][2][/yellow] UTC")
    console.print("  [yellow][3][/yellow] Select from common timezones")
    console.print("  [yellow][d][/yellow] Reset to default (Auto)")
    console.print("  [yellow][Enter][/yellow] Keep current setting")
    console.print()

    try:
        sys.stdout.write("> ")
        sys.stdout.flush()
        choice = input().strip()

        if not choice:
            # Keep current
            return

        if choice.lower() in ['d', 'default']:
            # Reset to default (Auto) by deleting from database
            from src.storage.snapshot_db import delete_user_preference
            delete_user_preference('timezone')
            console.print("[green]✓ Timezone reset to default: Auto (system detection)[/green]")
            console.print("[dim]  (Using value from src/config/defaults.py)[/dim]")

        elif choice == '1':
            # Auto mode
            save_func('timezone', 'auto')
            console.print("[green]✓ Timezone set to Auto (system detection)[/green]")

        elif choice == '2':
            # UTC mode
            save_func('timezone', 'UTC')
            console.print("[green]✓ Timezone set to UTC[/green]")

        elif choice == '3':
            # Show common timezones
            console.print()
            console.print("[dim]Common timezones:[/dim]")
            common_tzs = list_common_timezones()

            for idx, tz in enumerate(common_tzs, start=1):
                console.print(f"  [{idx:2d}] {tz['name']:25s} {tz['offset']}")

            console.print()
            console.print("[dim]Enter number (1-{}) or custom IANA timezone name:[/dim]".format(len(common_tzs)))

            sys.stdout.write("> ")
            sys.stdout.flush()
            tz_choice = input().strip()

            if tz_choice.isdigit():
                # Numeric selection
                idx = int(tz_choice)
                if 1 <= idx <= len(common_tzs):
                    selected_tz = common_tzs[idx - 1]['name']
                    save_func('timezone', selected_tz)
                    console.print(f"[green]✓ Timezone set to {selected_tz}[/green]")
                else:
                    console.print("[red]✗ Invalid selection[/red]")
            elif tz_choice:
                # Custom IANA name
                if validate_timezone(tz_choice):
                    save_func('timezone', tz_choice)
                    console.print(f"[green]✓ Timezone set to {tz_choice}[/green]")
                else:
                    console.print("[red]✗ Invalid timezone name[/red]")

        else:
            console.print("[yellow]Invalid choice[/yellow]")

    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Input cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def _edit_color_range_setting(console: Console, setting_num: int, prefs: dict, save_func) -> None:
    """
    Edit color range threshold settings (14, 15).

    Args:
        console: Rich console for rendering
        setting_num: Setting number (14 or 15)
        prefs: Current preferences dictionary
        save_func: Function to save preference
    """
    from src.config.defaults import DEFAULT_COLORS

    console.print()

    if setting_num == 14:
        # Color Range Low
        current = prefs.get('color_range_low', DEFAULT_COLORS.get('color_range_low', '60'))
        default = DEFAULT_COLORS.get('color_range_low', '60')
        console.print("[bold]Edit Color Range Low (%)[/bold]")
        console.print(f"[dim]Current value: {current}%[/dim]")
        console.print(f"[dim]Default value: {default}%[/dim]")
        console.print("[dim]This sets the upper bound for 'Low' gradient (0-X%).[/dim]")
        console.print("[dim]Enter percentage (1-99), 'd' for default, or press Enter to keep current:[/dim]")

        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            new_value = input().strip()

            if new_value:
                if new_value.lower() in ['d', 'default']:
                    from src.storage.snapshot_db import delete_user_preference
                    delete_user_preference('color_range_low')
                    console.print(f"[green]✓ Color Range Low reset to default: {default}%[/green]")
                    console.print(f"[dim]  (Using value from src/config/defaults.py)[/dim]")
                else:
                    try:
                        percent = int(new_value)
                        color_range_high = int(prefs.get('color_range_high', DEFAULT_COLORS.get('color_range_high', '85')))
                        if 1 <= percent <= 99 and percent < color_range_high:
                            save_func('color_range_low', str(percent))
                            console.print(f"[green]✓ Color Range Low set to {percent}%[/green]")
                            console.print(f"[dim]  Gradient Low: 0-{percent}%[/dim]")
                            console.print(f"[dim]  Gradient Mid: {percent}-{color_range_high}%[/dim]")
                        elif percent >= color_range_high:
                            console.print(f"[red]✗ Must be less than Color Range High ({color_range_high}%)[/red]")
                        else:
                            console.print("[red]✗ Percentage must be between 1 and 99[/red]")
                    except ValueError:
                        console.print("[red]✗ Invalid number. Enter a number or 'd' for default[/red]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Input cancelled[/yellow]")

    elif setting_num == 15:
        # Color Range High
        current = prefs.get('color_range_high', DEFAULT_COLORS.get('color_range_high', '85'))
        default = DEFAULT_COLORS.get('color_range_high', '85')
        console.print("[bold]Edit Color Range High (%)[/bold]")
        console.print(f"[dim]Current value: {current}%[/dim]")
        console.print(f"[dim]Default value: {default}%[/dim]")
        console.print("[dim]This sets the upper bound for 'Mid' gradient (X-Y%).[/dim]")
        console.print("[dim]Enter percentage (1-99), 'd' for default, or press Enter to keep current:[/dim]")

        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            new_value = input().strip()

            if new_value:
                if new_value.lower() in ['d', 'default']:
                    from src.storage.snapshot_db import delete_user_preference
                    delete_user_preference('color_range_high')
                    console.print(f"[green]✓ Color Range High reset to default: {default}%[/green]")
                    console.print(f"[dim]  (Using value from src/config/defaults.py)[/dim]")
                else:
                    try:
                        percent = int(new_value)
                        color_range_low = int(prefs.get('color_range_low', DEFAULT_COLORS.get('color_range_low', '60')))
                        if 1 <= percent <= 99 and percent > color_range_low:
                            save_func('color_range_high', str(percent))
                            console.print(f"[green]✓ Color Range High set to {percent}%[/green]")
                            console.print(f"[dim]  Gradient Mid: {color_range_low}-{percent}%[/dim]")
                            console.print(f"[dim]  Gradient High: {percent}-100%[/dim]")
                        elif percent <= color_range_low:
                            console.print(f"[red]✗ Must be greater than Color Range Low ({color_range_low}%)[/red]")
                        else:
                            console.print("[red]✗ Percentage must be between 1 and 99[/red]")
                    except ValueError:
                        console.print("[red]✗ Invalid number. Enter a number or 'd' for default[/red]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Input cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def _edit_exclude_haiku_setting(console: Console, prefs: dict, save_func) -> None:
    """
    Edit exclude haiku messages setting (16).

    Args:
        console: Rich console for rendering
        prefs: Current preferences dictionary
        save_func: Function to save preference
    """
    from src.config.defaults import DEFAULT_PREFERENCES

    console.print()
    console.print("[bold]Edit Exclude Haiku Messages[/bold]")
    console.print()

    current = prefs.get('exclude_haiku_messages', DEFAULT_PREFERENCES['exclude_haiku_messages'])
    current_display = "Enabled" if current == "1" else "Disabled"
    default = DEFAULT_PREFERENCES['exclude_haiku_messages']
    default_display = "Enabled" if default == "1" else "Disabled"

    console.print(f"[dim]Current value: {current_display}[/dim]")
    console.print(f"[dim]Default value: {default_display}[/dim]")
    console.print()
    console.print("[dim]When enabled, Haiku model messages are excluded from output displays.[/dim]")
    console.print("[dim]This affects statistics and visualizations across all views.[/dim]")
    console.print()
    console.print("[dim]Enter 'yes' to enable, 'no' to disable, 'd' for default, or press Enter to keep current:[/dim]")

    try:
        sys.stdout.write("> ")
        sys.stdout.flush()
        new_value = input().strip().lower()

        if not new_value:
            # Keep current
            return

        if new_value in ['d', 'default']:
            # Reset to default by deleting from database
            from src.storage.snapshot_db import delete_user_preference
            delete_user_preference('exclude_haiku_messages')
            console.print(f"[green]✓ Exclude Haiku Messages reset to default: {default_display}[/green]")
            console.print(f"[dim]  (Using value from src/config/defaults.py)[/dim]")
        elif new_value in ['yes', 'y', 'true', '1', 'enable', 'enabled']:
            save_func('exclude_haiku_messages', '1')
            console.print("[green]✓ Exclude Haiku Messages enabled[/green]")
            console.print("[dim]  Haiku messages will be excluded from all displays[/dim]")
        elif new_value in ['no', 'n', 'false', '0', 'disable', 'disabled']:
            save_func('exclude_haiku_messages', '0')
            console.print("[green]✓ Exclude Haiku Messages disabled[/green]")
            console.print("[dim]  Haiku messages will be included in displays[/dim]")
        else:
            console.print("[yellow]Invalid input. Please enter 'yes', 'no', or 'd'[/yellow]")

    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Input cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def _reset_to_defaults(console: Console, save_func) -> None:
    """
    Reset all settings to default values by deleting from database.

    This ensures defaults.py is the source of truth - by removing user
    customizations, defaults.py values automatically apply.

    Args:
        console: Rich console for rendering
        save_func: Function to save preferences (unused, kept for signature compatibility)
    """
    console.print()
    console.print("[bold yellow]Reset All Settings to Defaults[/bold yellow]")
    console.print()
    console.print("[dim]This will reset the following settings to their default values:[/dim]")
    console.print("[dim]  • Color settings (Solid, Unfilled)[/dim]")
    console.print("[dim]  • Model Pricing (loaded from src/config/defaults.py)[/dim]")
    console.print("[dim]  • Auto Refresh Interval (30 seconds)[/dim]")
    console.print("[dim]  • File Watch Interval (60 seconds)[/dim]")
    console.print("[dim]  • Auto Backup (Enabled)[/dim]")
    console.print("[dim]  • Keep Monthly Backups (Yes)[/dim]")
    console.print("[dim]  • Backup Retention (30 days)[/dim]")
    console.print("[dim]  • Display Timezone (Auto)[/dim]")
    console.print()
    console.print("[dim]After reset, default values from src/config/defaults.py will be used.[/dim]")
    console.print()
    console.print("[yellow]Are you sure? This will delete all your custom settings.[/yellow]")
    console.print("[dim]Type 'yes' to confirm or press Enter to cancel:[/dim]")

    try:
        sys.stdout.write("> ")
        sys.stdout.flush()
        confirmation = input().strip().lower()

        if confirmation == 'yes':
            # Delete all user preferences from database
            # This makes defaults.py values apply automatically
            from src.storage.snapshot_db import delete_user_preferences

            delete_user_preferences()

            # Reset backup settings (stored in separate config file)
            from src.config.user_config import (
                set_backup_enabled,
                set_backup_keep_monthly,
                set_backup_retention_days,
            )
            set_backup_enabled(True)
            set_backup_keep_monthly(True)
            set_backup_retention_days(30)

            # Reset model pricing to defaults
            from src.storage.snapshot_db import reset_pricing_to_defaults
            reset_pricing_to_defaults()

            console.print()
            console.print("[green]✓ All settings have been reset to defaults[/green]")
            console.print("[green]  Defaults from src/config/defaults.py will now be used[/green]")
        else:
            console.print()
            console.print("[yellow]Reset cancelled[/yellow]")

    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Reset cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def _edit_machine_name(console: Console) -> None:
    """
    Edit machine name setting.

    Args:
        console: Rich console for rendering
    """
    import socket
    from src.config.user_config import get_machine_name, set_machine_name, clear_machine_name

    console.print()
    console.print("[bold]Edit Machine Name[/bold]")
    console.print()

    current = get_machine_name()
    hostname = socket.gethostname()

    if current == hostname:
        console.print(f"[dim]Current: {current} (auto-detected)[/dim]")
    else:
        console.print(f"[dim]Current: {current} (custom)[/dim]")

    console.print(f"[dim]System hostname: {hostname}[/dim]")
    console.print()
    console.print("[dim]Enter a custom name, 'auto' to use hostname, or press Enter to keep current:[/dim]")
    console.print("[dim]Examples: Home-Desktop, Work-Laptop, Gaming-PC[/dim]")

    try:
        sys.stdout.write("> ")
        sys.stdout.flush()
        new_value = input().strip()

        if new_value:
            if new_value.lower() in ['auto', 'a', 'default', 'd']:
                clear_machine_name()
                console.print(f"[green]✓ Machine name set to auto: {hostname}[/green]")
            else:
                set_machine_name(new_value)
                console.print(f"[green]✓ Machine name set to: {new_value}[/green]")
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Input cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def _edit_database_path(console: Console) -> None:
    """
    Edit database path setting.

    Args:
        console: Rich console for rendering
    """
    from pathlib import Path
    from src.config.user_config import get_db_path, set_db_path, clear_db_path
    from src.storage.snapshot_db import DEFAULT_DB_PATH
    import platform
    import os

    console.print()
    console.print("[bold]Edit Database Path[/bold]")
    console.print()

    current = get_db_path()
    if current:
        console.print(f"[dim]Current: {current} (custom)[/dim]")
    else:
        console.print(f"[dim]Current: {DEFAULT_DB_PATH} (auto-detected)[/dim]")

    console.print()
    console.print("[dim]Choose database storage location:[/dim]")
    console.print()

    # Detect OneDrive/iCloud
    onedrive_path = None
    if platform.system() == "Linux" and "microsoft" in platform.release().lower():
        # WSL2 - check for OneDrive
        username = os.getenv("USER")
        for drive in ["c", "d", "e"]:
            candidate = Path(f"/mnt/{drive}/OneDrive")
            if candidate.exists():
                onedrive_path = candidate / ".claude-goblin" / "usage_history.db"
                break
        if not onedrive_path and username:
            candidate = Path(f"/mnt/c/Users/{username}/OneDrive")
            if candidate.exists():
                onedrive_path = candidate / ".claude-goblin" / "usage_history.db"
    elif platform.system() == "Darwin":
        # macOS - check for iCloud
        icloud_base = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if icloud_base.exists():
            onedrive_path = icloud_base / ".claude-goblin" / "usage_history.db"

    # Display options
    option_num = 1
    if onedrive_path:
        console.print(f"  [green][{option_num}][/green] OneDrive/iCloud Sync (multi-device)")
        console.print(f"      [dim]{onedrive_path}[/dim]")
        console.print()
        option_num += 1

    local_path = Path.home() / ".claude" / "usage" / "usage_history.db"
    console.print(f"  [yellow][{option_num}][/yellow] Local Storage (single device)")
    console.print(f"      [dim]{local_path}[/dim]")
    console.print()
    option_num += 1

    console.print(f"  [cyan][{option_num}][/cyan] Custom Path")
    console.print()

    console.print(f"  [dim][auto/a][/dim] Auto-detect (default)")
    console.print(f"  [dim][Enter][/dim] Keep current")
    console.print()

    try:
        sys.stdout.write("> ")
        sys.stdout.flush()
        choice = input().strip().lower()

        if not choice:
            # Keep current
            return

        if choice in ['auto', 'a', 'default', 'd']:
            # Auto-detect
            clear_db_path()
            console.print(f"[green]✓ Database path set to auto-detect[/green]")
            console.print(f"[dim]  Will use: {DEFAULT_DB_PATH}[/dim]")
        elif choice == '1' and onedrive_path:
            # OneDrive/iCloud
            set_db_path(str(onedrive_path))
            console.print(f"[green]✓ Database path set to OneDrive/iCloud[/green]")
            console.print(f"[green]  Multi-device sync enabled[/green]")
            console.print(f"[dim]  Path: {onedrive_path}[/dim]")
        elif choice == '2' if onedrive_path else choice == '1':
            # Local storage
            set_db_path(str(local_path))
            console.print(f"[green]✓ Database path set to local storage[/green]")
            console.print(f"[yellow]  Single device only (no cloud sync)[/yellow]")
            console.print(f"[dim]  Path: {local_path}[/dim]")
        elif choice == str(option_num - 1):
            # Custom path
            console.print()
            console.print("[dim]Enter full path to database file:[/dim]")
            console.print("[dim]Example: /mnt/d/MyFolder/.claude-goblin/usage_history.db[/dim]")
            sys.stdout.write("> ")
            sys.stdout.flush()
            custom_path = input().strip()

            if custom_path:
                try:
                    set_db_path(custom_path)
                    console.print(f"[green]✓ Database path set to custom location[/green]")
                    console.print(f"[dim]  Path: {custom_path}[/dim]")
                    console.print()
                    console.print("[yellow]⚠ Important: You must restart the program to use the new database path.[/yellow]")
                except ValueError as e:
                    console.print(f"[red]✗ Error: {e}[/red]")
        else:
            console.print("[red]Invalid option[/red]")

    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Input cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def handle_db_operation(console: Console, operation: str) -> None:
    """
    Handle database operations (Initialize, Delete, Restore, Backup).

    Args:
        console: Rich console for rendering
        operation: Operation type - "init", "delete", "restore", or "backup"
    """
    if operation == "init":
        console.print("[yellow]Initializing database...[/yellow]")
        # TODO: Call database initialization
        console.print("[green]Database initialized successfully.[/green]")
    elif operation == "delete":
        console.print("[red]Deleting database...[/red]")
        # TODO: Call database deletion with confirmation
        console.print("[green]Database deleted successfully.[/green]")
    elif operation == "restore":
        console.print("[yellow]Restoring from backup...[/yellow]")
        # TODO: Call database restore
        console.print("[green]Database restored successfully.[/green]")
    elif operation == "backup":
        console.print("[yellow]Creating backup...[/yellow]")
        # TODO: Call database backup
        console.print("[green]Backup created successfully.[/green]")


def _check_and_sync_data(console: Console) -> None:
    """
    Check data synchronization status and offer to sync if needed.

    Args:
        console: Rich console for rendering
    """
    from src.storage.snapshot_db import check_data_sync_status, save_snapshot
    from src.config.settings import get_claude_jsonl_files
    from src.data.jsonl_parser import parse_all_jsonl_files

    console.print("\n[bold]Checking Data Synchronization...[/bold]\n")

    # Show spinner while checking
    with console.status("[bold white]Analyzing source data and database...", spinner="dots", spinner_style="white"):
        sync_status = check_data_sync_status()

    # Display detailed status
    console.print(f"[cyan]Source Data:[/cyan]")
    console.print(f"  Records: {sync_status['source_count']:,}")
    if sync_status['source_latest']:
        console.print(f"  Latest: {sync_status['source_latest']}")
    console.print()

    console.print(f"[cyan]Database:[/cyan]")
    console.print(f"  Records: {sync_status['db_count']:,}")
    if sync_status['db_latest']:
        console.print(f"  Latest: {sync_status['db_latest']}")
    console.print()

    # Show sync status
    if sync_status['is_synced']:
        console.print(f"[green]✓ {sync_status['status_message']}[/green]")
    else:
        console.print(f"[yellow]⚠ {sync_status['status_message']}[/yellow]")

        # Offer to sync
        console.print("\n[dim]Would you like to sync now? ([#ff8800]y[/#ff8800]/n)[/dim]", end=" ")

        # Get user input
        key = _read_key()

        if key.lower() == 'y':
            console.print("\n\n[bold white]Synchronizing data...[/bold white]")

            try:
                with console.status("[bold white]Reading source files...", spinner="dots", spinner_style="white"):
                    jsonl_files = get_claude_jsonl_files()
                    if not jsonl_files:
                        console.print("[red]Error: No source files found[/red]")
                        return

                    records = parse_all_jsonl_files(jsonl_files)

                if not records:
                    console.print("[red]Error: No records to sync[/red]")
                    return

                with console.status(f"[bold white]Saving {len(records):,} records to database...", spinner="dots", spinner_style="white"):
                    save_snapshot(records)

                console.print(f"[green]✓ Successfully synced {len(records):,} records to database[/green]")

            except Exception as e:
                console.print(f"[red]Error during sync: {str(e)}[/red]")
        else:
            console.print()

    console.print("\n[dim]Press any key to return to settings...[/dim]", end="")
    _read_key()


def _edit_weekly_days_setting(console: Console, prefs: dict, save_func) -> None:
    """
    Edit weekly recommended days setting (17).

    Args:
        console: Rich console for rendering
        prefs: Current preferences dictionary
        save_func: Function to save preference
    """
    from src.config.defaults import DEFAULT_PREFERENCES

    current = prefs.get('weekly_recommended_days', DEFAULT_PREFERENCES['weekly_recommended_days'])
    default = DEFAULT_PREFERENCES['weekly_recommended_days']

    console.print()
    console.print(f"[bold]Edit Weekly Recommended Days[/bold]")
    console.print(f"[dim]Current value: {current} days[/dim]")
    console.print(f"[dim]Default value: {default} days[/dim]")
    console.print()
    console.print("[cyan]This setting controls how daily recommended usage is calculated:[/cyan]")
    console.print(f"  • Daily target = (100% / {current} days) × elapsed days")
    console.print(f"  • Example: On day 2, recommended usage = {(200 / int(current)):.1f}%")
    console.print()
    console.print("[dim]Enter days (1-7), 'd' for default, or press Enter to keep current:[/dim]")

    try:
        sys.stdout.write("> ")
        sys.stdout.flush()
        new_value = input().strip()

        if new_value:
            if new_value.lower() in ['d', 'default']:
                from src.storage.snapshot_db import delete_user_preference
                delete_user_preference('weekly_recommended_days')
                console.print(f"[green]✓ Weekly Recommended Days reset to default: {default} days[/green]")
                console.print(f"[dim]  (Using value from src/config/defaults.py)[/dim]")
            else:
                try:
                    days = int(new_value)
                    if 1 <= days <= 7:
                        save_func('weekly_recommended_days', str(days))
                        console.print(f"[green]✓ Weekly Recommended Days updated to {days} days[/green]")
                        console.print(f"[dim]  Daily target = {(100 / days):.1f}% per day[/dim]")
                    else:
                        console.print("[red]✗ Days must be between 1 and 7[/red]")
                except ValueError:
                    console.print("[red]✗ Invalid number. Enter 1-7 or 'd' for default[/red]")
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Input cancelled[/yellow]")

    console.print("\n[dim]Press any key to continue...[/dim]")
    _read_key()


def _detect_storage_mode(db_path: str) -> str:
    """
    Detect the storage/sync mode being used.

    Args:
        db_path: Path to the database file

    Returns:
        Formatted string indicating the storage mode with icon
    """
    from pathlib import Path

    # Check for Git Gist setup
    gist_configured = False
    try:
        from src.sync.token_manager import TokenManager
        token_manager = TokenManager()
        token = token_manager.get_token()
        gist_configured = token is not None
    except Exception:
        pass

    # Detect cloud storage from path
    is_onedrive = "OneDrive" in db_path
    is_icloud = "CloudDocs" in db_path or "iCloud" in db_path

    # Build storage mode string
    # Always show "Local" as the base (DB is always stored locally)
    modes = ["[yellow]Local[/yellow]"]

    # Add cloud sync if configured
    if is_onedrive:
        modes.append("[green]+ OneDrive[/green]")
    elif is_icloud:
        modes.append("[green]+ iCloud[/green]")

    # Add Gist backup if configured
    if gist_configured:
        modes.append("[cyan]+ Git Gist[/cyan]")

    return " ".join(modes)


# Cache for Gist status (to avoid slow API calls on every Settings refresh)
_gist_status_cache = {"data": None, "timestamp": 0}


def _get_gist_backup_info() -> dict:
    """
    Get Git Gist backup information for display in Settings.

    Returns:
        Dictionary with Gist status (may have "error" key if not configured/failed)
    """
    import time
    from typing import Any, Optional

    # Check cache (60 second TTL)
    cache_ttl = 60
    now = time.time()

    if _gist_status_cache["data"] and (now - _gist_status_cache["timestamp"]) < cache_ttl:
        return _gist_status_cache["data"]

    # Fetch fresh data
    try:
        from src.sync.token_manager import TokenManager
        from src.sync.sync_manager import SyncManager

        # Check if token configured
        token_manager = TokenManager()
        if not token_manager.has_token():
            result = {"error": "not_configured"}
            _gist_status_cache["data"] = result
            _gist_status_cache["timestamp"] = now
            return result

        # Get Gist status
        sync_manager = SyncManager()
        status = sync_manager.status()

        if "last_gist_sync" in status:
            result = {
                "last_sync": status["last_gist_sync"],
                "total_records": status.get("total_records_in_gist", 0),
                "gist_url": status.get("gist_url"),
                "total_machines": status.get("manifest", {}).get("total_machines", 0),
                "total_backups": status.get("manifest", {}).get("total_backups", 0),
            }
        else:
            result = {"error": "not_synced"}

        # Update cache
        _gist_status_cache["data"] = result
        _gist_status_cache["timestamp"] = now

        return result

    except Exception as e:
        # Silently fail - don't break Settings if Gist unavailable
        result = {"error": "failed", "message": str(e)}
        _gist_status_cache["data"] = result
        _gist_status_cache["timestamp"] = now
        return result
