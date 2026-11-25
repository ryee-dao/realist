import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("watchdog", ROOT / ".github/scripts/watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def run(created, conclusion="success", event="schedule", status="completed"):
    return {"created_at": created + "Z", "conclusion": conclusion, "event": event, "status": status}


class WatchdogTest(unittest.TestCase):
    def test_slots(self):
        self.assertEqual(wd.latest_slot("23 0,12,16,20 * * *", at("2026-11-25T21:00")), at("2026-11-25T20:23"))
        self.assertEqual(wd.latest_slot("23 0,12,16,20 * * *", at("2026-11-25T00:10")), at("2026-11-24T20:23"))
        self.assertEqual(wd.latest_slot("41 */2 * * *", at("2026-11-25T22:41")), at("2026-11-25T22:41"))
        self.assertEqual(wd.latest_slot("17 9 * * *", at("2026-11-25T09:00")), at("2026-11-24T09:17"))
        with self.assertRaises(ValueError):
            wd.latest_slot("0 0 * * 1", at("2026-11-25T09:00"))

    def test_every_watched_workflow_has_a_daily_schedule(self):
        for name in wd.WORKFLOWS:
            lines = wd.crons((ROOT / ".github/workflows" / name).read_text())
            self.assertTrue(lines, name)
            for line in lines:
                wd.latest_slot(line, at("2026-11-25T12:00"))

    def test_decide(self):
        slot = at("2026-11-25T20:41")
        old = run("2026-11-25T16:04:59")
        self.assertEqual(wd.decide([old], slot), "start")                                  # skipped
        self.assertEqual(wd.decide([run("2026-11-25T21:50:00"), old], slot), "ok")         # late but ran
        self.assertEqual(wd.decide([run("2026-11-25T20:45:00", None, status="queued"), old], slot), "wait")
        self.assertEqual(wd.decide([run("2026-11-25T20:45:00", "failure"), old], slot), "start")
        self.assertEqual(wd.decide([run("2026-11-25T20:4%d:00" % i, "failure") for i in (5, 6, 7)], slot), "stop")
        rebuild = run("2026-11-25T21:00:00", event="workflow_run")
        self.assertEqual(wd.decide([rebuild], slot, ("workflow_run",)), "start")           # a rebuild is not the harvest
        self.assertEqual(wd.decide([rebuild], slot), "ok")


if __name__ == "__main__":
    unittest.main()
