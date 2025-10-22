#region Imports
import sys
import shutil
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

from src.config.user_config import APP_DATA_DIR, CONFIG_PATH
#endregion


#region Functions


def run(console: Console) -> None:
    """
    완전 재설정 - 모든 설정과 캐시를 삭제하고 setup wizard로 이동.

    삭제되는 항목:
    - ~/.claude/claude-goblin-mod/ (전체 폴더)
      - 설정 파일 (claude-goblin.json)
      - 캐시 파일들
      - Gist 토큰 (파일 저장 방식인 경우)
      - 기타 모든 앱 데이터

    보존되는 항목:
    - 데이터베이스 파일 (OneDrive/iCloud/Local 위치)
    - JSONL 원본 파일 (~/.claude/projects/)
    - 시스템 keyring에 저장된 Gist 토큰

    Args:
        console: Rich console for output

    Flags:
        --force: 확인 없이 즉시 실행
    """
    force = "--force" in sys.argv

    # 삭제할 폴더 확인
    app_dir = APP_DATA_DIR

    if not app_dir.exists():
        console.print("[yellow]설정 폴더가 존재하지 않습니다. 이미 초기 상태입니다.[/yellow]")
        console.print(f"[dim]폴더: {app_dir}[/dim]")
        return

    # 확인 프롬프트 (--force가 없으면)
    if not force:
        from src.config.user_config import get_db_path
        from src.storage.snapshot_db import get_default_db_path
        from src.sync.token_manager import TokenManager

        console.print("\n[bold yellow]⚠ 프로그램 완전 재설정[/bold yellow]\n")

        # 현재 스토리지 모드 감지
        custom_db_path = get_db_path()
        db_path = Path(custom_db_path) if custom_db_path else get_default_db_path()
        db_path_str = str(db_path)

        if "OneDrive" in db_path_str:
            storage_mode = "OneDrive Sync"
        elif "CloudDocs" in db_path_str or "iCloud" in db_path_str:
            storage_mode = "iCloud Sync"
        else:
            storage_mode = "Local"

        console.print("[red]다음 항목이 삭제됩니다:[/red]")

        # 앱 데이터 폴더
        console.print(f"  • 설정 폴더: [dim]{app_dir}[/dim]")
        config_files = list(app_dir.glob("*.json"))
        if config_files:
            for cf in config_files[:3]:
                console.print(f"    - {cf.name}")

        # Gist 토큰 파일
        gist_token_file = Path.home() / ".claude" / "gist_token.txt"
        if gist_token_file.exists():
            console.print(f"  • Gist 토큰 파일: [dim]{gist_token_file}[/dim]")

        console.print("  • 모든 캐시 및 임시 파일")

        console.print("\n[green]보존되는 항목:[/green]")

        # 데이터베이스 (스토리지 모드별)
        console.print(f"  • 데이터베이스 ([cyan]{storage_mode}[/cyan])")
        console.print(f"    [dim]{db_path}[/dim]")

        # Claude Code 원본 데이터
        claude_projects = Path.home() / ".claude" / "projects"
        console.print(f"  • Claude Code usage 원본 데이터")
        console.print(f"    [dim]{claude_projects}/*.jsonl[/dim]")

        # Git Gist 백업
        try:
            token_manager = TokenManager()
            if token_manager.get_token():
                console.print("  • GitHub Gist 클라우드 백업 (기존 Gist는 유지됨)")
        except:
            pass

        # 시스템 keyring 토큰
        try:
            token_manager = TokenManager()
            if TokenManager.is_keyring_available():
                token_location = token_manager.get_storage_location()
                if "keyring" in token_location.lower():
                    console.print(f"  • 시스템 keyring의 Gist 토큰")
                    console.print(f"    [dim]{token_location}[/dim]")
        except:
            pass

        console.print("\n[cyan]재설정 후:[/cyan]")
        console.print("  • Setup wizard가 자동으로 실행됩니다")
        console.print("  • 데이터베이스 위치를 다시 선택할 수 있습니다")
        console.print("  • 머신 이름을 다시 설정할 수 있습니다")
        if storage_mode in ["OneDrive Sync", "iCloud Sync"]:
            console.print(f"  • Setup wizard에서 [cyan]{storage_mode}[/cyan] 위치를 다시 선택할 수 있습니다")

        console.print()
        confirmed = Confirm.ask("[bold]계속하시겠습니까?[/bold]", default=False)

        if not confirmed:
            console.print("[yellow]재설정이 취소되었습니다.[/yellow]")
            return

    try:
        deleted_items = []

        # 1. APP_DATA_DIR 전체 삭제 (~/.claude/claude-goblin-mod/)
        if app_dir.exists():
            # 삭제 전 백업할 파일 목록 표시
            important_files = list(app_dir.glob("*.json")) + list(app_dir.glob("*.txt"))
            if important_files and not force:
                console.print("\n[dim]삭제될 파일:[/dim]")
                for f in important_files[:5]:  # 최대 5개만 표시
                    console.print(f"[dim]  - {f.name}[/dim]")
                if len(important_files) > 5:
                    console.print(f"[dim]  ... 외 {len(important_files) - 5}개[/dim]")
                console.print()

            shutil.rmtree(app_dir)
            deleted_items.append(str(app_dir))
            console.print(f"[green]✓ 삭제됨: {app_dir}[/green]")

        # 2. Gist 토큰 파일 삭제 (파일 방식인 경우)
        gist_token_file = Path.home() / ".claude" / "gist_token.txt"
        if gist_token_file.exists():
            gist_token_file.unlink()
            deleted_items.append(str(gist_token_file))
            console.print(f"[green]✓ 삭제됨: {gist_token_file.name}[/green]")

        console.print(f"\n[bold green]✓ 재설정 완료![/bold green]")
        console.print(f"[dim]총 {len(deleted_items)}개 항목 삭제됨[/dim]\n")

        # Setup wizard 실행 안내
        console.print("[cyan]다음 단계:[/cyan]")
        console.print("  프로그램을 다시 실행하면 Setup wizard가 자동으로 시작됩니다:")
        console.print("  [bold cyan]ccu[/bold cyan]")
        console.print()
        console.print("[dim]또는 특정 설정만 변경하려면:[/dim]")
        console.print("[dim]  ccu config set-db-path <경로>     # DB 경로만 변경[/dim]")
        console.print("[dim]  ccu config set-machine-name <이름> # 머신 이름만 변경[/dim]")

    except Exception as e:
        console.print(f"[red]✗ 재설정 중 오류 발생: {e}[/red]")
        import traceback
        if "--debug" in sys.argv:
            traceback.print_exc()


#endregion
