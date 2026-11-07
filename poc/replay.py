"""Step-by-step replay of a run, from H's own session history.

Every agent step on H is an observation (the screenshot the agent saw), then a policy event (its short thought and
the action it chose). We download those screenshots (JPEG, next to the run) and pair each with its thought, its
action, and what our persona bridge did to that action (tremor jitter, keyboard-only block). The web UI plays
this back like a video with click markers and captions.

The same changes feed is polled during the run for live captions (`latest_thought`).
"""

from __future__ import annotations

import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

MAX_THOUGHT = 320
MAX_FRAME_WIDTH = 1280


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fetch_events(client, session_id: str, from_index: int = 0, max_pages: int = 20) -> tuple[list[dict], int]:
    """All events from from_index on (paged). Returns (events, next_index)."""
    events: list[dict] = []
    index = from_index
    for _ in range(max_pages):
        changes = client.sessions.get_session_changes(session_id, from_index=index, limit=200, wait_for_seconds=0,
                                                      request_options={"timeout_in_seconds": 20, "max_retries": 1})
        if changes is None:
            break
        data = changes.model_dump(mode="json")
        new = data.get("new_events") or []
        events.extend(new)
        index = (data.get("first_index") or index) + len(new)
        if len(new) < 200:
            break
    return events, index


def _clip(text: str | None, n: int = MAX_THOUGHT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def describe_action(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """A short label for the action, plus the fields the player draws (click point, keys)."""
    name = tool.removesuffix("_web")
    out: dict[str, Any] = {"tool": tool}
    element = args.get("element") or args.get("description") or args.get("target")
    if isinstance(args.get("x"), (int, float)) and isinstance(args.get("y"), (int, float)):
        out["x"], out["y"] = float(args["x"]), float(args["y"])  # normalized 0..1 on the screenshot
    if name in {"click", "double_click", "left_click", "right_click"}:
        out["kind"] = "click"
        out["label"] = f"Click {element}" if element else "Click"
    elif name in {"press_keys", "press_key", "key", "hotkey"}:
        keys = args.get("keys") or args.get("key") or []
        keys = keys if isinstance(keys, list) else [keys]
        out["kind"] = "keys"
        out["keys"] = [str(k) for k in keys][:6]
        out["label"] = "Press " + " + ".join(out["keys"]) if out["keys"] else "Press a key"
    elif name in {"write", "type", "write_at", "type_text", "input_text", "fill"}:
        out["kind"] = "type"
        text = str(args.get("text") or args.get("content") or "")
        out["text"] = _clip(text, 60)
        out["label"] = f"Type “{out['text']}”" + (f" in {element}" if element else "")
    elif name in {"scroll", "scroll_at", "scroll_page"}:
        out["kind"] = "scroll"
        direction = args.get("direction") or ("down" if (args.get("dy") or args.get("delta_y") or 1) > 0 else "up")
        out["label"] = f"Scroll {direction}"
    elif tool == "fill_credential":
        out["kind"] = "credential"
        out["label"] = f"Fill the stored {args.get('field', 'credential')} (value hidden from the agent)"
    elif tool == "answer":
        out["kind"] = "answer"
        ans = args.get("answer")
        out["label"] = "Final report" + (f": {_clip(ans if isinstance(ans, str) else str(ans), 120)}" if ans else "")
    else:
        out["kind"] = "other"
        out["label"] = name.replace("_", " ").capitalize() + (f" {element}" if element else "")
    return out


def latest_thought(events: list[dict]) -> dict[str, Any] | None:
    """Most recent agent thought + action, for live captions."""
    for e in reversed(events):
        d = e.get("data") or {}
        if e.get("type") == "AgentEvent" and d.get("kind") == "policy_event":
            tools = d.get("tool_reqs") or []
            action = describe_action(tools[0]["tool_name"], tools[0].get("args") or {}) if tools else None
            return {"thought": _clip(d.get("content") or d.get("reasoning_content"), 220),
                    "action": action["label"] if action else None}
    return None


def build_replay(events: list[dict], actions: list[dict], out_dir: Path, tag: str,
                 api_key: str | None = None, download: bool = True) -> dict[str, Any]:
    """Pair every agent step with the screenshot it was decided on. Downloads screenshots to frames/<tag>/."""
    from PIL import Image

    steps: list[dict[str, Any]] = []
    observation: dict | None = None
    t0 = None
    for e in events:
        d = e.get("data") or {}
        ts = _ts(e.get("timestamp"))
        if t0 is None and ts is not None:
            t0 = ts
        if e.get("type") != "AgentEvent":
            continue
        if d.get("kind") == "observation_event" and (d.get("image") or {}).get("source"):
            observation = {"src": d["image"]["source"], "url": (d.get("metadata") or {}).get("url"),
                           "title": (d.get("metadata") or {}).get("title"), "ts": ts}
        elif d.get("kind") == "policy_event":
            tools = d.get("tool_reqs") or []
            action = describe_action(tools[0]["tool_name"], tools[0].get("args") or {}) if tools else None
            steps.append({"ts": ts, "t": round((ts or 0) - (t0 or 0), 1) if ts and t0 else None,
                          "thought": _clip(d.get("content") or d.get("reasoning_content")),
                          "action": action, "obs": observation})
    # What our bridge did to each step's browser commands (between this step's decision and the next one).
    for i, s in enumerate(steps):
        start = s["ts"] or 0
        end = steps[i + 1]["ts"] if i + 1 < len(steps) and steps[i + 1]["ts"] else float("inf")
        mine = [a for a in actions if a.get("ts") and start - 0.5 <= a["ts"] < end - 0.5 and not a["cmd"].startswith("tool:")]
        effects: dict[str, Any] = {}
        if any(a.get("blocked") for a in mine):
            effects["blocked"] = True
        jit = next((a for a in mine if a.get("jitter")), None)
        if jit:
            effects["jitter"] = jit["jitter"]  # screenshot px
        if any(a.get("error") and not a.get("blocked") for a in mine):
            effects["error"] = True
        if effects:
            s["effects"] = effects

    frames_dir = out_dir / "frames" / tag
    sources: dict[str, int] = {}
    for s in steps:
        if s["obs"] and s["obs"]["src"] not in sources:
            sources[s["obs"]["src"]] = len(sources)
    frames: list[dict[str, Any] | None] = [None] * len(sources)

    def fetch(item: tuple[str, int]) -> None:
        src, i = item
        try:
            r = httpx.get(src, headers={"Authorization": f"Bearer {api_key or os.environ.get('HAI_API_KEY', '')}"},
                          timeout=30, follow_redirects=True)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            if img.width > MAX_FRAME_WIDTH:
                img = img.resize((MAX_FRAME_WIDTH, round(img.height * MAX_FRAME_WIDTH / img.width)))
            name = f"{i:03d}.jpg"
            img.save(frames_dir / name, quality=72)
            frames[i] = {"file": f"frames/{tag}/{name}", "w": img.width, "h": img.height}
        except Exception:
            frames[i] = None

    if download and sources:
        frames_dir.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(fetch, sources.items()))
    out_steps = []
    for s in steps:
        frame = sources.get(s["obs"]["src"]) if s["obs"] else None
        out_steps.append({k: v for k, v in {
            "t": s["t"], "thought": s["thought"], "action": s["action"], "effects": s.get("effects"),
            "frame": frame if frame is not None and frames[frame] else None,
            "url": s["obs"]["url"] if s["obs"] else None,
        }.items() if v is not None})
    return {"frames": [f or {} for f in frames], "steps": out_steps}
