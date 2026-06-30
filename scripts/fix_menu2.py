with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Fix cursor
html = html.replace('#menu span { cursor: default;', '#menu span { cursor: pointer;')

# 2. Add data-href to menu spans
html = html.replace(
    '<span>聊天</span><span>会议室</span><span>管理</span><span>关于</span>',
    '<span data-href="/">聊天</span><span data-href="/room">会议室</span><span data-href="/admin">管理</span><span data-href="/about">关于</span>'
)

# 3. Add click handler JS after connectWS()
old_js = 'connectWS();\n\n// ---- setWudaoState ----'
new_js = '''connectWS();

// ---- Menu click navigation ----
document.querySelectorAll('#menu span').forEach(function(el) {
  el.addEventListener('click', function() {
    var href = el.getAttribute('data-href');
    if (href) window.location.href = href;
  });
});

// ---- setWudaoState ----'''
html = html.replace(old_js, new_js)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

with open('static/index.html', 'r', encoding='utf-8') as f:
    h2 = f.read()

print('data-href:', 'data-href' in h2)
print('pointer:', 'cursor: pointer' in h2)
print('click handler:', 'data-href' in h2 and 'click' in h2)
print('WuDao title:', '悟道' in h2)
print('Size:', len(h2), 'bytes')
