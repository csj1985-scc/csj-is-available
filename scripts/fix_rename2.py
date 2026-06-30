"""Fix pGeo variable naming conflict"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    h = f.read()

# The issue: new entanglement code uses pGeo too, conflict with old particle system pGeo
# Simple approach: just rename old pGeo to pGeoOld
h = h.replace(
    'const pts = new THREE.Points(pGeo, new THREE.PointsMaterial({',
    'const pts = new THREE.Points(pGeoOld, new THREE.PointsMaterial({'
)
h = h.replace('const pGeo = new THREE.BufferGeometry();\n\tpGeo.setAttribute', 'const pGeoOld = new THREE.BufferGeometry();\n\tpGeoOld.setAttribute', 1)

# Also fix the entanglement pGeo references
# Already should be pGeoEnt. Let's check
import re

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(h)

# Verify
with open('static/index.html', 'r', encoding='utf-8') as f:
    h2 = f.read()

# Count declarations
pgeo_decl = len(re.findall(r'(?:const|let|var)\s+pGeo\b', h2))
pgeo_ref = len(re.findall(r'\bpGeo\b', h2))
print(f'pGeo declarations: {pgeo_decl}')
print(f'pGeo references: {pgeo_ref}')

# Check braces
opens = h2.count('{'); closes = h2.count('}')
print(f'Braces: {opens} {closes} {"OK" if opens==closes else "ERR"}')

# Check no duplicate declarations
for v in ['pGeo', 'pMat', 'pts', 'linkGeo', 'linkMat', 'linkLines']:
    decls = re.findall(rf'(?:const|let|var)\s+{v}\b', h2)
    if len(decls) > 1:
        print(f'WARN: {v} declared {len(decls)} times')
    else:
        print(f'OK: {v} declared {len(decls)} time')
