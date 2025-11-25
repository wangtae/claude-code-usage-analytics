#region Imports
from dataclasses import dataclass
from typing import Optional, Dict
import sqlite3
from pathlib import Path
#endregion


#region Pricing Cache
# Load pricing from database at module import time
# This avoids repeated DB connections during runtime
_PRICING_CACHE: Dict[str, Dict[str, float]] = {}

def _load_pricing_from_db() -> Dict[str, Dict[str, float]]:
    """Load all model pricing from database into memory cache."""
    from src.storage.snapshot_db import DEFAULT_DB_PATH

    pricing_dict = {}

    try:
        conn = sqlite3.connect(DEFAULT_DB_PATH, timeout=30.0)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT model_name, input_price_per_mtok, output_price_per_mtok,
                   cache_write_price_per_mtok, cache_read_price_per_mtok
            FROM model_pricing
        """)

        for row in cursor.fetchall():
            model_name = row[0]
            pricing_dict[model_name] = {
                'input_price': row[1],
                'output_price': row[2],
                'cache_write_price': row[3],
                'cache_read_price': row[4],
            }

        conn.close()
    except Exception:
        # If DB read fails, use hardcoded fallback
        from src.config.defaults import DEFAULT_MODEL_PRICING
        for model_name, pricing_info in DEFAULT_MODEL_PRICING.items():
            pricing_dict[model_name] = {
                'input_price': pricing_info['input_price'],
                'output_price': pricing_info['output_price'],
                'cache_write_price': pricing_info['cache_write_price'],
                'cache_read_price': pricing_info['cache_read_price'],
            }

    return pricing_dict

# Load pricing cache at module import
_PRICING_CACHE = _load_pricing_from_db()

# Legacy constants (fallback only)
LEGACY_INPUT_PRICE = 3.0
LEGACY_OUTPUT_PRICE = 15.0
LEGACY_CACHE_WRITE_PRICE = 3.75
LEGACY_CACHE_READ_PRICE = 0.30
#endregion


#region Data Classes


@dataclass(frozen=True)
class ModelPricing:
    """
    Represents pricing for a Claude model.

    Attributes:
        input_price: Price per million input tokens (USD)
        output_price: Price per million output tokens (USD)
        cache_write_price: Price per million cache creation tokens (USD)
        cache_read_price: Price per million cache read tokens (USD)
        model_name: Human-readable model name
    """

    input_price: float
    output_price: float
    cache_write_price: float
    cache_read_price: float
    model_name: str


#endregion


#region Functions


def get_model_pricing(model_id: str) -> ModelPricing:
    """
    Get pricing information for a given model ID.

    Args:
        model_id: Model identifier (e.g., 'claude-opus-4-1-20250805')

    Returns:
        ModelPricing object with pricing details
    """
    # First try exact match in cache
    if model_id in _PRICING_CACHE:
        pricing = _PRICING_CACHE[model_id]
        return ModelPricing(
            input_price=pricing['input_price'],
            output_price=pricing['output_price'],
            cache_write_price=pricing['cache_write_price'],
            cache_read_price=pricing['cache_read_price'],
            model_name=_get_display_name(model_id),
        )

    # If no exact match, try pattern matching for unknown model IDs
    model_lower = model_id.lower()

    # Try to find a matching model in cache by pattern
    for cached_model_id, pricing in _PRICING_CACHE.items():
        cached_lower = cached_model_id.lower()

        # Match by model family (most specific first)
        if "opus-4-5" in model_lower or "opus-4.5" in model_lower:
            if "opus-4-5" in cached_lower or "opus-4.5" in cached_lower:
                return ModelPricing(
                    input_price=pricing['input_price'],
                    output_price=pricing['output_price'],
                    cache_write_price=pricing['cache_write_price'],
                    cache_read_price=pricing['cache_read_price'],
                    model_name="Opus 4.5",
                )
        elif "opus-4" in model_lower and "opus-4" in cached_lower:
            return ModelPricing(
                input_price=pricing['input_price'],
                output_price=pricing['output_price'],
                cache_write_price=pricing['cache_write_price'],
                cache_read_price=pricing['cache_read_price'],
                model_name="Opus 4",
            )
        elif "sonnet-4-5" in model_lower or "sonnet-4.5" in model_lower:
            if "sonnet-4-5" in cached_lower or "sonnet-4.5" in cached_lower:
                return ModelPricing(
                    input_price=pricing['input_price'],
                    output_price=pricing['output_price'],
                    cache_write_price=pricing['cache_write_price'],
                    cache_read_price=pricing['cache_read_price'],
                    model_name="Sonnet 4.5",
                )
        elif "sonnet-4" in model_lower and "sonnet-4" in cached_lower:
            return ModelPricing(
                input_price=pricing['input_price'],
                output_price=pricing['output_price'],
                cache_write_price=pricing['cache_write_price'],
                cache_read_price=pricing['cache_read_price'],
                model_name="Sonnet 4",
            )
        elif "haiku-4-5" in model_lower or "haiku-4.5" in model_lower:
            if "haiku-4-5" in cached_lower or "haiku-4.5" in cached_lower:
                return ModelPricing(
                    input_price=pricing['input_price'],
                    output_price=pricing['output_price'],
                    cache_write_price=pricing['cache_write_price'],
                    cache_read_price=pricing['cache_read_price'],
                    model_name="Haiku 4.5",
                )
        elif ("haiku-3-5" in model_lower or "haiku-3.5" in model_lower):
            if "haiku-3-5" in cached_lower or "haiku-3.5" in cached_lower:
                return ModelPricing(
                    input_price=pricing['input_price'],
                    output_price=pricing['output_price'],
                    cache_write_price=pricing['cache_write_price'],
                    cache_read_price=pricing['cache_read_price'],
                    model_name="Haiku 3.5",
                )

    # Default/unknown model - use legacy fallback
    return ModelPricing(
        input_price=LEGACY_INPUT_PRICE,
        output_price=LEGACY_OUTPUT_PRICE,
        cache_write_price=LEGACY_CACHE_WRITE_PRICE,
        cache_read_price=LEGACY_CACHE_READ_PRICE,
        model_name="Unknown",
    )


def _get_display_name(model_id: str) -> str:
    """Get display name for a model ID."""
    model_lower = model_id.lower()

    if "opus-4-5" in model_lower or "opus-4.5" in model_lower:
        return "Opus 4.5"
    elif "opus-4" in model_lower:
        return "Opus 4"
    elif "sonnet-4-5" in model_lower or "sonnet-4.5" in model_lower:
        return "Sonnet 4.5"
    elif "sonnet-4" in model_lower:
        return "Sonnet 4"
    elif "haiku-4-5" in model_lower or "haiku-4.5" in model_lower:
        return "Haiku 4.5"
    elif "haiku-3-5" in model_lower or "haiku-3.5" in model_lower:
        return "Haiku 3.5"
    else:
        return "Unknown"


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model_id: str,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """
    Calculate the cost for given token usage including cache tokens.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        model_id: Model identifier
        cache_creation_tokens: Number of cache creation (write) tokens
        cache_read_tokens: Number of cache read tokens

    Returns:
        Total cost in USD
    """
    pricing = get_model_pricing(model_id)

    input_cost = (input_tokens / 1_000_000) * pricing.input_price
    output_cost = (output_tokens / 1_000_000) * pricing.output_price
    cache_write_cost = (cache_creation_tokens / 1_000_000) * pricing.cache_write_price
    cache_read_cost = (cache_read_tokens / 1_000_000) * pricing.cache_read_price

    return input_cost + output_cost + cache_write_cost + cache_read_cost


def format_cost(cost: float, precision: int = 2) -> str:
    """
    Format cost value for display.

    Args:
        cost: Cost in USD
        precision: Number of decimal places (default: 2)

    Returns:
        Formatted string with specified decimal places (e.g., "$0.00", "$1.23")
    """
    return f"${cost:.{precision}f}"


#endregion
