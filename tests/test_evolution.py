"""Fixture signals plus feedback that rolls a worse policy back and keeps a better one."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insight_host.app import Service, serve  # noqa: E402
from insight_host.aura_bridge import resolve_aura  # noqa: E402
from insight_host.github_ingest import issues_to_signals  # noqa: E402


def find_with(rows, *signal_ids):
    want = set(signal_ids)
    for row in rows or []:
        if want <= set(row.get("signal_ids") or []):
            return row
    raise AssertionError(f"no insight containing {signal_ids}")


class EvolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.aura = resolve_aura(ROOT)

    def make_service(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        service = Service(
            ROOT,
            root / "insight.sqlite",
            evo_log=root / "evo.jsonl",
            aura_bin=self.aura,
        )
        return service

    def test_worse_policy_rolls_back_and_better_one_is_kept(self):
        service = self.make_service()
        seed = service.bootstrap()
        self.assertEqual(seed["policy_name"], "seed")
        self.assertEqual(seed["policy_version"], 1)
        self.assertEqual(seed["knobs"]["min_support"], 1)
        self.assertFalse(seed["knobs"]["question_fake"])
        self.assertGreaterEqual(len(seed["insights"]), 5)

        install = find_with(seed["insights"], "q1")
        mascot = find_with(seed["insights"], "q2")
        errors = find_with(seed["insights"], "e1", "e2")
        worldline = find_with(seed["insights"], "w1", "w2")
        docs = find_with(seed["insights"], "d1", "d2", "d3")
        self.assertTrue(install["promote"])
        self.assertEqual(install["kind"], "real")
        self.assertGreater(install["confidence"], 0.9)

        denied = service.feedback(install["id"], "deny")
        self.assertEqual(denied["decision"], "BACK-OFF")
        self.assertEqual(denied["policy_version"], 1)
        self.assertEqual(denied["trials"], [])

        noted = service.feedback(errors["id"], "supplement", note="希望错误里带上源码片段")
        self.assertEqual(noted["decision"], "BACK-OFF")
        noted_row = find_with(noted["insights"], "e1")
        self.assertIn("希望错误里带上源码片段", noted_row["notes"])

        held_deny = service.feedback(mascot["id"], "deny", held_out=True)
        self.assertEqual(held_deny["decision"], "BACK-OFF")

        one_confirm = service.feedback(errors["id"], "confirm")
        self.assertEqual(one_confirm["decision"], "BACK-OFF")
        self.assertEqual(one_confirm["metrics"]["labeled"], 3)

        kept = service.feedback(worldline["id"], "confirm", held_out=True)
        trials = kept["trials"]
        self.assertEqual([row["arm"] for row in trials], ["overstrict", "stricter"])
        self.assertEqual(trials[0]["decision"], "ROLLBACK")
        self.assertEqual(trials[0]["via"], "infeasible")
        self.assertTrue(trials[0]["isolated"])
        self.assertAlmostEqual(trials[0]["agreement"], 0.5)
        self.assertEqual(trials[1]["decision"], "KEEP")
        self.assertEqual(trials[1]["via"], "atomic-batch")
        self.assertTrue(trials[1]["isolated"])
        self.assertAlmostEqual(trials[1]["agreement"], 1.0)
        self.assertEqual(kept["decision"], "KEEP")
        self.assertEqual(kept["policy_name"], "stricter")
        self.assertEqual(kept["policy_version"], 2)
        self.assertEqual(kept["knobs"]["min_support"], 2)
        self.assertTrue(kept["knobs"]["question_fake"])
        self.assertAlmostEqual(kept["metrics"]["base_agreement"], 0.5)
        self.assertAlmostEqual(kept["metrics"]["agreement"], 1.0)
        self.assertAlmostEqual(kept["metrics"]["base_heldout"], 0.5)
        self.assertAlmostEqual(kept["metrics"]["heldout"], 1.0)
        self.assertEqual(find_with(kept["forgotten"], "q1")["kind"], "fake")
        self.assertFalse(find_with(kept["forgotten"], "q2")["promote"])
        self.assertTrue(find_with(kept["insights"], "e1")["promote"])
        self.assertTrue(find_with(kept["insights"], "w1")["promote"])
        self.assertNotIn("c-q1", kept["memory"])
        source = service.store.current()["source"]
        self.assertIn('(define (policy-min-support) 2)', source)
        self.assertIn("(define (policy-question-fake?) #t)", source)

        again = service.feedback(docs["id"], "confirm")
        self.assertEqual(again["policy_version"], 2)
        self.assertEqual(again["policy_name"], "stricter")
        self.assertTrue(again["trials"])
        self.assertTrue(all(row["decision"] == "ROLLBACK" for row in again["trials"]))
        self.assertAlmostEqual(again["trials"][0]["agreement"], 0.4)

        restored = service.rollback(1)
        self.assertEqual(restored["decision"], "ROLLBACK")
        self.assertEqual(restored["policy_version"], 1)
        self.assertEqual(restored["policy_name"], "seed")
        self.assertTrue(find_with(restored["insights"], "q1")["promote"])
        self.assertIn("(define (policy-min-support) 1)", service.store.current()["source"])
        log = Path(service.evo_log).read_text(encoding="utf-8")
        self.assertIn('"decision":"KEEP"', log)
        self.assertIn('"decision":"ROLLBACK"', log)
        self.assertIn("infeasible: regression", log)
        self.assertIn("committed", log)
        self.assertIn("user-restore", log)

        lines = []
        lines.append("decision sequence:")
        lines.append("  deny install            BACK-OFF")
        lines.append("  supplement errors       BACK-OFF")
        lines.append("  deny mascot (held-out)  BACK-OFF")
        lines.append("  confirm errors          BACK-OFF")
        lines.append(
            "  confirm worldline       "
            f"{trials[0]['arm']} {trials[0]['decision']} "
            f"agreement={trials[0]['agreement']} ; "
            f"{trials[1]['arm']} {trials[1]['decision']} "
            f"via={trials[1]['via']} agreement={trials[1]['agreement']}"
        )
        lines.append(
            "  confirm docs            "
            + ", ".join(f"{row['arm']} {row['decision']}" for row in again["trials"])
            + f"  stay v{again['policy_version']} {again['policy_name']}"
        )
        lines.append(f"  rollback to v1          {restored['policy_name']} v{restored['policy_version']}")
        lines.append("evo log decisions:")
        for line in log.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("decision") in {"KEEP", "ROLLBACK", "SEED", "TRIAL", "SCORED"}:
                lines.append(
                    f"  {row.get('decision'):9} {row.get('target') or ''}  {row.get('reason') or ''}"
                )
        transcript = "\n".join(lines)
        sys.stderr.write("\n" + transcript + "\n")
        self.assertIn("overstrict ROLLBACK", transcript)
        self.assertIn("stricter KEEP", transcript)

    def test_http_smoke(self):
        service = self.make_service()
        service.bootstrap()
        httpd = serve(service, "127.0.0.1", 0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{port}"
            with urllib.request.urlopen(base + "/api/health", timeout=10) as resp:
                health = json.loads(resp.read().decode())
            self.assertTrue(health["ok"])
            self.assertEqual(health["aura"], "0.2.0")
            self.assertEqual(health["sandbox"], "off")
            with urllib.request.urlopen(base + "/api/insights", timeout=10) as resp:
                insights = json.loads(resp.read().decode())
            self.assertGreaterEqual(len(insights["insights"]), 4)
            with urllib.request.urlopen(base + "/", timeout=10) as resp:
                page = resp.read().decode()
            self.assertIn("需求洞察", page)
            self.assertIn("已晋升的假设", page)
            target = insights["insights"][0]["id"]
            req = urllib.request.Request(
                base + "/api/feedback",
                data=json.dumps({"insight_id": target, "verdict": "deny"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                denied = json.loads(resp.read().decode())
            self.assertEqual(denied["decision"], "BACK-OFF")
            with urllib.request.urlopen(base + "/app.js", timeout=10) as resp:
                script = resp.read().decode()
            self.assertIn("确认", script)
            self.assertIn("data-rollback", script)
        finally:
            httpd.shutdown()
            thread.join(timeout=3)

    def test_github_mapper_skips_pull_requests(self):
        signals = issues_to_signals(
            "cybrid-systems/aura",
            [
                {
                    "number": 7,
                    "title": "Need clearer sandbox preset grants",
                    "body": "docs",
                    "created_at": "2026-04-01T00:00:00Z",
                    "labels": [{"name": "documentation"}],
                    "comments": 2,
                    "reactions": {"total_count": 3},
                },
                {"number": 8, "title": "a pull request", "pull_request": {"url": "x"}},
            ],
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["id"], "gh-cybrid-systems-aura-7")
        self.assertEqual(signals[0]["reactions"], 3)
        self.assertEqual(signals[0]["comment_count"], 2)
        self.assertEqual(signals[0]["labels"], ["documentation"])

    def test_missing_github_token_is_an_error(self):
        service = self.make_service()
        service.bootstrap()
        import os

        old = os.environ.pop("GITHUB_TOKEN", None)
        old_gh = os.environ.pop("GH_TOKEN", None)
        try:
            with self.assertRaises(Exception):
                service.ingest_github(["cybrid-systems/aura"])
        finally:
            if old is not None:
                os.environ["GITHUB_TOKEN"] = old
            if old_gh is not None:
                os.environ["GH_TOKEN"] = old_gh


if __name__ == "__main__":
    unittest.main()
