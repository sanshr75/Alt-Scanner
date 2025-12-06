# src/scoring.py

from typing import Dict, Any


def score_signal(features: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    features: dict with computed indicator values and boolean flags, e.g.:
      {
        "ema_align": True,
        "macd_pos": True,
        "vol_spike": True,
        "mtf_ema_align": True,
        "breakout": True,
        "retest": False,
        "bounce_support": False,
        "support_retest": False,
        "fall_resistance": False,
        "ctx_adj": 0,
        "tags": [...]
      }

    config: full parsed config.yaml

    returns: dict with keys:
      final_score, base_score, mtf_score, ctx_adj, tags
    """

    weights = config.get("weights", {}) or {}
    mtf_weights = config.get("mtf_weights", {}) or {}

    base = 0

    # Core trend / momentum / volume
    if features.get("ema_align"):
        base += weights.get("ema_align", 0)

    if features.get("macd_pos"):
        base += weights.get("macd", 0)

    if features.get("vol_spike"):
        base += weights.get("vol_spike", 0)

    # Structure / S-R behaviour (map to breakout / retest weights)
    if features.get("breakout"):
        base += weights.get("breakout", 0)

    if features.get("retest"):
        base += weights.get("retest", 0)

    # Treat these as variants of "good S/R interaction" for now
    if features.get("bounce_support"):
        base += weights.get("retest", 0)

    if features.get("support_retest"):
        base += weights.get("retest", 0)

    if features.get("fall_resistance"):
        base += weights.get("retest", 0)

    # Multi-timeframe confirmations
    mtf = 0
    if features.get("mtf_ema_align"):
        mtf += mtf_weights.get("ema", 0)

    # Context adjustment (BTC trend, etc.)
    ctx = features.get("ctx_adj", 0)

    final = base + mtf + ctx

    return {
        "final_score": final,
        "base_score": base,
        "mtf_score": mtf,
        "ctx_adj": ctx,
        "tags": features.get("tags", []),
    }
