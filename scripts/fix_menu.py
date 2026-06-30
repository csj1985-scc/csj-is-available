with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

old = '<span>聊天</span><span>会议室</span><span>管理</span><span>关于</span>'
new = '<span data-href="/">聊天</span><span data-href="/room">会议室</span><span data-href="/admin">管理</span><span data-href="/about">关于</span>'
html = html.replace(old, new)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

# Verify
with open('static/index.html', 'r', encoding='utf-8') as f:
    h2 = f.read()
print('data-href present:', 'data-href' in h2)
print('Size:', len(h2), 'bytes')
