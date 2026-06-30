"""
修复方案：把 ScriptProcessor 替换为 MediaRecorder API（更稳定，不依赖过时API）
MediaRecorder 直接录制流音频，定时获取 blob 转 base64 发送
"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

old_mic_code = """// ---- 麦克风流式录音 ----
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

new_mic_code = """// ---- 麦克风流式录音 (MediaRecorder API) ----
var micStream = null;
var mediaRecorder = null;
var isMicRecording = false;
var mediaChunks = [];

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
  mediaChunks = [];

  try {
    navigator.mediaDevices.getUserMedia({ audio: {
      echoCancellation: true,
      noiseSuppression: true,
      sampleRate: 16000
    }}).then(function(stream) {
      micStream = stream;

      // 用 MediaRecorder 录制 PCM16 / wav
      mediaRecorder = new MediaRecorder(stream, {
        mimeType: 'audio/webm;codecs=opus'
      });

      mediaRecorder.ondataavailable = function(event) {
        if (event.data.size > 0) {
          // 保存 chunks 用于最终发送
          mediaChunks.push(event.data);

          // 同时实时发送——读取 blob 转 base64
          if (isRecording) {
            var reader = new FileReader();
            reader.onloadend = function() {
              if (reader.result && isRecording) {
                var base64data = reader.result.split(',')[1];
                if (base64data) {
                  wsSend({ type: 'voice_chunk', audio: base64data });
                }
              }
            };
            reader.readAsDataURL(event.data);
          }
        }
      };

      mediaRecorder.start(250); // 每 250ms 推送一块

    }).catch(function(err) {
      console.error('mic error:', err);
      isMicRecording = false;
    });
  } catch (err) {
    console.error('mic capture error:', err);
    isMicRecording = false;
  }
}

function stopMicCapture() {
  isMicRecording = false;

  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    mediaRecorder = null;
  }
  if (micStream) {
    micStream.getTracks().forEach(function(t) { t.stop(); });
    micStream = null;
  }
  mediaChunks = [];
}"""

html = html.replace(old_mic_code, new_mic_code)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

# Verify
with open('static/index.html', 'r', encoding='utf-8') as f:
    h2 = f.read()
print('MediaRecorder:', 'MediaRecorder' in h2)
print('ScriptProcessor:', 'ScriptProcessor' not in h2)
print('ondataavailable:', 'ondataavailable' in h2)
