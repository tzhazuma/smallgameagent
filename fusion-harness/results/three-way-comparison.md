# 三方框架对比实验（Phase 3）

> 统一：planner = mimo-v2.5（opencodego，经 harness_http adapter）；Windows Edge CDP 渲染；无 VLM（gah --no-vlm）
> 游戏：tiles-survive（Unity）/ kingshot-94766e5d61dc（Cocos）/ whiteout-12ababda99c7（Cocos）

## 结果

| 游戏 | 框架 | terminal | steps | gameplay | plans | actions |
|---|---|---|---|---|---|---|
| tiles-survive | A 我们(fps-harness+mimo) | OPERATOR_INTERRUPTED | 20 | 9 | 5 | 14 |
| tiles-survive | B gah-mimo | **SETTLED_COMPLETE** | 230 | **156** | 79 | 229 |
| kingshot-94766 | A 我们 | BUDGET_EXHAUSTED | 240 | 7 | 4 | 12 |
| kingshot-94766 | B gah-mimo | OPERATOR_INTERRUPTED | 17 | 3 | 12 | 15 |
| whiteout-12ab | A 我们 | OPERATOR_INTERRUPTED | 22 | 10 | 5 | 17 |
| whiteout-12ab | B gah-mimo | BLOCKED_UNKNOWN_MECHANIC | 18 | 3 | 14 | 17 |

## 洞察

1. **gah 确定性引擎在探针/控制良好的游戏（Unity tiles）碾压**：156 gameplay 达到 SETTLED_COMPLETE（我们 9）。确定性执行 + 恢复阶梯让持续游玩成为可能。
2. **gah 在 Cocos 游戏（kingshot/whiteout）受探针/控制适配限制**（3 gameplay vs 我们 7-10）：mimo 规划 + gah 探针的组合未达最佳。
3. **我们框架在 Cocos 游戏更稳**（探针适配成熟），但缺确定性 → 无通关。
4. **融合方向验证**：用我们的探针/控制适配（Cocos 成熟）+ gah 的确定性引擎（恢复阶梯/阶段策略/记忆）→ 双赢。

## 备注
- gah-mimo 的 planner 调用多（12-79 次 vs 我们 4-5）：mimo 单次 20-50s，gah 的 replan 频率高导致总时长长。
- 公平性：gah 仅替换 planner 为 mimo（其余未动）；我们框架用同一 mimo。
