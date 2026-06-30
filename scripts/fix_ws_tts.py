with open('core/ws.py', 'r', encoding='utf-8') as f:
    content = f.read()

tts_func = '''

async def _tts_and_send(ws: WebSocket, manager: WSManager, text: str):
    """Generate TTS audio and push via WS to frontend"""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural")
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]
        if audio_bytes:
            import base64 as b64mod
            b64 = b64mod.b64encode(audio_bytes).decode("ascii")
            await manager._send(ws, {
                "type": "tts_audio",
                "audio": b64,
            })
    except Exception as e:
        print(f"[_tts_and_send] error: {e}")
'''

content += tts_func

with open('core/ws.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Validate
import ast
ast.parse(open('core/ws.py', encoding='utf-8').read())
print('OK - ws.py syntax validated')
print('_tts_and_send present:', '_tts_and_send' in content)
