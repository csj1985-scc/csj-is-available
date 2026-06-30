#!/usr/bin/env python
"""小白式诊断——直接测试语音流程每个环节"""
import os, sys, asyncio, json, base64

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

async def diagnose():
    print("=" * 50)
    print("诊断：悟道语音流程")
    print("=" * 50)

    # 1. 检查 DeepSeek key
    key = os.getenv('DEEPSEEK_API_KEY', '')
    print(f"\n[1/6] DeepSeek API Key: {'✅ 存在' if key else '❌ 为空'}")
    if key:
        print(f"    前缀: {key[:8]}... 长度: {len(key)}")

    # 2. 检查 LLM
    print(f"\n[2/6] 测试 LLM 对话...")
    from core.llm import chat as llm_chat
    try:
        reply = llm_chat("你好, 说一句话回复我", history=[])
        print(f"    ✅ LLM 回复: \"{reply[:80]}\"")
    except Exception as e:
        print(f"    ❌ LLM 失败: {e}")

    # 3. 检查 Whisper
    print(f"\n[3/6] 检查 Whisper...")
    try:
        from core.audio import transcribe_audio
        # 生成一小段静音 PCM16 (16kHz, 16bit, mono) 看看会不会报错
        import struct
        silence = b'\x00\x00' * 16000  # 1秒静音
        text = await transcribe_audio(silence, sample_rate=16000)
        print(f"    ✅ Whisper 返回: \"{text}\"")
    except Exception as e:
        print(f"    ❌ Whisper 失败: {e}")

    # 4. 检查 edge-tts
    print(f"\n[4/6] 检查 TTS (edge-tts)...")
    try:
        import edge_tts
        communicate = edge_tts.Communicate("你好，我是悟道。", voice="zh-CN-XiaoxiaoNeural")
        audio_len = 0
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                audio_len += len(chunk['data'])
        print(f"    ✅ TTS 生成 {audio_len} bytes 音频")
    except Exception as e:
        print(f"    ❌ TTS 失败: {e}")

    # 5. 完整语音流程模拟
    print(f"\n[5/6] 模拟完整语音流程...")
    from core.realtime_voice import get_engine, reset_engine
    await reset_engine()
    engine = get_engine()
    
    events = []
    async def on_partial(t): events.append(f"partial:{t[:30]}"); print(f"    [partial] {t[:50]}")
    async def on_think(m): events.append(f"think:{m}"); print(f"    [think] {m}")
    async def on_state(s): events.append(f"state:{s}"); print(f"    [state] {s}")
    async def on_tts(t): events.append(f"tts:{t[:30]}"); print(f"    [tts] \"{t[:40]}\"")
    
    await engine.start_recording(
        session_id="test",
        on_partial_text=on_partial,
        on_thinking_update=on_think,
        on_state=on_state,
        on_tts=on_tts,
    )
    
    # 推送一小段模拟音频
    print(f"    [push] 推送静音PCM16音频...")
    silence_2s = b'\x00\x00' * 32000
    engine.push_audio(silence_2s)
    await asyncio.sleep(0.3)
    
    print(f"    [stop] 结束录音...")
    result = await engine.stop_recording()
    
    print(f"\n    结果:")
    print(f"      full_text: \"{result.full_text[:60] if result.full_text else '(空)'}\"")
    print(f"      reply: \"{result.final_reply[:60] if result.final_reply else '(空)'}\"")
    print(f"      耗时: {result.thinking_time:.2f}s")
    print(f"      事件: {events}")

    # 6. 检查前端是否包含麦克风代码
    print(f"\n[6/6] 检查前端 HTML...")
    idx_path = os.path.join('static', 'index.html')
    if os.path.exists(idx_path):
        with open(idx_path, 'r', encoding='utf-8') as f:
            html = f.read()
        checks = {
            'getUserMedia': 'getUserMedia' in html,
            'voice_chunk': 'voice_chunk' in html,
            'scriptProcessor': 'ScriptProcessor' in html or 'scriptProcessor' in html,
            'btoa': 'btoa' in html,
            'tts-player': 'tts-player' in html,
            'tts_audio': 'tts_audio' in html,
            'voice_start': 'voice_start' in html,
            'voice_end': 'voice_end' in html,
        }
        for k, v in checks.items():
            print(f"    {'✅' if v else '❌'} {k}")
    else:
        print(f"    ❌ static/index.html 不存在!")

    print("\n" + "=" * 50)
    print("诊断完成")
    print("=" * 50)

if __name__ == '__main__':
    asyncio.run(diagnose())
