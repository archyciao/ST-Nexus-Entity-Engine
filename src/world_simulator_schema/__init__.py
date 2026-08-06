"""World Simulator Entity 与 Component Schema 基础。"""

from .entity_ids import (
    is_valid_entity_id,
    is_valid_local_id,
    new_entity_id,
    new_local_id,
)
from .entity_extraction import (
    ENTITY_DISCOVERY_SYSTEM_PROMPT,
    ENTITY_ENRICHMENT_SYSTEM_PROMPTS,
    EntityEnrichmentJob,
    plan_entity_enrichment_jobs,
)
from .entity_network import (
    EntityNetworkValidationReport,
    EntityNetworkValidator,
    rebuild_derived_indexes,
)
from .entity_validator import EntityValidationReport, EntityValidator
from .event_context import build_event_recall_pack
from .stage_context import (
    StageContextError,
    StageIssue,
    build_stage_context,
    validate_stage_framework,
)
from .validator import ComponentValidator, ValidationReport

__all__ = [
    "ComponentValidator",
    "ENTITY_DISCOVERY_SYSTEM_PROMPT",
    "ENTITY_ENRICHMENT_SYSTEM_PROMPTS",
    "EntityEnrichmentJob",
    "EntityNetworkValidationReport",
    "EntityNetworkValidator",
    "EntityValidationReport",
    "EntityValidator",
    "StageContextError",
    "StageIssue",
    "ValidationReport",
    "build_stage_context",
    "build_event_recall_pack",
    "is_valid_entity_id",
    "is_valid_local_id",
    "new_entity_id",
    "new_local_id",
    "plan_entity_enrichment_jobs",
    "rebuild_derived_indexes",
    "validate_stage_framework",
]
__version__ = "0.1.0"
