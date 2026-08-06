"""Skill/Concept 阶段体系校验与最小上下文投影。

本模块只处理确定性的阶段结构、MUV 区间与召回裁剪。它不读取 Tavern，
不修改 Host 当前值，也不判断某个语义阶段是否应该变化。``semantic`` 和
``hybrid`` 模式中的正式阶段仍由 Host 的受控状态负责；本模块只按已确认
阶段生成给 AI 的最小资料包。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class StageIssue:
    """阶段体系的一项稳定错误。"""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


class StageContextError(ValueError):
    """无法安全解析当前阶段或召回范围。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def stage_id_field(data: dict[str, Any]) -> str:
    """识别 Concept StageFramework 或 SkillProgression 的阶段 ID 字段。"""

    stages = data.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict):
                if "stage_id" in stage:
                    return "stage_id"
                if "skill_stage_id" in stage:
                    return "skill_stage_id"
    return "stage_id" if "framework_summary" in data else "skill_stage_id"


def validate_stage_framework(data: dict[str, Any]) -> list[StageIssue]:
    """检查局部 ID、MUV 绑定和区间结构。

    JSON Schema 负责字段形状；这里补充跨数组约束。首版只允许一个数值绑定
    直接决定 ``numeric_derived`` 阶段，多变量公式留给 Tavern 适配专项。
    ``hybrid`` 区间可以重叠，但仍必须引用已声明 Binding。
    """

    issues: list[StageIssue] = []
    evaluation = data.get("stage_evaluation")
    stages = data.get("stages")
    if not isinstance(evaluation, dict) or not isinstance(stages, list):
        return issues

    mode = evaluation.get("mode")
    bindings = [
        item
        for item in evaluation.get("numeric_bindings", [])
        if isinstance(item, dict) and isinstance(item.get("numeric_binding_id"), str)
    ]
    binding_ids = {item["numeric_binding_id"] for item in bindings}
    if mode == "semantic" and bindings:
        issues.append(
            StageIssue(
                "SEMANTIC_STAGE_HAS_NUMERIC_BINDING",
                "/stage_evaluation/numeric_bindings",
                "semantic 模式不应声明 MUV Binding。",
            )
        )
    if mode in {"numeric_derived", "hybrid"} and not bindings:
        issues.append(
            StageIssue(
                "NUMERIC_STAGE_BINDING_REQUIRED",
                "/stage_evaluation/numeric_bindings",
                f"{mode} 模式至少需要一个数值 Binding。",
            )
        )
    if mode == "numeric_derived" and len(bindings) != 1:
        issues.append(
            StageIssue(
                "NUMERIC_STAGE_SINGLE_BINDING_REQUIRED",
                "/stage_evaluation/numeric_bindings",
                "首版 numeric_derived 只允许一个数值 Binding；多变量公式尚未定义。",
            )
        )

    ranges_by_binding: dict[str, list[tuple[float, float, int]]] = {}
    context_ids: set[str] = set()
    identifier_field = stage_id_field(data)
    for stage_index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        for guidance_index, guidance in enumerate(stage.get("numeric_guidance", [])):
            if not isinstance(guidance, dict):
                continue
            binding_id = guidance.get("numeric_binding_id")
            path = f"/stages/{stage_index}/numeric_guidance/{guidance_index}"
            if binding_id not in binding_ids:
                issues.append(
                    StageIssue(
                        "UNKNOWN_NUMERIC_BINDING",
                        f"{path}/numeric_binding_id",
                        "阶段区间引用了未声明的 numeric_binding_id。",
                    )
                )
                continue
            lower = guidance.get("min_inclusive")
            upper = guidance.get("max_exclusive")
            if not isinstance(lower, (int, float)) or not isinstance(upper, (int, float)):
                continue
            if lower >= upper:
                issues.append(
                    StageIssue(
                        "INVALID_STAGE_RANGE",
                        path,
                        "阶段区间必须满足 min_inclusive < max_exclusive。",
                    )
                )
                continue
            ranges_by_binding.setdefault(str(binding_id), []).append(
                (float(lower), float(upper), stage_index)
            )
        for block_index, block in enumerate(stage.get("context_blocks", [])):
            if not isinstance(block, dict):
                continue
            context_id = block.get("stage_context_id")
            if not isinstance(context_id, str):
                continue
            if context_id in context_ids:
                issues.append(
                    StageIssue(
                        "DUPLICATE_STAGE_CONTEXT_ID",
                        f"/stages/{stage_index}/context_blocks/{block_index}/stage_context_id",
                        "同一阶段体系中的 stage_context_id 必须唯一。",
                    )
                )
            context_ids.add(context_id)

    if mode == "semantic":
        for stage_index, stage in enumerate(stages):
            if isinstance(stage, dict) and stage.get("numeric_guidance"):
                issues.append(
                    StageIssue(
                        "SEMANTIC_STAGE_HAS_NUMERIC_GUIDANCE",
                        f"/stages/{stage_index}/numeric_guidance",
                        "semantic 模式不应声明 MUV 区间。",
                    )
                )

    if mode == "numeric_derived" and len(bindings) == 1:
        binding_id = bindings[0]["numeric_binding_id"]
        ranges = sorted(ranges_by_binding.get(binding_id, []))
        if len(ranges) != len(stages):
            issues.append(
                StageIssue(
                    "NUMERIC_STAGE_RANGE_REQUIRED",
                    "/stages",
                    "numeric_derived 模式要求每个阶段恰好声明一个当前 Binding 区间。",
                )
            )
        for prior, current in zip(ranges, ranges[1:]):
            if current[0] < prior[1]:
                issues.append(
                    StageIssue(
                        "OVERLAPPING_STAGE_RANGE",
                        f"/stages/{current[2]}/numeric_guidance",
                        "numeric_derived 阶段区间不能重叠。",
                    )
                )
            elif current[0] > prior[1]:
                issues.append(
                    StageIssue(
                        "GAPPED_STAGE_RANGE",
                        f"/stages/{current[2]}/numeric_guidance",
                        "numeric_derived 阶段区间之间不能存在未声明空档。",
                    )
                )

    # 让静态检查明确使用了阶段 ID 字段；重复 ID 由 Registry 规则负责。
    del identifier_field
    return issues


def build_stage_context(
    data: dict[str, Any],
    *,
    recall_mode: str = "current_stage",
    purpose: str | None = None,
    explicit_stage_id: str | None = None,
    numeric_values: dict[str, float] | None = None,
    candidate_stage_ids: Iterable[str] = (),
    include_numeric: bool = False,
) -> dict[str, Any]:
    """从完整框架生成普通回合、阶段检查或管理总览资料包。"""

    issues = validate_stage_framework(data)
    if issues:
        first = issues[0]
        raise StageContextError(first.code, first.message)
    if recall_mode not in {"current_stage", "transition_check", "framework_overview"}:
        raise StageContextError("INVALID_RECALL_MODE", f"未知召回模式：{recall_mode}")
    if recall_mode == "framework_overview":
        return {"recall_mode": recall_mode, "framework": data}

    evaluation = data["stage_evaluation"]
    mode = evaluation["mode"]
    stages = [item for item in data["stages"] if isinstance(item, dict)]
    id_field = stage_id_field(data)
    stages_by_id = {str(item.get(id_field)): item for item in stages}
    numeric_values = numeric_values or {}
    numeric_candidates = _numeric_stage_candidates(stages, numeric_values)

    if mode == "numeric_derived":
        if len(numeric_candidates) != 1:
            raise StageContextError(
                "NUMERIC_STAGE_UNRESOLVED",
                "当前 MUV 值没有唯一对应阶段；不会退化为加载全部阶段。",
            )
        current_id = numeric_candidates[0]
    else:
        current_id = explicit_stage_id
        if current_id not in stages_by_id:
            raise StageContextError(
                "EXPLICIT_STAGE_REQUIRED",
                f"{mode} 模式需要 Host 提供有效的显式阶段。",
            )

    selected_ids = [current_id]
    if recall_mode == "transition_check":
        selected_ids.extend(numeric_candidates if mode == "hybrid" else [])
        selected_ids.extend(str(item) for item in candidate_stage_ids)
    selected_ids = list(dict.fromkeys(item for item in selected_ids if item in stages_by_id))

    package: dict[str, Any] = {
        "recall_mode": recall_mode,
        "evaluation_mode": mode,
        "framework_summary": data.get("framework_summary") or data.get("progression_summary"),
        "current_stage_id": current_id,
        "stages": [
            _stage_view(
                stages_by_id[item],
                id_field=id_field,
                purpose=purpose,
                transition=recall_mode == "transition_check",
            )
            for item in selected_ids
        ],
    }
    if include_numeric:
        package["numeric_values"] = {
            key: value for key, value in numeric_values.items() if isinstance(value, (int, float))
        }
    return package


def _numeric_stage_candidates(
    stages: list[dict[str, Any]], numeric_values: dict[str, float]
) -> list[str]:
    id_field = stage_id_field({"stages": stages})
    matches: list[str] = []
    for stage in stages:
        for guidance in stage.get("numeric_guidance", []):
            if not isinstance(guidance, dict):
                continue
            binding_id = guidance.get("numeric_binding_id")
            value = numeric_values.get(str(binding_id))
            if not isinstance(value, (int, float)):
                continue
            if guidance["min_inclusive"] <= value < guidance["max_exclusive"]:
                matches.append(str(stage[id_field]))
                break
    return list(dict.fromkeys(matches))


def _stage_view(
    stage: dict[str, Any], *, id_field: str, purpose: str | None, transition: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "stage_id": stage[id_field],
        "name": stage["name"],
        "description": stage["description"],
    }
    blocks = [
        block
        for block in stage.get("context_blocks", [])
        if isinstance(block, dict) and purpose is not None and block.get("purpose") == purpose
    ]
    if blocks:
        result["context_blocks"] = blocks
    if transition:
        for field in ("entry_guidance", "exit_guidance"):
            if field in stage:
                result[field] = stage[field]
    return result
