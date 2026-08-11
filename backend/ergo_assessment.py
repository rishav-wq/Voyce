"""ergo_assessment.py — turn a real KnowErgo assessment into carousel content.

Input is the JSON that the knowella-ml backend's `GET /video/result/<id>`
returns:
    { "status": "done",
      "data": [ {"Time(s)": int, "Persons": [ {"Label","Score","Angle",
                 "NIOSH","SNOOK","MAC","WISHA"} ]} ],
      "timelines": { "Person A": [ {"t", "NECK","TRUNK","RUARM","LUARM",
                 "RDARM","LDARM","RWRIST","LWRIST","RKNEE","LKNEE"} ] },
      "timeline": [...], "video_file": "...", "video_url": "..." }

Output is a `content` dict ready for carousel.render_carousel_pdf — every number
comes from the assessment (nothing invented). The per-body-part risk thresholds
match the customerweb postural-analysis UI so the carousel agrees with the app.
"""

from __future__ import annotations

# body part -> (angle keys, medium-risk °, high-risk °) — from customerweb
# postural-analysis (mediumThresh / highThresh). Angle keys map to the timeline.
_PARTS = [
    ("Neck",     ["NECK"],              20, 45),
    ("Trunk",    ["TRUNK"],             20, 60),
    ("Shoulder", ["RUARM", "LUARM"],    45, 90),
    ("Elbow",    ["RDARM", "LDARM"],    60, 100),
    ("Wrist",    ["RWRIST", "LWRIST"],  15, 30),
    ("Knee",     ["RKNEE", "LKNEE"],    30, 60),
]

# bodymap region -> the angle key(s) that drive it, with each key's thresholds
_REGION_KEYS = {
    "neck":      [("NECK", 20, 45)],
    "trunk":     [("TRUNK", 20, 60)],
    "left_arm":  [("LUARM", 45, 90), ("LDARM", 60, 100), ("LWRIST", 15, 30)],
    "right_arm": [("RUARM", 45, 90), ("RDARM", 60, 100), ("RWRIST", 15, 30)],
    "left_leg":  [("LKNEE", 30, 60)],
    "right_leg": [("RKNEE", 30, 60)],
}


def _reba_risk_label(score) -> str:
    if score is None:
        return ""
    if score <= 1:  return "Negligible"
    if score <= 3:  return "Low risk"
    if score <= 7:  return "Medium risk"
    if score <= 10: return "High risk"
    return "Very high risk"


def _pcts(values, med, high):
    """(% low, % medium, % high, peak) for one angle series, by |angle|."""
    vals = [abs(float(v)) for v in values if v is not None]
    if not vals:
        return None
    n = len(vals)
    lo = sum(1 for v in vals if v <= med)
    hi = sum(1 for v in vals if v > high)
    mid = n - lo - hi
    return (round(lo / n * 100), round(mid / n * 100), round(hi / n * 100), round(max(vals)))


def _rows_for_subject(result: dict) -> tuple[str, list]:
    """The worker to feature: the label with the most timeline rows (the actual
    subject — matches the backend's own `timeline` = richest-series choice)."""
    timelines = result.get("timelines") or {}
    if timelines:
        label = max(timelines, key=lambda k: len(timelines[k]))
        return label, timelines[label]
    return "Person A", result.get("timeline") or []


def _combined_series(rows, keys):
    """Per-row worst (max |angle|) across the given keys — one value per second."""
    out = []
    for r in rows:
        vs = [abs(float(r[k])) for k in keys if r.get(k) is not None]
        out.append(max(vs) if vs else None)
    return out


def _region_level(rows, keyspec) -> str:
    """Worst risk level across a region's driving angle keys."""
    worst = "low"
    order = {"low": 0, "medium": 1, "high": 2}
    for key, med, high in keyspec:
        p = _pcts([r.get(key) for r in rows], med, high)
        if not p:
            continue
        _, pmid, phi, _ = p
        lvl = "high" if phi >= 15 else ("medium" if (phi + pmid) >= 30 or pmid >= 25 else "low")
        if order[lvl] > order[worst]:
            worst = lvl
    return worst


def _overall_reba(result: dict, label: str):
    """Peak REBA (max of L/R side) for the subject across the clip."""
    peak, peak_sec = None, 0
    for entry in result.get("data") or []:
        for person in entry.get("Persons") or []:
            if person.get("Label") != label:
                continue
            reba = (person.get("Score") or {}).get("REBA") or {}
            vals = [reba.get("RSCORE"), reba.get("LSCORE")]
            vals = [v for v in vals if v is not None]
            if vals and (peak is None or max(vals) > peak):
                peak, peak_sec = max(vals), entry.get("Time(s)", 0)
    return peak, peak_sec


def assessment_to_carousel(result: dict, frame_image: str | None = None,
                           product: str = "KnowErgo") -> dict:
    """Build a carousel `content` dict from a KnowErgo assessment result.
    `frame_image`: filename of an annotated still (see extract_annotated_frame)
    for the hero photo slide; omit to skip it. Returns {} if the result has no
    usable timeline data."""
    if result.get("status") not in (None, "done"):
        return {}
    label, rows = _rows_for_subject(result)
    if not rows:
        return {}

    # per-body-part risk distribution
    parts = []
    for name, keys, med, high in _PARTS:
        p = _pcts(_combined_series(rows, keys), med, high)
        if p:
            lo, mid, hi, _peak = p
            parts.append({"name": name, "low": lo, "medium": mid, "high": hi})
    parts_sorted = sorted(parts, key=lambda d: d["high"], reverse=True)
    worst = parts_sorted[0] if parts_sorted else None

    # bodymap regions
    joints = {region: _region_level(rows, spec) for region, spec in _REGION_KEYS.items()}

    reba, peak_sec = _overall_reba(result, label)
    risk = _reba_risk_label(reba)
    score_line = f"REBA {reba}  ·  {risk.upper()}" if reba is not None else ""

    slides = []
    if frame_image:
        slides.append({
            "kind": "photo", "image": frame_image,
            "title": "This is what KnowErgo sees",
            "body": "Every joint tracked, every angle scored, live on the floor.",
        })
    slides.append({
        "kind": "riskbars", "title": "Where the task hurt most",
        "body": "Share of the clip each joint spent above its risk threshold.",
        "parts": parts_sorted[:6],
    })
    slides.append({
        "kind": "bodymap", "title": "The joint that drove the score",
        "joints": joints,
        "worst": (f"{worst['name']} was in the red {worst['high']}% of the clip — "
                  f"fix that first." if worst and worst["high"] else
                  "Risk was spread low across the body on this task."),
    })
    if worst and worst["high"]:
        slides.append({
            "kind": "stat", "stat": f"{worst['high']}%",
            "title": f"of the clip, the {worst['name'].lower()} was past its red line",
            "body": "Averaged over every second, both sides scored independently.",
        })

    headline = (f"This task scored REBA {reba} — {risk}" if reba is not None
                else "Here is exactly where the risk lived")
    return {
        "hook_slide": {"headline": headline,
                       "subtext": "Scored from one video. Here is where the risk lived."},
        "content_slides": slides,
        "cta_slide": {"headline": "Re-assess after the fix and prove the risk dropped",
                      "cta": f"{product} by Knowella"},
        # meta for the caller (which second to grab a frame from, etc.)
        "_meta": {"subject": label, "reba": reba, "peak_second": peak_sec,
                  "score_line": score_line, "worst": worst},
    }


def extract_annotated_frame(video_file: str, second: float, out_path: str) -> bool:
    """Grab a still from the KnowErgo *output* video (skeleton + scores already
    burned in) at `second`, for the hero photo slide. Returns True on success."""
    try:
        import cv2
        cap = cv2.VideoCapture(video_file)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(0, second) * fps))
        ok, frame = cap.read()
        cap.release()
        if ok:
            cv2.imwrite(out_path, frame)
        return bool(ok)
    except Exception:
        return False
