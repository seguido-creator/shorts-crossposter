"""Turns a Reel's .json sidecar into honest YouTube Shorts metadata.

Rules (from YouTube limits and our own content rules):
  * title <= 100 chars, no '<' or '>', no fake clickbait, must match the video
  * description <= 5000 bytes, ends with #Shorts + a few hashtags
  * tags total <= 500 chars
  * AI use stated plainly in the description
"""
import json
import os
import re

import requests

TITLE_MAX = 100
DESC_MAX_BYTES = 4900
TAGS_MAX_CHARS = 480
MAX_HASHTAGS = 4  # plus #Shorts

# Words that usually signal engagement bait or hype; titles using them are rejected.
BAIT_PATTERNS = [
    r"\byou won'?t believe\b", r"\bshocking\b", r"\bgone wrong\b", r"\bmust see\b",
    r"\bnobody is talking\b", r"\bwatch till the end\b", r"\bsmash (that )?like\b",
    r"\b100% (true|real)\b", r"\bsecret (they|government)\b", r"\bexposed\b",
]


def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\\n", "\n").replace("\r", "")
    s = s.replace("<", "").replace(">", "")
    s = s.replace("—", " - ").replace("–", "-")  # no long dashes
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def strip_hashtag_lines(caption: str) -> str:
    """Removes trailing lines that are only hashtags (we rebuild them)."""
    lines = caption.split("\n")
    while lines and (not lines[-1].strip() or re.fullmatch(r"(\s*#\w+)+\s*", lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


_STOP = {"the", "and", "of", "in", "on", "a", "an", "to", "for", "with", "by", "from",
         "how", "why", "what", "list", "history", "wiki", "biography", "www", "com", "org",
         "html", "htm", "php", "index", "en", "article", "articles", "news", "story", "page"}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOP}


def relevant_sources(meta: dict) -> list:
    """Drops source links that clearly have nothing to do with the video.
    A link is dropped when its URL path names at least two topic words and none
    of them appear in the title, topic, hook or caption. Links with no readable
    words (DOIs, homepages, single-name pages) are kept."""
    context = _words(" ".join(str(meta.get(k, "")) for k in ("title", "topic", "hook", "caption", "series")))
    keep = []
    for s in meta.get("sources") or []:
        s = str(s).strip()
        if not s:
            continue
        path = re.sub(r"^https?://[^/]+", "", s)
        words = {w for w in _words(re.sub(r"[-_/.%]", " ", path)) if w.isalpha()}
        if len(words) >= 2 and not (words & context):
            continue
        keep.append(s)
    return keep


def norm_hashtag(tag: str) -> str:
    tag = re.sub(r"[^\w]", "", tag.lstrip("#"))
    return f"#{tag}" if tag else ""


def is_bait(title: str) -> bool:
    t = title.lower()
    if any(re.search(p, t) for p in BAIT_PATTERNS):
        return True
    words = [w for w in re.findall(r"[A-Za-z]+", title) if len(w) > 3]
    caps = [w for w in words if w.isupper()]
    return len(caps) >= 2 or title.count("!") > 1


def valid_title(title: str) -> bool:
    return bool(title) and len(title) <= TITLE_MAX and "<" not in title and ">" not in title and not is_bait(title)


def fit_title(title: str) -> str:
    title = clean_text(title).replace("\n", " ").replace("#Shorts", "").strip()
    if len(title) <= TITLE_MAX:
        return title
    cut = title[: TITLE_MAX - 3].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "..."


def opener(title: str) -> str:
    m = re.match(r"\s*([A-Za-z']+)", title or "")
    return m.group(1).lower() if m else ""


# ---------------------------------------------------------------- Gemini
def gemini_title_options(meta: dict, avoid_openers: list, recent_titles: list):
    """Asks Gemini for 3 honest title options and its pick. Returns (options, best)
    or ([], '') if Gemini is not configured or fails."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return [], ""
    model = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
    prompt = f"""You write YouTube Shorts titles for the channel "{meta.get('brand','')}".
Video working title: {meta.get('title','')}
Topic: {meta.get('topic','')}
Caption (what the video actually says): {strip_hashtag_lines(clean_text(meta.get('caption','')))[:900]}
Voice and language: {meta.get('language','English')}

Write 3 different title options. Rules:
- Max 70 characters each. Plain, natural language. No hashtags. No emojis.
- Use curiosity, a clear promise, or a surprise, but the video MUST deliver exactly what the title says. No fake clickbait, no exaggeration, no ALL CAPS.
- Make the 3 options use different structures (e.g. a question, a statement, a "how/why" line).
- Avoid starting with these words (used too often recently): {', '.join(avoid_openers) or 'none'}.
- Must not be near-duplicates of these recent titles: {json.dumps(recent_titles[:10])}
Then pick the single best option for a viewer scrolling Shorts.
Reply ONLY with JSON: {{"options": ["...","...","..."], "best": 0}}"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        r = requests.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"},
            },
            timeout=60,
        )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text)
        opts = [fit_title(o) for o in data.get("options", []) if isinstance(o, str)]
        opts = [o for o in opts if valid_title(o)]
        if not opts:
            return [], ""
        best_i = data.get("best", 0)
        raw_best = data.get("options", [""])[best_i] if isinstance(best_i, int) and best_i < len(data.get("options", [])) else ""
        best = fit_title(raw_best) if raw_best else ""
        if best not in opts:
            best = opts[0]
        return opts, best
    except Exception as e:  # never block an upload on title generation
        print(f"  Gemini title step skipped: {e}")
        return [], ""


# ---------------------------------------------------------------- build
def build(meta: dict, channel_cfg: dict, avoid_openers=None, recent_titles=None, use_gemini=True) -> dict:
    avoid_openers = avoid_openers or []
    recent_titles = recent_titles or []

    options, title = ([], "")
    if use_gemini:
        options, title = gemini_title_options(meta, avoid_openers, recent_titles)
    fallback = fit_title(meta.get("title") or meta.get("hook") or meta.get("topic") or "")
    if not title:
        title = fallback if valid_title(fallback) else fit_title(meta.get("topic", "Untitled"))
        options = options or [title]

    body = strip_hashtag_lines(clean_text(meta.get("caption", "")))
    sources = [clean_text(s) for s in relevant_sources(meta)]
    if sources:
        source_note = "Sources: " + "; ".join(sources[:5])
    else:
        source_note = channel_cfg.get("source_note_default", "")
    ai_note = "Script written with AI assistance. Narration uses an AI voice."

    hashtags = []
    for h in (meta.get("hashtags") or []):
        h = norm_hashtag(str(h))
        if h and h.lower() != "#shorts" and h.lower() not in {x.lower() for x in hashtags}:
            hashtags.append(h)
    hashtags = ["#Shorts"] + hashtags[:MAX_HASHTAGS]

    parts = [p for p in [body, source_note, ai_note, " ".join(hashtags)] if p]
    description = "\n\n".join(parts)
    while len(description.encode("utf-8")) > DESC_MAX_BYTES and body:
        body = body[: int(len(body) * 0.9)].rsplit(" ", 1)[0] + "..."
        parts = [p for p in [body, source_note, ai_note, " ".join(hashtags)] if p]
        description = "\n\n".join(parts)

    tags, total = [], 0
    candidates = [h.lstrip("#") for h in hashtags[1:]] + [meta.get("topic", "")] + \
        list(channel_cfg.get("default_tags", [])) + [meta.get("brand", "")]
    for t in candidates:
        t = clean_text(str(t)).replace(",", "").strip()
        if not t or t.lower() in {x.lower() for x in tags}:
            continue
        cost = len(t) + (2 if " " in t else 0) + 1
        if total + cost > TAGS_MAX_CHARS:
            break
        tags.append(t)
        total += cost

    return {
        "title": title,
        "title_options": options,
        "description": description,
        "tags": tags,
        "category_id": channel_cfg.get("category_id", "22"),
        "language": channel_cfg.get("language", "en"),
        "synthetic_media": bool(channel_cfg.get("synthetic_media", True)),
    }
