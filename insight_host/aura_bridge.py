"""Invoke the pinned Aura binary on aura/insight.aura.

Stdout of the helper install script is only the binary path. This bridge
forces AURA_SANDBOX=off: on v0.2.0 a restricted sandbox grants mutate on
workspace 0 only, so evo:trial's child workspace cannot set-code a candidate
policy. That requirement is documented in std/evo-core.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

LIST_KEYS = {
    "insights",
    "forgotten",
    "evidence",
    "signal_ids",
    "labels",
    "comments",
    "trials",
    "audit",
    "memory",
    "notes",
    "history",
    "specs",
}

AURA_COMMIT = "cf7353373ea276bc03e8364592814382ebd62539"


class AuraError(RuntimeError):
    pass


def restore_lists(value):
    """Aura #5160: json-encode writes () as null. Restore known list fields."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in LIST_KEYS and item is None:
                out[key] = []
            else:
                out[key] = restore_lists(item)
        return out
    if isinstance(value, list):
        return [restore_lists(item) for item in value]
    return value


def resolve_aura(root: Path) -> str:
    env = os.environ.get("AURA_BIN")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    local = root / ".tools" / "aura" / "bin" / "aura"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    script = root / "scripts" / "aura-path.sh"
    proc = subprocess.run(
        ["bash", "-c", f'source "{script}" && install_aura "{root}" "0.2.0"'],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AuraError(proc.stderr.strip() or "无法安装 Aura v0.2.0")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise AuraError("Aura 安装脚本没有返回路径")
    return lines[-1]


class AuraBridge:
    def __init__(self, root: Path, aura_bin: str | None = None):
        self.root = root
        self.aura_bin = aura_bin or resolve_aura(root)
        self.program = root / "aura" / "insight.aura"

    def run(self, payload: dict, evo_log: Path) -> dict:
        evo_log.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="insight-") as tmp:
            folder = Path(tmp)
            src = folder / "in.json"
            dst = folder / "out.json"
            src.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            env = os.environ.copy()
            env["AURA_SANDBOX"] = "off"
            env["AURA_EVO_LOG"] = str(evo_log)
            env["INSIGHT_IN"] = str(src)
            env["INSIGHT_OUT"] = str(dst)
            proc = subprocess.run(
                [self.aura_bin, "--quiet", "--preset", "dev", str(self.program)],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "aura 退出").strip()
                raise AuraError(detail[-4000:])
            raw = dst.read_text(encoding="utf-8") if dst.is_file() else proc.stdout
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AuraError(f"Aura 输出不是 JSON：{raw[:500]}") from exc
            if not isinstance(data, dict):
                raise AuraError("Aura 输出不是对象")
            return restore_lists(data)
