#region Imports
import subprocess
import re
import os
import pty
import select
import time
from src.config.reset_times import update_reset_time, format_reset_for_display
from src.utils.timezone import get_user_timezone
#endregion


#region Functions


def _strip_ansi(text: str) -> str:
    """
    Remove ANSI escape codes from text.

    Args:
        text: Text with ANSI codes

    Returns:
        Clean text without ANSI codes
    """
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def capture_limits() -> dict | None:
    """
    Capture usage limits from `claude /usage` without displaying output.

    Returns:
        Dictionary with keys: session_pct, week_pct, sonnet_pct,
        session_reset, week_reset, sonnet_reset, or None if capture failed
    """
    try:
        # Create a pseudo-terminal pair
        master, slave = pty.openpty()

        # Start claude /usage with the PTY
        # Run from current working directory (should be a trusted project folder)
        # Note: If ccu is run from untrusted folder, this will fail and return untrusted_folder error
        process = subprocess.Popen(
            ['claude', '/usage'],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            cwd=os.getcwd()
        )

        # Close slave in parent process (child keeps it open)
        os.close(slave)

        # Read output until we see complete data
        output = b''
        start_time = time.time()
        max_wait = 20  # Increased from 10 to 20 seconds for SDK version
        trust_prompt_handled = False
        loading_detected = False

        while time.time() - start_time < max_wait:
            # Check if data is available to read
            ready, _, _ = select.select([master], [], [], 0.1)

            if ready:
                try:
                    chunk = os.read(master, 4096)
                    if chunk:
                        output += chunk

                        # Check if we hit trust prompt and auto-accept
                        # Support both old and new Claude Code SDK formats
                        if not trust_prompt_handled:
                            # New SDK format: numbered menu (1. Yes, continue / 2. No, exit)
                            if b'Yes, continue' in output or b'Ready to code here?' in output:
                                time.sleep(0.3)
                                try:
                                    # Press Enter to select default option (1. Yes, continue)
                                    os.write(master, b'\r')
                                    trust_prompt_handled = True
                                except:
                                    pass
                                continue
                            # Old format: "Do you trust the files in this folder? (y/n)"
                            elif b'Do you trust' in output:
                                time.sleep(0.3)
                                try:
                                    # Send 'y' for yes
                                    os.write(master, b'y\r')
                                    trust_prompt_handled = True
                                except:
                                    pass
                                continue

                        # Detect loading state - need to wait longer
                        if not loading_detected and b'Loading usage data' in output:
                            loading_detected = True
                            # Reset timer to allow more time for data to load
                            start_time = time.time()
                            continue

                        # Check if we have complete data
                        # Look for the usage screen's exit message, not the loading screen's "esc to interrupt"
                        # Support both "Current week (Sonnet)" and "Current week (Sonnet only)"
                        if b'Current week (Sonnet' in output and b'Esc to exit' in output:
                            # Wait a tiny bit more to ensure all data is flushed
                            time.sleep(0.2)
                            # Try to read any remaining data
                            try:
                                while True:
                                    ready, _, _ = select.select([master], [], [], 0.05)
                                    if not ready:
                                        break
                                    chunk = os.read(master, 4096)
                                    if chunk:
                                        output += chunk
                            except:
                                pass
                            break
                except OSError:
                    break

        # Send ESC to exit cleanly
        try:
            os.write(master, b'\x1b')
            time.sleep(0.1)
        except:
            pass

        # Clean up
        try:
            process.terminate()
            process.wait(timeout=1)
        except:
            process.kill()

        os.close(master)

        # Decode output
        output_str = output.decode('utf-8', errors='replace')

        # Strip ANSI codes
        clean_output = _strip_ansi(output_str)

        # Debug: Save output to temp file for inspection
        import tempfile
        debug_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='_claude_usage_debug.txt')
        debug_file.write(f"=== Raw output ({len(output_str)} bytes) ===\n")
        debug_file.write(output_str)
        debug_file.write(f"\n\n=== Clean output ({len(clean_output)} bytes) ===\n")
        debug_file.write(clean_output)
        debug_file.close()

        # Parse for percentages and reset times
        # Support both "used" and "left" formats (Claude Code v2.0.50+ uses "left")
        session_match = re.search(r'Current session.*?(\d+)%\s+(used|left).*?Resets\s+(.+?)(?:\r?\n|$)', clean_output, re.DOTALL)
        week_match = re.search(r'Current week \(all models\).*?(\d+)%\s+(used|left).*?Resets\s+(.+?)(?:\r?\n|$)', clean_output, re.DOTALL)
        # Support both "Current week (Sonnet)" and "Current week (Sonnet only)"
        sonnet_match = re.search(r'Current week \(Sonnet(?: only)?\).*?(\d+)%\s+(used|left)', clean_output, re.DOTALL)

        # For Sonnet reset time: try to find "Resets" info, fallback to week reset if 0%
        sonnet_reset_match = re.search(r'Current week \(Sonnet(?: only)?\).*?(\d+)%\s+(used|left).*?Resets\s+(.+?)(?:\r?\n|$)', clean_output, re.DOTALL)

        # Debug: Write match results
        with open(debug_file.name, 'a') as f:
            f.write(f"\n\n=== Pattern matching results ===\n")
            f.write(f"session_match: {session_match.groups() if session_match else None}\n")
            f.write(f"week_match: {week_match.groups() if week_match else None}\n")
            f.write(f"sonnet_match: {sonnet_match.groups() if sonnet_match else None}\n")

        # Check if Claude Code returned an error
        if 'Error: Failed to load usage data' in clean_output:
            # Distinguish between untrusted folder and Claude server issues
            # Untrusted folder shows "Do you want to work in this folder?" prompt
            # Server issues show error directly without prompt
            if 'Do you trust' in clean_output or 'Do you want to work in this folder' in clean_output:
                return {
                    "error": "untrusted_folder",
                    "message": "Claude Code cannot load usage data in untrusted folder",
                    "debug_file": debug_file.name
                }
            else:
                return {
                    "error": "claude_server_error",
                    "message": "Claude Code failed to load usage data (server/network issue)",
                    "debug_file": debug_file.name
                }

        # If parsing failed, return error with debug file path
        if not (session_match and week_match and sonnet_match):
            return {
                "error": "parse_failed",
                "message": f"Failed to parse claude /usage output. Debug file: {debug_file.name}",
                "debug_file": debug_file.name
            }

        if session_match and week_match and sonnet_match:
            # Clean reset strings (remove \r and extra whitespace)
            # Note: group(3) is reset time (group(2) is now "used" or "left")
            session_reset = session_match.group(3).strip().replace('\r', '')
            week_reset = week_match.group(3).strip().replace('\r', '')

            # If Sonnet has reset info, use it; otherwise use week reset (when Sonnet is 0%)
            if sonnet_reset_match:
                sonnet_reset = sonnet_reset_match.group(3).strip().replace('\r', '')
            else:
                # Sonnet is 0%, use week reset time
                sonnet_reset = week_reset

            # Store parsed reset times for future use
            try:
                current_tz = get_user_timezone()
                update_reset_time("session_reset", session_reset, current_tz)
                update_reset_time("week_reset", week_reset, current_tz)
                update_reset_time("sonnet_reset", sonnet_reset, current_tz)
            except Exception:
                # Don't fail if storage fails - just use the parsed values
                pass

            # Use stored reset times for display (fall back to parsed if not available)
            try:
                session_reset_display = format_reset_for_display("session_reset")
                week_reset_display = format_reset_for_display("week_reset")
                sonnet_reset_display = format_reset_for_display("sonnet_reset")

                # Only use stored values if they're valid (not "Not available")
                if session_reset_display != "Not available":
                    session_reset = session_reset_display
                if week_reset_display != "Not available":
                    week_reset = week_reset_display
                if sonnet_reset_display != "Not available":
                    sonnet_reset = sonnet_reset_display
            except Exception:
                # Fall back to parsed values
                pass

            # Convert percentages to "used" basis
            # If Claude shows "X% left", convert to "used" (100 - X)
            # If Claude shows "X% used", use as-is
            session_pct = int(session_match.group(1))
            if session_match.group(2) == "left":
                session_pct = 100 - session_pct

            week_pct = int(week_match.group(1))
            if week_match.group(2) == "left":
                week_pct = 100 - week_pct

            sonnet_pct = int(sonnet_match.group(1))
            if sonnet_match.group(2) == "left":
                sonnet_pct = 100 - sonnet_pct

            return {
                "session_pct": session_pct,
                "week_pct": week_pct,
                "sonnet_pct": sonnet_pct,
                "session_reset": session_reset,
                "week_reset": week_reset,
                "sonnet_reset": sonnet_reset,
            }

        return None

    except Exception:
        return None


#endregion
