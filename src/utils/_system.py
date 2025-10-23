#region Imports
import platform
import subprocess
from pathlib import Path
from typing import Optional

from src.utils.security import validate_sound_name
#endregion


#region Functions


def open_file(file_path: Path) -> None:
    """
    Open a file with the default application (cross-platform).

    Args:
        file_path: Path to the file to open
    """
    system = platform.system()
    try:
        if system == "Darwin":  # macOS
            subprocess.run(["open", str(file_path)], check=False)
        elif system == "Windows":
            # Use cmd with explicit start command (no shell=True)
            # Empty string after /c is required for proper file path handling
            subprocess.run(["cmd", "/c", "start", "", str(file_path)], check=False)
        else:  # Linux and others
            subprocess.run(["xdg-open", str(file_path)], check=False)
    except Exception:
        pass  # Silently fail if opening doesn't work


def get_version() -> str:
    """
    Get version from pyproject.toml.

    Returns:
        Version string (e.g., "1.3.8") or "Unknown" if not found
    """
    try:
        import tomllib
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject_data = tomllib.load(f)
            return pyproject_data.get("project", {}).get("version", "Unknown")
    except Exception:
        return "Unknown"


def get_sound_command(sound_name: str) -> Optional[str]:
    """
    Get the command to play a sound (cross-platform).
    
    Security: Validates sound_name to prevent command injection.
    Only alphanumeric, hyphens, and underscores are allowed.
 
    Args:
        sound_name: Name of the sound file (without extension)
 
    Returns:
        Command string to play the sound, or None if not supported or invalid
    """
    # SECURITY: Validate sound name to prevent command injection
    if not validate_sound_name(sound_name):
        return None
    
    system = platform.system()
 
    if system == "Darwin":  # macOS
        # Safe: sound_name is validated to contain only safe characters
        return f"afplay /System/Library/Sounds/{sound_name}.aiff &"
    elif system == "Windows":
        # Safe: sound_name is validated to contain only safe characters
        return f'powershell -c "(New-Object Media.SoundPlayer \'C:\\Windows\\Media\\{sound_name}.wav\').PlaySync();" &'
    else:  # Linux
        # Safe: sound_name is validated to contain only safe characters
        # Try to use paplay (PulseAudio) or aplay (ALSA)
        return f"(paplay /usr/share/sounds/freedesktop/stereo/{sound_name}.oga 2>/dev/null || aplay /usr/share/sounds/alsa/{sound_name}.wav 2>/dev/null) &"


#endregion
