"""Gates that keep each channel clearly original for YouTube review.

1. is_original: only our own original Reels pass (never pack/bundle clips).
2. too_similar: skip a Reel whose topic/title is near-identical to a recent
   upload on the same channel (avoids 'mass-produced / repetitive' signals).
3. overused_openers: tells the title writer which first words to avoid.
"""
import re
from collections import Counter

STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "why", "how", "what", "your", "you", "this", "that", "it", "its", "with",
    "can", "do", "does", "be", "at", "by", "from", "still", "more", "most",
}
SIMILARITY_LIMIT = 0.6
RECENT_WINDOW = 30
PACK_HINTS = ("bundle", "mzshop", "mz shop", "pack")


def is_original(meta: dict, allowed_kinds) -> tuple:
    src = str(meta.get("source", "original")).strip().lower()
    if src != "original":
        return False, f"source={src}"
    kind = str(meta.get("kind", "")).strip().lower()
    if kind not in {k.lower() for k in allowed_kinds}:
        return False, f"kind={kind or 'missing'}"
    blob = " ".join(str(meta.get(k, "")) for k in ("folder", "file_name", "origin")).lower()
    if any(h in blob for h in PACK_HINTS):
        return False, "looks like a pack clip"
    return True, ""


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {w.strip("'") for w in words if w not in STOP and len(w) > 2}


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def too_similar(meta: dict, recent_rows: list) -> tuple:
    cand_topic = meta.get("topic", "")
    cand_title = meta.get("title", "")
    for r in recent_rows[-RECENT_WINDOW:]:
        if r.get("status") not in {"UPLOADED", "KIT_READY", "UPLOADING"}:
            continue
        s = max(jaccard(cand_topic, r.get("topic", "")), jaccard(cand_title, r.get("title", "")))
        if s >= SIMILARITY_LIMIT:
            return True, f"{s:.2f} similar to '{r.get('title','')[:60]}'"
    return False, ""


def overused_openers(recent_titles: list, last_n: int = 6, min_count: int = 2) -> list:
    opens = []
    for t in recent_titles[-last_n:]:
        m = re.match(r"\s*([A-Za-z']+)", t or "")
        if m:
            opens.append(m.group(1).lower())
    return [w for w, n in Counter(opens).items() if n >= min_count]
