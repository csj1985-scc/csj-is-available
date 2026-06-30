#!/usr/bin/env python
"""从头生成干净的前端页面——不再叠补丁"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

html = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>悟道</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  background: #050810; color: rgba(200,208,224,0.85);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  overflow: hidden; width: 100vw; height: 100vh;
}
#canvas-container { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
#chat-panel {
  position: fixed; top: 0; right: 0; width: 360px; height: 100%;
  z-index: 10; display: flex; flex-direction: column;
  background: rgba(5,8,16,0.6); backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
#chat-header {
  padding: 24px 20px 12px; font-size: 12px; color: rgba(200,208,224,0.4);
  letter-spacing: 2px; border: none; flex-shrink: 0;
}
#chat-messages {
  flex: 1; overflow-y: auto; padding: 0 20px 16px;
}
#chat-messages::-webkit-scrollbar { width: 3px; }
#chat-messages::-webkit-scrollbar-thumb { background: rgba(100,180,255,0.2); border-radius: 2px; }
.msg { margin-bottom: 12px; font-size: 14px; line-height: 1.5; word-break: break-word; display: flex; gap: 8px; align-items: flex-start; }
.msg-text { flex: 1; color: rgba(200,208,224,0.85); }
.msg-time { font-size: 11px; color: rgba(200,208,224,0.25); flex-shrink: 0; margin-top: 2px; }
.msg-copy {
  flex-shrink: 0; cursor: pointer; opacity: 0.3; font-size: 11px;
  padding: 2px 6px; border: none; background: none; color: rgba(200,208,224,0.5);
  transition: opacity 0.15s; margin-top: 2px;
}
.msg-copy:hover { opacity: 0.8; }
#chat-footer {
  padding: 12px 20px 24px; flex-shrink: 0; display: flex; align-items: center; gap: 12px;
}
#status-label {
  font-size: 12px; color: rgba(200,208,224,0.35); letter-spacing: 1px;
  transition: color 0.3s;
}
#status-label.active { color: rgba(100,180,255,0.7); }
#mic-btn {
  width: 32px; height: 32px; border-radius: 50%; background: rgba(100,180,255,0.15);
  border: none; cursor: pointer; display: flex; align-items: center; justify-content: center;
  transition: all 0.2s; flex-shrink: 0;
}
#mic-btn:hover { background: rgba(100,180,255,0.25); }
#mic-btn.active { background: rgba(100,180,255,0.4); box-shadow: 0 0 12px rgba(100,180,255,0.3); }
#mic-btn svg { width: 16px; height: 16px; fill: rgba(200,208,224,0.6); }
#mic-btn.active svg { fill: rgba(200,208,224,0.9); }
#state-dot {
  width: 6px; height: 6px; border-radius: 50%; background: rgba(100,180,255,0.2);
  transition: all 0.3s; flex-shrink: 0;
}
#state-dot.listening { background: rgba(100,200,255,0.6); box-shadow: 0 0 10px rgba(100,200,255,0.3); }
#state-dot.thinking { background: rgba(200,180,255,0.8); box-shadow: 0 0 14px rgba(200,180,255,0.4); }
#partial-text {
  font-size: 13px; color: rgba(200,208,224,0.4); padding: 0 20px 8px;
  min-height: 18px; flex-shrink: 0;
}
</style>
</head>
<body>

<div id="canvas-container"></div>

<div id="chat-panel">
  <div id="chat-header">TOKENS</div>
  <div id="partial-text"></div>
  <div id="chat-messages"></div>
  <div id="chat-footer">
    <div id="state-dot"></div>
    <div id="status-label">空闲</div>
    <button id="mic-btn">
      <svg viewBox="0 0 24 24"><path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zm5 9c0 2.76-2.24 5-5 5s-5-2.24-5-5H5a7 7 0 0 0 6 6.93V21h2v-3.07A7 7 0 0 0 19 11h-2z"/></svg>
    </button>
  </div>
</div>

<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
  }
}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

// ---- Three.js Setup ----
const scene = new THREE.Scene();
scene.background = new THREE.Color('#050810');
const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 50);
camera.position.set(0, 1, 7);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.toneMapping = THREE.ReinhardToneMapping;
document.getElementById('canvas-container').appendChild(renderer.domElement);

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0.5, 0.2, 0.05);
composer.addPass(bloom);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.dampingFactor = 0.08;
controls.minDistance = 3; controls.maxDistance = 20;
controls.target.set(0, 0, 0);

// ---- Core object: glowing torus knot ----
const knotGeo = new THREE.TorusKnotGeometry(1.2, 0.4, 128, 16);
const knotMat = new THREE.MeshStandardMaterial({
  color: 0x4488ff, emissive: 0x2244aa,
  roughness: 0.2, metalness: 0.1,
  transparent: true, opacity: 0.85,
});
const knot = new THREE.Mesh(knotGeo, knotMat);
knot.position.y = 0.3;
scene.add(knot);

// ---- Inner glow core ----
const coreGeo = new THREE.SphereGeometry(0.3, 16, 16);
const coreMat = new THREE.MeshBasicMaterial({ color: 0x6aafff, transparent: true, opacity: 0.6 });
const core = new THREE.Mesh(coreGeo, coreMat);
core.position.y = 0.3;
scene.add(core);

// ---- Lights ----
const ambient = new THREE.AmbientLight(0x112244, 0.5);
scene.add(ambient);
const dirLight = new THREE.DirectionalLight(0x4488ff, 1.2);
dirLight.position.set(2, 3, 4);
scene.add(dirLight);
const backLight = new THREE.PointLight(0x6644ff, 0.5);
backLight.position.set(-3, -1, -3);
scene.add(backLight);

// ---- Floating particles ----
const particleCount = 400;
const positions = new Float32Array(particleCount * 3);
const colors = new Float32Array(particleCount * 3);
for (let i = 0; i < particleCount; i++) {
  const r = 1.5 + Math.random() * 4;
  const theta = Math.random() * Math.PI * 2;
  const phi = Math.random() * Math.PI * 2;
  positions[i*3] = Math.sin(theta) * Math.cos(phi) * r;
  positions[i*3+1] = Math.sin(theta) * Math.sin(phi) * r + 0.3;
  positions[i*3+2] = Math.cos(theta) * r;
  const c = new THREE.Color(0x4488ff).lerp(new THREE.Color(0x8866ff), Math.random());
  colors[i*3] = c.r; colors[i*3+1] = c.g; colors[i*3+2] = c.b;
}
const pGeo = new THREE.BufferGeometry();
pGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
pGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
const pMat = new THREE.PointsMaterial({
  size: 0.04, vertexColors: true, transparent: true, opacity: 0.5,
  blending: THREE.AdditiveBlending, depthWrite: false,
});
const particles = new THREE.Points(pGeo, pMat);
scene.add(particles);

// ---- Wudao State control ----
var currentAnim = { speed: 1, bloomIntensity: 0.5 };

window.setIdle = function() {
  currentAnim.speed = 1; currentAnim.bloomIntensity = 0.5;
  bloom.strength = 0.5;
  document.getElementById('state-dot').className = '';
  document.getElementById('status-label').textContent = '空闲';
  document.getElementById('status-label').className = '';
};
window.setListen = function() {
  currentAnim.speed = 0.3; currentAnim.bloomIntensity = 0.3;
  bloom.strength = 0.3;
  document.getElementById('state-dot').className = 'listening';
  document.getElementById('status-label').textContent = '倾听';
  document.getElementById('status-label').className = 'active';
};
window.setThink = function() {
  currentAnim.speed = 2.5; currentAnim.bloomIntensity = 0.8;
  bloom.strength = 0.8;
  document.getElementById('state-dot').className = 'thinking';
  document.getElementById('status-label').textContent = '思考';
  document.getElementById('status-label').className = 'active';
};

// ---- WebSocket ----
var ws = null;
function connectWS() {
  try {
    if (ws && ws.readyState <= WebSocket.OPEN) return;
    ws = new WebSocket('ws://localhost:8000/ws');
    ws.onopen = function() {
      console.log('[WS] connected');
      ws.send(JSON.stringify({ type: 'message', text: '' }));
      _addMsg('system', '悟道已连接');
    };
    ws.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        switch (data.type) {
          case 'tentacle_state':
            switch (data.state) {
              case 'idle': window.setIdle(); break;
              case 'listening': window.setListen(); break;
              case 'thinking': window.setThink(); break;
            }
            break;
          case 'partial_text':
            document.getElementById('partial-text').textContent = data.text;
            break;
          case 'voice_result':
            if (data.text) _addMsg('user', data.text);
            if (data.reply) _addMsg('wudao', data.reply, true);
            break;
          case 'message':
            _addMsg('wudao', data.text, true);
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
              player.play().catch(function(err) { console.log('play error:', err); });
            }
            break;
        }
      } catch(err) { console.log('WS msg error:', err); }
    };
    ws.onclose = function() {
      console.log('[WS] closed, reconnect in 3s');
      setTimeout(connectWS, 3000);
    };
    ws.onerror = function() { console.log('[WS] error'); };
  } catch(err) {
    console.log('[WS] connect error, retry in 3s');
    setTimeout(connectWS, 3000);
  }
}
connectWS();

// ---- Chat messages ----
function _addMsg(type, text, hasCopy) {
  var el = document.getElementById('chat-messages');
  var now = new Date();
  var time = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
  var div = document.createElement('div');
  div.className = 'msg';
  div.innerHTML = '<div class="msg-text">' + text + '</div>' +
    (hasCopy ? '<button class="msg-copy" onclick="var t=this.parentElement.querySelector(\\'.msg-text\\').textContent;navigator.clipboard.writeText(t).catch(function(){})">复制</button>' : '') +
    '<div class="msg-time">' + time + '</div>';
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

// ---- Mic Recording ----
var isRecording = false;
var audioCtx = null;
var stream = null;
var intervalId = null;
var isCapturing = false;

function startMicCapture() {
  if (isCapturing) return;
  isCapturing = true;
  navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    .then(function(s) {
      stream = s;
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      var source = audioCtx.createMediaStreamSource(s);
      var analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      var bufLen = analyser.frequencyBinCount;
      var arr = new Uint8Array(bufLen);
      intervalId = setInterval(function() {
        if (!isRecording) return;
        analyser.getByteTimeDomainData(arr);
        var pcm = new Int16Array(bufLen);
        for (var i = 0; i < bufLen; i++) {
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

function stopMicCapture() {
  isCapturing = false;
  if (intervalId) { clearInterval(intervalId); intervalId = null; }
  if (audioCtx) { audioCtx.close().catch(function(){}); audioCtx = null; }
  if (stream) { stream.getTracks().forEach(function(t){t.stop();}); stream = null; }
}

// ---- Space key & mic button ----
var micBtn = document.getElementById('mic-btn');
document.addEventListener('keydown', function(e) {
  if (e.key === ' ' && e.target === document.body) {
    e.preventDefault();
    if (!isRecording) {
      isRecording = true;
      micBtn.classList.add('active');
      window.setListen();
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'voice_start', session_id: 'main' }));
      }
      startMicCapture();
    }
  }
});
document.addEventListener('keyup', function(e) {
  if (e.key === ' ' && isRecording) {
    e.preventDefault();
    isRecording = false;
    micBtn.classList.remove('active');
    stopMicCapture();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'voice_end' }));
    }
    window.setThink();
  }
});
micBtn.addEventListener('click', function() {
  if (isRecording) {
    isRecording = false;
    micBtn.classList.remove('active');
    stopMicCapture();
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'voice_end' }));
    window.setThink();
  } else {
    isRecording = true;
    micBtn.classList.add('active');
    window.setListen();
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'voice_start', session_id: 'main' }));
    startMicCapture();
  }
});
micBtn.addEventListener('mouseleave', function() {
  if (isRecording) {
    isRecording = false;
    micBtn.classList.remove('active');
    stopMicCapture();
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'voice_end' }));
    window.setThink();
  }
});

// ---- Animation loop ----
var clock = new THREE.Clock();
function animate() {
  requestAnimationFrame(animate);
  var t = clock.getElapsedTime();
  var s = currentAnim.speed;

  knot.rotation.x += 0.003 * s;
  knot.rotation.y += 0.005 * s;
  knot.rotation.z += 0.001 * s;

  var pulse = 1 + Math.sin(t * 0.5 * s) * 0.08;
  knot.scale.setScalar(pulse);

  core.scale.setScalar(1 + Math.sin(t * 0.8 * s) * 0.12);
  core.material.opacity = 0.5 + Math.sin(t * 0.6 * s) * 0.2;

  var posArr = particles.geometry.attributes.position.array;
  for (var i = 0; i < posArr.length; i += 3) {
    var angle = t * 0.02 * s + i * 0.001;
    var r = Math.sqrt(posArr[i]*posArr[i] + posArr[i+1]*posArr[i+1] + posArr[i+2]*posArr[i+2]);
    var theta = Math.atan2(posArr[i+1], posArr[i]) + 0.001 * s;
    var phi = Math.acos(posArr[i+2] / Math.max(r, 0.1)) + 0.0008 * s;
    posArr[i] = Math.sin(theta) * Math.cos(phi) * r;
    posArr[i+1] = Math.sin(theta) * Math.sin(phi) * r;
    posArr[i+2] = Math.cos(theta) * r;
  }
  particles.geometry.attributes.position.needsUpdate = true;

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
print('OK size:', len(h))
print('charset utf-8:', 'charset="UTF-8"' in h)
print('setIdle/setListen/setThink:', all(x in h for x in ['setIdle', 'setListen', 'setThink']))
print('getUserMedia:', 'getUserMedia' in h)
print('getByteTimeDomainData:', 'getByteTimeDomainData' in h)
print('voice_start:', 'voice_start' in h)
print('voice_chunk:', 'voice_chunk' in h)
print('voice_end:', 'voice_end' in h)
print('tts_audio:', 'tts_audio' in h)
print('WS:', 'localhost:8000/ws' in h)
# Check brace balance
opens = h.count('{')
closes = h.count('}')
print(f'braces: open={opens} close={closes} {"OK" if opens==closes else "ERROR"}')
