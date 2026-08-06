# game-agent-harness 详细实现方法分析

> 源码版本：fps-research/game-agent-harness main（2026-08 克隆，/tmp/gah-latest）
> 本文基于三路源码深读：主循环/策略（orchestration + strategy + statechart）、执行/记忆（execution + memory + world）、感知/治理（perception + cognition + evaluation + governance + planning）

## 一、总体架构：确定性守护 + LLM 策略编排

核心闭环：**"LLM 写策略 → 确定性引擎执行 → 结果反馈 LLM"**，确定性引擎拥有最终执行权威。

```
探针 → 版本化世界(Event-sourced DSG) → 连续层次 FSM → Option 监督器
→ JSON-RPC → Macro Task Runtime → VLM 观察(final-v2, Qwen3.5-9B)
→ 证据缓冲 → Codex 策略修复 → 新鲜度/风险/预算/完成闸门 → 闭环控制
```

**信任控制流单向**：VLM 只观察、不允许给动作；Codex 只做策略修复、不直接控制浏览器；确定性 Option 监督器是唯一执行入口。

## 二、主自治循环（src/orchestration/autonomous-loop.mjs, 19.9K LOC）

### 2.1 run() 主循环（:19295）
每步流程：
1. 效率门控 `evaluateEfficiencyBoundary("cycle_entry")`
2. 引导预算门控 `enforceBootstrapBudgetBoundary`
3. 终止检查 `pollStopRequest` + `stopForObservedFailure`
4. 感知-决策-执行（两条路径：策略模式 / legacy 单意图）
5. 后执行门控：`stopForTargetProgressBoundary`、连续阻断 ≥8 → BLOCKED_UNSAFE
6. No-gameplay 停滞检测（≥阈值 → 本地 replan）
7. 预算延伸 `activeProgressBudgetExtensionDecision`（游戏步达限 +60）
8. 状态机更新 `updateStatechart` + 写工作记忆

### 2.2 两条执行路径
- **策略模式**（strategyExecutionEnabled）：`executeStrategyCycle() → nextStrategyDecision() → 安装/执行 strategy → executeIntent()`（:13930）
- **legacy 单意图**：`continuationIntent() → scheduledPerception() → plan() → executeIntent()`

### 2.3 四大门控

| 门控 | 实现 | 机制 |
|---|---|---|
| **新鲜度门** | `preflightInputIntent()`（:8125） | 提交游戏输入前：①自动转换输入阻断轮询（≤10s）；②动态目标轻量快照（10 项检查）；③移动签名比对（玩家漂移/目标距离/路由拓扑/控制域/场景结构）→ 不匹配拒为 stale_intent |
| **预算门** | 多层 | 编排节拍 maxSteps×3；游戏步 240（+60 延伸一次）；引导预算；规划器调用上限；策略实验拒绝 ≥3 → BLOCKED_UNKNOWN_MECHANIC |
| **完成门** | `verifyCompletion()`（:17777） | 三次确认采样 `fixed_three_sample_settled_completion`，全部一致才 SETTLED_COMPLETE；终止键帧后再完整探测 |
| **风险门** | IntentGate | maximumRisk=medium；maxHighRiskExperiments=3；控制故障隔离/通关隔离 |

## 三、策略状态机（src/strategy/, 15K LOC）

### 3.1 StrategySpec 数据结构
```
{ base, strategy_id, confidence, entry_state,
  states[1-16]: {
    state_id, description,
    objective: { selector: current_guide|target_id|target_role|none, sticky, selection_policy?, required_resource? },
    actions: [{ action_id, option, target_binding, parameters, route_policy, repeat, max_local_iterations, expected_effect }],
    transitions: [{ predicate, key?, value?, next: state|REPLAN|VERIFY_COMPLETION|STOP }],
    recovery: { no_progress_before_replan, max_action_failures, settle_before_retry },
    causal_contract?
  },
  invariants, global_replan_triggers, evidence_refs }
```

### 3.2 状态机执行（strategy-machine-runtime.mjs:1114, next() :1839）
主循环（≤states.length 次防环路）：
1. 挂起动作检查 → blocked
2. 游戏/运行 ID 校验（防跨污染）
3. 强制 replan / 恢复 settle
4. 状态循环：`resolveStrategyObjective`（目标解析）→ `_globalDecision`（全局触发器）→ `_invariantDecision`（不变量）→ `_transitionDecision`（守卫按数组顺序）→ 命中则 `_enterState` 继续
5. `_materializeAction`（动作物化 + 路由计算 `selectStrategyRouteStep`）
6. `_tryHardRouteRecovery`（不可达路由结构恢复）

### 3.3 谓词求值（:853）
30+ 谓词：completion_suspected / failure_active / phase_is / phase_changed_from_entry / control_domain_is / guide_changed_from_entry / guide_absent / guide_present / objective_target_inactive / objective_reached / waypoint_reached / target_relevant_progress / resource_counter_at_least / resource_counter_increased_from_entry / no_progress_at_least / cross_state_target_no_progress_at_least / local_iterations_at_least 等。
**关键**：交互租约激活（targetInteractionLeaseActive）时抑制 no_progress/local_iterations 转换——防止有效交互被误判停滞。

### 3.4 全局 replan 触发器（10 类）
phase_changed_from_entry / control_domain_changed_from_entry / guide_changed_from_entry / objective_target_inactive / repeated_no_progress / repeated_action_failure / evidence_contradiction / new_mechanism_evidence / completion_suspected / failure_active

## 四、确定性执行层（src/execution/, 8.6K LOC）

### 4.1 Option 系统（option-catalog.mjs:36-187）
9 个注册 Option，每个含 risk / requires_target / observable_effects / compile()：

| Option | risk | requires_target | observable_effects |
|---|---|---|---|
| observe_settle | none | - | state_settles, any_relevant_progress |
| probe_tap | low | - | any_relevant_progress, scene_changes, completion_suspected |
| probe_drag | medium | - | 同上 |
| probe_joystick | low | - | player_position_changes |
| explore_sector_sweep | medium | - | 位置/进度/场景变化 |
| approach_target | medium | ✅ | 位置/距离/目标值/进度 |
| dwell_at_target | low | ✅ | 目标值/场景/进度 |
| recover_reverse | low | - | 位置/失败清除 |
| verify_completion | none | - | settled_completion |

compile(parameters, context) 将高层意图编译为原始指令（primitives）数组；参数由 finite/point/worldPoint 辅助函数强制校验（越界 throw）。

### 4.2 Option 监督器（deterministic-option-supervisor.mjs:47-304）
- **无 AI 端口**（不能在执行中调 planner/VLM）
- RPC 只含 JSON 数据 + opaque task id（不能注入回调）
- maximumIterations=1 / maximumRecoveryAttempts=0（单步执行）
- JSON-RPC：start_task → run_until_boundary，返回含 planner_calls:0, vlm_calls:0

### 4.3 IntentGate 安全规则（intent-gate.mjs:240-1032，15+ 条）
版本新鲜度 / 风险限额 / 高险预算 / 关键监控隔离 / 控制故障恢复隔离 / 通关隔离 / 重复语义无效拒绝 / 采集高原需下游 / 控制映射校验 / 扇形扫描边界（4-6 primitives, ≤1800ms）/ tap 不能替代导航 / 目标存在活跃可导航 / 终极目标延迟 / approach 需校准 / 交互重入严格条件 / 起点需局部离场路点 / 路点约束（≤25 单位、不穿阻挡、路径清晰、不重复）/ 目标邻域 / **语义过渡路点授权**（门/传送点：当前引导匹配 + 相位兼容 + 高置信机制 ≥0.95 + 有界脉冲 ≤4）

### 4.4 失败分类（failure-taxonomy.mjs:3-362）
12 失败码：NO_PATH / OCCLUDED / OUT_OF_REACH / STUCK / OSCILLATING / WRONG_CONTROL_MAPPING / TARGET_STALE / NO_RESOURCE / CAPACITY_FULL / SEMANTIC_UNKNOWN / WORLD_CHANGED / TIMEOUT。
分类优先级：语义进展→null；failure.active→SEMANTIC_UNKNOWN；目标消失→TARGET_STALE；控制错→WRONG_CONTROL_MAPPING；容量→CAPACITY_FULL；资源→NO_RESOURCE；阻挡→OUT_OF_REACH；无路径→NO_PATH；振荡→OSCILLATING（优先于 WORLD_CHANGED）；无位移→STUCK；距离不减→OCCLUDED；scene 变化→WORLD_CHANGED（最后，避免掩盖局部拓扑失败）；预算→TIMEOUT。
每分类附带运动轨迹观察（效率/方向反转/振荡）。

### 4.5 恢复阶梯（recovery-ladder.mjs:6-233）
每失败码一组有序恢复步骤（NO_PATH: REFRESH_LOCAL_MAP→REPLAN→PORTAL_CENTER→...；STUCK: REFRESH→REPLAN→INCREASE_CLEARANCE→REVERSE→EXIT_REENTER；WRONG_CONTROL: SETTLE→RECALIBRATE→DIRECTION_CORRECTION...）。
升级机制：cursor 推进 + attempts_per_step；**目标指纹保护**（objectiveFingerprint 不变，拒绝改语义目标）；exhausted 唤醒上层。

## 五、记忆层（src/memory/, 3.8K LOC）

### 5.1 事件溯源动态场景图（DSG）
- 实体：world/player/region/resource/phase/control_domain/capability/target/obstacle/structure
- 关系：contains/blocks_navigation_in/holds_resource/has_capability/has_control_domain/has_phase/requires_resource/shares_physical_identity_with
- ID：sha256(kind:identityKey) 截断；observe() diff 生成 upsert/retire 操作；事件流 .jsonl 追加 + 重放恢复；state_version 单调递增

### 5.2 经验记忆（Experience Card）
- 字段：card_id(内容哈希)/claim/precondition(phase+control_domain+target_role+recovery_state+structural)/action(option+参数模式)/observed_delta/lesson_type(confirmed_effective|confirmed_no_effect|candidate)/confidence/evidence_refs/supporting_run_ids
- 检索：匹配打分（phase +5、control_domain +3、target_role +2、confirmed +2），top-N
- 生成：successes≥2 且失败率≤0.25 → confirmed_effective；failures≥2 且≥0.5 → confirmed_no_effect
- 经验→操作者约束：confidence≥0.8 + ≥2 独立 run，shadow 模式（enforcement_authorized:false）

### 5.3 因果决策图（CDG）
- 因果状态描述符 → 状态 ID（sha256）；recordTransition 累加 attempts/semantic_progress_count/no_effect_count；confidence = progress/attempts；plannerView 标记 repeated_no_effect_edges（no_effect≥2 且 progress=0）

### 5.4 策略注册表（策略晋升）
candidate → evaluated → known-pass/superseded。晋升条件：fresh_start_passes≥3 + 无导航逃逸 + 无 protected_prefix 回归 + 关键监控在限。

### 5.5 策略学习器
实验键去重：probe_joystick→方向扇区+时长桶；probe_tap→100px 屏幕网格；approach_target→target_id+waypoint 桶。verified = successes≥2 && 失败率≤0.25。

## 六、感知与认知（src/perception/ + src/cognition/）

### 6.1 VLM /observe 协议
请求：schema_version/request_id/task_type/base/prompt/max_output_tokens(按任务 640/384/320)/images[{mime_type, data_base64, evidence_ref}]
响应：request_id/raw_text/model_id/adapter_id/latency_ms/input_tokens/generated_tokens/output_token_budget

### 6.2 提示构造（prompt-builder.mjs）
7 种任务：visual_grounding(640 tokens)/backend_grounding(384)/action_effect_observation(384)/phase_observation(320)/temporal_change_observation(320)/completion_evidence(320)/failure_observation(384)。
约束：实体上限 11、像素坐标原点左上、禁止后端世界坐标、终端 UI 覆盖优先。

### 6.3 审计日志（vlm-audit-log）
JSONL：requested/accepted/guard_rejected/transport_rejected/input_guard_rejected + 输入预算评估。

### 6.4 证据缓冲（evidence-buffer）
content_hash 去重、FIFO 200 上限、digest 按 critical>state_version 优先、probe_delta 摘要合并、最多 16 包。
**信息增益**：TASK_GAIN×confidence×criticalBoost（completion_evidence 2.5 / failure_observation 2.2 / action_effect 1.5 / backend_grounding 1.4 / visual_grounding 1.2）。

### 6.5 自适应证据预算（adaptive-evidence-budget）
shadow 模式：必需证据（run_boundary/terminal/stage_boundary/visual_model_input）→ capture；可选→检查数量(48)/字节(12MB)/间隔(3步)/重复原因间隔(10步) → capture/suppress。false_warning 审查通过才升 enforce。

### 6.6 认知调度器（cognitive-scheduler）
VLM 调用优先级：failure_observation(100) > completion_evidence(95) > action_effect(80) > backend_grounding(70) > visual_grounding(65) > phase(55) > temporal(50)；max_calls_per_run=5、evidence 最大年龄 12 步、no_progress_trigger=2。
Planner：max_calls=6、min_information_gain=2.5、调用间隔 ≥3 步。

## 七、评估与治理（src/evaluation/ + src/governance/）

### 7.1 渐进审计（progressive-run-audit）
硬失败 6 类：runtime_fault / unsafe_terminal / navigation_escape / protected_prefix_regression / untrusted_settled_completion_evidence / abnormal_termination。
verdict：硬失败→STOP；需继续→REVISE；否则 PASS。

### 7.2 Settled 通关（settled-completion）
5 必要条件：全探针阳性 + 全无失败 + 导航安全 + 独立验证一致（VLM corroborated ≥0.7 / 后端权威 / Codex 评估）。
反作弊：初始基线残留游戏矛盾（零步通关+初始证据+残留引导）、残留引导无权威终端。

### 7.3 多游戏验证（governance）
分组：canary / frozen_unseen / stable_regression；变体：stable_replay / backend_only / backend_codex / backend_vlm_codex / candidate_exploration / vlm_only_shadow。
调度：确定性首次适配 + 浏览器/planner/VLM 插槽 + 内存限制。
验证项：game_identity / exact_bundle / memory_isolated / planner_disabled / vlm_disabled / fresh_start / settled_complete / navigation_safe。

### 7.4 Codex Planner（src/planning/, 5.3K LOC）
- 决策包 ≤32KB（正常）/48KB（紧急），含 base/goal/phase/world/evidence/hypotheses/capabilities
- Intent 契约：base 完全匹配 brief / option 白名单 / abort_conditions ≥1 / evidence_refs 存在于 brief
- StrategySpec 契约：状态可达性 / 37 谓词合法性 / 转换目标存在 / 因果合约格式 / 全局 replan 触发器
- strategyPrompt 约 5000 词指令：1-16 状态相位局部状态机、causal_contract、严格谓词枚举

## 八、核心设计理念

> **VLM 是观察者（只读不决策）、Codex 是决策者（受约束）、确定性门控是执行者（最终权威）**

## 九、可借鉴清单（对我们 smallgameagent 的启示）

1. **新鲜度门**（preflightInputIntent 签名比对）→ 我们 L2 策略执行前加状态签名校验，防陈旧策略
2. **IntentGate 15 条安全规则** → 我们 normalizer 之外加执行闸门（目标存在/控制映射/路点约束）
3. **失败分类 + 恢复阶梯** → 我们 L0 规则层按失败码升级恢复（REFRESH→REPLAN→REVERSE）
4. **证据缓冲 + 信息增益** → 我们 VLM 观察先缓冲再决策，按增益阈值触发 planner
5. **认知调度器优先级** → 我们 vlm_policy 升级为优先级调度（failure>completion>grounding）
6. **经验卡 + 策略晋升** → 我们 memory.py 增加跨 run 经验卡（phase/control_domain/target_role 匹配）
7. **多游戏验证**（canary/frozen/regression）→ 我们 50 游戏批量建立分层回归机制
8. **VLM 只观察不决策** → 我们 /describe 输出仅作上下文，不给动作（已符合）
9. **settled 通关反作弊** → 我们补零步通关+残留引导检测（防假阳性 SETTLED_COMPLETE）

## 十、关键文件索引

| 机制 | 位置 |
|---|---|
| 主循环 | autonomous-loop.mjs:19295 (run) |
| 策略循环 | autonomous-loop.mjs:13930 (executeStrategyCycle) |
| 新鲜度预检 | autonomous-loop.mjs:8125 (preflightInputIntent) |
| 完成验证 | autonomous-loop.mjs:17777 (verifyCompletion) |
| 策略安装校验 | autonomous-loop.mjs:12349 (installStrategy) |
| Option 目录 | option-catalog.mjs:36-187 |
| Option 监督器 | deterministic-option-supervisor.mjs:47-304 |
| Intent 闸门 | intent-gate.mjs:240-1032 |
| 失败分类 | failure-taxonomy.mjs:188-362 |
| 恢复阶梯 | recovery-ladder.mjs:6-74 |
| 状态机运行时 | strategy-machine-runtime.mjs:1114 (next :1839) |
| 谓词求值 | strategy-machine-runtime.mjs:853 |
| 全局决策 | strategy-machine-runtime.mjs:1320 |
| DSG 投影 | event-sourced-dynamic-scene-graph.mjs:171-558 |
| DSG 观察 | event-sourced-dynamic-scene-graph.mjs:834-953 |
| 经验卡检索 | experience-memory.mjs:384-425 |
| 策略晋升 | strategy-registry.mjs:102-134 |
| 证据缓冲 | evidence-buffer.mjs:97-198 |
| 认知调度 | cognitive-scheduler.mjs:53-198 |
| 渐进审计 | progressive-run-audit.mjs:11-127 |
| Settled 通关 | settled-completion.mjs:81-183 |
| 多游戏验证 | multi-game-validation.mjs:485-524 |
| Codex prompt | codex-planner.mjs:17-52 |
| Intent 校验 | planner-contract.mjs:23-48 |
| StrategySpec 校验 | strategy-contract.mjs:391-468 |
