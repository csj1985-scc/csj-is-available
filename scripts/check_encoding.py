with open('D:\\openclaw-team\\workspace-pm\\wudao\\static\\index.html', 'rb') as f:
    raw = f.read()
print('File size:', len(raw))
# Check for UTF-8 encoding
try:
    raw.decode('utf-8')
    print('UTF-8 decode: OK')
except UnicodeDecodeError as e:
    print('UTF-8 decode FAILED:', e)
    pos = e.start
    print('Bytes around position {}: {}'.format(pos, raw[pos-2:pos+6]))
    print('Hex:', ':'.join(f'{x:02x}' for x in raw[pos-2:pos+6]))

# Read via GBK
try:
    text_gbk = raw.decode('gbk', errors='replace')
    print('GBK decode: OK, length:', len(text_gbk))
except:
    print('GBK decode FAILED')

# Try to find what encoding it actually is
import chardet
result = chardet.detect(raw)
print('Charset detected:', result)
