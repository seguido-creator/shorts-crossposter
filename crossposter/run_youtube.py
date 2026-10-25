"""Main entry: pick new original Reels from Drive and send them to YouTube.

Modes (repo variable YT_MODE, or --mode):
  kit      - no upload. Writes ready-to-paste title/description/tags + the Drive
             video link to the 'YouTube' tab, for manual upload from the phone.
  private  - uploads as PRIVATE (preview / unverified API project).
  unlisted - uploads as UNLISTED.
  public   - uploads as PUBLIC. Only works if repo variable
             YT_PUBLIC_APPROVED is 'yes'; otherwise it falls back to private.
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.auth.exceptions import RefreshError

from . import drive as gdrive
from . import metadata, originality, sheet, youtube
from .google_clients import MissingSecret, data_clients, youtube_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOKBACK_DAYS = 21
MIN_GAP_MINUTES = 60
LATE_WINDOW_HOURS = 3


def load_config(path=None) -> dict:
    with open(path or os.path.join(ROOT, "config", "channels.json"), encoding="utf-8") as f:
        return json.load(f)


def parse_utc(s: str):
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------- schedule
def due_now(cfg: dict, rows: list, now_utc: datetime, kit: bool = False) -> tuple:
    tz = ZoneInfo(cfg["timezone"])
    local = now_utc.astimezone(tz)
    slots = sorted(cfg.get("slots_local", []))
    if not slots:
        return False, "no slots configured", 0
    def at(hhmm):
        h, m = map(int, hhmm.split(":"))
        return local.replace(hour=h, minute=m, second=0, microsecond=0)
    passed = sum(1 for s in slots if at(s) <= local)
    if passed == 0:
        return False, f"before first slot ({slots[0]} {cfg['timezone']})", 0
    if local > at(slots[-1]) + timedelta(hours=LATE_WINDOW_HOURS):
        return False, "past today's posting window", 0
    today = [
        r for r in rows
        if r.get("status") in sheet.COUNTS_TOWARD_DAILY
        and (d := parse_utc(r.get("logged_at_utc", ""))) and d.astimezone(tz).date() == local.date()
    ]
    target = min(passed, int(cfg.get("max_per_day", 1)))
    if len(today) >= target:
        return False, f"{len(today)}/{target} done for today", 0
    remaining = target - len(today)
    last = max((parse_utc(r["logged_at_utc"]) for r in today), default=None)
    # Real uploads are spaced out; kits (no upload) can catch up at once when
    # GitHub skipped a scheduled run.
    if not kit and last and now_utc - last < timedelta(minutes=MIN_GAP_MINUTES):
        return False, "too soon after last upload", 0
    if not kit:
        remaining = 1
    return True, f"slot {passed}, {len(today)}/{target} done", remaining


def score_of(item, scores: dict) -> float:
    fid = item.fb_video_id
    if not fid:
        return -1
    if fid in scores:
        return scores[fid]
    for k, v in scores.items():
        if k.endswith("_" + fid):
            return v
    return -1


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=os.environ.get("YT_MODE", "") or "kit",
                    choices=["kit", "private", "unlisted", "public"])
    ap.add_argument("--channel", default="all")
    ap.add_argument("--limit", type=int, default=1, help="max items per channel this run")
    ap.add_argument("--ignore-schedule", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print metadata only; no uploads, no Sheet writes")
    ap.add_argument("--no-gemini", action="store_true")
    args = ap.parse_args(argv)

    conf = load_config()
    mode = args.mode
    if mode == "public" and os.environ.get("YT_PUBLIC_APPROVED", "").lower() != "yes":
        print("Public mode not approved yet (YT_PUBLIC_APPROVED != yes). Using private.")
        mode = "private"

    channels = {
        k: v for k, v in conf["channels"].items()
        if v.get("enabled") and (args.channel in ("all", k))
    }
    if not channels:
        print("No enabled channel matches; nothing to do.")
        return 0

    drive, sheets = data_clients()
    log = sheet.Log(sheets, conf["sheet_id"])
    if not args.dry_run:
        log.ensure_tab()
        log.load()
    else:
        try:
            log.load()
        except Exception:
            log.rows = []

    now = datetime.now(timezone.utc)
    due, limits = {}, {}
    for key, cfg in channels.items():
        if args.ignore_schedule:
            ok, why, n = True, "schedule ignored", args.limit
        else:
            ok, why, n = due_now(cfg, log.channel_rows(key), now, kit=(mode == "kit"))
        print(f"[{key}] {'DUE' if ok else 'not due'}: {why}")
        if ok:
            due[key] = cfg
            limits[key] = max(1, n)
    if not due:
        return 0

    folder_id = gdrive.find_folder_id(drive, conf["drive_published_folder"])
    since = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    brands = {c["brand"] for c in due.values()}
    prefixes = tuple(p for c in due.values() for p in c.get("file_prefixes", []))
    if any(not c.get("file_prefixes") for c in due.values()):
        prefixes = ()
    items = gdrive.list_items(drive, folder_id, brands, log.handled_json_ids(),
                              since_iso=since, prefixes=prefixes)
    scores = sheet.performance_scores(sheets, conf["sheet_id"])
    print(f"Found {len(items)} new candidate(s); {len(scores)} performance score(s) loaded.")

    summary = []
    stop_all = False
    for key, cfg in due.items():
        if stop_all:
            break
        cands = [i for i in items if i.meta.get("brand") == cfg["brand"]]
        cands.sort(key=lambda i: (score_of(i, scores), i.meta.get("posted_at_utc", "")), reverse=True)
        done = 0
        for item in cands:
            if done >= limits[key] or stop_all:
                break
            meta = item.meta
            base = {
                "channel": key, "page": meta.get("page", ""), "brand": cfg["brand"],
                "drive_file": item.mp4_name, "drive_json_id": item.json_id,
                "drive_mp4_id": item.mp4_id, "fb_video_id": item.fb_video_id,
                "mode": mode, "topic": meta.get("topic", ""),
            }
            ok, why = originality.is_original(meta, conf["allowed_kinds"])
            if not ok:
                print(f"  skip {item.stem}: not original ({why})")
                if not args.dry_run:
                    log.append({**base, "status": "SKIPPED_NOT_ORIGINAL", "notes": why})
                continue
            rows = log.channel_rows(key)
            sim, why = originality.too_similar(meta, rows)
            if sim:
                print(f"  skip {item.stem}: {why}")
                if not args.dry_run:
                    log.append({**base, "status": "SKIPPED_SIMILAR", "title": meta.get("title", ""), "notes": why})
                continue

            recent_titles = [r.get("title", "") for r in rows if r.get("title")]
            out = metadata.build(
                meta, cfg,
                avoid_openers=originality.overused_openers(recent_titles),
                recent_titles=recent_titles,
                use_gemini=not args.no_gemini,
            )
            preview = {**base, "title": out["title"], "title_options": out["title_options"],
                       "description": out["description"], "tags": out["tags"]}
            summary.append(preview)
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            if args.dry_run:
                done += 1
                continue

            record = {**base, "title": out["title"], "description": out["description"],
                      "tags": ", ".join(out["tags"])}
            if mode == "kit":
                log.append({**record, "status": "KIT_READY", "url": item.mp4_link,
                            "notes": "Upload manually from YouTube app. Options: " + " | ".join(out["title_options"])})
                done += 1
                continue

            row_no = log.append({**record, "status": "UPLOADING"})
            try:
                yt = youtube_client(cfg["token_secret"])
                with tempfile.TemporaryDirectory() as tmp:
                    path = gdrive.download(drive, item.mp4_id, os.path.join(tmp, "video.mp4"))
                    vid = youtube.upload(yt, path, out, mode)
                log.update(row_no, {"status": "UPLOADED", "youtube_video_id": vid,
                                    "url": f"https://youtube.com/shorts/{vid}",
                                    "notes": f"privacy={mode}; options: " + " | ".join(out["title_options"])})
                print(f"  uploaded {item.stem} -> {vid} ({mode})")
                preview["youtube_url"] = f"https://youtube.com/shorts/{vid}"
                done += 1
            except youtube.StopAll as e:
                log.update(row_no, {"status": "FAILED_LIMIT", "notes": str(e)})
                print(f"  STOP: YouTube limit reached ({e}). No more uploads this run.")
                stop_all = True
            except MissingSecret as e:
                log.update(row_no, {"status": "FAILED_CONFIG", "notes": str(e)})
                print(f"  {e}")
                break
            except RefreshError as e:
                log.update(row_no, {"status": "FAILED_AUTH",
                                    "notes": f"{cfg['token_secret']} rejected: {e}"[:500]})
                print(f"  Login token {cfg['token_secret']} rejected ({e}). Stopping this channel.")
                break
            except Exception as e:
                log.update(row_no, {"status": "FAILED", "notes": str(e)[:500]})
                print(f"  upload failed for {item.stem}: {e}")

    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step and summary:
        with open(step, "a", encoding="utf-8") as f:
            f.write(f"## YouTube run ({mode})\n\n")
            for s in summary:
                f.write(f"### {s['title']}\n- channel: {s['channel']} | file: {s['drive_file']}\n")
                if s.get("youtube_url"):
                    f.write(f"- url: {s['youtube_url']}\n")
                f.write(f"- title options: {' | '.join(s['title_options'])}\n- tags: {', '.join(s['tags'])}\n\n")
                f.write("```\n" + s["description"] + "\n```\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
