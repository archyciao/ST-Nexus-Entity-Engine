"""Entity ID 的生成与校验。

文件功能：为所有 Entity 生成不含姓名或身份语义的稳定 ID，并校验引用中的
ID 是否符合 ``type_series`` 规则。

架构位置：本模块属于 Entity Core 基础能力。AI、导入器和业务处理器只能
请求创建 Entity，正式 ID 由受控系统流程调用本模块生成。

输入与运作方式：调用方提供已在 Type Registry 中确认的 Entity Type；本模块
组合当前毫秒时间和安全随机数生成 ULID，再返回 ``type_ULID``。既有 Entity
必须先由上层实体匹配流程查找，不能通过再次生成 ID 代替匹配。

本模块也为 Component 内需要独立定位的语义条目生成局部 ID。局部 ID 沿用
“对象种类 + ULID”的易辨识形式，但不是 Entity ID，不能进入 Entity Reference。

输出与边界：输出不可变 ID；本模块不查重数据库、不匹配既有 Entity，也不
负责显示名称。持久化层仍必须设置唯一约束，并在极低概率冲突时重新生成。
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Final


CROCKFORD_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
"""ULID 使用的 Crockford Base32 字符表，排除易混淆字符 I、L、O、U。"""

ENTITY_TYPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
)
"""Entity Type 的稳定格式；与 Type Registry 使用同一命名规则。"""

ENTITY_SERIES_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"
)
"""26 位 ULID 序列格式；第一位受 128 位取值范围限制，只能为 0 到 7。"""

ENTITY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<type>[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)_"
    r"(?P<series>[0-7][0-9A-HJKMNP-TV-Z]{25})$"
)
"""完整 ``type_series`` ID 格式。"""

LOCAL_ID_KINDS: Final[frozenset[str]] = frozenset(
    {
        "motivation",
        "preference",
        "objective",
        "relation_aspect",
        "location_state",
        "item_state",
        "organization_role",
        "organization_direction",
        "organization_state",
        "skill_mechanic",
        "skill_requirement",
        "skill_numeric_binding",
        "skill_stage",
        "skill_stage_context",
        "concept_rule",
        "stage_numeric_binding",
        "concept_stage",
        "concept_stage_context",
    }
)
"""当前允许由系统生成的 Component 局部标识类型。"""


def new_entity_id(entity_type: str) -> str:
    """为新 Entity 生成正式 ID。

    参数：
        entity_type：经过 Type Registry 确认的 Entity Type。

    返回：
        ``type_ULID`` 格式的不可变 ID。

    异常：
        ValueError：Entity Type 格式不合法。

    业务边界：
        本函数只用于确认需要创建新 Entity 之后。调用前必须先执行实体匹配，
        调用后仍由数据库唯一约束处理极低概率的冲突重试。
    """

    if ENTITY_TYPE_PATTERN.fullmatch(entity_type) is None:
        raise ValueError(f"Entity Type 格式不合法: {entity_type}")

    return f"{entity_type}_{_new_series()}"


def is_valid_entity_id(entity_id: str, expected_type: str | None = None) -> bool:
    """判断 ID 格式是否合法，并可校验前缀是否等于预期 Type。"""

    match = ENTITY_ID_PATTERN.fullmatch(entity_id)
    if match is None:
        return False
    return expected_type is None or match.group("type") == expected_type


def new_local_id(kind: str) -> str:
    """为已登记的 Component 局部对象生成稳定标识。

    局部 ID 只定位 Component 内的一条语义内容。调用方不得将返回值登记为
    Entity，也不得把它放入 Entity Reference。
    """

    if kind not in LOCAL_ID_KINDS:
        raise ValueError(f"不支持的 Component 局部 ID 类型: {kind}")
    return f"{kind}_{_new_series()}"


def is_valid_local_id(local_id: str, expected_kind: str | None = None) -> bool:
    """校验 Component 局部 ID，并确认其种类属于当前登记集合。"""

    match = ENTITY_ID_PATTERN.fullmatch(local_id)
    if match is None:
        return False
    kind = match.group("type")
    return kind in LOCAL_ID_KINDS and (
        expected_kind is None or kind == expected_kind
    )


def _new_series() -> str:
    """生成供 Entity ID 与局部 ID 共用的 26 位 ULID 序列。"""

    timestamp_ms = int(time.time() * 1000)
    # 时间部分便于按创建时间排序，但不承担业务时间或世界时间语义。
    random_bits = secrets.randbits(80)
    # 80 位安全随机数避免同一毫秒创建多个对象时发生冲突。
    return _encode_ulid(timestamp_ms, random_bits)


def _encode_ulid(timestamp_ms: int, random_bits: int) -> str:
    """把 48 位毫秒时间和 80 位随机数编码为 26 位 ULID。"""

    if not 0 <= timestamp_ms < 2**48:
        raise ValueError("ULID 时间戳超出 48 位范围。")
    if not 0 <= random_bits < 2**80:
        raise ValueError("ULID 随机数超出 80 位范围。")

    value = (timestamp_ms << 80) | random_bits
    characters = ["0"] * 26
    for index in range(25, -1, -1):
        characters[index] = CROCKFORD_ALPHABET[value & 31]
        value >>= 5
    return "".join(characters)
