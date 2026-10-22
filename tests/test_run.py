"""End-to-end run with fake Drive / Sheets / YouTube (no network)."""
import json

from crossposter import run_youtube, sheet
from crossposter.drive import Item
from tests.test_core import HISTORY


class FakeReq:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeValues:
    def __init__(self, store):
        self.store = store

    def get(self, spreadsheetId, range):
        tab = range.split("!")[0]
        return FakeReq({"values": self.store.get(tab, [])})

    def update(self, spreadsheetId, range, valueInputOption, body):
        tab, cell = range.split("!")
        rows = self.store.setdefault(tab, [])
        if ":" in cell:  # header write
            rows[:1] = body["values"]
        else:
            col = "".join(ch for ch in cell if ch.isalpha())
            r = int("".join(ch for ch in cell if ch.isdigit()))
            c = ord(col) - 65
            while len(rows[r - 1]) <= c:
                rows[r - 1].append("")
            rows[r - 1][c] = body["values"][0][0]
        return FakeReq({})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        tab = range.split("!")[0]
        rows = self.store.setdefault(tab, [])
        rows.extend(body["values"])
        return FakeReq({"updates": {"updatedRange": f"{tab}!A{len(rows)}:Q{len(rows)}"}})


class FakeSheets:
    def __init__(self):
        self.store = {"Performance": [["page", "post_id", "score"], ["HIST", "222", "9"]]}

    def spreadsheets(self):
        return self

    def values(self):
        return FakeValues(self.store)

    def get(self, spreadsheetId):
        return FakeReq({"sheets": [{"properties": {"title": t}} for t in self.store]})

    def batchUpdate(self, spreadsheetId, body):
        self.store[body["requests"][0]["addSheet"]["properties"]["title"]] = []
        return FakeReq({})


def make_items():
    a = Item("HIST_a", "json_a", "mp4_a", "HIST_a.mp4", 1000, "link_a", dict(HISTORY, fb_video_id="111"))
    b = Item("HIST_b", "json_b", "mp4_b", "HIST_b.mp4", 1000, "link_b",
             dict(HISTORY, fb_video_id="222", title="The Day Napoleon Lost at Waterloo", topic="Battle of Waterloo"))
    return [a, b]


def setup(monkeypatch, uploaded):
    fs = FakeSheets()
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)  # keep test output out of the run summary
    monkeypatch.setattr(run_youtube, "data_clients", lambda: (object(), fs))
    monkeypatch.setattr(run_youtube.gdrive, "find_folder_id", lambda d, n: "folder")
    monkeypatch.setattr(run_youtube.gdrive, "list_items",
                        lambda d, f, brands, skip, **k: [i for i in make_items() if i.json_id not in skip])
    monkeypatch.setattr(run_youtube.gdrive, "download", lambda d, fid, p: p)
    monkeypatch.setattr(run_youtube, "youtube_client", lambda s: object())

    def fake_upload(yt, path, out, privacy):
        uploaded.append((out["title"], privacy))
        return f"vid{len(uploaded)}"
    monkeypatch.setattr(run_youtube.youtube, "upload", fake_upload)
    return fs


def test_private_upload_picks_best_score_and_never_repeats(monkeypatch):
    uploaded = []
    fs = setup(monkeypatch, uploaded)
    args = ["--mode", "private", "--channel", "history", "--ignore-schedule", "--no-gemini"]
    run_youtube.main(args)
    assert uploaded == [("The Day Napoleon Lost at Waterloo", "private")]  # score 9 first
    rows = fs.store["YouTube"]
    status_col = sheet.COLUMNS.index("status")
    assert rows[1][status_col] == "UPLOADED"
    assert rows[1][sheet.COLUMNS.index("url")] == "https://youtube.com/shorts/vid1"
    run_youtube.main(args)
    run_youtube.main(args)
    assert len(uploaded) == 2  # second item once, then nothing left


def test_public_needs_approval(monkeypatch):
    uploaded = []
    setup(monkeypatch, uploaded)
    monkeypatch.delenv("YT_PUBLIC_APPROVED", raising=False)
    run_youtube.main(["--mode", "public", "--channel", "history", "--ignore-schedule", "--no-gemini"])
    assert uploaded[0][1] == "private"


def test_kit_mode_does_not_upload(monkeypatch):
    uploaded = []
    fs = setup(monkeypatch, uploaded)
    run_youtube.main(["--mode", "kit", "--channel", "history", "--ignore-schedule", "--no-gemini", "--limit", "2"])
    assert uploaded == []
    statuses = [r[sheet.COLUMNS.index("status")] for r in fs.store["YouTube"][1:]]
    assert statuses == ["KIT_READY", "KIT_READY"]
