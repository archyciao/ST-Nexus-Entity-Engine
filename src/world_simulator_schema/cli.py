from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from .validator import ComponentValidator


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_input(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    value = (
        yaml.safe_load(text)
        if path.suffix.lower() in {".yaml", ".yml"}
        else json.loads(text)
    )
    if not isinstance(value, dict):
        raise ValueError("Component 输入必须是对象。")
    return value


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="world-schema",
        description="World Simulator Component Schema validator",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_default_root(),
        help="World_Simulator_Frame 项目根目录。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-registry", help="检查 Registry 与 Schema 一致性。")

    validate = commands.add_parser("validate", help="校验 Component 实例。")
    validate.add_argument("--component", required=True)
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--repair", action="store_true")
    validate.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    validator = ComponentValidator(args.root)

    if args.command == "check-registry":
        errors = validator.check_registry()
        print(
            json.dumps(
                {"valid": not errors, "errors": errors},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if not errors else 1

    try:
        instance = _load_input(args.input)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [
                        {"code": "PARSE_ERROR", "path": "", "message": str(exc)}
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    report = validator.validate(
        args.component,
        instance,
        repair=args.repair,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

    if args.output is not None and report.valid:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.instance, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())