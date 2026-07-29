"""Component 代码文件中文说明模板。

一、文件功能
本模板展示如何用中文解释 Python 文件，使不懂代码的项目负责人和后续 AI
能够先理解整体用途，再阅读具体类、函数和变量。

二、架构位置
本文件属于代码交接说明层，位于 docs/templates，不参与正式包加载。
正式运行行为以 src 中的代码、schemas 中的 JSON Schema 和 Registry 为准。

三、输入
- Component 名称和实例数据；
- 对应 JSON Schema 与 Component Registry 条目；
- 当前操作者角色、操作类型和目标字段路径。

四、整体运作方式
1. SchemaStore 加载 Schema 和 Registry，并检查两者是否一致；
2. CorrectionEngine 只处理确定性的字段格式、登记别名和唯一高置信拼写错误；
3. ComponentValidator 执行结构、版本和权限校验，生成稳定错误码；
4. 业务数据通过后，才交给 Resolver、Projector 或其他受控流程；
5. 任何歧义、冲突或高风险值都停止自动修正并返回问题。

五、输出
- 规范化后的候选 Component 数据；
- 自动纠正记录；
- 结构化错误列表；
- 是否允许进入下一处理阶段的校验结果。

六、本模块不负责
- 不判断世界行为是否合理；
- 不确认 Event 是否成立；
- 不直接写入数据库；
- 不模糊修改 Entity ID、Type、引用目标或自由文本。

七、主要组成与阅读顺序
建议先阅读文件级说明，再看类和函数 docstring，最后查看关键变量与业务代码块注释。
不需要从 import 开始逐行猜测。

对应正式模块：
- schema_store.py：加载 Schema 与 Registry，并检查两者一致性；
- correction.py：只修正安全、确定性的字段结构错误；
- validator.py：编排校验并返回稳定的结构化报告；
- schema_tools.py：解析 Schema 路径和复用结构；
- cli.py：把上述能力提供给人工命令行使用。

本文件是说明模板。新建正式模块时复制所需结构，并将文件名中的
“_说明模板”移除；若通用模板不能准确解释模块，应创建专用说明模板。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


DEFAULT_THRESHOLD: Final[float] = 0.88
"""字段名模糊匹配的默认最低置信度；高风险值不得使用该阈值自动改写。"""


@dataclass(frozen=True)
class ExampleIssue:
    """结构化问题示例。

    属性：
        code：稳定错误码，供程序和 AI 判断错误类别。
        path：问题所在的 JSON Pointer 路径。
        message：面向人和 AI 的中文说明，不作为程序分支条件。
    """

    code: str
    """稳定错误码。"""

    path: str
    """问题字段路径；根对象使用空字符串。"""

    message: str
    """中文问题说明。"""

    def to_dict(self) -> dict[str, str]:
        """转换为可序列化字典。

        返回：
            只包含稳定字段的字典，可直接写入 JSON 校验报告。
        """

        result = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        # result 是对外报告对象，不应加入仅供调试的临时字段。
        return result


def normalize_candidate(
    value: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[str, list[ExampleIssue]]:
    """演示“先安全规范化，再严格校验”的函数写法。

    参数：
        value：待处理的字段名；这里只演示字段名，不处理 Entity ID 或自由文本值。
        threshold：唯一候选必须达到的最低置信度。

    返回：
        二元组：规范化后的字段名，以及需要调用方处理的问题列表。

    异常：
        ValueError：阈值超出 0 到 1 的合法范围。

    业务边界：
        本函数只做确定性格式处理。模糊候选是否唯一、是否允许修改，
        仍需结合 Registry 和当前对象 Schema 判断。
    """

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold 必须位于 0 到 1 之间。")

    normalized_value = value.strip().lower().replace("-", "_")
    # normalized_value 只改变字段名格式，不得用于 Entity ID、Type 或自由文本。

    issues: list[ExampleIssue] = []
    # issues 收集结构化问题；不要用自然语言字符串代替稳定错误码。

    return normalized_value, issues


def main() -> int:
    """说明模板的最小人工演示入口。

    正式业务代码不应依赖本模板。返回 0 仅表示示例执行成功。
    """

    normalized, issues = normalize_candidate("Primary-Name")
    print({"normalized": normalized, "issues": [item.to_dict() for item in issues]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())