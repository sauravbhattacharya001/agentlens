"""CLI config command – manage persistent AgentLens configuration.

Stores settings in ~/.agentlens.json so users don't need --endpoint / --api-key every call.

Usage:
    agentlens-cli config show
    agentlens-cli config set <key> <value>
    agentlens-cli config unset <key>
    agentlens-cli config reset
    agentlens-cli config path
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_PATH = Path.home() / ".agentlens.json"

KNOWN_KEYS = {
    "endpoint": "Backend URL (default: http://localhost:3000)",
    "api_key": "API key for authentication",
    "default_format": "Default output format: table, json, csv, markdown",
    "default_limit": "Default --limit for list commands (integer)",
    "color": "Enable colored output: true/false",
    "pager": "Pipe long output through a pager: true/false",
    "timeout": "HTTP request timeout in seconds (integer)",
}


def load_config() -> Dict[str, Any]:
    """Load config from disk, returning empty dict if missing/corrupt."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(cfg: Dict[str, Any]) -> None:
    """Persist config to disk."""
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def get_config_value(key: str) -> Optional[str]:
    """Get a single config value (used by other CLI modules)."""
    cfg = load_config()
    return cfg.get(key)


def apply_config_defaults(args: Any) -> Any:
    """Apply config-file defaults to parsed args when CLI flags are absent.

    Call this in main() after parse_args so config values act as fallbacks.
    """
    cfg = load_config()
    if not cfg:
        return args

    # endpoint
    if getattr(args, "endpoint", None) is None and "endpoint" in cfg:
        args.endpoint = cfg["endpoint"]

    # api_key
    if getattr(args, "api_key", None) is None and "api_key" in cfg:
        args.api_key = cfg["api_key"]

    return args


def _cmd_show(cfg: Dict[str, Any]) -> None:
    if not cfg:
        print("No configuration set. Use 'agentlens-cli config set <key> <value>' to configure.")
        print(f"\nConfig file: {CONFIG_PATH}")
        return
    print("Current configuration:\n")
    max_key = max(len(k) for k in cfg)
    for k, v in sorted(cfg.items()):
        display = "***" if k == "api_key" else v
        desc = KNOWN_KEYS.get(k, "")
        hint = f"  # {desc}" if desc else ""
        print(f"  {k:<{max_key}}  = {display}{hint}")
    print(f"\nConfig file: {CONFIG_PATH}")


def _cmd_set(cfg: Dict[str, Any], key: str, value: str) -> None:
    # Type coercion for known keys
    if key == "default_limit":
        try:
            value_store: Any = int(value)
        except ValueError:
            print(f"Error: '{key}' must be an integer.", file=sys.stderr)
            sys.exit(1)
    elif key == "timeout":
        try:
            value_store = int(value)
        except ValueError:
            print(f"Error: '{key}' must be an integer.", file=sys.stderr)
            sys.exit(1)
    elif key in ("color", "pager"):
        value_store = value.lower() in ("true", "1", "yes", "on")
    else:
        value_store = value

    cfg[key] = value_store
    save_config(cfg)

    display = "***" if key == "api_key" else value_store
    print(f"Set {key} = {display}")

    if key not in KNOWN_KEYS:
        print(f"Note: '{key}' is not a recognized key. Known keys:")
        for k, desc in KNOWN_KEYS.items():
            print(f"  {k:16s} {desc}")


def _cmd_unset(cfg: Dict[str, Any], key: str) -> None:
    if key not in cfg:
        print(f"Key '{key}' is not set.", file=sys.stderr)
        sys.exit(1)
    del cfg[key]
    save_config(cfg)
    print(f"Removed '{key}' from configuration.")


def _cmd_reset() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
        print("Configuration reset. All settings removed.")
    else:
        print("No configuration file to reset.")


def _cmd_path() -> None:
    exists = "exists" if CONFIG_PATH.exists() else "does not exist"
    print(f"{CONFIG_PATH}  ({exists})")


def cmd_config(args: Any) -> None:
    """Entry point for the config subcommand."""
    action = getattr(args, "config_action", None)

    if action is None or action == "show":
        _cmd_show(load_config())
    elif action == "set":
        cfg = load_config()
        _cmd_set(cfg, args.config_key, args.config_value)
    elif action == "unset":
        cfg = load_config()
        _cmd_unset(cfg, args.config_key)
    elif action == "reset":
        _cmd_reset()
    elif action == "path":
        _cmd_path()
    else:
        print(f"Unknown config action: {action}", file=sys.stderr)
        sys.exit(1)


def register_config_parser(subparsers: Any) -> None:
    """Register the 'config' subcommand with the main CLI parser."""
    p = subparsers.add_parser(
        "config",
        help="Manage persistent CLI configuration (~/.agentlens.json)",
    )
    config_sub = p.add_subparsers(dest="config_action")

    config_sub.add_parser("show", help="Show current configuration")

    sp = config_sub.add_parser("set", help="Set a configuration value")
    sp.add_argument("config_key", metavar="key", help="Configuration key")
    sp.add_argument("config_value", metavar="value", help="Configuration value")

    sp = config_sub.add_parser("unset", help="Remove a configuration value")
    sp.add_argument("config_key", metavar="key", help="Configuration key to remove")

    config_sub.add_parser("reset", help="Remove all configuration")
    config_sub.add_parser("path", help="Show configuration file path")
