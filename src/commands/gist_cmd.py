"""
CLI commands for GitHub Gist synchronization.
"""

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.sync.gist_client import GistClient
from src.sync.sync_manager import SyncManager
from src.sync.token_manager import TokenManager


app = typer.Typer(help="GitHub Gist synchronization commands")
console = Console()


@app.command(name="setup")
def setup_wizard():
    """
    Interactive setup wizard for Gist synchronization.
    """
    console.print("\n[bold cyan]GitHub Gist Sync Setup[/bold cyan]\n")

    # Check if keyring is available
    token_manager = TokenManager()
    if not TokenManager.is_keyring_available():
        console.print(
            "[yellow]⚠ Keyring library not installed[/yellow]\n"
            "For better security, install: [cyan]pip install keyring[/cyan]\n"
        )

    # Get GitHub token
    console.print("[bold]Step 1: GitHub Personal Access Token[/bold]")
    console.print("Create a token at: https://github.com/settings/tokens")
    console.print("Required scope: [cyan]gist[/cyan]\n")

    token = typer.prompt("Enter your GitHub token", hide_input=True)

    # Validate token
    console.print("\nValidating token...", end="")
    try:
        client = GistClient(token)
        if client.test_token():
            console.print(" [green]✓ Valid[/green]")
        else:
            console.print(" [red]✗ Invalid[/red]")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f" [red]✗ Error: {e}[/red]")
        raise typer.Exit(1)

    # Save token
    token_manager.set_token(token)
    console.print(f"\n[green]✓[/green] Token saved: {token_manager.get_storage_location()}")

    # Test sync
    console.print("\n[bold]Step 2: Test Synchronization[/bold]")
    if typer.confirm("Create test Gist and sync now?", default=True):
        try:
            sync_manager = SyncManager()
            stats = sync_manager.push()

            console.print("\n[green]✓ Sync successful![/green]")
            console.print(f"  Gist ID: {stats['gist_id']}")
            console.print(f"  Records: {stats['exported_records']}")

            # Show Gist URL
            status = sync_manager.status()
            if status.get("gist_url"):
                console.print(f"  URL: {status['gist_url']}")

        except Exception as e:
            console.print(f"\n[red]✗ Sync failed: {e}[/red]")
            raise typer.Exit(1)

    console.print("\n[green]✓ Setup complete![/green]")
    console.print("\nAvailable commands:")
    console.print("  [cyan]ccu gist push[/cyan]   - Upload local data to Gist")
    console.print("  [cyan]ccu gist pull[/cyan]   - Download Gist data to local")
    console.print("  [cyan]ccu gist status[/cyan] - Show sync status\n")


@app.command()
def set_token(token: str):
    """
    Set GitHub Personal Access Token.
    """
    token_manager = TokenManager()

    # Validate token
    console.print("Validating token...", end="")
    try:
        client = GistClient(token)
        if not client.test_token():
            console.print(" [red]✗ Invalid token[/red]")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f" [red]✗ Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(" [green]✓ Valid[/green]")

    # Save token
    token_manager.set_token(token)
    console.print(f"[green]✓[/green] Token saved: {token_manager.get_storage_location()}")


@app.command()
def push(
    force: bool = typer.Option(False, "--force", "-f", help="Force push, skip conflict detection (may overwrite changes)"),
    export_all: bool = typer.Option(False, "--export-all", help="Export all data (not incremental)"),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip backup creation"),
):
    """
    Push local data to GitHub Gist (incremental).

    Automatically detects and resolves conflicts when multiple devices push simultaneously.
    Use --force to skip conflict detection and force overwrite.
    """
    try:
        from src.sync.exceptions import ConflictError

        sync_manager = SyncManager()
        console.print("Pushing to Gist...", end="")

        # If --force, skip conflict check. Otherwise, use auto-merge.
        stats = sync_manager.push(
            force=export_all,
            create_backup=not no_backup,
            skip_conflict_check=force
        )

        if stats.get("status") == "nothing_to_sync":
            console.print(" [yellow]Nothing to sync[/yellow]")
            return

        console.print(" [green]✓ Done[/green]")

        # Show conflict resolution message if applicable
        if stats.get("conflicts_resolved"):
            console.print("[green]✓ Conflicts auto-resolved via merge[/green]")

        console.print()

        # Show statistics
        table = Table(show_header=False, box=None)
        table.add_row("Records exported:", f"{stats['exported_records']:,}")
        table.add_row("Backup created:", "Yes" if stats.get("backup_created") else "No")
        table.add_row("Gist ID:", stats.get("gist_id", "N/A"))

        if stats.get("backups_deleted"):
            table.add_row("Old backups deleted:", str(stats["backups_deleted"]))

        console.print(table)

        # Show Gist URL
        status = sync_manager.status()
        if status.get("gist_url"):
            console.print(f"\n[dim]View at: {status['gist_url']}[/dim]")

    except ConflictError as e:
        console.print(f" [red]✗ Conflict Error[/red]\n")
        console.print(f"[yellow]{e}[/yellow]\n")
        console.print("[dim]Suggestions:[/dim]")
        console.print("  1. Run [cyan]ccu gist pull[/cyan] to sync latest data, then push again")
        console.print("  2. Or use [cyan]ccu gist push --force[/cyan] to override (may lose data)")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" [red]✗ Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def pull(
    machines: Optional[list[str]] = typer.Argument(None, help="Machine names to pull (all if omitted)"),
):
    """
    Pull data from GitHub Gist to local database.
    """
    try:
        sync_manager = SyncManager()
        console.print("Pulling from Gist...", end="")

        stats = sync_manager.pull(machines=machines)

        console.print(" [green]✓ Done[/green]\n")

        # Show statistics
        table = Table(show_header=False, box=None)
        table.add_row("Machines pulled:", str(stats["machines_pulled"]))
        table.add_row("New records:", f"{stats['new_records']:,}")
        table.add_row("Duplicates skipped:", f"{stats['duplicate_records']:,}")

        if stats.get("errors"):
            table.add_row("Errors:", f"[red]{stats['errors']}[/red]")

        console.print(table)

    except Exception as e:
        console.print(f" [red]✗ Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def status():
    """
    Show synchronization status.
    """
    try:
        sync_manager = SyncManager()
        status_data = sync_manager.status()

        # Token status
        token_panel = Panel(
            f"[{'green' if status_data['token_configured'] else 'red'}]"
            f"{'✓ Configured' if status_data['token_configured'] else '✗ Not configured'}[/]\n"
            f"Location: {status_data['token_location']}",
            title="GitHub Token",
            border_style="green" if status_data['token_configured'] else "red",
        )
        console.print(token_panel)

        if not status_data['token_configured']:
            console.print("\n[yellow]Run 'ccu gist setup' to configure[/yellow]")
            return

        # Gist status
        if status_data.get("error"):
            console.print(f"\n[red]Error: {status_data['error']}[/red]")
            return

        # Machine info
        table = Table(title="Local Machine", show_header=False, box=None)
        table.add_row("Name:", status_data["machine_name"])
        table.add_row("Last export:", status_data.get("last_local_export") or "Never")
        console.print("\n", table)

        # Gist info
        if status_data.get("gist_id"):
            gist_table = Table(title="GitHub Gist", show_header=False, box=None)
            gist_table.add_row("ID:", status_data["gist_id"])
            gist_table.add_row("URL:", status_data.get("gist_url", "N/A"))

            if status_data.get("last_gist_sync"):
                gist_table.add_row("Last sync:", status_data["last_gist_sync"])
            if status_data.get("total_records_in_gist") is not None:
                gist_table.add_row("Records in Gist:", f"{status_data['total_records_in_gist']:,}")

            console.print("\n", gist_table)

            # Manifest statistics
            if status_data.get("manifest"):
                manifest = status_data["manifest"]
                manifest_table = Table(title="All Machines", show_header=False, box=None)
                manifest_table.add_row("Total machines:", str(manifest["total_machines"]))
                manifest_table.add_row("Total records:", f"{manifest['total_records']:,}")
                manifest_table.add_row("Total backups:", str(manifest["total_backups"]))
                manifest_table.add_row("Retention days:", str(manifest["retention_days"]))

                console.print("\n", manifest_table)
        else:
            console.print("\n[yellow]No Gist found. Run 'ccu gist push' to create one.[/yellow]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def info():
    """
    Show detailed Gist information.
    """
    try:
        sync_manager = SyncManager()

        if sync_manager.gist_id is None:
            # Try to find Gist
            gist = sync_manager.client.find_gist_by_description(sync_manager.GIST_DESCRIPTION)
            if gist:
                sync_manager.gist_id = gist["id"]
            else:
                console.print("[yellow]No Gist found[/yellow]")
                return

        # Get Gist details
        gist = sync_manager.client.get_gist(sync_manager.gist_id)

        console.print(f"\n[bold cyan]{gist['description']}[/bold cyan]")
        console.print(f"URL: {gist['html_url']}")
        console.print(f"Created: {gist['created_at']}")
        console.print(f"Updated: {gist['updated_at']}")
        console.print(f"Public: {'Yes' if gist['public'] else 'No'}")

        # List files
        console.print("\n[bold]Files:[/bold]")
        files_table = Table()
        files_table.add_column("Filename")
        files_table.add_column("Size", justify="right")
        files_table.add_column("Type")

        for filename, file_data in gist["files"].items():
            size = file_data.get("size", 0)
            file_type = file_data.get("type", "unknown")
            files_table.add_row(filename, f"{size:,} bytes", file_type)

        console.print(files_table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
