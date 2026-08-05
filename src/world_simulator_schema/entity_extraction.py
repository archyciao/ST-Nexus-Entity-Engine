"""World Entity 的两阶段 AI 提取任务规划。

第一次调用只发现跨类型候选和变化范围；固定脚本再按发生实际变化的 Type，
为每个 Type 最多建立一个批量细化任务。模块只生成任务，不调用模型、不生成
正式 ID，也不把候选写入数据库。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable


ENTITY_DISCOVERY_SYSTEM_PROMPT = """
从本批原文发现新增或确有变化的 Character、Location、Item、Organization、Skill、
Concept 与 Character Relation。只判断对象身份、Type、证据、实际 Event 挂接依据和
哪些资料区块需要细化；不要生成正式 ID，不展开完整字段，不重判 Event，不生成 Memory。
相同对象只返回一次。仅提及、计划前往或背景知识不等于实际参与。Description 只写
“它是什么、核心作用、最有辨识力的稳定特征”，不塞入当前位置、持有人、当前状态和流水。

只输出 JSON：
{"entities":[{"entity_key":"type:主要名称","type":"character | location | item | organization | skill | concept","primary_name":"新候选主要名称","aliases_add":[],"description":"一句稳定检索摘要","evidence_refs":[],"event_link_evidence":[{"source_ref":"消息书签","quote":"实际参与的原文短引","roles":[]}],"changed_sections":["profile | environment | atmosphere | characteristic | state | placement | structure | culture | strategy | objective | mechanics | requirement | progression | definition | rule | applicability | reference"]}],"relations":[{"participant_keys":["character:甲","character:乙"],"description":"当前关系摘要","evidence_refs":[],"aspects":[],"directional_states":[]}]}
""".strip()


_COMMON_ENRICHMENT_RULES = """
只细化 payload.candidates 中列出的同一 Type 候选。新候选填写有证据的初始必需资料；
既有候选只返回 changed_sections 对应的稀疏补丁。省略表示不变。带 state_key、entry_key、
objective_key、stage_key、binding_key 或 context_key 的数组按这些语义键增量合并，不要求
重发整个旧数组；若要结束一项状态，保留原 key 并将 status 改为 inactive。不要生成正式
Entity ID、Component 局部 ID、反向 Index、Event、Memory 或未经原文支持的默认值。
所有引用只使用 payload 中已存在或本批已发现的 type:主要名称候选键。只输出 JSON：
{"entities":[{"entity_key":"type:主要名称","domain_data_patch":{}}]}
""".strip()


ENTITY_ENRICHMENT_SYSTEM_PROMPTS: dict[str, str] = {
    "character": f"""{_COMMON_ENRICHMENT_RULES}

本任务返回 character_data_patch，不返回 domain_data_patch。可用字段：profile、state、
personality、motivations、preferences、objectives、inventory、skills、current_location_key。
Intent、Action、Event 和 Process 不写入 Character。inventory 只表示 Item 放置候选，
Item 自身 CurrentPlacement 才是权威。Skill 当前阶段使用 stage_state：framework_key、
evaluation_mode、可选 current_stage_key，以及 muv_values 中的 binding_key/value；不要把
公共阶段定义复制进 Character。""".strip(),
    "location": f"""{_COMMON_ENRICHMENT_RULES}

domain_data_patch 可含：
profile={{location_kind,primary_functions,scale_description,spatial_characteristics}}；
environment={{terrain_and_landform,climate_tendencies,natural_resources,fixed_facilities,persistent_conditions}}；
atmosphere={{atmosphere_summary,sensory_features,cultural_impressions}}；
states=[{{state_key,kind,description,status}}]；parent_key；related_concepts。
Profile/Environment/Atmosphere 保存稳定资料，State 保存当前有效变化。计划前往不是当前地点，
人物和物品清单由脚本反向索引，不写入 Location。""".strip(),
    "item": f"""{_COMMON_ENRICHMENT_RULES}

domain_data_patch 可含：
profile={{item_kind,instance_mode,primary_functions,materials,form_description}}；
characteristic={{appearance_and_sensory,craftsmanship,typical_behavior,symbolic_meanings}}；
states=[{{state_key,kind,description,status}}]；stack 可含 quantity={{amount,unit}}；
container={{capacity_description,allowed_contents,forbidden_contents,stable_capabilities,access_requirements}}；
placement={{target_key,role,detail}}；related_concepts。detail 只写相对目标的当前放置细节。
每个 Item 最多一个直接 Placement；目标 Item
必须真是容器。人物 Inventory、地点 Containment 和容器 Contents 由脚本重建。所有权不是
放置关系，不能从“拿着”直接推断“拥有”。""".strip(),
    "organization": f"""{_COMMON_ENRICHMENT_RULES}

domain_data_patch 可含：
profile={{organization_kind,public_role,operating_scope,continuity_basis}}；
structure={{governance_model,roles:[{{role_key,name,description}}]}}；
culture={{summary,core_values,norms,taboos,behavioral_style}}；
strategy={{long_term_directions:[{{direction_key,description}}],decision_style}}；
objectives=[{{objective_key,description,horizon,related_entity_keys}}]；
states=[{{state_key,kind,description,status}}]；parent_key；related_concepts。
成员、领导者、领地、资产和 Process 进度不复制进 Organization；这些连接另走 Relation、
Reference 或 Index。临时人群不是持续组织。""".strip(),
    "skill": f"""{_COMMON_ENRICHMENT_RULES}

domain_data_patch 可含：
definition={{skill_kind,domain,primary_capabilities,form_description}}；
mechanics=[{{entry_key,kind,description}}]；
characteristic={{style_summary,sensory_signatures,tactical_tendencies,distinguishing_features}}；
requirements=[{{entry_key,kind,description,requirement_role}}]；progression；related_concepts。
progression 使用 summary、ordered、evaluation={{mode,numeric_bindings:[{{binding_key,provider,
variable_key,value_source}}]}} 和 stages=[{{stage_key,name,order,description,entry_guidance,
exit_guidance,numeric_guidance:[{{binding_key,min_inclusive,max_exclusive}}],context_blocks:
[{{context_key,purpose,content}}]}}]。Skill 只保存公共定义和 MUV 映射；角色熟练度、当前阶段
和当前 MUV 属于 Character SkillReference。普通动作和个人心得不自动建成公共 Skill。""".strip(),
    "concept": f"""{_COMMON_ENRICHMENT_RULES}

domain_data_patch 可含：
definition={{concept_kind,definition,epistemic_status,operational_role,scope_summary}}；
rules=[{{rule_key,kind,statement,applicability,consequence,status}}]；applicability=
{{applies_to_entity_types,conditions,exclusions,exceptions,prerequisites}}；stage_framework；
related_concepts。stage_framework 与 Skill progression 使用同样的 summary、ordered、evaluation、
stages、numeric_guidance 和 context_blocks 候选键结构。只为可稳定复用的世界机制、制度、
社会文化、分类阶段、理论、历史背景或术语建立 Concept；人物观点和争议理论必须保留认识
地位。Host 当前 stage 与 MUV 不写入 Concept。""".strip(),
}


@dataclass(frozen=True)
class EntityEnrichmentJob:
    """同一 Type 的一次批量细化任务。"""

    entity_type: str
    system_prompt: str
    payload: dict[str, Any]


def plan_entity_enrichment_jobs(
    discovery_plan: dict[str, Any],
    *,
    source_messages: Iterable[dict[str, Any]],
    existing_previews: Iterable[dict[str, Any]] = (),
) -> list[EntityEnrichmentJob]:
    """按实际变化 Type 生成任务，并只携带候选引用到的原文片段。

    新候选即使 ``changed_sections`` 为空也需要细化；既有候选只有明确变化区块
    才进入第二阶段。每个 Type 最多一个任务，避免逐对象重复调用。
    """

    messages = {
        str(item.get("ref")): deepcopy(item)
        for item in source_messages
        if isinstance(item, dict) and item.get("ref")
    }
    previews = {
        str(item.get("entity_key")): deepcopy(item)
        for item in existing_previews
        if isinstance(item, dict) and item.get("entity_key")
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in discovery_plan.get("entities", []):
        if not isinstance(candidate, dict):
            continue
        entity_type = candidate.get("type")
        if entity_type not in ENTITY_ENRICHMENT_SYSTEM_PROMPTS:
            continue
        key = str(candidate.get("entity_key", ""))
        is_new = key not in previews
        changed = candidate.get("changed_sections")
        if not is_new and not (isinstance(changed, list) and changed):
            continue
        grouped.setdefault(str(entity_type), []).append(deepcopy(candidate))

    jobs: list[EntityEnrichmentJob] = []
    for entity_type in sorted(grouped):
        candidates = grouped[entity_type]
        refs = {
            str(ref)
            for candidate in candidates
            for ref in candidate.get("evidence_refs", [])
            if str(ref) in messages
        }
        refs.update(
            str(link.get("source_ref"))
            for candidate in candidates
            for link in candidate.get("event_link_evidence", [])
            if isinstance(link, dict) and str(link.get("source_ref")) in messages
        )
        jobs.append(
            EntityEnrichmentJob(
                entity_type=entity_type,
                system_prompt=ENTITY_ENRICHMENT_SYSTEM_PROMPTS[entity_type],
                payload={
                    "entity_type": entity_type,
                    "candidates": candidates,
                    "source_messages": [messages[ref] for ref in messages if ref in refs],
                    "existing_previews": [
                        previews[key]
                        for key in previews
                        if any(key == str(item.get("entity_key")) for item in candidates)
                    ],
                },
            )
        )
    return jobs
