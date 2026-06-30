"""
修复：在 voice_end 后添加 TTS 朗读 + 推 WS 音频
"""
import os

# === 1. 修改 realtime_voice.py：stop_recording 加 TTS 回调 ===
with open('core/realtime_voice.py', 'r', encoding='utf-8') as f:
    rt = f.read()

# 在 stop_recording 尾部，return result 之前插入 TTS 调用
old = """        if self._on_state:
            await self._on_state("speaking")

        result = RecordingResult("""
new = """        if self._on_state:
            await self._on_state("speaking")

        # 如果有 on_tts 回调，生成 TTS 音频
        if self._on_tts and final_reply:
            try:
                await self._on_tts(final_reply)
            except Exception as e:
                print(f"[realtime_voice] TTS 错误: {e}")

        result = RecordingResult("""

rt = rt.replace(old, new)

# 在 __init__ 里加 on_tts
rt = rt.replace(
    'self._on_state: Optional[Callable[[str], Awaitable[None]]] = None',
    'self._on_state: Optional[Callable[[str], Awaitable[None]]] = None\n        self._on_tts: Optional[Callable[[str], Awaitable[None]]] = None'
)

# 在 start_recording 参数列表加 on_tts
old_sig = """    async def start_recording(
        self,
        session_id: str = "default",
        on_partial_text: Optional[Callable[[str], Awaitable[None]]] = None,
        on_thinking_update: Optional[Callable[[str], Awaitable[None]]] = None,
        on_state: Optional[Callable[[str], Awaitable[None]]] = None,
    ):"""

new_sig = """    async def start_recording(
        self,
        session_id: str = "default",
        on_partial_text: Optional[Callable[[str], Awaitable[None]]] = None,
        on_thinking_update: Optional[Callable[[str], Awaitable[None]]] = None,
        on_state: Optional[Callable[[str], Awaitable[None]]] = None,
        on_tts: Optional[Callable[[str], Awaitable[None]]] = None,
    ):"""

rt = rt.replace(old_sig, new_sig)

# 在 start_recording 体内存 on_tts
rt = rt.replace(
    'self._on_thinking_update = on_thinking_update\n        self._on_state = on_state',
    'self._on_thinking_update = on_thinking_update\n        self._on_state = on_state\n        self._on_tts = on_tts'
)

with open('core/realtime_voice.py', 'w', encoding='utf-8') as f:
    f.write(rt)
print('[OK] realtime_voice.py updated')


# === 2. 修改 ws.py：注册 TTS 回调，把音频推到前端 ===
with open('core/ws.py', 'r', encoding='utf-8') as f:
    ws = f.read()

# 在 voice_start 处理里找到 start_recording 调用，加上 on_tts
old_voice_start = """                engine = get_engine()
                await engine.start_recording(
                    session_id=session_id,
                    on_partial_text=lambda t: manager._send(ws, {"type": "partial_text", "text": t}),
                    on_thinking_update=lambda m: manager._send(ws, {"type": "thinking_update", "msg": m}),
                    on_state=lambda s: _broadcast_tentacle_state(ws, manager, s),
                )"""

new_voice_start = """                engine = get_engine()
                await engine.start_recording(
                    session_id=session_id,
                    on_partial_text=lambda t: manager._send(ws, {"type": "partial_text", "text": t}),
                    on_thinking_update=lambda m: manager._send(ws, {"type": "thinking_update", "msg": m}),
                    on_state=lambda s: _broadcast_tentacle_state(ws, manager, s),
                    on_tts=lambda text: _tts_and_send(ws, manager, text),
                )"""

ws = ws.replace(old_voice_start, new_voice_start)

# 加上 _tts_and_send 辅助函数（加在文件末尾附近）
old_eof = """            except Exception as e:
                if engine.is_recording():
                    await engine.stop_recording()
                manager.disconnect(ws)
                try:
                    await ws.close()
                except Exception:
                    pass"""

new_eof = """            except Exception as e:
                if engine.is_recording():
                    await engine.stop_recording()
                manager.disconnect(ws)
                try:
                    await ws.close()
                except Exception:
                    pass


async def _tts_and_send(ws: WebSocket, manager: WSManager, text: str):
    \"\"\"生成 TTS 音频并通过 WS 推送到前端\"\"\"
    try:
        import edge_tts
        import io

        communicate = edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural")
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]

        if audio_bytes:
            import base64
            b64 = base64.b64encode(audio_bytes).decode("ascii")
            await manager._send(ws, {
                "type": "tts_audio",
                "audio": b64,
            })
    except Exception as e:
        print(f"[_tts_and_send] 错误: {e}")"""

ws = ws.replace(old_eof, new_eof)

with open('core/ws.py', 'w', encoding='utf-8') as f:
    f.write(ws)
print('[OK] ws.py updated')


# === 3. 修改前端 index.html：播放 tts_audio ===
with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 在一开始 body 里加 audio 元素
if '<audio id="tts-player"' not in html:
    html = html.replace(
        '<div id="canvas-container"></div>',
        '<div id="canvas-container"></div>\n<audio id="tts-player" style="display:none"></audio>'
    )

# 在 WS 消息处理里加 tts_audio 分支
old_switch = """      case 'tentacle_state':
          // 触手状态同步到 UI
          switch (data.state) {"""
new_switch = """      case 'tts_audio':
          // 播放 TTS 语音回复
          if (data.audio) {
            var audioBytes = atob(data.audio);
            var arrayBuffer = new ArrayBuffer(audioBytes.length);
            var uint8Array = new Uint8Array(arrayBuffer);
            for (var i = 0; i < audioBytes.length; i++) {
              uint8Array[i] = audioBytes.charCodeAt(i);
            }
            var blob = new Blob([uint8Array], { type: 'audio/mp3' });
            var url = URL.createObjectURL(blob);
            var player = document.getElementById('tts-player');
            player.src = url;
            player.onended = function() { URL.revokeObjectURL(url); };
            player.play().catch(function(e) { console.log('TTS play error:', e); });
          }
          break;

      case 'tentacle_state':
          // 触手状态同步到 UI
          switch (data.state) {"""

html = html.replace(old_switch, new_switch)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('[OK] index.html updated')

print('\n=== 全部修改完成 ===')
