with open('static/index.html', 'rb') as f:
    raw = bytearray(f.read())

# Walk through and find non-ASCII bytes that broke
# The problem: some multi-byte UTF-8 sequences got individual byte 0x89 -> 0x3f corruption
# Let's look for the pattern: 0x89 followed by non-valid continuation
fixes = 0
i = 0
while i < len(raw) - 1:
    if raw[i] == 0x89:  # potential damaged continuation byte
        # Show context
        ctx = raw[max(0,i-4):min(len(raw),i+6)]
        print(f'Found 0x89 at byte {i}: context={" ".join(f"{b:02x}" for b in ctx)}')
        print(f'  As text: {ctx.decode("utf-8", errors="replace")}')
        fixes += 1
    i += 1

print(f'\nTotal 0x89 occurrences: {fixes}')

# Also check for 0x80
i = 0
fixes2 = 0
while i < len(raw):
    if raw[i] == 0x80:
        fixes2 += 1
    i += 1
print(f'Total 0x80 occurrences: {fixes2}')
