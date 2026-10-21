"""Reads the 'Reels Autoposter - Published' folder (read-only)."""
import io
import json
import os
from dataclasses import dataclass, field

from googleapiclient.http import MediaIoBaseDownload


@dataclass
class Item:
    stem: str
    json_id: str
    mp4_id: str
    mp4_name: str
    mp4_size: int
    mp4_link: str
    meta: dict = field(default_factory=dict)

    @property
    def fb_video_id(self) -> str:
        return str(self.meta.get("fb_video_id", ""))


def find_folder_id(drive, name: str) -> str:
    q = (
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false "
        f"and name = '{name}'"
    )
    res = drive.files().list(q=q, fields="files(id,name)", pageSize=10).execute()
    files = res.get("files", [])
    if not files:
        raise RuntimeError(f"Drive folder '{name}' not found")
    return files[0]["id"]


def _list_children(drive, folder_id: str, since_iso: str = ""):
    out, token = [], None
    q = f"'{folder_id}' in parents and trashed = false"
    if since_iso:
        q += f" and createdTime > '{since_iso}'"
    while True:
        res = (
            drive.files()
            .list(
                q=q,
                fields="nextPageToken, files(id,name,mimeType,size,webViewLink,createdTime)",
                pageSize=1000,
                pageToken=token,
                orderBy="createdTime desc",
            )
            .execute()
        )
        out.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            return out


def _read_json(drive, file_id: str) -> dict:
    raw = drive.files().get_media(fileId=file_id).execute()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def list_items(drive, folder_id: str, brands: set, skip_json_ids: set,
               since_iso: str = "", prefixes: tuple = ()) -> list:
    """Pairs every .json sidecar with its .mp4. Only sidecars for the wanted
    brands are kept; sidecars already logged are not downloaded again.
    since_iso limits the scan to recent files; prefixes (e.g. 'US_') skip
    downloading sidecars that clearly belong to other pages."""
    files = _list_children(drive, folder_id, since_iso)
    mp4s = {
        os.path.splitext(f["name"])[0]: f
        for f in files
        if f["name"].lower().endswith(".mp4")
    }
    items = []
    for f in files:
        if not f["name"].lower().endswith(".json"):
            continue
        if f["id"] in skip_json_ids:
            continue
        stem = os.path.splitext(f["name"])[0]
        if prefixes and not f["name"].startswith(prefixes):
            continue
        mp4 = mp4s.get(stem)
        if not mp4:
            continue  # video not uploaded yet, try next run
        meta = _read_json(drive, f["id"])
        if meta.get("brand") not in brands:
            continue
        items.append(
            Item(
                stem=stem,
                json_id=f["id"],
                mp4_id=mp4["id"],
                mp4_name=mp4["name"],
                mp4_size=int(mp4.get("size") or 0),
                mp4_link=mp4.get("webViewLink", ""),
                meta=meta,
            )
        )
    return items


def download(drive, file_id: str, dest_path: str) -> str:
    req = drive.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk(num_retries=3)
    return dest_path
