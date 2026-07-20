# 大规模实验综合分析

## 自动校准结果

- 总游戏数: 20
- joystick 驱动 (A 类): 5
- tap-to-move (B 类): 15

| 游戏 | 类型 | basis |
|---|---|---|
| SSD_00219P01 | B (tap-only) | — |
| SSD_00332P01 | B (tap-only) | — |
| SSD_00342P01 | B (tap-only) | — |
| SSD_00382P01 | B (tap-only) | — |
| SSD_00394P01 | B (tap-only) | — |
| SSD_00427P01 | B (tap-only) | — |
| SSD_00434P01 | B (tap-only) | — |
| SSD_00440P01 | A (joystick) | (-6.42,2.34) / (-2.34,-6.42) |
| SSD_00475P01 | B (tap-only) | — |
| SSD_00482P01 | B (tap-only) | — |
| SSD_00483P01 | A (joystick) | (3.41,-3.41) / (2.42,2.42) |
| SSD_00496P01 | A (joystick) | (0.75,-0.90) / (0.82,3.57) |
| SSD_00517P01 | A (joystick) | (7.42,-0.00) / (0.00,9.44) |
| SSD_00522P02 | A (joystick) | (4.92,0.00) / (-0.27,3.46) |
| SSD_00526P01 | B (tap-only) | — |
| SSD_00532P01 | B (tap-only) | — |
| SSD_00594P02 | B (tap-only) | — |
| SSD_00669P01 | B (tap-only) | — |
| SSD_00733P01 | B (tap-only) | — |
| SSD_00742P01 | B (tap-only) | — |

## 关键实验汇总

### 记忆读回 A/B

| 阶段 | composite | memory_hits |
|---|---|---|
| phase1_write | 0.150 | 92 |
| phase2_read | 0.300 | 116 |
| control_no_memory | 0.150 | 0 |

### Critic 反馈循环 A/B

| 配置 | composite | bus_messages | critic_invocations |
|---|---|---|---|
| no_critic_r1 | 0.150 | 11 | 2 |
| with_critic_r2 | 0.150 | 15 | 2 |

### 本地 VLM 闭环

| 模式 | composite | activity | latency |
|---|---|---|---|
| vlm_local_gemma | 0.178 | 0.684 | 276.0s |
| rule_baseline | 0.150 | 1.000 | 22.1s |

### 云端 API 在线 gameplay

| 模型 | composite | move | tap | stall | latency |
|---|---|---|---|---|---|
| kimi-k2.7-code | 0.150 | 0 | 0 | 14 | 310.6s |
| mimo-v2.5 | 0.150 | 0 | 0 | 14 | 307.5s |
| None | 0.150 | 3 | 11 | 0 | 15.3s |

### 本地 VLM struct 基准

| 模型 | 解析成功 | 平均墙钟/帧 | 生成 tok/s |
|---|---|---|---|
| Qwen3.5-4B-Q4KM | 0/3 | Nones | None |
| Qwen3.5-9B-Q4KM | 2/3 | 16.87s | 44.8 |
| gemma-4-E4B-it-Q4KM | 3/3 | 4.781s | 54.9 |

### 云端 API 策略生成

| 游戏 | 模式 | composite | activity | latency/step |
|---|---|---|---|---|
| SSD_00461P01 | rule_baseline | 0.106 | 0.708 | 1.42s |
| SSD_00461P01 | multi_bus_memory | 0.056 | 0.375 | 1.31s |
| SSD_00461P01 | hierarchical_api | 0.150 | 0.000 | 6.86s |
| SSD_00736P01 | rule_baseline | 0.275 | 0.833 | 1.62s |
| SSD_00736P01 | multi_bus_memory | 0.237 | 0.583 | 1.32s |
| SSD_00736P01 | hierarchical_api | 0.150 | 0.000 | 9.51s |

### VLM 视觉管线

| 游戏 | 模式 | composite | tap | stall | latency/step |
|---|---|---|---|---|---|
| SSD_00461P01 | probe_only | 0.044 | 7 | 17 | 9.16s |
| SSD_00461P01 | pil_vision | 0.087 | 14 | 10 | 1.96s |
| SSD_00461P01 | vlm_gemma | 0.150 | 0 | 24 | 3.83s |
| SSD_00736P01 | probe_only | 0.269 | 20 | 5 | 1.16s |
| SSD_00736P01 | pil_vision | 0.269 | 20 | 5 | 1.17s |
| SSD_00736P01 | vlm_gemma | 0.150 | 0 | 24 | 4.08s |
