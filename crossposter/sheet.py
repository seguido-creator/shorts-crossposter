"""Logging to the 'YouTube' tab and reading the 'Performance' tab of Reels Queue."""
import re
from datetime import datetime, timezone

YT_TAB = "YouTube"
PERF_TAB = "Performance"

COLUMNS = [
    "logged_at_utc", "channel", "page", "brand", "drive_file", "drive_json_id",
    "drive_mp4_id", "fb_video_id", "mode", "status", "youtube_video_id", "url",
    "title", "description", "tags", "topic", "notes",
]

# Statuses that mean "this item is handled, never pick it again".
FINAL_STATUSES = {"UPLOADED", "KIT_READY", "UPLOADING", "SKIPPED_SIMILAR", "SKIPPED_NOT_ORIGINAL"}
COUNTS_TOWARD_DAILY = {"UPLOADED", "KIT_READY", "UPLOADING"}
MAX_FAILS = 3


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


LAST_COL = _col_letter(len(COLUMNS))


class Log:
    def __init__(self, sheets, sheet_id: str):
        self.sheets = sheets
        self.sheet_id = sheet_id
        self.rows = []  # list of dicts

    def ensure_tab(self):
        meta = self.sheets.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
        if YT_TAB not in titles:
            self.sheets.spreadsheets().batchUpdate(
                spreadsheetId=self.sheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": YT_TAB}}}]},
            ).execute()
            self.sheets.spreadsheets().values().update(
                spreadsheetId=self.sheet_id,
                range=f"{YT_TAB}!A1:{LAST_COL}1",
                valueInputOption="RAW",
                body={"values": [COLUMNS]},
            ).execute()

    def load(self):
        res = self.sheets.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"{YT_TAB}!A1:{LAST_COL}"
        ).execute()
        values = res.get("values", [])
        self.rows = []
        for i, row in enumerate(values[1:], start=2):
            d = {c: (row[j] if j < len(row) else "") for j, c in enumerate(COLUMNS)}
            d["_row"] = i
            self.rows.append(d)
        return self.rows

    # ---- queries -------------------------------------------------------
    def handled_json_ids(self) -> set:
        """JSON ids never to pick again (final status or too many failures)."""
        fails = {}
        done = set()
        for r in self.rows:
            jid = r["drive_json_id"]
            if r["status"] in FINAL_STATUSES:
                done.add(jid)
            elif r["status"].startswith("FAILED"):
                fails[jid] = fails.get(jid, 0) + 1
        done |= {j for j, n in fails.items() if n >= MAX_FAILS}
        return done

    def channel_rows(self, channel: str) -> list:
        return [r for r in self.rows if r["channel"] == channel]

    # ---- writes --------------------------------------------------------
    def append(self, record: dict) -> int:
        record = {**record, "logged_at_utc": record.get("logged_at_utc") or now_utc()}
        row = [str(record.get(c, ""))[:45000] for c in COLUMNS]
        res = self.sheets.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{YT_TAB}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        rng = res.get("updates", {}).get("updatedRange", "")
        m = re.search(r"![A-Z]+(\d+)", rng)
        row_no = int(m.group(1)) if m else 0
        record["_row"] = row_no
        self.rows.append(record)
        return row_no

    def update(self, row_no: int, changes: dict):
        if not row_no:
            return
        for col, val in changes.items():
            idx = COLUMNS.index(col) + 1
            self.sheets.spreadsheets().values().update(
                spreadsheetId=self.sheet_id,
                range=f"{YT_TAB}!{_col_letter(idx)}{row_no}",
                valueInputOption="RAW",
                body={"values": [[str(val)[:45000]]]},
            ).execute()
        for r in self.rows:
            if r.get("_row") == row_no:
                r.update(changes)


def performance_scores(sheets, sheet_id: str) -> dict:
    """Returns {post_id: score} from the Performance tab (empty if none)."""
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{PERF_TAB}!A1:L"
        ).execute()
    except Exception:  # tab missing or unreadable: just skip ranking
        return {}
    values = res.get("values", [])
    if not values:
        return {}
    head = [h.strip().lower() for h in values[0]]
    try:
        i_id, i_score = head.index("post_id"), head.index("score")
    except ValueError:
        return {}
    out = {}
    for row in values[1:]:
        if len(row) > max(i_id, i_score):
            try:
                out[str(row[i_id]).strip()] = float(row[i_score])
            except ValueError:
                pass
    return out
