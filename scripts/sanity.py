import httpx, json, sys
sys.stdout.reconfigure(encoding='utf-8')

# 1. 老接口 /chat
r = httpx.post('http://localhost:8000/chat', json={'message': '你好，重启后测试', 'session_id': 'restart_test'}, timeout=60)
d = r.json()
print(f'[老接口/chat] status={r.status_code}  reply={d.get("reply","")[:80]}  安全={d.get("safety_blocked")}')

# 2. 新接口 /api/v1/chat (worknote场景)
r2 = httpx.post('http://localhost:8000/api/v1/chat', json={'scene_id': 'worknote', 'query': '今天干了什么活'}, timeout=60)
d2 = r2.json()
print(f'[新接口worknote] status={r2.status_code}  scene_id={d2.get("scene_id")}  reply={d2.get("reply","")[:80]}  model={d2.get("model_used","")}')

# 3. 新接口 /api/v1/chat (elder_care场景)
r3 = httpx.post('http://localhost:8000/api/v1/chat', json={'scene_id': 'elder_care', 'query': '老人血压偏高怎么办'}, timeout=60)
d3 = r3.json()
print(f'[新接口elder_care] status={r3.status_code}  scene_id={d3.get("scene_id")}  reply={d3.get("reply","")[:80]}')

# 4. v0.7.1 场景列表
r4 = httpx.get('http://localhost:8000/api/v1/scenes', timeout=10)
if r4.status_code == 200:
    scenes = r4.json()
    print(f'[场景列表] {list(scenes.keys())}')
else:
    print(f'[场景列表] 暂不可用 status={r4.status_code}')

print('\n=== 全部完成 ===')
