"""Start a scheduled workflow again when GitHub skipped its slot, or when its run for that slot failed.

GitHub drops or delays scheduled runs without notice. For each workflow listed below this reads the cron line from
the workflow file itself, finds the latest slot that is at least GRACE old, and looks at the runs made since then:

  a run succeeded, or one is still queued or running   -> nothing to do
  no run at all, or every run since the slot failed    -> start one (at most TRIES runs per slot)

Standard library only. Needs GITHUB_TOKEN (actions: write) and GITHUB_REPOSITORY; --dry-run only prints.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRACE = timedelta(minutes=30)     # how late a slot may be before it counts as skipped
TRIES = 3                         # runs per slot, counting GitHub's own
# file -> run events that do NOT count as that slot's run (a rebuild after a harvest is not the daily harvest)
WORKFLOWS = {"listings.yml": (), "photo-tags.yml": (), "pipeline.yml": ("workflow_run",)}
API = "https://api.github.com"


def _field(text, top):
    """One cron field (a number list, * or */n) as a sorted list of values below `top`."""
    if text == "*":
        return list(range(top))
    if m := re.fullmatch(r"\*/(\d+)", text):
        return list(range(0, top, int(m[1])))
    if re.fullmatch(r"\d+(,\d+)*", text):
        return sorted({int(v) for v in text.split(",") if int(v) < top})
    raise ValueError(f"cron field not understood: {text}")


def crons(workflow_text):
    return re.findall(r"^\s*-\s*cron:\s*[\"']([^\"']+)[\"']", workflow_text, re.M)


def latest_slot(cron, now):
    """The newest time this daily cron line fired at or before `now` (UTC)."""
    minute, hour, *rest = cron.split()
    if rest != ["*", "*", "*"]:
        raise ValueError(f"only daily schedules are watched: {cron}")
    for back in (0, 1):
        day = (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
        slots = [day + timedelta(hours=h, minutes=m) for h in _field(hour, 24) for m in _field(minute, 60)]
        if past := [s for s in slots if s <= now]:
            return max(past)
    raise ValueError(f"no slot found for {cron}")


def decide(runs, slot, ignore=()):
    """'ok', 'wait', 'start' or 'stop' for the runs (API objects) of one workflow, given its latest due slot."""
    since = [r for r in runs if r["event"] not in ignore
             and datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= slot - timedelta(minutes=1)]
    if any(r["status"] != "completed" for r in runs):
        return "wait"                       # anything queued or running will do the work, whatever started it
    if any(r["conclusion"] == "success" for r in since):
        return "ok"
    if len([r for r in since if r["conclusion"] != "skipped"]) >= TRIES:      # failed, timed out or cancelled
        return "stop"
    return "start"


def _call(path, token, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "realist-watchdog"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()
    return json.loads(raw) if raw else {}


def main(argv):
    dry = "--dry-run" in argv
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    now = datetime.now(timezone.utc)
    failed = False
    for name, ignore in WORKFLOWS.items():
        try:
            lines = crons(Path(".github/workflows", name).read_text())
            slot = max(latest_slot(c, now - GRACE) for c in lines)
            runs = _call(f"/repos/{repo}/actions/workflows/{name}/runs?per_page=30", token)["workflow_runs"]
            verdict = decide(runs, slot, ignore)
            print(f"{name}: slot {slot:%Y-%m-%d %H:%M} UTC -> {verdict}")
            if verdict == "start":
                print(f"::warning::{name} has no good run for its {slot:%H:%M} UTC slot; starting one.")
                if not dry:
                    _call(f"/repos/{repo}/actions/workflows/{name}/dispatches", token, {"ref": ref})
            elif verdict == "stop":
                print(f"::error::{name} failed {TRIES} times for its {slot:%H:%M} UTC slot; not retried until the next slot.")
                failed = True
        except Exception as err:            # one workflow's trouble must not stop the check of the others
            print(f"::error::Could not check {name}: {err}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
