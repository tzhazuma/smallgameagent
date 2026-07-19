# Batch Experiment Analysis

## Per-Mode Summary

| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |
|---|---|---|---|---|---|
| hierarchical | 2 | 0.150 | 0.000 | 205.1 | 0 |
| multi-bus | 2 | 0.300 | 1.000 | 21.9 | 0 |
| multi-bus-memory | 2 | 0.215 | 0.931 | 23.4 | 0 |
| rule | 2 | 0.101 | 0.672 | 34.7 | 0 |

## Per-Game Breakdown

### SSD_00461P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| hierarchical | 42 | 30 | 0.150 | 0.000 | 193.6s | 0 | 0 | 29 |
| hierarchical | 123 | 30 | 0.150 | 0.000 | 216.7s | 0 | 0 | 29 |
| multi-bus | 42 | 30 | 0.300 | 1.000 | 21.8s | 29 | 0 | 0 |
| multi-bus | 123 | 30 | 0.300 | 1.000 | 21.9s | 29 | 0 | 0 |
| multi-bus-memory | 42 | 30 | 0.129 | 0.862 | 24.9s | 25 | 4 | 4 |
| multi-bus-memory | 123 | 30 | 0.300 | 1.000 | 21.9s | 29 | 0 | 0 |
| rule | 42 | 30 | 0.098 | 0.655 | 35.9s | 19 | 10 | 10 |
| rule | 123 | 30 | 0.103 | 0.690 | 33.5s | 20 | 9 | 9 |

## Trajectory Data

8 runs produced trajectory JSONL files.

| File | Steps |
|---|---|
| `SSD_00461P01_rule_seed42.jsonl` | 30 |
| `SSD_00461P01_rule_seed123.jsonl` | 30 |
| `SSD_00461P01_multi-bus-memory_seed42.jsonl` | 30 |
| `SSD_00461P01_multi-bus-memory_seed123.jsonl` | 30 |
| `SSD_00461P01_hierarchical_seed42.jsonl` | 30 |
| `SSD_00461P01_hierarchical_seed123.jsonl` | 30 |
| `SSD_00461P01_multi-bus_seed42.jsonl` | 30 |
| `SSD_00461P01_multi-bus_seed123.jsonl` | 30 |
