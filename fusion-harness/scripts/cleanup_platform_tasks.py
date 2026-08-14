#!/usr/bin/env python3
"""清理平台上 A_ 开头的任务（先 cancel 再 delete）。"""
import json, sys, urllib.request

PLATFORM = "http://34.24.205.23:4097"

def api_call(method, path):
    req = urllib.request.Request(f"{PLATFORM}{path}", method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

# 获取所有 A_ 任务
with urllib.request.urlopen(f"{PLATFORM}/api/tasks", timeout=30) as r:
    tasks = json.loads(r.read())
if isinstance(tasks, dict):
    tasks = tasks.get('tasks', tasks.get('data', []))

a_tasks = [t for t in tasks if (t.get('title') or '').startswith('A_')]
print(f"找到 {len(a_tasks)} 个 A_ 任务需要清理")

for t in sorted(a_tasks, key=lambda x: x.get('title','')):
    tid = t.get('id','')
    title = t.get('title','?')
    status = t.get('status','?')
    
    if status in ('completed', 'failed', 'cancelled'):
        # 先 cancel（幂等），再 delete
        try:
            api_call('POST', f'/api/tasks/{tid}/cancel')
            print(f"  [cancel] {title} ({status})")
        except Exception as e:
            print(f"  [skip-cancel] {title}: {e}")
        
        try:
            api_call('DELETE', f'/api/tasks/{tid}/delete')
            print(f"  [delete] {title}")
        except Exception as e:
            print(f"  [fail-delete] {title}: {e}")
    elif status == 'running':
        try:
            api_call('POST', f'/api/tasks/{tid}/cancel')
            print(f"  [cancel-running] {title}")
        except Exception as e:
            print(f"  [skip] {title}: {e}")
    elif status == 'queued':
        try:
            api_call('POST', f'/api/tasks/{tid}/cancel')
            print(f"  [cancel-queued] {title}")
        except Exception as e:
            print(f"  [skip] {title}: {e}")
    else:
        print(f"  [unknown] {title}: {status}")

# 验证清理结果
with urllib.request.urlopen(f"{PLATFORM}/api/tasks", timeout=30) as r:
    remaining = json.loads(r.read())
if isinstance(remaining, dict):
    remaining = remaining.get('tasks', remaining.get('data', []))
a_remaining = [t for t in remaining if (t.get('title') or '').startswith('A_')]
print(f"\n清理后剩余 A_ 任务: {len(a_remaining)}")
if a_remaining:
    for t in a_remaining:
        print(f"  {t.get('title','?')}: {t.get('status','?')}")
