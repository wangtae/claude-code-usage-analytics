"""
Default configuration values for CCU (Claude Code Usage).

Edit this file to change default settings.
This file is used for:
1. Initial setup (first-time database initialization)
2. Reset to defaults (when user presses 'r' in Settings)

All prices are per million tokens (MTok) in USD.
"""

#region Model Pricing Defaults
# Prices per million tokens (USD)
# Source: https://docs.claude.com/en/docs/about-claude/pricing (2025-01-15)

DEFAULT_MODEL_PRICING = {
    # Sonnet 4.5 - Current balanced model (≤200K tokens)
    "claude-sonnet-4-5-20250929": {
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "display_name": "Sonnet 4.5",
        "notes": "Current balanced model (≤200K tokens)",
    },
    # Sonnet 4 - Legacy balanced model
    "claude-sonnet-4-20250514": {
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "display_name": "Sonnet 4",
        "notes": "Legacy Sonnet 4",
    },
    # Sonnet 3.7 - Legacy model
    "claude-sonnet-3-7-20250219": {
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "display_name": "Sonnet 3.7",
        "notes": "Legacy Sonnet 3.7",
    },
    # Opus 4.1 - Current flagship model
    "claude-opus-4-1-20250805": {
        "input_price": 15.0,
        "output_price": 75.0,
        "cache_write_price": 18.75,
        "cache_read_price": 1.50,
        "display_name": "Opus 4.1",
        "notes": "Current flagship model",
    },
    # Opus 4 - Legacy flagship model
    "claude-opus-4-20250514": {
        "input_price": 15.0,
        "output_price": 75.0,
        "cache_write_price": 18.75,
        "cache_read_price": 1.50,
        "display_name": "Opus 4",
        "notes": "Legacy Opus 4",
    },
    # Haiku 3.5 - Current fast model
    "claude-haiku-3-5-20241022": {
        "input_price": 0.80,
        "output_price": 4.0,
        "cache_write_price": 1.0,
        "cache_read_price": 0.08,
        "display_name": "Haiku 3.5",
        "notes": "Current fast model",
    },
    # Synthetic/test model
    "<synthetic>": {
        "input_price": 0.0,
        "output_price": 0.0,
        "cache_write_price": 0.0,
        "cache_read_price": 0.0,
        "display_name": "Synthetic",
        "notes": "Test/synthetic model - no cost",
    },
}

# Model groups for Settings display (only show these in Settings UI)
SETTINGS_MODEL_GROUPS = [
    {
        "key": "sonnet-4.5",
        "display_name": "Sonnet 4.5",
        "model_ids": ["claude-sonnet-4-5-20250929", "claude-sonnet-4-20250514", "claude-sonnet-3-7-20250219"],
    },
    {
        "key": "opus-4",
        "display_name": "Opus 4",
        "model_ids": ["claude-opus-4-1-20250805", "claude-opus-4-20250514"],
    },
]

#endregion


#region Color Defaults

DEFAULT_COLORS = {
    # Solid mode colors (single color display)
    "color_solid": "#B1B9f9",          # Bright blue for progress bars
    "color_unfilled": "#505370",       # Grey for unfilled portion

    # Recommended usage indicator colors
    "color_recommended": "grey46",     # Recommended usage range color (dimmed)
    "color_exceeded": "#ff4444",       # Exceeded warning color (red)

    # Legacy gradient colors (kept for backward compatibility, not used in UI)
    "color_gradient_low": "#B1B9f9",   # Blue for 0-60% (legacy)
    "color_gradient_mid": "#FFC10C",   # Yellow for 60-85% (legacy)
    "color_gradient_high": "#FF1744",  # Red for 85-100% (legacy)
    "color_range_low": "60",           # Low range threshold (legacy)
    "color_range_high": "85",          # High range threshold (legacy)
}

#endregion


#region Interval Defaults

DEFAULT_INTERVALS = {
    "refresh_interval": "30",   # Auto refresh interval in seconds
    "watch_interval": "60",     # File watch interval in seconds
}

#endregion


#region Other Defaults

DEFAULT_PREFERENCES = {
    "usage_display_mode": "0",      # M1=0, M2=1, M3=2, M4=3
    "color_mode": "solid",          # solid mode only (gradient removed)
    "tracking_mode": "both",        # both | usage | limits
    "machine_name": "",             # custom name or empty (auto-detect)
    "db_path": "",                  # custom path or empty (auto-detect)
    "anonymize_projects": "0",      # 0=off, 1=on
    "timezone": "auto",             # auto | UTC | Asia/Seoul | ...
    "exclude_haiku_messages": "1",  # 1=exclude Haiku from message output, 0=include
    "weekly_recommended_days": "7", # Days for weekly recommended usage calculation (1-7)
    "gist_auto_sync": "1",          # 0=disabled, 1=enabled (auto-sync to GitHub Gist)
    "gist_sync_interval": "600",    # Sync interval in seconds (default: 10 minutes)
    "gist_sync_mode": "bidirectional",  # bidirectional | pull_only | push_only
}

#endregion


def get_all_defaults() -> dict:
    """
    Get all default settings merged into a single dictionary.

    Returns:
        Dictionary with all default settings combined
    """
    defaults = {}
    defaults.update(DEFAULT_COLORS)
    defaults.update(DEFAULT_INTERVALS)
    defaults.update(DEFAULT_PREFERENCES)
    return defaults
