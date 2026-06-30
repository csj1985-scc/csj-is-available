"""检查 8002 端口所有页面和 API 状态"""
import urllib.request, json

base = 'http://localhost:8002'

# 1. 首页
try:
    req = urllib.request.Request(base + '/')
    resp = urllib.request.urlopen(req, timeout=5)
    html = resp.read().decode('utf-8')
    print('=== 首页 (/) ===')
    print(f'  状态码: {resp.status}')
    print(f'  长度: {len(html)} bytes')
    brain_mention = '3d' in html.lower() or '大脑' in html
    room_link = 'room' in html.lower() or '会议室' in html
    print(f'  3D大脑元素: {brain_mention}')
    print(f'  会议室链接: {room_link}')
except Exception as e:
    print(f'首页错误: {e}')

# 2. 会议室页面
try:
    req = urllib.request.Request(base + '/room')
    resp = urllib.request.urlopen(req, timeout=5)
    html = resp.read().decode('utf-8')
    print('\n=== 会议室 (/room) ===')
    print(f'  状态码: {resp.status}')
    print(f'  长度: {len(html)} bytes')
    print(f'  是完整HTML: {"<!DOCTYPE" in html or "<html" in html.lower()}')
    has_role_select = 'engineer' in html.lower() or 'agent_' in html
    has_start_btn = 'start' in html.lower() or '开始' in html
    print(f'  角色选择器: {has_role_select}')
    print(f'  开始按钮: {has_start_btn}')
    if len(html) > 100:
        print(f'  前150字符: {html[:150]}')
except Exception as e:
    print(f'会议室错误: {e}')

# 3. API /agents
try:
    req = urllib.request.Request(base + '/agents')
    resp = urllib.request.urlopen(req, timeout=5)
    data = json.loads(resp.read().decode('utf-8'))
    print('\n=== API /agents ===')
    agents = data if isinstance(data, list) else data.get('agents', [])
    print(f'  角色数: {len(agents)}')
    for a in agents:
        print(f'    - {a.get("name","?")} (temp={a.get("temperature","?")})')
except Exception as e:
    print(f'/agents错误: {e}')

# 4. API /consultation/history
try:
    req = urllib.request.Request(base + '/consultation/history')
    resp = urllib.request.urlopen(req, timeout=5)
    data = json.loads(resp.read().decode('utf-8'))
    count = len(data) if isinstance(data, list) else 0
    print(f'\n=== API /consultation/history ===')
    print(f'  历史会议数: {count}')
except Exception as e:
    print(f'/history错误: {e}')

# 5. 测试创建会议（轻量，不跑完）
topic = '测试：新功能优先级排序'
body = json.dumps({
    'topic': topic,
    'agent_ids': ['agent_engineer', 'agent_designer'],
    'max_rounds': 1
}).encode('utf-8')
try:
    req = urllib.request.Request(base + '/consultation/start', data=body,
                                 headers={'Content-Type': 'application/json'}, method='POST')
    resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))
    print(f'\n=== API /consultation/start ===')
    sid = resp.get('session_id', 'N/A')
    status = resp.get('status', 'N/A')
    print(f'  session_id: {sid}')
    print(f'  status: {status}')
    print(f'  议题: {resp.get("topic","")[:40]}')
    print(f'  agents: {[a["name"] for a in resp.get("agents",[])]}')
except Exception as e:
    print(f'/start错误: {e}')
