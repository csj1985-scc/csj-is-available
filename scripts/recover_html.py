import json, re

with open(r'C:\Users\CaoSu\.claude\projects\C--WINDOWS-system32\8333e02b-fa9a-40fd-9f21-b0b03efebd21.jsonl', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if '悟道' in line and 'index.html' in line and 'three' in line:
        print(f"Line {i}: length={len(line)}")
        if len(line) > 10000:
            match = re.search(r'<!DOCTYPE html>.*?</html>', line, re.DOTALL)
            if match:
                html = match.group()
                html = html.replace('\\n', '\n').replace('\\t', '\t')
                html = html.replace('\\"', '"')
                html = html.replace('\\\\', '\\')
                with open(r'D:\openclaw-team\workspace-pm\wudao\static\index_recovered.html', 'w', encoding='utf-8') as f:
                    f.write(html)
                print(f"Recovered! {len(html)} chars")
                break
