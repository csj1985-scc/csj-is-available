"""
语音录制引擎 - v0.3.4

核心思路：
1. 用户按空格 → 开始录音（仅收音频）
2. 用户松开空格 → 完整音频一次性送 Whisper 转录
3. 转录文本走 _handle_text_message 统一处理路径

使用方法：
    engine = RealtimeVoiceEngine()
    await engine.start_recording(session_id, on_state)
    ...
    result = await engine.stop_recording()
    # result.full_text 包含完整识别文本

依赖：
    - core.audio.transcribe_audio (Whisper)
"""
import asyncio
from typing import Optional, Callable, Awaitable, List
from dataclasses import dataclass

# ---- 常量 ----
SAMPLE_RATE = 16000             # 采样率 16kHz


@dataclass
class RecordingResult:
    """一次完整的录音结果"""
    full_text: str               # 最终识别的完整文本
    pre_thoughts: List           # 保留字段（兼容旧调用方）
    final_reply: str             # 由 _handle_text_message 统一生成
    thinking_time: float         # 思考耗时（秒）


class RealtimeVoiceEngine:
    """
    语音边录边想引擎
    用法:
        engine = RealtimeVoiceEngine()
        await engine.start_recording(session_id)
        # 客户端持续 push 音频 chunk
        engine.push_audio(chunk)
        # 客户端说录完了
        result = await engine.stop_recording()
    """

    def __init__(self):
        self._session_id: str = "default"
        self._recording: bool = False

        # 音频相关
        self._audio_buffer: List[bytes] = []      # 所有音频 chunk

        # 回调
        self._on_state: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_tts: Optional[Callable[[str], Awaitable[None]]] = None

        self._final_text: str = ""

    async def start_recording(
        self,
        session_id: str = "default",
        on_state: Optional[Callable[[str], Awaitable[None]]] = None,
        on_tts: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """
        开始录音会话
        录音期间只收音频，松开后一次性送 Whisper 完整转录
        """
        self._session_id = session_id
        self._audio_buffer = []
        self._final_text = ""
        self._recording = True

        self._on_state = on_state
        self._on_tts = on_tts

        # 通知状态：开始录音
        if self._on_state:
            await self._on_state("listening")

        print(f"[realtime_voice] 开始录音 session={session_id}")

    def push_audio(self, chunk: bytes):
        """客户端每帧 push 音频数据进来"""
        if not self._recording:
            return
        self._audio_buffer.append(chunk)

    async def stop_recording(self) -> RecordingResult:
        """
        停止录音，用完整音频做一次性 Whisper 转录
        返回最终结果（不含 LLM 回复，由 _handle_text_message 统一处理）
        """
        if not self._recording:
            return RecordingResult(
                full_text="", pre_thoughts=[], final_reply="", thinking_time=0
            )

        self._recording = False

        if self._on_state:
            await self._on_state("thinking")

        all_bytes = b"".join(self._audio_buffer)
        if all_bytes:
            final_text = await self._transcribe_final(all_bytes)
        else:
            final_text = ""

        self._final_text = final_text.strip()
        if not self._final_text:
            if self._on_state:
                await self._on_state("idle")
            return RecordingResult(
                full_text="", pre_thoughts=[], final_reply="", thinking_time=0
            )

        print(f"[realtime_voice] 录音结束 text='{self._final_text[:60]}'")

        return RecordingResult(
            full_text=self._final_text,
            pre_thoughts=[],
            final_reply="",
            thinking_time=0,
        )


    async def _transcribe_final(self, audio_bytes: bytes) -> str:
        """对最终完整音频做一次性转录"""
        from core.audio import transcribe_audio
        if not audio_bytes:
            print("[realtime_voice] _transcribe_final: audio_bytes 为空")
            return ""
        print(f"[realtime_voice] _transcribe_final: 音频大小={len(audio_bytes)} 字节")
        text = await transcribe_audio(audio_bytes, sample_rate=SAMPLE_RATE)
        print(f"[realtime_voice] _transcribe_final: 转录结果={text!r}")
        return text.strip()

    def is_recording(self) -> bool:
        return self._recording

    def get_response_time_estimate(self) -> float:
        """松开后需要等 Whisper 完整转录，约 2-3s"""
        return 3.0


# ---- 全局引擎实例（单例） ----
_engine_instance: Optional[RealtimeVoiceEngine] = None


def get_engine() -> RealtimeVoiceEngine:
    """获取/创建全局语音引擎实例"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = RealtimeVoiceEngine()
    return _engine_instance


async def reset_engine():
    """重置引擎（释放资源）"""
    global _engine_instance
    if _engine_instance is not None:
        if _engine_instance.is_recording():
            await _engine_instance.stop_recording()
    _engine_instance = RealtimeVoiceEngine()
