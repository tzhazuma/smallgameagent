# Batch Experiment Analysis

## Per-Mode Summary

| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |
|---|---|---|---|---|---|
| multi-bus | 3 | 0.180 | 0.597 | 16.6 | 0 |
| multi-bus-memory | 3 | 0.230 | 0.667 | 15.5 | 0 |
| rule | 3 | 0.188 | 0.722 | 16.9 | 0 |

## Per-Game Breakdown

### SSD_00461P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.161 | 0.875 | 13.0s | 21 | 3 | 3 |
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 11.7s | 24 | 0 | 0 |
| rule | 42 | 25 | 0.106 | 0.708 | 14.6s | 17 | 7 | 7 |
### SSD_00483P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.150 | 0.000 | 20.6s | 0 | 25 | 24 |
| multi-bus-memory | 42 | 25 | 0.150 | 0.000 | 19.7s | 0 | 25 | 24 |
| rule | 42 | 25 | 0.244 | 0.625 | 20.4s | 15 | 10 | 9 |
### SSD_00522P02

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.227 | 0.917 | 16.1s | 22 | 3 | 2 |
| multi-bus-memory | 42 | 25 | 0.240 | 1.000 | 15.0s | 24 | 1 | 0 |
| rule | 42 | 25 | 0.215 | 0.833 | 15.7s | 20 | 5 | 4 |

## Trajectory Data

9 runs produced trajectory JSONL files.

| File | Steps |
|---|---|
| `SSD_00461P01_rule_seed42.jsonl` | 25 |
| `SSD_00461P01_multi-bus_seed42.jsonl` | 25 |
| `SSD_00461P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00483P01_rule_seed42.jsonl` | 25 |
| `SSD_00483P01_multi-bus_seed42.jsonl` | 25 |
| `SSD_00483P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00522P02_rule_seed42.jsonl` | 25 |
| `SSD_00522P02_multi-bus_seed42.jsonl` | 25 |
| `SSD_00522P02_multi-bus-memory_seed42.jsonl` | 25 |
