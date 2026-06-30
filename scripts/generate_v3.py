#!/usr/bin/env python
"""重新生成悟道前端——仿昨晚00点左右的青色主题版"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

html = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>悟道</title>
<style>
  body {
    margin: 0; overflow: hidden; background: #050810;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    color: #c8d6e5;
  }
  .top {
    position: fixed; top: 20px; left: 24px; z-index: 10;
    display: flex; align-items: center; gap: 14px;
    pointer-events: none;
  }
  .logo {
    font-size: 29px; font-weight: 700; letter-spacing: 6px;
    background: linear-gradient(135deg, #40e0d0, #00bcd4);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    text-shadow: 0 0 40px rgba(64,224,208,0.2);
  }
  .menu-btn {
    pointer-events: auto; cursor: pointer;
    font-size: 22px; opacity: 0.5; transition: opacity 0.3s;
    background: none; border: none; color: #c8d6e5;
  }
  .menu-btn:hover { opacity: 0.9; }
  .hint {
    position: fixed; bottom: 120px; left: 50%; transform: translateX(-50%);
    z-index: 10; font-size: 11px; letter-spacing: 3px;
    color: rgba(200,214,229,0.2);
  }
  .menu {
    position: fixed; top: 60px; left: 24px; z-index: 10;
    display: none; flex-direction: column; gap: 8px;
    background: rgba(5,8,16,0.85); border: 1px solid rgba(64,224,208,0.15);
    border-radius: 10px; padding: 8px 0; min-width: 130px;
    backdrop-filter: blur(8px); pointer-events: auto;
  }
  .menu.show { display: flex; }
  .menu-item {
    padding: 8px 18px; font-size: 12px; letter-spacing: 2px;
    cursor: pointer; transition: background 0.2s; color: #8899aa;
  }
  .menu-item:hover { background: rgba(64,224,208,0.08); color: #40e0d0; }
  .status {
    position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%);
    z-index: 10; font-size: 10px; letter-spacing: 4px;
    color: rgba(64,224,208,0.3);
  }
  .input-area {
    position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%);
    z-index: 10; display: flex; gap: 12px; align-items: center;
    pointer-events: none;
  }
  .input-area input {
    pointer-events: auto; width: 300px; max-width: 60vw;
    background: rgba(5,8,16,0.6); border: 1px solid rgba(64,224,208,0.12);
    border-radius: 20px; padding: 10px 20px;
    font-size: 13px; color: #c8d6e5; outline: none;
    transition: border-color 0.3s; letter-spacing: 1px;
  }
  .input-area input:focus { border-color: rgba(64,224,208,0.4); }
  .input-area input::placeholder { color: rgba(200,214,229,0.2); }
  .send-btn {
    pointer-events: auto; background: transparent; border: none;
    color: rgba(64,224,208,0.5); font-size: 13px; letter-spacing: 4px;
    cursor: pointer; padding: 8px 0; transition: color 0.2s; font-family: inherit;
  }
  .send-btn:hover { color: rgba(64,224,208,0.8); }
  .chat-area {
    position: fixed; right: 28px; top: 56px; bottom: 90px;
    width: 340px; overflow-y: auto; z-index: 5;
    pointer-events: none;
  }
  .chat-area::-webkit-scrollbar { display: none; }
  .chat-bubble {
    pointer-events: auto; margin-bottom: 12px;
    padding: 12px 16px; border-radius: 12px 4px 12px 12px;
    background: rgba(64, 224, 208, 0.08);
    border: 1px solid rgba(64, 224, 208, 0.12);
    font-size: 13px; line-height: 1.6; color: #c8d6e5;
    word-break: break-word; position: relative;
    backdrop-filter: blur(4px);
    animation: fadeIn 0.3s ease;
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateX(10px); } to { opacity: 1; transform: translateX(0); } }
  .chat-bubble .copy-btn {
    position: absolute; bottom: 4px; right: 8px;
    font-size: 10px; opacity: 0.3; cursor: pointer;
    background: none; border: none; color: #c8d6e5;
    transition: opacity 0.2s; font-family: inherit;
  }
  .chat-bubble .copy-btn:hover { opacity: 0.8; }
  .speech-indicator {
    position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%);
    z-index: 100; display: none;
    font-size: 11px; letter-spacing: 4px; color: rgba(64, 224, 208, 0.6);
    background: rgba(0,0,0,0.6); padding: 16px 32px;
    border-radius: 8px; border: 1px solid rgba(64, 224, 208, 0.2);
    pointer-events: none;
  }
</style>
</head>
<body>

<div class="top">
  <div class="logo">悟道</div>
  <button class="menu-btn" id="menuToggle">⋯</button>
</div>
<div class="hint">拖动旋转 · 滚轮缩放</div>
<div class="menu" id="menu">
  <div class="menu-item" id="m-auto" onclick="window.toggleAuto()">自转：开</div>
  <div class="menu-item" onclick="window.resetView()">重置视角</div>
  <div class="menu-item" onclick="open('/admin', '_blank')">管理面板</div>
</div>
<div class="status" id="status">呼吸中</div>

<div class="input-area">
  <input id="msg" type="text" placeholder="说点什么…" autocomplete="off" />
  <button class="send-btn" onclick="window.sendText()">发送</button>
</div>

<div class="speech-indicator" id="speechIndicator"></div>
<div class="chat-area" id="chatArea"></div>

<script type="importmap">
{ "imports": {
  "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
} }
</script>

<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

// ---- 3D Scene ----
const scene = new THREE.Scene();
scene.background = new THREE.Color('#050810');
const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
camera.position.set(14, 8, 22);
camera.lookAt(0, 0, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.toneMapping = THREE.ReinhardToneMapping;
document.body.prepend(renderer.domElement);

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0.3, 0.2, 0.05);
composer.addPass(bloom);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 5;
controls.maxDistance = 50;

// ---- Torus Knot ----
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
scene.add(glow);

// ---- Lights ----
scene.add(new THREE.AmbientLight(0x112244, 0.5));
const d = new THREE.DirectionalLight(0x40e0d0, 1.0); d.position.set(5, 10, 7); scene.add(d);
const d2 = new THREE.DirectionalLight(0x00bcd4, 0.5); d2.position.set(-5, -3, -5); scene.add(d2);

// ---- Particles ----
const N = 500;
const pos = new Float32Array(N * 3);
const col = new Float32Array(N * 3);
for (let i = 0; i < N; i++) {
  const r = 4 + Math.random() * 8;
  const th = Math.random() * Math.PI * 2;
  const ph = Math.random() * Math.PI * 2;
  pos[i*3] = Math.sin(th) * Math.cos(ph) * r;
  pos[i*3+1] = Math.sin(th) * Math.sin(ph) * r;
  pos[i*3+2] = Math.cos(th) * r;
  const c = new THREE.Color(0x40e0d0).lerp(new THREE.Color(0x00bcd4), Math.random());
  col[i*3] = c.r; col[i*3+1] = c.g; col[i*3+2] = c.b;
}
const pGeo = new THREE.BufferGeometry();
pGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
pGeo.setAttribute('color', new THREE.BufferAttribute(col, 3));
const pts = new THREE.Points(pGeo, new THREE.PointsMaterial({
  size: 0.05, vertexColors: true, transparent: true, opacity: 0.4,
  blending: THREE.AdditiveBlending, depthWrite: false,
}));
scene.add(pts);

// ---- State control ----
let autoRotate = true;
const anim = { speed: 1 };

function setState(s) {
  const el = document.getElementById('status');
  if (s === 'idle') { anim.speed = 1; el.textContent = '呼吸中'; bloom.strength = 0.3; }
  else if (s === 'listening') { anim.speed = 0.3; el.textContent = '倾听中'; bloom.strength = 0.2; }
  else if (s === 'thinking') { anim.speed = 2.5; el.textContent = '思考中'; bloom.strength = 0.6; }
}

window.setIdle = function() { setState('idle'); };
window.setListen = function() { setState('listening'); };
window.setThink = function() { setState('thinking'); };

window.toggleAuto = function() {
  autoRotate = !autoRotate;
  document.getElementById('m-auto').textContent = '自转：' + (autoRotate ? '开' : '关');
  controls.autoRotate = autoRotate;
  controls.autoRotateSpeed = 1.0;
};

window.resetView = function() {
  camera.position.set(14, 8, 22);
  controls.target.set(0, 0, 0);
  controls.update();
};

// ---- WebSocket ----
let ws = null;
function connectWS() {
  try {
    if (ws && ws.readyState <= WebSocket.OPEN) return;
    ws = new WebSocket('ws://localhost:8000/ws');
    ws.onopen = function() {
      console.log('[WS] 已连接');
      ws.send(JSON.stringify({ type: 'message', text: '' }));
      addBubble('system', '悟道已连接');
    };
    ws.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        switch (data.type) {
          case 'tentacle_state':
            if (data.state === 'idle') setState('idle');
            else if (data.state === 'listening') setState('listening');
            else if (data.state === 'thinking') setState('thinking');
            break;
          case 'partial_text':
            document.getElementById('speechIndicator').textContent = data.text;
            break;
          case 'voice_result':
            if (data.text) addBubble('user', data.text);
            if (data.reply) addBubble('ai', data.reply);
            break;
          case 'message':
            addBubble('ai', data.text);
            break;
          case 'tts_audio':
            if (data.audio) {
              var raw = atob(data.audio);
              var buf = new ArrayBuffer(raw.length);
              var u8 = new Uint8Array(buf);
              for (var i = 0; i < raw.length; i++) u8[i] = raw.charCodeAt(i);
              var blob = new Blob([buf], { type: 'audio/mp3' });
              var url = URL.createObjectURL(blob);
              var player = document.getElementById('tts-player');
              if (!player) {
                player = document.createElement('audio');
                player.id = 'tts-player';
                player.style.display = 'none';
                document.body.appendChild(player);
              }
              player.onended = function() { URL.revokeObjectURL(url); };
              player.src = url;
              player.play().catch(function(){});
            }
            break;
        }
      } catch(err) {}
    };
    ws.onclose = function() { setTimeout(connectWS, 3000); };
    ws.onerror = function() {};
  } catch(err) { setTimeout(connectWS, 3000); }
}
connectWS();

// ---- Chat ----
function addBubble(role, text) {
  var el = document.getElementById('chatArea');
  var div = document.createElement('div');
  div.className = 'chat-bubble';
  div.innerHTML = text + '<button class="copy-btn" onclick="navigator.clipboard.writeText(this.parentElement.textContent.replace(\'复制\',\'\').trim()).catch(function(){})">复制</button>';
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

window.sendText = function() {
  var input = document.getElementById('msg');
  var text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  addBubble('user', text);
  ws.send(JSON.stringify({ type: 'message', text: text }));
  input.value = '';
};

document.addEventListener('keydown', function(e) {
  // Enter to send text
  if (e.key === 'Enter' && document.activeElement === document.getElementById('msg')) {
    sendText();
    return;
  }
  // Space for voice
  if (e.key === ' ' && e.target === document.body) {
    e.preventDefault();
    var indicator = document.getElementById('speechIndicator');
    indicator.style.display = 'block';
    indicator.textContent = '倾听...';
    setState('listening');
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'voice_start', session_id: 'main' }));
    }
    startMic();
  }
});

document.addEventListener('keyup', function(e) {
  if (e.key === ' ') {
    e.preventDefault();
    var indicator = document.getElementById('speechIndicator');
    indicator.style.display = 'none';
    indicator.textContent = '';
    setState('thinking');
    stopMic();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'voice_end' }));
    }
  }
});

// ---- Menu toggle ----
document.getElementById('menuToggle').addEventListener('click', function() {
  document.getElementById('menu').classList.toggle('show');
});
document.addEventListener('click', function(e) {
  if (!e.target.closest('.menu') && !e.target.closest('.menu-btn')) {
    document.getElementById('menu').classList.remove('show');
  }
});

// ---- Mic capture (PCM16 via AnalyserNode) ----
let isCapturing = false;
let micStream = null;
let micCtx = null;
let micInterval = null;

function startMic() {
  if (isCapturing) return;
  isCapturing = true;
  navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    .then(function(s) {
      micStream = s;
      micCtx = new (window.AudioContext || window.webkitAudioContext)();
      var source = micCtx.createMediaStreamSource(s);
      var analyser = micCtx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      var len = analyser.frequencyBinCount;
      var arr = new Uint8Array(len);
      micInterval = setInterval(function() {
        analyser.getByteTimeDomainData(arr);
        var pcm = new Int16Array(len);
        for (var i = 0; i < len; i++) {
          var v = (arr[i] - 128) / 128;
          pcm[i] = Math.max(-32768, Math.min(32767, v * 32768));
        }
        var raw = '';
        var bytes = new Uint8Array(pcm.buffer);
        for (var j = 0; j < bytes.length; j++) raw += String.fromCharCode(bytes[j]);
        var b64 = btoa(raw);
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'voice_chunk', audio: b64 }));
        }
      }, 100);
    })
    .catch(function(err) {
      console.error('mic error:', err);
      isCapturing = false;
    });
}

function stopMic() {
  isCapturing = false;
  if (micInterval) { clearInterval(micInterval); micInterval = null; }
  if (micCtx) { micCtx.close().catch(function(){}); micCtx = null; }
  if (micStream) { micStream.getTracks().forEach(function(t){t.stop();}); micStream = null; }
}

// ---- Animation ----
const clock = new THREE.Clock();
function animate() {
  requestAnimationFrame(animate);
  var t = clock.getElapsedTime();
  var s = anim.speed;

  knot.rotation.x += 0.002 * s;
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
  pts.geometry.attributes.position.needsUpdate = true;

  controls.update();
  composer.render();
}
animate();

// ---- Resize ----
window.addEventListener('resize', function() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  composer.setSize(window.innerWidth, window.innerHeight);
});

</script>
</body>
</html>'''

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

# Verify
with open('static/index.html', 'r', encoding='utf-8') as f:
    h = f.read()

print('=== 验证 ===')
checks = ['setState', 'setIdle', 'setListen', 'setThink', 'getUserMedia',
          'getByteTimeDomainData', 'voice_start', 'voice_chunk', 'voice_end',
          'tts_audio', 'sendText', 'toggleAuto', 'resetView',
          'localhost:8000/ws', 'addBubble', 'chat-bubble',
          'TorusKnotGeometry', 'EffectComposer']
for k in checks:
    print('OK' if k in h else 'MISS', k)
print(f'大小: {len(h)} bytes')
print(f'括号: {h.count("{")} {h.count("}")}')
print('ScriptProcessor:', 'ScriptProcessor' in h)
print('MediaRecorder:', 'MediaRecorder' in h)
