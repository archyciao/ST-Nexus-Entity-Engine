"""SillyTavern 批量任务启动器。

这里只负责把宿主参数交给正式 ``airp_extraction_v2_probe.run_probe``，并额外写一份
便于 Node 服务导入的 JSON 结果；不复制提取提示词、Schema 或提交规则。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import airp_extraction_v2_probe as extraction
from world_simulator_schema.extraction.cache import atomic_json


def diagnostics(run):
    result = []
    for batch in run.get("batches", []):
        for task_name, task in batch.get("tasks", {}).items():
            errors = []
            last_attempt = (task.get("attempts") or [{}])[-1]
            code = last_attempt.get("api", {}).get("error_code") or ("output_format" if last_attempt.get("parse_error") else "validation")
            if not task.get("ok"):
                errors = [error for attempt in task.get("attempts", [])[-1:] for error in attempt.get("validation_errors", [])]
                if not errors:
                    errors = [task.get("fatal_error") or "任务未通过校验"]
            for message in errors:
                result.append({"batch": batch["batch_number"], "task": task_name, "severity": "error", "code": code, "message": str(message)})
            for message in task.get("warnings", []):
                result.append({"batch": batch["batch_number"], "task": task_name, "severity": "warning", "message": str(message)})
        for error in batch.get("boundary_errors", []):
            result.append({"batch": batch["batch_number"], "task": "boundary", "severity": "error", "message": str(error)})
        for message in batch.get("script_warnings", []):
            result.append({"batch": batch["batch_number"], "task": "commit", "severity": "warning", "message": str(message)})
        for error in batch.get("network_validation", {}).get("errors", []):
            result.append({"batch": batch["batch_number"], "task": "commit", "severity": "error", "message": str(error)})
    return result


def main(argv: list[str] | None = None) -> int:
    wrapper = argparse.ArgumentParser(add_help=False)
    wrapper.add_argument("--result-json", required=True)
    known, remaining = wrapper.parse_known_args(argv)
    result_path = Path(known.result_json).resolve()
    try:
        run = extraction.run_probe(extraction.parse_args(remaining))
        result = {
            "status": run.get("status"),
            "completed_at": run.get("completed_at"),
            "checkpoint_round_end": run.get("checkpoint_round_end", 0),
            "validation": run.get("final_network_validation", {}),
            "state": run.get("checkpoint_state", {}),
            "diagnostics": diagnostics(run),
            "entities": run.get("final_network", []),
        }
        failures = [item for item in result["diagnostics"] if item.get("severity") == "error"]
        result["error"] = "；".join(item["message"] for item in failures)[:8000]
        code = 0 if result["status"] == "completed" else 1
    except Exception as exc:
        result = {"status": "failed", "error": str(exc), "entities": [], "validation": {}}
        code = 1
    result_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(result_path, result)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
