"""
Claude Goblin CLI - Command-line interface using typer.

Main entry point for all claude-goblin commands.
"""
from typing import Optional
import typer
from rich.console import Console

from src.commands import (
    usage,
    heatmap,
    reset_db,
    reset,
    config_cmd,
    settings,
    gist_cmd,
)


# Create typer app
app = typer.Typer(
    name="claude-goblin",
    help="Python CLI for Claude Code utilities and usage tracking/analytics",
    add_completion=False,
    no_args_is_help=False,  # Changed to False to allow callback
)

# Create console for commands
console = Console()


@app.callback(invoke_without_command=True)
def default_callback(
    ctx: typer.Context,
    anon: bool = typer.Option(False, "--anon", help="Anonymize project names to project-001, project-002, etc"),
    refresh: Optional[int] = typer.Option(None, "--refresh", help="Refresh interval in seconds (e.g., --refresh=30). Watches file changes if not specified.", hidden=True),
    watch_interval: int = typer.Option(60, "--watch-interval", help="File watch check interval in seconds (default: 60)", hidden=True),
    limits_interval: int = typer.Option(60, "--limits-interval", help="Usage limits update interval in seconds (default: 60)", hidden=True),
):
    """
    Python CLI for Claude Code utilities and usage tracking/analytics.

    Run without command to show interactive usage dashboard.

    Options:
      --anon     Anonymize project names (for screenshots)
      --help     Show this help message

    Note: Refresh intervals and display settings are configured via Settings menu (press 's' in dashboard).
    """
    if ctx.invoked_subcommand is None:
        # No command provided, run usage command with options
        usage.run(console, refresh=refresh, anon=anon, watch_interval=watch_interval, limits_interval=limits_interval)


@app.command(name="usage", hidden=True)
def usage_command(
    refresh: Optional[int] = typer.Option(None, "--refresh", help="Refresh interval in seconds (e.g., --refresh=30). Watches file changes if not specified."),
    anon: bool = typer.Option(False, "--anon", help="Anonymize project names to project-001, project-002, etc"),
    watch_interval: int = typer.Option(60, "--watch-interval", help="File watch check interval in seconds (default: 60)"),
    limits_interval: int = typer.Option(60, "--limits-interval", help="Usage limits update interval in seconds (default: 60)"),
):
    """Show interactive usage dashboard with file watching and keyboard shortcuts (hidden, use 'ccu' instead)."""
    usage.run(console, refresh=refresh, anon=anon, watch_interval=watch_interval, limits_interval=limits_interval)


@app.command(name="heatmap")
def heatmap_command(
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Year to display (default: current year)"),
    fast: bool = typer.Option(False, "--fast", help="Skip updates, read from database only (faster)"),
):
    """Show GitHub-style activity heatmap in the terminal."""
    heatmap.run(console, year=year, fast=fast)


@app.command(name="reset")
def reset_command(
    force: bool = typer.Option(False, "--force", help="확인 없이 즉시 실행"),
):
    """프로그램 완전 재설정 (설정 파일 삭제 후 setup wizard 재실행)."""
    import sys
    if force and "--force" not in sys.argv:
        sys.argv.append("--force")
    reset.run(console)


@app.command(name="reset-db")
def reset_db_command(
    force: bool = typer.Option(False, "--force", help="Force reset without confirmation"),
    keep_backups: bool = typer.Option(False, "--keep-backups", help="Keep backup files"),
):
    """Reset database (delete and start fresh)."""
    import sys
    if force and "--force" not in sys.argv:
        sys.argv.append("--force")
    if keep_backups and "--keep-backups" not in sys.argv:
        sys.argv.append("--keep-backups")
    reset_db.run(console)


@app.command(name="init-db")
def init_db_command(
    force: bool = typer.Option(False, "--force", help="Force initialization without confirmation"),
    keep_backups: bool = typer.Option(False, "--keep-backups", help="Keep backup files"),
):
    """Initialize/reset database (alias for reset-db)."""
    import sys
    if force and "--force" not in sys.argv:
        sys.argv.append("--force")
    if keep_backups and "--keep-backups" not in sys.argv:
        sys.argv.append("--keep-backups")
    reset_db.run(console)


@app.command(name="config")
def config_command(
    action: str = typer.Argument(..., help="Action: show, set-db-path, clear-db-path, set-machine-name, clear-machine-name"),
    value: Optional[str] = typer.Argument(None, help="Value for set actions"),
):
    """Manage configuration (database path, machine name, etc)."""
    config_cmd.run(console, action, value)


# Add gist subcommand
app.add_typer(gist_cmd.app, name="gist", help="GitHub Gist synchronization")


@app.command(name="settings", hidden=True)
def settings_command():
    """Show settings menu (hidden)."""
    settings.run(console)


def main() -> None:
    """
    Main CLI entry point for Claude Goblin Usage tracker.

    Loads Claude Code usage data and provides interactive dashboard
    for viewing usage statistics across multiple time periods.

    Usage:
        ccu                     Show interactive usage dashboard
        ccu --anon              Anonymize project names (for screenshots)
        ccu --help              Show help message

    Note: All settings (refresh intervals, colors, etc.) are configured
          inside the program via Settings menu (press 's' in dashboard).

    Exit:
        Press Ctrl+C, [q], or [Esc] to exit
    """
    try:
        # Check if first-time setup is needed
        try:
            from src.commands.setup_wizard import should_run_setup_wizard, run_setup_wizard, mark_setup_completed

            if should_run_setup_wizard():
                setup_console = Console()
                if run_setup_wizard(setup_console):
                    mark_setup_completed()
                else:
                    # User cancelled setup, use defaults
                    setup_console.print("\n[yellow]Setup cancelled. Using default settings.[/yellow]")
                    setup_console.print("[dim]You can run 'ccu config show' to view settings anytime.[/dim]\n")
                    mark_setup_completed()
        except KeyboardInterrupt:
            # Ctrl+C during setup - exit immediately
            import sys
            sys.exit(0)
        except Exception as e:
            # If setup wizard fails, continue with defaults
            console = Console()
            console.print(f"[yellow]Setup wizard error (using defaults): {e}[/yellow]")

        # Auto-backup on program start (silent, won't block execution)
        try:
            from src.utils.backup import auto_backup
            auto_backup()
        except Exception:
            pass  # Silently ignore backup errors

        app()
    except KeyboardInterrupt:
        # Ctrl+C at any point - exit immediately without message
        import sys
        sys.exit(0)


if __name__ == "__main__":
    main()
