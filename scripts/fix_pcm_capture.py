"""Fix: Change MediaRecorder format from webm to wav for PCM compatibility"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Check current MediaRecorder options
import re
m = re.search(r'MediaRecorder\([^)]+\)', html)
if m:
    print('MediaRecorder line:', m.group())

# The simplest fix: don't set mimeType (let browser choose default)
# But backend needs raw PCM16. Better approach: 
# Revert to ScriptProcessor-like raw PCM capture but using AudioContext directly
# MediaRecorder doesn't give us raw PCM easily.

# Best fix: Keep MediaRecorder but force WAV recording via a custom setup
# Actually the cleanest solution: use AudioContext + offline for capture, 
# but real-time we need something else.

# Simplest working approach: just remove mimeType constraint and let browser 
# send whatever it supports. BUT backend needs PCM16.

# The REAL fix: use AudioContext API directly (not ScriptProcessor, not deprecated)
# Use AudioWorklet as recommended, but that requires a separate JS file.

# ACTUAL easiest: use a getUserMedia → AudioContext → capture raw PCM via 
# createMediaStreamSource + periodic wave sampling WITHOUT ScriptProcessor
# by using requestAnimationFrame/setInterval to read current time domain data

old_mic_section = """function startMicCapture() {
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

new_mic_section = """// ---- 直接 PCM16 录音 (AudioContext + AnalyserNode) ----
var audioCtx = null;
var micStream = null;
var micInterval = null;
var isMicRecording = false;

function startMicCapture() {
  if (isMicRecording) return;
  isMicRecording = true;

  try {
    navigator.mediaDevices.getUserMedia({ audio: {
      echoCancellation: true,
      noiseSuppression: true
    }}).then(function(stream) {
      micStream = stream;
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();

      var source = audioCtx.createMediaStreamSource(stream);
      var analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);

      var bufferLength = analyser.frequencyBinCount;
      var dataArray = new Uint8Array(bufferLength);

      // 每隔 100ms 采集一次时域数据 → 转为 PCM16 → 发送
      micInterval = setInterval(function() {
        if (!isRecording) return;

        analyser.getByteTimeDomainData(dataArray);

        // Uint8 (0-255) → PCM16 Int16 (-32768 to 32767)
        // Center is 128, convert to signed float then to int16
        var pcm16 = new Int16Array(dataArray.length);
        for (var i = 0; i < dataArray.length; i++) {
          var normalized = (dataArray[i] - 128) / 128.0;  // -1 to 1
          pcm16[i] = Math.max(-32768, Math.min(32767, normalized * 32768));
        }

        // To base64
        var bytes = new Uint8Array(pcm16.buffer);
        var binary = '';
        for (var j = 0; j < bytes.length; j++) {
          binary += String.fromCharCode(bytes[j]);
        }
        var b64 = btoa(binary);
        wsSend({ type: 'voice_chunk', audio: b64 });
      }, 100);

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

  if (micInterval) {
    clearInterval(micInterval);
    micInterval = null;
  }
  if (audioCtx) {
    audioCtx.close().catch(function(){});
    audioCtx = null;
  }
  if (micStream) {
    micStream.getTracks().forEach(function(t) { t.stop(); });
    micStream = null;
  }
}"""

html = html.replace(old_mic_section, new_mic_section)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

# Verify
with open('static/index.html', 'r', encoding='utf-8') as f:
    h2 = f.read()
print('MediaRecorder:', 'MediaRecorder' in h2)
print('AnalyserNode:', 'AnalyserNode' in h2 or 'analyser' in h2.lower())
print('getByteTimeDomainData:', 'getByteTimeDomainData' in h2)
print('PCM16 chunk:', 'voice_chunk' in h2)
print('Size:', len(h2))
