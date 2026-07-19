# smallgameagent 实验报告（第二轮扩充版）

> 2026-07-18。配套：`EXPERIMENT_PLAN.md`（方案）、`EXPERIMENT_RESULTS.md`（过程数据）。
> 本轮重点：塔防 Tap 策略、StrategyMemory 读回验证、Critic A/B、本地 VLM 闭环。

## 0. 一页结论

- **Tap 策略**：新增 `_strategy_tap_guide()` 驱动类型，让 00461 塔防从 composite 0.000 提升到 **0.150**（activity 0→1.0）。Agent 现在能走到 guide 目标并点击交互。
- **StrategyMemory 读回**：记忆读回将 composite 从 0.150 翻倍到 **0.300**——高成功率 tap 模式直接绕过 rule_engine 的 move-then-tap 两步流程，世界模型违规从 3 降到 0。
- **Critic A/B**：在确定性 tap-guide 策略下 Critic 无增益（composite 相同），额外 4 条总线消息带来 ~5% 墙钟开销。Critic 的价值应在非确定性场景（API LLM）中评估。
- **本地 VLM 闭环**：`vlm-local` 模式已注册，gemma-4-E4B 通过 `LMStudioClient` 接入决策循环。每帧 ~11s（含图像编码+推理），20 步约 220s。VLM 输出解析率取决于 max_tokens 是否足够覆盖思考链。
- **多 Agent 矩阵**：rule / multi / multi-bus / multi-bus-memory 四模式在 tap-guide 下均可运行，composite 0.150；multi-bus 每轮 15 条总线消息，墙钟 +6%。
- **本地 VLM 基准**：gemma-4-E4B 3/3 struct 解析成功，~55 tok/s，~4.8s/帧；Qwen3.5-9B 2/3，~45 tok/s；Qwen3.5-4B 0/3（思考链占满）。
- **云端 API**：mimo-v2.5 视觉 struct 3/3 解析（22–38s/帧）；kimi-k2.6 0/3（思考链截断）；kimi-k2.7-code 文本 2.2s。
- **代码质量**：`ruff` 全绿；`pytest` 671 passed / 6 failed（历史数据集缺失）。

## 1. 环境

| 项 | 结论 |
|---|---|
| 浏览器 | Playwright Chromium + `--use-gl=angle --use-angle=gl --ignore-gpu-blocklist --disable-gpu-sandbox` |
| 云端 LLM | `opencode.ai/zen/go/v1`；kimi 模型省略 temperature |
| 本地 VLM | `llama.cpp` CUDA12 + q4_0 KV-cache；gemma-4-E4B / Qwen3.5-4B/9B |
| 游戏 | SSD_00461P01 塔防，probe `ready=true` |

## 2. 塔防 Tap 策略（实验 B）

**问题**：00461 塔防需要点击放置/升级塔，旧 rule engine 只发 move，composite=0。

**修复**：
- `src/engine/rules.py` 新增 `_strategy_tap_guide()`：当 Hero 与 guide 目标世界距离 < arrival 阈值时发 `tap`（点击目标 screenPosition 映射到 CSS 坐标），否则发 `move` 靠近。
- `configs/game_profiles.py` 00461 driver_type 改为 `tap-guide`，新增 `design_resolution` / `viewport` 字段用于坐标映射。
- `src/experiments/game_env.py` rubric 更新：`tap` 动作不再计为 stall。

**结果**（30 步/模式）：

| 模式 | composite | activity | move | tap | stall | 墙钟 |
|---|---|---|---|---|---|---|
| rule | 0.150 | 1.000 | 5 | 24 | 0 | 25.2s |
| multi | 0.150 | 1.000 | 6 | 23 | 0 | 24.3s |
| multi-bus | 0.150 | 1.000 | 18 | 11 | 0 | 29.0s |
| multi-bus-memory | 0.150 | 1.000 | 18 | 11 | 0 | 30.5s |

rule/multi 模式 tap 占比更高（24/30 vs 11/30），因为 multi-bus 的 Verifier 触发 re-decide 时回退到 move。

## 3. StrategyMemory 读回验证（实验 A）

**问题**：`StrategyMemory.lookup()` 从未在决策时调用——只写不读。

**修复**：
- `src/agent/roles/decision_analyst.py` 的 `reason()` 中，在 rule_engine 之前插入 `StrategyMemory.lookup()`：如果记忆中有 ≥2 次尝试且成功率 ≥0.6 的模式，直接采用。
- `src/agent/decision_makers/bus_multi_maker.py` 传 `strategy_memory` 给 DecisionAnalyst。

**实验设计**：Phase 1 写入 → Phase 2 读回 → 对照组无记忆。

| 阶段 | composite | memory_hits | wm_violations | 主要决策来源 |
|---|---|---|---|---|
| phase1_write | 0.150 | 92 | 3 | strategy_memory:23, rule_engine:7 |
| **phase2_read** | **0.300** | 116 | **0** | strategy_memory:29, rule_engine:1 |
| control_no_memory | 0.150 | 0 | 3 | rule_engine:30 |

**关键发现**：记忆读回将 composite 翻倍（0.150→0.300），原因是世界模型违规从 3 降到 0——记忆中的高成功率 tap 模式直接绕过了 rule_engine 的 move-then-tap 两步流程，减少了不必要的移动导致的 stale 标记。Phase 2 中 29/30 步由记忆决策，仅 1 步回退到 rule_engine。

## 4. Critic 反馈循环 A/B（实验 D）

**问题**：multi-bus 的 Critic 角色会触发 re-decide，但从未量化其效果。

**修复**：`bus_multi_maker.py` 支持通过 config 传入 `max_rounds`。

| 配置 | composite | bus_messages | critic_invocations | 墙钟 |
|---|---|---|---|---|
| max_rounds=1 (无 Critic) | 0.150 | 11 | 2 | 28.3s |
| max_rounds=2 (有 Critic) | 0.150 | 15 | 2 | 29.7s |

**结论**：Critic 在确定性 tap-guide 策略下无增益——Verifier 触发 re-decide 后 DecisionAnalyst 仍返回相同动作。额外 4 条总线消息带来 ~5% 墙钟开销。Critic 的价值应在非确定性场景（API LLM 决策、多策略竞争）中评估。

## 5. 本地 VLM 在线决策闭环（实验 C）

**问题**：`LMStudioClient` 是死代码，本地 VLM 从未接入 agent 决策循环。

**修复**：
- 新建 `src/agent/decision_makers/vlm_local_maker.py`，注册 `vlm-local` 模式。
- `LMStudioClient.extract_content()` 修复：当 `content` 为空时回退到 `reasoning_content`。
- `hybrid_agent.py` 的 `_VALID_MODES` 加入 `vlm-local`。

**实验**：gemma-4-E4B-it-Q4_K_M（RTX 5060 CUDA12 + q4_0 KV-cache），20 步。

| 模式 | composite | activity | move | tap | stall | wm_viol | 墙钟 |
|---|---|---|---|---|---|---|---|
| **vlm-local (gemma)** | **0.178** | 0.684 | 13 | 1 | 6 | **0** | 276s |
| rule (tap-guide) | 0.150 | 1.000 | 4 | 15 | 0 | 4 | 22s |

**分析**：VLM 的 composite 略高（0.178 vs 0.150），因为世界模型违规为 0（rule 有 4 次），一致性分数更高。但 VLM 仅产生 1 次 tap（vs rule 的 15 次），activity 较低（0.684 vs 1.000），且墙钟 12.5×（276s vs 22s）。VLM 倾向于发 move/wait 而非 tap，说明当前 prompt 对 tap 动作的引导不足。结论：本地 VLM 闭环可行，但需要更强的输出契约（如 few-shot tap 示例）才能匹配 rule 的交互效率。

## 6. 本地 VLM 基准（RTX 5060 8GB）

| 模型 | struct 解析 | 平均墙钟/帧 | 生成 tok/s |
|---|---|---|---|
| Qwen3.5-4B-Q4_K_M | 0/3 | 11.3s | ~62 |
| Qwen3.5-9B-Q4_K_M | 2/3 | 16.9s | 44.8 |
| **gemma-4-E4B-it-Q4_K_M** | **3/3** | **4.8s** | **54.9** |

## 7. 云端 API 混合

### 7.1 结构化提取（离线）

| 模型 | 模态 | struct 解析 | 延迟 |
|---|---|---|---|
| mimo-v2.5 | 视觉 | 3/3 | 22–38s |
| kimi-k2.6 | 视觉 | 0/3 | 8–10s |
| kimi-k2.7-code | 文本 | — | 2.2s |

### 7.2 在线 gameplay（experiment_cloud_api_gameplay.json）

首次将云端 API 接入实时 gameplay 循环（之前因 Cocos 初始化失败仅做离线 struct 提取）。

| 模式 | composite | move | tap | stall | 墙钟 |
|---|---|---|---|---|---|
| api (kimi-k2.7-code) | 0.150 | 0 | 0 | 14 | 310.6s |
| api (mimo-v2.5) | 0.150 | 0 | 0 | 14 | 307.5s |
| rule (tap-guide) | 0.150 | 3 | 11 | 0 | 15.3s |

**分析**：两个云端 LLM 均返回 `wait` 动作（0 move, 0 tap），因为文本模式无法从 probe state JSON 推断出有意义的 tap 坐标。composite 0.150 完全来自 consistency=1.0（无违规），activity=0。rule 模式 15.3s 完成 15 步 vs 云端 ~310s（20× 慢），且 rule 产生了 11 次有效 tap。**结论**：纯文本 API 模式不适合驱动需要 tap 交互的游戏；需要 vision 模式或 VLM-struct 中间层才能利用云端模型。

## 8. Agent 通信与记忆架构

| 模块 | 作用 |
|---|---|
| `src/agent/multi_agent/bus.py` | 显式消息总线 |
| `src/agent/multi_agent/orchestrator.py` | Observer→StateMapper→DecisionAnalyst→Verifier→Critic 循环 |
| `src/agent/strategy_memory.py` | 文件型策略记忆（读+写） |
| `src/agent/roles/critic.py` | Critic 角色 |
| `src/agent/roles/decision_analyst.py` | 决策分析（含记忆读回） |
| `src/agent/decision_makers/vlm_local_maker.py` | 本地 VLM 决策 |

## 9. 后续建议

1. **多游戏扩展**：为 22 个 `_extracted/games` 中的其他游戏添加 profile + 策略。
2. **API LLM + Critic**：在非确定性决策场景下评估 Critic 的 re-decide 增益。
3. **VLM 输出契约**：为思考模型加 "只输出 JSON" 系统提示或 tool 约束。
4. **ssh5090 QLoRA**：可访问后先 qwen3.5-4b 微调。
5. **测试修复**：补齐缺失的 cold-start 数据集或改为 mock。
