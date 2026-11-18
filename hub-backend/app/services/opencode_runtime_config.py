"""Dependency-free configuration boundary for managed OpenCode subprocesses."""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys


def runner_data_root() -> str:
    """Match the Runner archive root without importing backend services."""
    explicit = str(os.environ.get("SODUWORK_DATA_ROOT") or "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(home, ".soduwork"),
                            "soduwork")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "soduwork")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"),
                        "soduwork")


def prepare_environment(env: dict, data_root: str | None = None) -> None:
    """Isolate managed state, retaining only a read-only legacy migration source."""
    # Pin the root before replacing XDG_DATA_HOME. The standalone ACP child must
    # use exactly the parent's database, including on Linux with a custom XDG root.
    root = Path(data_root or env.get("SODUWORK_OPENCODE_DATA_ROOT") or runner_data_root()).absolute()
    base = root / "agents" / "opencode"
    database = base / "data" / "opencode" / "opencode.db"
    names = {key.upper(): value for key, value in env.items()}
    previous = str(names.get("OPENCODE_DB") or "").strip()
    same_database = (os.path.isabs(previous) and
                     os.path.normcase(os.path.normpath(previous)) == os.path.normcase(os.path.normpath(database)))
    if not same_database:
        home = str((env.get("USERPROFILE") if os.name == "nt" else env.get("HOME")) or "").strip()
        home = home or os.path.expanduser("~")
        legacy = previous or str(Path(names.get("XDG_DATA_HOME") or Path(home) / ".local" / "share")
                                 / "opencode" / "opencode.db")
        env["SODUWORK_OPENCODE_LEGACY_DB"] = legacy
    env["SODUWORK_OPENCODE_DATA_ROOT"] = str(root)
    # Explicit native overrides bypass XDG_CONFIG_HOME. Managed turns must not
    # merge personal providers, plugins, or settings into their injected config.
    for key in list(env):
        if key.upper() in {"OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_DB",
                           "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"}:
            del env[key]
    for key, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                        ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache")):
        path = base / folder
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    env["OPENCODE_DB"] = str(database)


def config_error_message(message: str) -> str:
    """Known config parser diagnostics may contain config contents or credentials."""
    text = message.lower()
    if (any(marker in text for marker in (
            "configjsonerror", "configinvaliderror", "endoffileexpected",
            "--- jsonc input ---"))
            or re.search(r"\bconfig file at\b[^\r\n]*\bis not valid json(?:\(c\)|c)?(?=[:\s]|$)", text)):
        return ("OpenCode 执行组件配置无法解析，本轮尚未完成。"
                "请检查 SoduWork 的模型配置或修复执行组件；个人 OpenCode 配置保持不变。")
    return ""
