"""fusion-harness: Python 三方融合框架。

融合 smallgameagent（L2 多 provider / L1 可微调 VLM / L0 热更新规则）与
game-agent-harness（确定性执行 / 记忆 / 治理 / 阶段策略）的优势。

分层：
  L2  cloud planning   : multi-provider API (mimo/kimi/qwen) + normalizer
  L1  local VLM        : /describe 微调模型 (5090) + vlm_policy 自适应开关
  L0  rule engine      : runtime_rules.json 热更新 (rule_update)
  Deterministic layer  : options / intent_gate / failure / recovery (gah 思想)
  Memory layer         : dsg / experience / cdg / registry (跨 run 学习)
  Governance           : multi-game validation
"""

__version__ = "0.1.0"

from .options import (
    Option,
    OptionCatalog,
    compile_option,
    OPTION_NAMES,
)
from .intent_gate import IntentGate, IntentVerdict
from .failure import classify_execution_failure, FAILURE_CODES
from .recovery import RecoveryLadder
from .phase_strategy import (
    PhaseStrategy,
    StrategySpec,
    StrategyMachineRuntime,
    parse_strategy_spec,
)
from .dsg import DynamicSceneGraph, project_world_to_graph
from .experience import ExperienceMemory, ExperienceCard
from .cdg import CausalDecisionGraph
from .registry import StrategyRegistry
from .governance import MultiGameValidator
from .fusion_agent import FusionAgent, FusionConfig

__all__ = [
    "Option", "OptionCatalog", "compile_option", "OPTION_NAMES",
    "IntentGate", "IntentVerdict",
    "classify_execution_failure", "FAILURE_CODES",
    "RecoveryLadder",
    "PhaseStrategy", "StrategySpec", "StrategyMachineRuntime", "parse_strategy_spec",
    "DynamicSceneGraph", "project_world_to_graph",
    "ExperienceMemory", "ExperienceCard",
    "CausalDecisionGraph",
    "StrategyRegistry",
    "MultiGameValidator",
    "FusionAgent", "FusionConfig",
]
