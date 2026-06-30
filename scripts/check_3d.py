"""检查首页是否包含3D大脑+触手元素"""
import urllib.request

req = urllib.request.Request('http://localhost:8002/')
resp = urllib.request.urlopen(req, timeout=5)
html = resp.read().decode('utf-8')

checks = [
    ('Three.js', 'three' in html.lower()),
    ('canvas 3D', '<canvas' in html.lower()),
    ('大脑', '大脑' in html),
    ('章鱼/octopus', '章鱼' in html or 'octopus' in html.lower()),
    ('触手', '触手' in html),
    ('tentacle', 'tentacle' in html.lower()),
    ('scene/场景', 'scene' in html.lower()),
    ('renderer', 'renderer' in html.lower()),
    ('brain', 'brain' in html.lower()),
]

print('首页元素检查:')
for label, ok in checks:
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {label}')

# 提取含有关键词的行看看具体内容
print('\n--- Three.js/大脑 相关片段 ---')
lines = html.split('\n')
for i, line in enumerate(lines):
    lower = line.lower()
    if any(kw in lower for kw in ['three', 'brain', '大脑', '触手', 'tentacle', 'scene', 'renderer']):
        print(f'  L{i}: {line.strip()[:150]}')

print(f'\n页面总行数: {len(lines)}')
