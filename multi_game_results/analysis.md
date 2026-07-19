# Batch Experiment Analysis

## Per-Mode Summary

| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |
|---|---|---|---|---|---|
| multi-bus-memory | 5 | 0.210 | 0.400 | 28.8 | 0 |
| rule | 5 | 0.165 | 0.300 | 30.3 | 0 |

## Per-Game Breakdown

### SSD_00342P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.150 | 0.000 | 31.5s | 0 | 0 | 24 |
| rule | 7 | 25 | 0.150 | 0.000 | 31.8s | 0 | 0 | 24 |
### SSD_00461P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.300 | 1.000 | 19.9s | 24 | 0 | 0 |
| rule | 7 | 25 | 0.106 | 0.708 | 24.2s | 17 | 7 | 7 |
### SSD_00482P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.150 | 0.000 | 34.9s | 0 | 0 | 24 |
| rule | 7 | 25 | 0.150 | 0.000 | 34.5s | 0 | 0 | 24 |
### SSD_00532P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.150 | 0.000 | 32.3s | 0 | 25 | 24 |
| rule | 7 | 25 | 0.150 | 0.000 | 34.7s | 0 | 25 | 24 |
### SSD_00736P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.300 | 1.000 | 25.2s | 25 | 0 | 0 |
| rule | 7 | 25 | 0.269 | 0.792 | 26.4s | 20 | 5 | 5 |

## Trajectory Data

10 runs produced trajectory JSONL files.

| File | Steps |
|---|---|
| `SSD_00461P01_rule_seed7.jsonl` | 25 |
| `SSD_00461P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00482P01_rule_seed7.jsonl` | 25 |
| `SSD_00482P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00736P01_rule_seed7.jsonl` | 25 |
| `SSD_00736P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00342P01_rule_seed7.jsonl` | 25 |
| `SSD_00342P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00532P01_rule_seed7.jsonl` | 25 |
| `SSD_00532P01_multi-bus-memory_seed7.jsonl` | 25 |
