# src/scoring.py
from typing import Dict, Any

def score_signal(features: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    features: dict with computed indicator values and boolean flags:
      e.g. {"ema_align": True, "macd_pos": True, "vol_spike": True, ...}
    config: parsed config.yaml

    returns: dict with keys: final_score, base_score, mtf_score, ctx_adj, tags (list)
    """
    # placeholder simple scoring - will expand later
    base = 0
    if features.get("ema_align"): base += config["weights"]["ema_align"]
    if features.get("macd_pos"): base += config["weights"]["macd"]
    if features.get("vol_spike"): base += config["weights"]["vol_spike"]
    mtf = 0
    if features.get("mtf_ema_align"): mtf += config["mtf_weights"]["ema"]
    ctx = features.get("ctx_adj", 0)
    final = base + mtf + ctx
    return {"final_score": final, "base_score": base, "mtf_score": mtf, "ctx_adj": ctx, "tags": features.get("tags", [])}

