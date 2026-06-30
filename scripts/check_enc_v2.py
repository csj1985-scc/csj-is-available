with open('static/index.html', 'rb') as f:
    raw = f.read()

text = raw.decode('gbk', errors='replace')
idx = text.find('悟道')
print('悟道 found at:', idx)
idx2 = text.find('setWudaoState')
print('setWudaoState found at:', idx2)

region = raw[360:380]
print('Region hex:', region.hex())
rtext = raw.decode('gbk', errors='replace')
print('Region as text:', rtext[360:380])
