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
            "entities": run.get("final_network", []),
        }
        code = 0 if result["status"] == "completed" else 1
    except Exception as exc:
        result = {"status": "failed", "error": str(exc), "entities": [], "validation": {}}
        code = 1
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
