# Batch Experiment Analysis

## Per-Mode Summary

| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |
|---|---|---|---|---|---|
| multi-bus | 3 | 0.110 | 0.333 | 21.4 | 0 |
| multi-bus-memory | 6 | 0.218 | 0.688 | 17.4 | 0 |
| rule | 6 | 0.251 | 0.875 | 16.0 | 0 |

## Per-Game Breakdown

### SSD_00382P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.287 | 0.917 | 14.0s | 22 | 0 | 2 |
| rule | 42 | 25 | 0.300 | 1.000 | 12.9s | 25 | 0 | 0 |
### SSD_00461P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.050 | 0.333 | 18.4s | 8 | 16 | 16 |
| multi-bus-memory | 42 | 25 | 0.044 | 0.292 | 18.0s | 7 | 17 | 17 |
| rule | 42 | 25 | 0.149 | 0.792 | 13.3s | 19 | 5 | 5 |
### SSD_00483P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.103 | 0.083 | 27.8s | 2 | 23 | 22 |
| multi-bus-memory | 42 | 25 | 0.200 | 0.333 | 22.9s | 8 | 17 | 16 |
| rule | 42 | 25 | 0.244 | 0.625 | 20.2s | 15 | 10 | 9 |
### SSD_00522P02

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.177 | 0.583 | 18.0s | 14 | 11 | 10 |
| multi-bus-memory | 42 | 25 | 0.177 | 0.583 | 17.7s | 14 | 11 | 10 |
| rule | 42 | 25 | 0.215 | 0.833 | 15.9s | 20 | 5 | 4 |
### SSD_00594P02

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 16.8s | 25 | 0 | 0 |
| rule | 42 | 25 | 0.300 | 1.000 | 17.7s | 25 | 0 | 0 |
### SSD_00742P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 15.2s | 25 | 0 | 0 |
| rule | 42 | 25 | 0.300 | 1.000 | 15.8s | 25 | 0 | 0 |

## Trajectory Data

15 runs produced trajectory JSONL files.

| File | Steps |
|---|---|
| `SSD_00461P01_rule_seed42.jsonl` | 25 |
| `SSD_00483P01_rule_seed42.jsonl` | 25 |
| `SSD_00522P02_rule_seed42.jsonl` | 25 |
| `SSD_00461P01_multi-bus_seed42.jsonl` | 25 |
| `SSD_00483P01_multi-bus_seed42.jsonl` | 25 |
| `SSD_00522P02_multi-bus_seed42.jsonl` | 25 |
| `SSD_00461P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00483P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00522P02_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00382P01_rule_seed42.jsonl` | 25 |
| `SSD_00594P02_rule_seed42.jsonl` | 25 |
| `SSD_00742P01_rule_seed42.jsonl` | 25 |
| `SSD_00382P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00594P02_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00742P01_multi-bus-memory_seed42.jsonl` | 25 |
