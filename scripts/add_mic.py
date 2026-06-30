"""
给前端加上真正的麦克风录音 → voice_chunk 发送逻辑
"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 在 script 末尾（connectWS() 之前）插入麦克风录音逻辑
old = """function startRecording() {
  if (isRecording) return;
  isRecording = true;
  micBtn.classList.add('active');
  window.setListen();
  wsSend({ type: 'voice_start', session_id: 'main' });
}

function stopRecording() {
  if (!isRecording) return;
  isRecording = false;
  micBtn.classList.remove('active');
  wsSend({ type: 'voice_end' });
  // 后端会推 voice_result 或置 idle; 为防网络卡顿
  // 先显示思考状态
  window.setThink();
}"""

new = """// ---- 麦克风流式录音 ----
var audioContext = null;
var micStream = null;
var scriptProcessor = null;
var isMicRecording = false;

function startRecording() {
  if (isRecording) return;
  isRecording = true;
  micBtn.classList.add('active');
  window.setListen();
  wsSend({ type: 'voice_start', session_id: 'main' });

  // 开始真正的麦克风录音
  startMicCapture();
}

function stopRecording() {
  if (!isRecording) return;
  isRecording = false;
  micBtn.classList.remove('active');

  // 停止麦克风
  stopMicCapture();

  wsSend({ type: 'voice_end' });
  window.setThink();
}

function startMicCapture() {
  if (isMicRecording) return;
  isMicRecording = true;

  try {
    navigator.mediaDevices.getUserMedia({ audio: {
      sampleRate: 16000,
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true
    }}).then(function(stream) {
      micStream = stream;
      audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

      var source = audioContext.createMediaStreamSource(stream);
      scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);

      source.connect(scriptProcessor);
      scriptProcessor.connect(audioContext.destination);

      scriptProcessor.onaudioprocess = function(event) {
        if (!isMicRecording || !isRecording) return;

        var input = event.inputBuffer.getChannelData(0);
        var pcm16 = new Int16Array(input.length);
        for (var i = 0; i < input.length; i++) {
          var s = Math.max(-1, Math.min(1, input[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        // 发送 voice_chunk
        var bytes = new Uint8Array(pcm16.buffer);
        var binary = '';
        for (var j = 0; j < bytes.length; j++) {
          binary += String.fromCharCode(bytes[j]);
        }
        var b64 = btoa(binary);
        wsSend({ type: 'voice_chunk', audio: b64 });
      };
    }).catch(function(err) {
      console.error('麦克风错误:', err);
      isMicRecording = false;
    });
  } catch (err) {
    console.error('getUserMedia 不支持:', err);
    isMicRecording = false;
  }
}

function stopMicCapture() {
  isMicRecording = false;

  if (scriptProcessor) {
    scriptProcessor.disconnect();
    scriptProcessor = null;
  }
  if (audioContext) {
    audioContext.close().catch(function(){});
    audioContext = null;
  }
  if (micStream) {
    micStream.getTracks().forEach(function(t) { t.stop(); });
    micStream = null;
  }
}"""

html = html.replace(old, new)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

# Verify
with open('static/index.html', 'r', encoding='utf-8') as f:
    h2 = f.read()
print('getUserMedia:', 'getUserMedia' in h2)
print('scriptProcessor:', 'ScriptProcessor' in h2)
print('voice_chunk:', 'voice_chunk' in h2)
print('btoa:', 'btoa' in h2)
