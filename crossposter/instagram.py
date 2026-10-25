"""Instagram Reels publishing through the Instagram API with Facebook Login.

Flow (Meta Content Publishing docs):
  1. POST /{ig_id}/media  media_type=REELS, upload_type=resumable  -> container id + upload uri
  2. POST https://rupload.facebook.com/ig-api-upload/<ver>/<container>  (raw video bytes)
  3. Poll GET /{container}?fields=status_code until FINISHED
  4. POST /{ig_id}/media_publish creation_id=<container>
Only one secret per account is needed: a long-lived Page token for the linked
Facebook Page. The Instagram account id is read from the Page.
"""
import os
import re
import time
from urllib.parse import urlparse

import requests

from . import metadata

CAPTION_MAX = 2200
MAX_HASHTAGS = 5
TIMEOUT = 120


class IGError(RuntimeError):
    pass


class LimitReached(IGError):
    pass


def _check(r: requests.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        raise IGError(f"{r.status_code} {err.get('type','')} {err.get('code','')}: {err.get('message', r.text[:300])}")
    return data


class Client:
    def __init__(self, page_token: str, version: str = "v26.0"):
        if not page_token:
            raise IGError("Instagram Page token secret is not set")
        self.token = page_token
        self.ver = version
        self.base = f"https://graph.facebook.com/{version}"
        self._ig_id = None

    def get(self, path, **params):
        params["access_token"] = self.token
        return _check(requests.get(f"{self.base}/{path}", params=params, timeout=TIMEOUT))

    def post(self, path, **data):
        data["access_token"] = self.token
        return _check(requests.post(f"{self.base}/{path}", data=data, timeout=TIMEOUT))

    @property
    def ig_id(self) -> str:
        if not self._ig_id:
            page = self.get("me", fields="id,name,instagram_business_account{id,username}")
            acct = page.get("instagram_business_account")
            if not acct:
                raise IGError(f"Facebook Page '{page.get('name')}' has no linked Instagram professional account")
            self._ig_id = acct["id"]
            self.username = acct.get("username", "")
            self.page_name = page.get("name", "")
        return self._ig_id

    def quota_left(self) -> int:
        data = self.get(f"{self.ig_id}/content_publishing_limit", fields="quota_usage,config")
        item = (data.get("data") or [{}])[0]
        total = int((item.get("config") or {}).get("quota_total", 50))
        used = int(item.get("quota_usage", 0))
        return total - used

    def publish_reel(self, video_path: str, caption: str, ai_label: bool = True,
                     share_to_feed: bool = True, poll_every: int = 30, max_wait: int = 900) -> dict:
        if self.quota_left() <= 0:
            raise LimitReached("Instagram daily publishing limit reached")
        params = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
        }
        if ai_label:
            params["is_ai_generated"] = "true"
        cont = self.post(f"{self.ig_id}/media", **params)
        cid = cont["id"]
        uri = cont.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{self.ver}/{cid}"
        size = os.path.getsize(video_path)
        with open(video_path, "rb") as fh:
            r = requests.post(uri, data=fh, timeout=600, headers={
                "Authorization": f"OAuth {self.token}",
                "offset": "0",
                "file_size": str(size),
            })
        _check(r)
        waited, status = 0, ""
        while waited <= max_wait:
            status = self.get(cid, fields="status_code,status").get("status_code", "")
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise IGError(f"container {cid} {status}")
            time.sleep(poll_every)
            waited += poll_every
        if status != "FINISHED":
            raise IGError(f"container {cid} not ready after {max_wait}s (last status {status})")
        media_id = self.post(f"{self.ig_id}/media_publish", creation_id=cid)["id"]
        try:
            link = self.get(media_id, fields="permalink").get("permalink", "")
        except IGError:
            link = ""
        return {"media_id": media_id, "permalink": link, "container_id": cid}


# ---------------------------------------------------------------- caption
def _source_names(sources) -> list:
    names = []
    for s in sources or []:
        s = str(s).strip()
        host = urlparse(s).netloc.lower() if s.startswith("http") else ""
        host = re.sub(r"^(www|en|m)\.", "", host)
        name = {
            "wikipedia.org": "Wikipedia", "britannica.com": "Britannica",
            "theguardian.com": "The Guardian", "loc.gov": "Library of Congress",
            "archives.gov": "US National Archives", "doi.org": "peer-reviewed journal",
        }.get(host)
        if not name and host:
            for k, v in {"wikipedia": "Wikipedia", "archives.gov": "US National Archives"}.items():
                if k in host:
                    name = v
        name = name or (host or s)[:40]
        if name and name not in names:
            names.append(name)
    return names[:4]


def build_caption(meta: dict) -> str:
    body = metadata.strip_hashtag_lines(metadata.clean_text(meta.get("caption", "")))
    srcs = _source_names(meta.get("sources"))
    tags = []
    for h in meta.get("hashtags") or []:
        h = metadata.norm_hashtag(str(h))
        if h and h.lower() not in {"#shorts"} and h.lower() not in {t.lower() for t in tags}:
            tags.append(h)
    parts = [body]
    if srcs:
        parts.append("Sources: " + ", ".join(srcs))
    parts.append("Script written with AI assistance. Narration uses an AI voice.")
    parts.append(" ".join(tags[:MAX_HASHTAGS]))
    cap = "\n\n".join(p for p in parts if p)
    if len(cap) > CAPTION_MAX:
        over = len(cap) - CAPTION_MAX + 3
        body = body[: max(0, len(body) - over)].rsplit(" ", 1)[0] + "..."
        parts[0] = body
        cap = "\n\n".join(p for p in parts if p)
    return cap
