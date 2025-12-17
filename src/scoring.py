# src/scoring.py
from typing import Dict, Any


def score_signal(
    features: Dict[str, Any],
    config: Dict[str, Any],
    side: str = "BUY",
) -> Dict[str, Any]:
    """
    features: dict with computed indicator values and boolean flags.
    config: parsed config.yaml
    side: "BUY" or "SELL" – so we can use bullish vs bearish weights.
    """

    w = config.get("weights", {})
    mtfw = config.get("mtf_weights", {})

    base = 0

    if side == "BUY":
        # bullish weights
        if features.get("ema_align"):
            base += w.get("ema_align", 0)
        if features.get("macd_pos"):
            base += w.get("macd", 0)
        if features.get("vol_spike"):
            base += w.get("vol_spike", 0)
        if features.get("breakout"):
            base += w.get("breakout", 0)
        if features.get("retest"):
            base += w.get("retest", 0)
    else:
        # bearish weights – short-side equivalents
        if features.get("ema_down"):
            base += w.get("ema_down", w.get("ema_align", 0))
        if features.get("macd_neg"):
            base += w.get("macd_neg", w.get("macd", 0))
        if features.get("breakdown"):
            base += w.get("breakdown", w.get("breakout", 0))
        if features.get("retest_short"):
            base += w.get("retest_short", w.get("retest", 0))
        if features.get("vol_spike"):
            base += w.get("vol_spike", 0)

    # common MTF bonus
    mtf = 0
    if features.get("mtf_ema_align"):
        mtf += mtfw.get("ema", 0)

    # BTC / context adjustment
    ctx = features.get("ctx_adj", 0)

    final = base + mtf + ctx

    return {
        "final_score": final,
        "base_score": base,
        "mtf_score": mtf,
        "ctx_adj": ctx,
        "tags": features.get("tags", []),
    }
