# Fix: rename conflicting pGeo variable in entanglement section
with open('static/index.html', 'r', encoding='utf-8') as f:
    h = f.read()

# Only rename the first occurrence (the entanglement one)
# The old particles at bottom use 'pGeo' too - rename entanglement one to 'pGeoEnt'
h = h.replace('const pGeo = new THREE.BufferGeometry();\npGeo.setAttribute', 'const pGeoEnt = new THREE.BufferGeometry();\npGeoEnt.setAttribute', 1)
h = h.replace('const particleSystem = new THREE.Points(pGeo,', 'const particleSystem = new THREE.Points(pGeoEnt,')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(h)

# Verify
with open('static/index.html', 'r', encoding='utf-8') as f:
    h2 = f.read()

import re
# Count variable declarations
pgeos = re.findall(r'\bpGeo\b', h2)
pgeoents = re.findall(r'\bpGeoEnt\b', h2)
print(f'pGeo count: {len(pgeos)} (should be 3: const, setAttribute position, setAttribute color)')
print(f'pGeoEnt count: {len(pgeoents)} (should be 4: const, 2x setAttribute, new Points)')

# Check braces
opens = h2.count('{')
closes = h2.count('}')
print(f'Braces: {opens} {closes} {"OK" if opens==closes else "ERR"}')

# Check for any other duplicate JS declarations
for var_name in ['pts', 'pMat', 'pGeo']:
    decls = re.findall(rf'(?:const|let|var)\s+{var_name}\b', h2)
    if len(decls) > 1:
        print(f'WARN: {var_name} declared {len(decls)} times')
    else:
        print(f'OK: {var_name} declared {len(decls)} time')
