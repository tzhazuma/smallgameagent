# fusion-harness

三方融合框架：**smallgameagent × game-agent-harness**（Python 主框架）。

保留我们（smallgameagent）的 L2 多 provider + L1 可微调 VLM + 自适应开关 + 规则热更新；
融合 gah 的确定性执行、记忆、治理、阶段策略、跨 run 学习。

## 架构

```
L2  cloud planning   : api_client 多 provider (mimo/kimi/qwen) → StrategySpec
L1  local VLM        : vlm_observe_adapter + vlm_policy 自适应 + 5090 /describe 微调模型
L0  rule engine      : runtime_rules.json 热更新 (rule_update)
─────────────────────────────────────────────
确定性执行层 (gah 思想, Python 移植) :
  options.py       9 个 Option (observe_settle/probe_tap/probe_joystick/
                  explore_sector_sweep/approach_target/dwell_at_target/
                  recover_reverse/verify_completion) + compile → primitives
  intent_gate.py   15 条安全规则 (版本新鲜度/风险/目标/控制映射/路点)
  failure.py       12 失败码分类 (NO_PATH/STUCK/OSCILLATING/TARGET_STALE...)
  recovery.py      恢复阶梯 (每失败码有序恢复步骤, 目标指纹保护)
  phase_strategy.py 阶段锁定 + StrategySpec 状态机解释器 (30+ 谓词)
记忆层 :
  dsg.py           事件溯源动态场景图 (实体/关系/事件流)
  experience.py    经验卡 (匹配打分检索 + shadow 约束)
  cdg.py           因果决策图 (repeated_no_effect 边标记)
  registry.py      策略晋升 (candidate→known-pass)
治理层 :
  governance.py    多游戏验证 (canary/frozen/regression 分组)
主循环 :
  fusion_agent.py  决策 → IntentGate → Option 执行 → ExpectedEffect 验证 → 失败恢复 → 记忆
```

## 快速开始

```bash
cd fusion-harness
python -m pytest tests/ -q          # 6 个核心单测
python -c "
import sys; sys.path.insert(0, '.')
from fusion import FusionAgent, FusionConfig
agent = FusionAgent(FusionConfig(game_id='demo'))
print(agent.run())
"
```

## 与 L2/L1/L0 对接

`FusionConfig` 注入：
- `plan_strategy(brief)` — L2 云端规划（返回 StrategySpec dict）
- `vlm_observe(prompt, image)` — L1 VLM（5090 /describe）
- `rule_step(state)` — L0 规则快速路径
- `update_rule(request)` — 规则热更新
- `execute_primitive(primitive, controls)` — 浏览器动作执行
- `observe_world()` — 探针世界快照

## 三方对比（Phase 3 摘要）

| 游戏 | A 我们 | B gah-mimo | 结论 |
|---|---|---|---|
| tiles-survive | 9 gp | **156 gp (通关)** | gah 确定性引擎碾压 |
| kingshot-94766 | 7 gp | 3 gp | 我们 Cocos 探针更成熟 |
| whiteout-12ab | 10 gp | 3 gp | 同上 |

融合方向：我们的 Cocos 探针适配 + gah 的确定性/记忆/治理 → 双赢。详见 `results/three-way-comparison.md`。

## 模块文件

```
fusion/
  __init__.py       公共 API
  options.py        Option 目录
  intent_gate.py    安全闸门
  failure.py        失败分类
  recovery.py       恢复阶梯
  phase_strategy.py 阶段策略 + 状态机
  dsg.py            动态场景图
  experience.py     经验记忆
  cdg.py            因果决策图
  registry.py       策略注册
  governance.py     多游戏验证
  fusion_agent.py   主循环
```
