import re

with open(r'D:\openclaw-team\workspace-pm\wudao\static\index_recovered.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove line number prefixes (like "1\t" or "1 " at start of each line)
lines = content.split('\n')
cleaned = []
for line in lines:
    # Remove leading line number + tab/space
    cleaned.append(re.sub(r'^\d+\s', '', line))

with open(r'D:\openclaw-team\workspace-pm\wudao\static\index_recovered.html', 'w', encoding='utf-8') as f:
    f.write('\n'.join(cleaned))

print(f"Cleaned, {len(cleaned)} lines")
# Verify
with open(r'D:\openclaw-team\workspace-pm\wudao\static\index_recovered.html', 'r') as f:
    first = f.readline()
print(f"First line: {first[:50]}")
