#!/usr/bin/env python
"""替换3D形态：扭结环 → 纠缠粒子态  (其他UI不变)"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 找到 TorusKnot 及其相关的代码段，替换为纠缠粒子态
old_3d_block = """// ---- Torus Knot ----
const knot = new THREE.Mesh(
  new THREE.TorusKnotGeometry(2.5, 0.7, 128, 16),
  new THREE.MeshPhysicalMaterial({
    color: 0x40e0d0, emissive: 0x008080,
    roughness: 0.2, metalness: 0.3, clearcoat: 0.1,
    transparent: true, opacity: 0.85,
  })
);
scene.add(knot);

// ---- Inner glow ----
const glow = new THREE.Mesh(
  new THREE.SphereGeometry(0.5, 20, 20),
  new THREE.MeshBasicMaterial({ color: 0x40e0d0, transparent: true, opacity: 0.5 })
);
scene.add(glow);"""

new_3d_block = """// ---- 纠缠粒子态 ----
// 双色粒子云团，互相环绕，带发光连线

// 粒子参数
const PAIR_COUNT = 200;   // 每团粒子数
const CLOUD_RADIUS = 1.8; // 云团半径
const ORBIT_RADIUS = 2.2; // 轨道半径
const LINK_DIST = 0.8;    // 两团粒子之间链的最短距离

const particlePositions = new Float32Array(PAIR_COUNT * 2 * 3);
const particleColors = new Float32Array(PAIR_COUNT * 2 * 3);
const particleSizes = new Float32Array(PAIR_COUNT * 2);
const pSeed = []; // 存储每个粒子的随机角度偏移

for (let i = 0; i < PAIR_COUNT * 2; i++) {
  // 球体内随机分布
  const theta = Math.random() * Math.PI * 2;
  const phi = Math.acos(2 * Math.random() - 1);
  const r = Math.cbrt(Math.random()) * CLOUD_RADIUS;
  // 存种子用于动画
  pSeed.push({ theta, phi, r, offset: Math.random() * Math.PI * 2 });

  particleSizes[i] = 0.04 + Math.random() * 0.08;
}

// 颜色：团A青色（#40e0d0），团B蓝紫（#8866ff）
function setColor(i, r, g, b) {
  particleColors[i*3] = r;
  particleColors[i*3+1] = g;
  particleColors[i*3+2] = b;
}

for (let i = 0; i < PAIR_COUNT; i++) {
  const cA = new THREE.Color(0x40e0d0);
  const cB = new THREE.Color(0x8866ff);
  setColor(i, cA.r, cA.g, cA.b);
  setColor(PAIR_COUNT + i, cB.r, cB.g, cB.b);
}

const pGeo = new THREE.BufferGeometry();
pGeo.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));
pGeo.setAttribute('color', new THREE.BufferAttribute(particleColors, 3));
pGeo.setAttribute('size', new THREE.BufferAttribute(particleSizes, 1));

const pMat = new THREE.PointsMaterial({
  size: 0.08,
  vertexColors: true,
  transparent: true,
  opacity: 0.8,
  blending: THREE.AdditiveBlending,
  depthWrite: false,
});
const particleSystem = new THREE.Points(pGeo, pMat);
scene.add(particleSystem);

// ---- 纠缠连线（显示邻近粒子对之间的连接）----
// 用 LineSegments 画连线
const MAX_LINKS = PAIR_COUNT * 3; // 最多这么多连线
const linkPos = new Float32Array(MAX_LINKS * 2 * 3);
const linkGeo = new THREE.BufferGeometry();
linkGeo.setAttribute('position', new THREE.BufferAttribute(linkPos, 3));
const linkMat = new THREE.LineBasicMaterial({
  color: 0x40e0d0,
  transparent: true,
  opacity: 0.12,
  blending: THREE.AdditiveBlending,
});
const linkLines = new THREE.LineSegments(linkGeo, linkMat);
scene.add(linkLines);

// 记录活跃连线数
let activeLinks = 0;

// ---- 替换扭结环和glow（移除旧引用）----
let knot = null; // 不再使用
let glow = null;
"""

# 找到动画循环部分，替换为缠结粒子的动画
old_anim_block = """  knot.rotation.x += 0.002 * s;
  knot.rotation.y += 0.004 * s;
  var scale = 1 + Math.sin(t * 0.4 * s) * 0.06;
  knot.scale.setScalar(scale);

  glow.scale.setScalar(1 + Math.sin(t * 0.6 * s) * 0.15);
  glow.material.opacity = 0.4 + Math.sin(t * 0.5 * s) * 0.15;

  var pa = pts.geometry.attributes.position.array;
  for (var i = 0; i < pa.length; i += 3) {
    var r = Math.sqrt(pa[i]*pa[i] + pa[i+1]*pa[i+1] + pa[i+2]*pa[i+2]);
    var th = Math.atan2(pa[i+1], pa[i]) + 0.001 * s;
    var ph = Math.acos(pa[i+2] / Math.max(r, 0.1)) + 0.0006 * s;
    pa[i] = Math.sin(th) * Math.cos(ph) * r;
    pa[i+1] = Math.sin(th) * Math.sin(ph) * r;
    pa[i+2] = Math.cos(th) * r;
  }
  pts.geometry.attributes.position.needsUpdate = true;"""

new_anim_block = """  // 纠缠粒子动画
  var pPos = particleSystem.geometry.attributes.position.array;

  // 团A绕Y轴旋转，团B反向绕Y轴 + 上下摆动
  var angleA = t * 0.3 * s;
  var angleB = -t * 0.3 * s + Math.PI;

  for (var i = 0; i < PAIR_COUNT * 2; i++) {
    var seed = pSeed[i];
    // 根据时间变化的有效半径（呼吸效应）
    var breatheR = seed.r * (1 + Math.sin(t * 0.5 * s + seed.offset) * 0.15);
    // 本地坐标
    var lx = breatheR * Math.sin(seed.theta) * Math.cos(seed.phi);
    var ly = breatheR * Math.sin(seed.theta) * Math.sin(seed.phi);
    var lz = breatheR * Math.cos(seed.theta);

    // 轨道位置
    var orbitAngle = i < PAIR_COUNT ? angleA : angleB;
    // 加上每个粒子的随机相位偏移
    orbitAngle += seed.offset * 0.3;

    var ox = Math.cos(orbitAngle) * ORBIT_RADIUS;
    var oz = Math.sin(orbitAngle) * ORBIT_RADIUS;
    var oy = Math.sin(orbitAngle * 0.7 + seed.offset) * 0.4; // 上下摆动

    pPos[i*3] = lx + ox;
    pPos[i*3+1] = ly + oy;
    pPos[i*3+2] = lz + oz;
  }
  particleSystem.geometry.attributes.position.needsUpdate = true;

  // 更新连线：计算团A和团B最近粒子对
  activeLinks = 0;
  var lPos = linkLines.geometry.attributes.position.array;
  for (var a = 0; a < PAIR_COUNT && activeLinks < MAX_LINKS; a += 1) {
    var ax = pPos[a*3], ay = pPos[a*3+1], az = pPos[a*3+2];
    for (var b = PAIR_COUNT; b < PAIR_COUNT * 2 && activeLinks < MAX_LINKS; b += 1) {
      var bx = pPos[b*3], by = pPos[b*3+1], bz = pPos[b*3+2];
      var dx = ax - bx, dy = ay - by, dz = az - bz;
      var dist = Math.sqrt(dx*dx + dy*dy + dz*dz);
      if (dist < LINK_DIST) {
        var idx = activeLinks * 6;
        lPos[idx] = ax; lPos[idx+1] = ay; lPos[idx+2] = az;
        lPos[idx+3] = bx; lPos[idx+4] = by; lPos[idx+5] = bz;
        activeLinks++;
      }
    }
  }
  linkLines.geometry.setDrawRange(0, activeLinks * 2);
  linkLines.geometry.attributes.position.needsUpdate = true;

  // 原粒子系统动画
  var pa = pts.geometry.attributes.position.array;
  for (var i = 0; i < pa.length; i += 3) {
    var r = Math.sqrt(pa[i]*pa[i] + pa[i+1]*pa[i+1] + pa[i+2]*pa[i+2]);
    var th = Math.atan2(pa[i+1], pa[i]) + 0.001 * s;
    var ph = Math.acos(pa[i+2] / Math.max(r, 0.1)) + 0.0006 * s;
    pa[i] = Math.sin(th) * Math.cos(ph) * r;
    pa[i+1] = Math.sin(th) * Math.sin(ph) * r;
    pa[i+2] = Math.cos(th) * r;
  }
  pts.geometry.attributes.position.needsUpdate = true;"""

html = html.replace(old_3d_block, new_3d_block)
html = html.replace(old_anim_block, new_anim_block)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

# Verify
with open('static/index.html', 'r', encoding='utf-8') as f:
    h = f.read()
print('纠缠粒子态:', 'PAIR_COUNT' in h)
print('扭结环残量:', 'TorusKnot' not in h)
print('glow残量:', h.count('glow'))
print('连线:', 'LineSegments' in h)
print('大小:', len(h), 'bytes')
print('括号:', h.count('{'), h.count('}'))
