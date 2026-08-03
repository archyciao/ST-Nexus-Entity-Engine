"""World Simulator Entity 与 Component Schema 基础。"""

from .entity_ids import (
    is_valid_entity_id,
    is_valid_local_id,
    new_entity_id,
    new_local_id,
)
from .entity_network import (
    EntityNetworkValidationReport,
    EntityNetworkValidator,
    rebuild_derived_indexes,
)
from .entity_validator import EntityValidationReport, EntityValidator
from .validator import ComponentValidator, ValidationReport

__all__ = [
    "ComponentValidator",
    "EntityNetworkValidationReport",
    "EntityNetworkValidator",
    "EntityValidationReport",
    "EntityValidator",
    "ValidationReport",
    "is_valid_entity_id",
    "is_valid_local_id",
    "new_entity_id",
    "new_local_id",
    "rebuild_derived_indexes",
]
__version__ = "0.1.0"
