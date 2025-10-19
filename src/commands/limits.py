#region Imports
import subprocess
import re
import os
import pty
import select
import time
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
        Dictionary with keys: session_pct, week_pct, opus_pct,
        session_reset, week_reset, opus_reset, or None if capture failed
    """
    try:
        # Create a pseudo-terminal pair
        master, slave = pty.openpty()

        # Start claude /usage with the PTY
        # Run from home directory to avoid trust prompts in project folders
        process = subprocess.Popen(
            ['claude', '/usage'],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            cwd=os.path.expanduser('~')
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
                        if b'Current week (Opus)' in output and b'Esc to exit' in output:
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
        session_match = re.search(r'Current session.*?(\d+)%\s+used.*?Resets\s+(.+?)(?:\r?\n|$)', clean_output, re.DOTALL)
        week_match = re.search(r'Current week \(all models\).*?(\d+)%\s+used.*?Resets\s+(.+?)(?:\r?\n|$)', clean_output, re.DOTALL)
        opus_match = re.search(r'Current week \(Opus\).*?(\d+)%\s+used', clean_output, re.DOTALL)

        # For Opus reset time: try to find "Resets" info, fallback to week reset if 0%
        opus_reset_match = re.search(r'Current week \(Opus\).*?(\d+)%\s+used.*?Resets\s+(.+?)(?:\r?\n|$)', clean_output, re.DOTALL)

        # Debug: Write match results
        with open(debug_file.name, 'a') as f:
            f.write(f"\n\n=== Pattern matching results ===\n")
            f.write(f"session_match: {session_match.groups() if session_match else None}\n")
            f.write(f"week_match: {week_match.groups() if week_match else None}\n")
            f.write(f"opus_match: {opus_match.groups() if opus_match else None}\n")

        # If parsing failed, return error with debug file path
        if not (session_match and week_match and opus_match):
            return {
                "error": "parse_failed",
                "message": f"Failed to parse claude /usage output. Debug file: {debug_file.name}",
                "debug_file": debug_file.name
            }

        if session_match and week_match and opus_match:
            # Clean reset strings (remove \r and extra whitespace)
            session_reset = session_match.group(2).strip().replace('\r', '')
            week_reset = week_match.group(2).strip().replace('\r', '')

            # If Opus has reset info, use it; otherwise use week reset (when Opus is 0%)
            if opus_reset_match:
                opus_reset = opus_reset_match.group(2).strip().replace('\r', '')
            else:
                # Opus is 0%, use week reset time
                opus_reset = week_reset

            return {
                "session_pct": int(session_match.group(1)),
                "week_pct": int(week_match.group(1)),
                "opus_pct": int(opus_match.group(1)),
                "session_reset": session_reset,
                "week_reset": week_reset,
                "opus_reset": opus_reset,
            }

        return None

    except Exception:
        return None


#endregion
