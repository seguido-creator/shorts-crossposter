from datetime import datetime, timezone

from crossposter import metadata, originality
from crossposter.run_youtube import due_now, load_config

CFG = load_config()
HIST = CFG["channels"]["history"]

# Real sidecar shape from the Published folder (Throttle Decoded sample).
THROTTLE = {
    "page": "US", "brand": "Throttle Decoded", "kind": "explainer",
    "title": "Your Truck's Analog Needle Isn't Connected to a Cable Anymore",
    "topic": "Analog Speedometer Motor Glitch", "type": "fact", "series": "",
    "caption": "Modern analog gauges aren't mechanical cables anymore—they're fully computerized motors that can glitch out. \U0001F697 Always check recall databases if your instrument cluster starts acting erratic.\\n\\nHas your speedometer ever frozen up on you?\\n\\n#TruckMaintenance #CarSafety #AutoRepair #VehicleTips",
    "hashtags": ["#CarEngineering", "#TruckLife", "#FSeries", "#AutoTech"],
    "sources": [], "fact_checked": False, "fb_video_id": "28765304013125148",
}
HISTORY = {
    "page": "HIST", "brand": "History Decoded", "kind": "photo_reel",
    "title": "While Rome Burned, China Was Inventing Paper",
    "topic": "Cai Lun paper invention vs Great Fire of Rome",
    "caption": "In 64 AD Rome burned for six days. Around the same era, Han dynasty China was refining paper.\\n\\nWhich invention changed more lives?\\n\\n#History #AncientRome #AncientChina",
    "hashtags": ["#History", "#AncientRome", "#AncientChina", "#WorldHistory", "#Extra"],
    "sources": ["Wikipedia: Great Fire of Rome", "Wikipedia: Cai Lun"],
    "fact_checked": True, "fb_video_id": "111",
}


def test_metadata_throttle_fallback_title_and_description():
    out = metadata.build(THROTTLE, CFG["channels"]["throttle"], use_gemini=False)
    assert out["title"] == THROTTLE["title"]
    assert len(out["title"]) <= 100
    d = out["description"]
    assert "\\n" not in d and "—" not in d
    assert d.count("#TruckMaintenance") == 0  # old caption hashtag line rebuilt
    assert d.rstrip().endswith("#AutoTech")
    assert "#Shorts" in d and "AI voice" in d and "Pixabay" in d
    assert sum(len(t) + 1 for t in out["tags"]) <= 500
    assert out["category_id"] == "2" and out["synthetic_media"] is True


def test_metadata_history_sources_and_hashtag_cap():
    out = metadata.build(HISTORY, HIST, use_gemini=False)
    d = out["description"]
    assert "Sources: Wikipedia: Great Fire of Rome; Wikipedia: Cai Lun" in d
    tag_line = d.strip().split("\n")[-1]
    assert tag_line.split()[0] == "#Shorts" and len(tag_line.split()) == 5
    assert out["category_id"] == "27"


def test_title_limits_and_bait():
    long = "A " * 80
    assert len(metadata.fit_title(long)) <= 100
    assert metadata.is_bait("You WON'T BELIEVE This SHOCKING Car")
    assert metadata.is_bait("Watch till the end for the twist")
    assert not metadata.is_bait("Why Rome's Roads Still Work Today")
    assert not metadata.valid_title("Bad <title>")


def test_originality_gate():
    assert originality.is_original(HISTORY, CFG["allowed_kinds"])[0]
    assert not originality.is_original({**HISTORY, "source": "pack"}, CFG["allowed_kinds"])[0]
    assert not originality.is_original({**HISTORY, "kind": "pack_reel"}, CFG["allowed_kinds"])[0]
    assert not originality.is_original({**HISTORY, "folder": "/3000+ DOGS REELS BUNDLE"}, CFG["allowed_kinds"])[0]
    rows = [{"status": "UPLOADED", "topic": "Cai Lun paper invention vs Great Fire of Rome", "title": "x"}]
    assert originality.too_similar(HISTORY, rows)[0]
    rows = [{"status": "UPLOADED", "topic": "Napoleon height myth", "title": "Was Napoleon short?"}]
    assert not originality.too_similar(HISTORY, rows)[0]
    assert originality.overused_openers(["Why A", "Why B", "How C"]) == ["why"]


def test_schedule():
    # 23:00 UTC on 21 Oct 2026 = 19:00 New York (EDT): first slot (18:30) passed.
    now = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
    assert due_now(HIST, [], now)[0]
    done = [{"status": "UPLOADED", "logged_at_utc": "2026-10-21T22:35:00+00:00"}]
    assert not due_now(HIST, done, now)[0]  # 1/1 done for slot 1
    later = datetime(2026, 10, 22, 0, 45, tzinfo=timezone.utc)  # 20:45 NY, slot 2 passed
    assert due_now(HIST, done, later)[0]
    morning = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)  # 10:00 NY
    assert not due_now(HIST, [], morning)[0]


def test_kit_mode_catches_up_missed_slot():
    # 01:00 UTC on 22 Oct = 21:00 New York: both slots passed, nothing done yet.
    now = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)
    ok, _, n = due_now(HIST, [], now, kit=True)
    assert ok and n == 2
    ok, _, n = due_now(HIST, [], now, kit=False)
    assert ok and n == 1
    recent = [{"status": "KIT_READY", "logged_at_utc": "2026-10-22T00:40:00+00:00"}]
    ok, _, n = due_now(HIST, recent, now, kit=True)
    assert ok and n == 1  # no 60-minute gap needed for kits


def test_unrelated_sources_are_dropped():
    lincoln = {
        "title": "Abraham Lincoln and Charles Darwin Were Born on the Exact Same Day",
        "topic": "Lincoln and Darwin Birthday Overlap",
        "caption": "On February 12, 1809, Abraham Lincoln and Charles Darwin were born hours apart.",
        "sources": [
            "https://www.britannica.com/biography/Abraham-Lincoln",
            "https://www.smithsonianmag.com/history/how-lincoln-and-darwin-shaped-the-modern-world-43397263/",
            "https://en.wikipedia.org/wiki/Donald_Trump_and_fascism",
            "https://doi.org/10.1016/j.jaa.2026.101767",
        ],
    }
    kept = metadata.relevant_sources(lincoln)
    assert not any("Trump" in s for s in kept) and len(kept) == 3
    oreo = {"title": "What Do the Titanic, Oreos, and Imperial China Have in Common?",
            "caption": "China's imperial dynasties fell, Nabisco sold its first Oreos",
            "sources": ["https://en.wikipedia.org/wiki/Puyi", "https://en.wikipedia.org/wiki/Oreo",
                        "https://en.wikipedia.org/wiki/History_of_climate_change_science"]}
    kept = metadata.relevant_sources(oreo)
    assert kept == ["https://en.wikipedia.org/wiki/Puyi", "https://en.wikipedia.org/wiki/Oreo"]
