"""Find and fix encoding corruption in index.html"""
with open('static/index.html', 'rb') as f:
    raw = f.read()

# Try to decode as UTF-8 with replace to find bad spots
text = raw.decode('utf-8', errors='replace')
# Find replacement characters
bad_positions = []
for i, ch in enumerate(text):
    if ch == '\ufffd':
        bad_positions.append(i)

print(f'Found {len(bad_positions)} bad characters')
if bad_positions:
    print(f'First bad at string pos: {bad_positions[0]}')
    # Convert string pos back to byte pos
    byte_pos = len(text[:bad_positions[0]].encode('utf-8', errors='replace'))
    print(f'Approx byte position: {byte_pos}')
    print(f'Surrounding bytes: {raw[byte_pos-5:byte_pos+10]}')

# Show corrupt section as hex
region = raw[360:400]
print(f'\nHex of bytes 360-400:')
print(' '.join(f'{b:02x}' for b in region))
