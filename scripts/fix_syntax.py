"""Fix broken JS syntax"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Fix the broken comment
html = html.replace('/* var mediaRecorder = null;', '')
html = html.replace('var isMicRecording = false;', 'var isMicRecording = false;')
html = html.replace('var /* mediaChunks = [];', '')

# Clean up any artifact
html = html.replace('// ---- 麦克风流式录音 (PCM16 direct capture ----', '')
html = html.replace('/* mediaChunks = [];', '')

# Deduplicate: if there are two startRecording or stopRecording, remove one
count = html.count('function startRecording()')
if count > 1:
    # Find and remove the duplicate (the first one that has the old comment approach)
    print(f'Found {count} startRecording functions')
    
# Fix: the real issue is the duplicate with missing paren close in comment
# Let's just regenerate the clean version of the mic section

# Find exact section to replace
old_comment = """});

// ---- 麦克风流式录音 (PCM16 direct capture ----
var micStream = null;
/* var mediaRecorder = null;
var isMicRecording = false;
var /* mediaChunks = [];

function startRecording()"""
new_clean = """});

var micStream = null;
var isMicRecording = false;

function startRecording()"""

if old_comment in html:
    html = html.replace(old_comment, new_clean)
    print('Fixed broken section')
else:
    print('Could not find exact broken section, trying alternative...')
    # Just aggressively clean up
    lines = html.split('\n')
    cleaned = []
    for i, line in enumerate(lines):
        if 'PCM16 direct capture' in line:
            continue
        if line.strip().startswith('/* var ') or line.strip().startswith('var /* '):
            continue
        cleaned.append(line)
    html = '\n'.join(cleaned)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

# Verify syntax - extract script content and check
import re
m = re.search(r'<script[^>]*>([\s\S]*)</script>', html)
if m:
    script = m.group(1)
    print(f'Script length: {len(script)}')
    # Simple check - count braces
    opens = script.count('{')
    closes = script.count('}')
    print(f'Brace balance: open={opens} close={closes}')
    print('Balanced' if opens == closes else f'IMBALANCE: {opens - closes}')
else:
    print('No script tag found!')
