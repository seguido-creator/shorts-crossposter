"""One-off cleanup: removes given source links from existing YouTube descriptions.

Input (env FIXES or --fixes): JSON list of {"channel": key, "video_id": id, "remove": [url, ...]}.
Needs channel tokens made with the youtube.force-ssl scope. Changes only the description;
title, tags, category and privacy stay the same. Prints before/after for the log.
"""
import argparse
import json
import os
import re
import sys

from googleapiclient.discovery import build

from .google_clients import _creds
from .run_youtube import load_config

EDIT_SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


def clean(description: str, urls: list) -> str:
    out = []
    for line in description.split("\n"):
        if line.startswith("Sources:"):
            items = [s.strip() for s in line[len("Sources:"):].split(";")]
            items = [s for s in items if s and s not in urls]
            if not items:
                continue
            line = "Sources: " + "; ".join(items)
        out.append(line)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixes", default=os.environ.get("FIXES", "[]"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    conf = load_config()
    for fx in json.loads(args.fixes):
        cfg = conf["channels"][fx["channel"]]
        yt = build("youtube", "v3", credentials=_creds(cfg["token_secret"], EDIT_SCOPES), cache_discovery=False)
        items = yt.videos().list(part="snippet", id=fx["video_id"]).execute().get("items", [])
        if not items:
            print(f"{fx['video_id']}: not found on {fx['channel']}")
            continue
        sn = items[0]["snippet"]
        new = clean(sn.get("description", ""), fx["remove"])
        if new == sn.get("description", "").strip():
            print(f"{fx['video_id']}: nothing to remove")
            continue
        print(f"--- {fx['video_id']} ({fx['channel']}) BEFORE ---\n{sn['description']}\n--- AFTER ---\n{new}\n")
        if args.dry_run:
            continue
        body = {"id": fx["video_id"], "snippet": {"title": sn["title"], "categoryId": sn["categoryId"],
                                                  "description": new, "tags": sn.get("tags", []),
                                                  "defaultLanguage": sn.get("defaultLanguage", "en")}}
        yt.videos().update(part="snippet", body=body).execute()
        print(f"{fx['video_id']}: description updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
