"""YouTube upload via the Data API (videos.insert, resumable, safe retries)."""
import random
import time

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

RETRIABLE_STATUS = {500, 502, 503, 504}
STOP_REASONS = {"uploadLimitExceeded", "quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}
MAX_RETRIES = 6


class StopAll(RuntimeError):
    """Quota or upload limit hit: stop every upload for this run."""


def error_reason(err: HttpError) -> str:
    try:
        import json
        data = json.loads(err.content.decode("utf-8"))
        return data["error"]["errors"][0].get("reason", "")
    except Exception:
        return ""


def body_for(meta_out: dict, privacy: str) -> dict:
    return {
        "snippet": {
            "title": meta_out["title"],
            "description": meta_out["description"],
            "tags": meta_out["tags"],
            "categoryId": meta_out["category_id"],
            "defaultLanguage": meta_out["language"],
            "defaultAudioLanguage": meta_out["language"],
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": meta_out["synthetic_media"],
            "embeddable": True,
            "license": "youtube",
        },
    }


def upload(yt, path: str, meta_out: dict, privacy: str) -> str:
    media = MediaFileUpload(path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(
        part="snippet,status",
        body=body_for(meta_out, privacy),
        media_body=media,
        notifySubscribers=True,
    )
    response, retry = None, 0
    while response is None:
        failed = False
        try:
            _, response = req.next_chunk()
        except HttpError as e:
            reason = error_reason(e)
            if reason in STOP_REASONS:
                raise StopAll(reason) from e
            if e.resp.status not in RETRIABLE_STATUS:
                raise
            failed = True
        except (ConnectionError, TimeoutError, OSError):
            failed = True
        if failed:
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError("upload failed after retries")
            time.sleep(min(60, (2 ** retry) + random.random()))
    if "id" not in response:
        raise RuntimeError(f"unexpected upload response: {response}")
    return response["id"]


def channel_title(yt) -> str:
    res = yt.channels().list(part="snippet", mine=True).execute()
    items = res.get("items", [])
    return items[0]["snippet"]["title"] if items else ""
