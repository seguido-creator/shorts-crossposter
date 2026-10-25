import os

from crossposter import instagram, run_instagram, sheet
from crossposter.drive import Item
from tests.test_core import HISTORY
from tests.test_run import FakeSheets


def test_caption_has_sources_ai_note_and_capped_hashtags():
    meta = dict(HISTORY, sources=[
        "https://en.wikipedia.org/wiki/Cai_Lun",
        "https://www.britannica.com/x",
        "https://www.theguardian.com/y",
    ])
    cap = instagram.build_caption(meta)
    assert "Sources: Wikipedia, Britannica, The Guardian" in cap
    assert "AI voice" in cap
    assert "#Shorts" not in cap
    last = cap.strip().split("\n")[-1].split()
    assert len(last) <= 5 and all(t.startswith("#") for t in last)
    assert len(cap) <= 2200 and "—" not in cap


def test_caption_trims_long_body():
    meta = dict(HISTORY, caption="word " * 1000)
    assert len(instagram.build_caption(meta)) <= 2200


class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code, self.text = data, code, str(data)

    def json(self):
        return self._d


def test_publish_flow(monkeypatch, tmp_path):
    calls = []
    statuses = iter(["IN_PROGRESS", "FINISHED"])

    def fake_get(url, params=None, timeout=None):
        calls.append(("GET", url))
        if url.endswith("/me"):
            return Resp({"id": "p1", "name": "History Decoded",
                         "instagram_business_account": {"id": "ig1", "username": "historydecoded"}})
        if "content_publishing_limit" in url:
            return Resp({"data": [{"quota_usage": 2, "config": {"quota_total": 50}}]})
        if url.endswith("/c1"):
            return Resp({"status_code": next(statuses)})
        if url.endswith("/m1"):
            return Resp({"permalink": "https://www.instagram.com/reel/abc/"})
        raise AssertionError(url)

    def fake_post(url, data=None, timeout=None, headers=None):
        calls.append(("POST", url))
        if url.endswith("/ig1/media"):
            assert data["media_type"] == "REELS" and data["is_ai_generated"] == "true"
            return Resp({"id": "c1", "uri": "https://rupload.facebook.com/ig-api-upload/v26.0/c1"})
        if "rupload" in url:
            assert headers["offset"] == "0" and int(headers["file_size"]) == 4
            return Resp({"success": True})
        if url.endswith("/ig1/media_publish"):
            assert data["creation_id"] == "c1"
            return Resp({"id": "m1"})
        raise AssertionError(url)

    monkeypatch.setattr(instagram.requests, "get", fake_get)
    monkeypatch.setattr(instagram.requests, "post", fake_post)
    monkeypatch.setattr(instagram.time, "sleep", lambda s: None)
    f = tmp_path / "v.mp4"
    f.write_bytes(b"abcd")
    res = instagram.Client("tok").publish_reel(str(f), "cap")
    assert res == {"media_id": "m1", "permalink": "https://www.instagram.com/reel/abc/", "container_id": "c1"}


def test_limit_stops(monkeypatch, tmp_path):
    c = instagram.Client("tok")
    c._ig_id = "ig1"
    monkeypatch.setattr(c, "quota_left", lambda: 0)
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    try:
        c.publish_reel(str(f), "cap")
        assert False
    except instagram.LimitReached:
        pass


def _setup(monkeypatch):
    fs = FakeSheets()
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(run_instagram, "data_clients", lambda: (object(), fs))
    monkeypatch.setattr(run_instagram.gdrive, "find_folder_id", lambda d, n: "folder")
    item = Item("HIST_a", "json_a", "mp4_a", "HIST_a.mp4", 1000, "link_a", dict(HISTORY))
    monkeypatch.setattr(run_instagram.gdrive, "list_items",
                        lambda d, f, brands, skip, **k: [i for i in [item] if i.json_id not in skip])
    monkeypatch.setattr(run_instagram.gdrive, "download", lambda d, fid, p: p)
    return fs


def test_preview_never_posts_and_publish_needs_approval(monkeypatch):
    fs = _setup(monkeypatch)
    monkeypatch.delenv("IG_PUBLIC_APPROVED", raising=False)

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("must not post")
    monkeypatch.setattr(run_instagram.instagram, "Client", Boom)
    run_instagram.main(["--mode", "publish", "--account", "history", "--ignore-schedule"])
    rows = fs.store["Instagram"]
    assert rows[1][sheet.IG_COLUMNS.index("status")] == "PREVIEW"
    assert "AI voice" in rows[1][sheet.IG_COLUMNS.index("caption")]


def test_off_mode_does_nothing(monkeypatch):
    fs = _setup(monkeypatch)
    run_instagram.main(["--mode", "off"])
    assert "Instagram" not in fs.store
