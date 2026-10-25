"""Pick new original Reels from Drive and publish them to Instagram.

Modes (repo variable IG_MODE, or --mode):
  off      - do nothing (default until the owner approves).
  preview  - no post. Writes the caption and Drive link to the 'Instagram' tab
             (status PREVIEW) so the owner can review. The item stays available.
  publish  - posts publicly. Only works if repo variable IG_PUBLIC_APPROVED is
             'yes'; otherwise it falls back to preview.
Instagram has no private mode, so preview is the review step.
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from . import drive as gdrive
from . import instagram, originality, sheet
from .google_clients import data_clients
from .run_youtube import LOOKBACK_DAYS, due_now, load_config, score_of

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINAL = {"PUBLISHED", "PUBLISHING", "SKIPPED_SIMILAR", "SKIPPED_NOT_ORIGINAL"}
DAILY = {"PUBLISHED", "PUBLISHING"}


def load_ig_config() -> dict:
    with open(os.path.join(ROOT, "config", "instagram.json"), encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=os.environ.get("IG_MODE", "") or "off",
                    choices=["off", "preview", "publish"])
    ap.add_argument("--account", default="all")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--ignore-schedule", action="store_true")
    args = ap.parse_args(argv)

    mode = args.mode
    if mode == "off":
        print("Instagram mode is off. Nothing to do.")
        return 0
    if mode == "publish" and os.environ.get("IG_PUBLIC_APPROVED", "").lower() != "yes":
        print("Publishing not approved yet (IG_PUBLIC_APPROVED != yes). Using preview.")
        mode = "preview"

    yt_conf = load_config()
    ig_conf = load_ig_config()
    accounts = {k: v for k, v in ig_conf["accounts"].items()
                if v.get("enabled") and args.account in ("all", k)}
    if not accounts:
        print("No enabled Instagram account matches.")
        return 0

    drive, sheets = data_clients()
    log = sheet.Log(sheets, yt_conf["sheet_id"], tab=sheet.IG_TAB, columns=sheet.IG_COLUMNS)
    log.ensure_tab()
    log.load()

    now = datetime.now(timezone.utc)
    due = {}
    for key, cfg in accounts.items():
        rows = [r for r in log.channel_rows(key) if r.get("status") in DAILY or r.get("status", "").startswith("FAILED")]
        if args.ignore_schedule:
            ok, why = True, "schedule ignored"
        else:
            daily_rows = [dict(r, status="UPLOADED") for r in rows if r.get("status") in DAILY]
            ok, why, _ = due_now(cfg, daily_rows, now, kit=False)
        print(f"[{key}] {'DUE' if ok else 'not due'}: {why}")
        if ok:
            due[key] = cfg
    if not due:
        return 0

    folder_id = gdrive.find_folder_id(drive, yt_conf["drive_published_folder"])
    since = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    prefixes = tuple(p for c in due.values() for p in c.get("file_prefixes", []))
    if any(not c.get("file_prefixes") for c in due.values()):
        prefixes = ()
    items = gdrive.list_items(drive, folder_id, {c["brand"] for c in due.values()},
                              log.handled_json_ids(FINAL), since_iso=since, prefixes=prefixes)
    scores = sheet.performance_scores(sheets, yt_conf["sheet_id"])

    summary = []
    for key, cfg in due.items():
        cands = [i for i in items if i.meta.get("brand") == cfg["brand"]]
        cands.sort(key=lambda i: (score_of(i, scores), i.meta.get("posted_at_utc", "")), reverse=True)
        done = 0
        for item in cands:
            if done >= args.limit:
                break
            meta = item.meta
            base = {"channel": key, "page": meta.get("page", ""), "brand": cfg["brand"],
                    "drive_file": item.mp4_name, "drive_json_id": item.json_id,
                    "drive_mp4_id": item.mp4_id, "fb_video_id": item.fb_video_id,
                    "mode": mode, "topic": meta.get("topic", "")}
            ok, why = originality.is_original(meta, yt_conf["allowed_kinds"])
            if not ok:
                log.append({**base, "status": "SKIPPED_NOT_ORIGINAL", "notes": why})
                continue
            posted = [dict(r, title=r.get("caption", "")[:120]) for r in log.channel_rows(key)
                      if r.get("status") in DAILY]
            sim, why = originality.too_similar(meta, [dict(r, status="UPLOADED") for r in posted])
            if sim:
                log.append({**base, "status": "SKIPPED_SIMILAR", "notes": why})
                continue
            caption = instagram.build_caption(meta)
            summary.append({**base, "caption": caption})
            print(f"--- {item.stem}\n{caption}\n")
            if mode == "preview":
                log.append({**base, "status": "PREVIEW", "url": item.mp4_link, "caption": caption,
                            "notes": "Not posted. Set IG_MODE=publish after approval."})
                done += 1
                continue
            row_no = log.append({**base, "status": "PUBLISHING", "caption": caption})
            try:
                client = instagram.Client(os.environ.get(cfg["token_secret"], "").strip(),
                                          ig_conf.get("graph_version", "v26.0"))
                with tempfile.TemporaryDirectory() as tmp:
                    path = gdrive.download(drive, item.mp4_id, os.path.join(tmp, "video.mp4"))
                    res = client.publish_reel(path, caption, ai_label=cfg.get("ai_label", True))
                log.update(row_no, {"status": "PUBLISHED", "ig_media_id": res["media_id"],
                                    "url": res["permalink"], "notes": f"@{getattr(client, 'username', '')}"})
                summary[-1]["url"] = res["permalink"]
                done += 1
            except instagram.LimitReached as e:
                log.update(row_no, {"status": "FAILED_LIMIT", "notes": str(e)})
                break
            except Exception as e:
                log.update(row_no, {"status": "FAILED", "notes": str(e)[:500]})
                print(f"  publish failed for {item.stem}: {e}")
                break  # stop this account; avoid hammering the API

    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step and summary:
        with open(step, "a", encoding="utf-8") as f:
            f.write(f"## Instagram run ({mode})\n\n")
            for s in summary:
                f.write(f"### {s['drive_file']} ({s['channel']})\n")
                if s.get("url"):
                    f.write(f"- url: {s['url']}\n")
                f.write("```\n" + s["caption"] + "\n```\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
