"""HTTP API and the observe / feedback / evolve service."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from insight_host.aura_bridge import AURA_COMMIT, AuraBridge, AuraError
from insight_host.github_ingest import DEFAULT_REPOS, GitHubError, ingest, token_from_env
from insight_host.store import Store

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FIXTURES = ROOT / "fixtures" / "issues.json"

VERDICTS = {"confirm", "deny", "supplement"}


def load_fixtures() -> list[dict]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def cache_view(result: dict) -> dict:
    data = dict(result)
    for key in ("policy_source", "saved_version", "history_event"):
        data.pop(key, None)
    return data


class Service:
    def __init__(self, root: Path, db_path: Path, evo_log: Path | None = None, aura_bin: str | None = None):
        self.root = root
        self.store = Store(db_path)
        self.bridge = AuraBridge(root, aura_bin)
        self.evo_log = evo_log or Path(
            os.environ.get("AURA_EVO_LOG") or (db_path.parent / "evo.jsonl")
        )
        self.lock = threading.Lock()

    def bootstrap(self) -> dict:
        with self.lock:
            if not self.store.signals():
                self.store.replace_signals(load_fixtures())
            if self.store.current() is None:
                return self._observe_locked()
            return self.store.cache()

    def reset(self) -> dict:
        with self.lock:
            self.store.clear()
            self.store.replace_signals(load_fixtures())
            return self._observe_locked()

    def insights(self) -> dict:
        with self.lock:
            cached = self.store.cache()
            if cached:
                return cached
            return self._observe_locked()

    def policy_view(self) -> dict:
        with self.lock:
            current = self.store.current() or {
                "version": 0,
                "name": "",
                "source": "",
                "knobs": {},
            }
            return {
                "ok": True,
                "current": current,
                "versions": self.store.versions(),
                "events": self.store.events(),
                "aura": {"version": "0.2.0", "commit": AURA_COMMIT, "sandbox": "off"},
            }

    def audit(self) -> dict:
        with self.lock:
            return {"ok": True, "audit": self.store.audit()}

    def feedback(self, insight_id: str, verdict: str, note: str = "", held_out: bool = False) -> dict:
        if verdict not in VERDICTS:
            raise ValueError("verdict 必须是 confirm、deny 或 supplement")
        if not insight_id:
            raise ValueError("缺少 insight_id")
        note = (note or "")[:2000]
        with self.lock:
            self.store.add_feedback(insight_id, verdict, note, bool(held_out))
            return self._evolve_locked()

    def evolve(self) -> dict:
        with self.lock:
            return self._evolve_locked()

    def rollback(self, version: int) -> dict:
        with self.lock:
            source = self.store.source_for(int(version))
            if not source:
                raise ValueError(f"没有策略版本 {version}")
            result = self.bridge.run(
                self._payload("rollback", rollback_to=int(version), rollback_source=source),
                self.evo_log,
            )
            self._apply(result)
            return cache_view(result)

    def ingest_github(self, repos: list[str] | None = None) -> dict:
        token = token_from_env()
        signals = ingest(token, repos)
        with self.lock:
            count = self.store.upsert_signals(signals)
            viewed = self._observe_locked()
            viewed["ingested"] = count
            return viewed

    def _payload(self, op: str, **extra) -> dict:
        current = self.store.current()
        payload = {
            "op": op,
            "signals": self.store.signals(),
            "feedback": [
                {
                    "insight_id": item["insight_id"],
                    "verdict": item["verdict"],
                    "note": item["note"],
                    "held_out": item["held_out"],
                }
                for item in self.store.feedback()
            ],
            "policy_source": current["source"] if current else "",
            "policy_version": current["version"] if current else 0,
        }
        payload.update(extra)
        return payload

    def _apply(self, result: dict) -> None:
        if not result.get("ok"):
            raise AuraError(result.get("error") or "Aura 返回失败")
        saved = result.get("saved_version")
        if isinstance(saved, dict) and saved.get("source"):
            self.store.upsert_version(saved)
        event = result.get("history_event")
        if isinstance(event, dict):
            self.store.add_event(event)
        current_source = result.get("policy_source") or (self.store.current() or {}).get("source") or ""
        self.store.set_current(
            int(result.get("policy_version") or 0),
            result.get("policy_name") or "",
            current_source,
            result.get("knobs") or {},
        )
        self.store.set_cache(cache_view(result))
        self.store.add_audit(
            {
                "kind": "round",
                "decision": result.get("decision"),
                "gate": result.get("gate"),
                "reason": result.get("reason"),
                "version": result.get("policy_version"),
                "name": result.get("policy_name"),
                "publish_via": result.get("publish_via"),
            }
        )
        for trial in result.get("trials") or []:
            self.store.add_audit({"kind": "trial", **trial})

    def _observe_locked(self) -> dict:
        result = self.bridge.run(self._payload("observe"), self.evo_log)
        self._apply(result)
        return cache_view(result)

    def _evolve_locked(self) -> dict:
        result = self.bridge.run(self._payload("evolve"), self.evo_log)
        self._apply(result)
        return cache_view(result)


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            return

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _text(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON 必须是对象")
            return data

        def _static(self, name: str, content_type: str) -> None:
            path = WEB / name
            if not path.is_file():
                self._json(404, {"ok": False, "error": "找不到页面"})
                return
            self._text(200, path.read_bytes(), content_type)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/api/health":
                    self._json(
                        200,
                        {
                            "ok": True,
                            "aura": "0.2.0",
                            "commit": AURA_COMMIT,
                            "sandbox": "off",
                        },
                    )
                elif path == "/api/insights":
                    self._json(200, service.insights())
                elif path == "/api/policy":
                    self._json(200, service.policy_view())
                elif path == "/api/audit":
                    self._json(200, service.audit())
                elif path in ("/", "/index.html"):
                    self._static("index.html", "text/html; charset=utf-8")
                elif path == "/app.js":
                    self._static("app.js", "text/javascript; charset=utf-8")
                elif path == "/style.css":
                    self._static("style.css", "text/css; charset=utf-8")
                else:
                    self._json(404, {"ok": False, "error": "未知路径"})
            except (AuraError, ValueError, OSError) as exc:
                self._json(500, {"ok": False, "error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                body = self._read_json()
                if path == "/api/feedback":
                    result = service.feedback(
                        str(body.get("insight_id") or ""),
                        str(body.get("verdict") or ""),
                        str(body.get("note") or ""),
                        bool(body.get("held_out")),
                    )
                    self._json(200, result)
                elif path == "/api/rollback":
                    result = service.rollback(int(body.get("version")))
                    self._json(200, result)
                elif path == "/api/reset":
                    self._json(200, service.reset())
                elif path == "/api/evolve":
                    self._json(200, service.evolve())
                elif path == "/api/ingest/github":
                    repos = body.get("repos") or list(DEFAULT_REPOS)
                    if not isinstance(repos, list):
                        raise ValueError("repos 必须是数组")
                    self._json(200, service.ingest_github([str(item) for item in repos]))
                else:
                    self._json(404, {"ok": False, "error": "未知路径"})
            except GitHubError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except (AuraError, ValueError, OSError, TypeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})

    return Handler


def serve(service: Service, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(service))
    return httpd


def main() -> None:
    root = ROOT
    db = Path(os.environ.get("INSIGHT_DB") or (root / "data" / "insight.sqlite"))
    service = Service(root, db)
    service.bootstrap()
    host = os.environ.get("INSIGHT_HOST") or "127.0.0.1"
    port = int(os.environ.get("INSIGHT_PORT") or "8080")
    httpd = serve(service, host, port)
    print(f"需求洞察  http://{host}:{port}", flush=True)
    print("Aura v0.2.0  AURA_SANDBOX=off  示例数据已装入", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
