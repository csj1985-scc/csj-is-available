"""
核心音频模块 - v0.3.3
语音输入输出封装：
- 录音采集（客户端负责，后端接收音频chunk）
- Whisper 语音识别
- edge-tts 语音合成朗读

保持向后兼容，只新增功能不改旧接口。
"""
import os
import io
import wave
import json
import asyncio
import base64
import tempfile
from typing import Optional, List, Callable, Awaitable
from pathlib import Path

# ---- Whisper 语音识别 ----
# 支持 faster-whisper（优先）和 openai-whisper（后备）
import numpy as np

_WHISPER_AVAILABLE = False
_transcriber = None
_WHISPER_BACKEND = None  # "faster" or "openai"

# 尝试加载 faster-whisper
try:
    from faster_whisper import WhisperModel
    _WHISPER_BACKEND = "faster"
except ImportError:
    pass

# faster-whisper 不可用时尝试 openai-whisper
if _WHISPER_BACKEND is None:
    try:
        import whisper as _openai_whisper
        _WHISPER_BACKEND = "openai"
    except ImportError:
        pass

# 模块加载时自动初始化 Whisper（惰性加载，首次调用 transcribe 时初始化）
_whisper_initialized = False


def init_whisper(model_size: str = "small", device: str = "auto") -> bool:
    """
    初始化 Whisper 模型
    优先 faster-whisper，后备 openai-whisper
    """
    global _transcriber, _WHISPER_AVAILABLE, _whisper_initialized
    if _WHISPER_BACKEND is None:
        print("[audio] 未找到任何 whisper 后端")
        _whisper_initialized = True
        return False

    if is_whisper_ready():
        return True

    try:
        if _WHISPER_BACKEND == "faster":
            _transcriber = WhisperModel(model_size, device=device, compute_type="float16")
        else:
            import whisper
            _transcriber = whisper.load_model(model_size)
        _WHISPER_AVAILABLE = True
        return True
    except Exception as e:
        print(f"[audio] Whisper 初始化失败: {e}")
        _WHISPER_AVAILABLE = False
        return False
    finally:
        _whisper_initialized = True


def is_whisper_ready() -> bool:
    return _WHISPER_AVAILABLE and _transcriber is not None


def _pcm_to_numpy(pcm_bytes: bytes) -> np.ndarray:
    """将 PCM16 bytes 转为 float32 numpy 数组 [-1, 1]，绕过 ffmpeg 依赖"""
    import numpy as np
    raw = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    raw /= 32768.0
    return raw


def _lazy_init():
    """首次使用时自动初始化 Whisper"""
    global _whisper_initialized
    if not _whisper_initialized and not is_whisper_ready():
        _whisper_initialized = True
        init_whisper()


def _fix_common_errors(text: str) -> str:
    """修正 Whisper 常见识别错误"""
    fixes = {
        "5道": "悟道",
        "5 道": "悟道",
        "大鱼": "悟道",
        "无道": "悟道",
    }
    for wrong, right in fixes.items():
        text = text.replace(wrong, right)
    return text


async def transcribe_audio(audio_bytes: bytes, sample_rate: int = 16000) -> str:
    """将 PCM 音频数据转录为文字（numpy 直传，不依赖 ffmpeg）"""
    _lazy_init()
    if not is_whisper_ready() or not audio_bytes:
        return ""

    audio = _pcm_to_numpy(audio_bytes)
    loop = asyncio.get_event_loop()

    try:
        if _WHISPER_BACKEND == "faster":
            segments, _ = await loop.run_in_executor(
                None,
                lambda: _transcriber.transcribe(audio, language="zh", vad_filter=True)
            )
            parts = []
            for seg in segments:
                parts.append(seg.text.strip())
            return _fix_common_errors("".join(parts))
        else:
            result = await loop.run_in_executor(
                None,
                lambda: _transcriber.transcribe(audio, language="zh", fp16=False)
            )
            return _fix_common_errors(result.get("text", "").strip())
    except Exception as e:
        print(f"[audio] 转录失败: {e}")
        return ""


async def transcribe_audio_sync(audio_bytes: bytes, sample_rate: int = 16000) -> str:
    """同步转录（numpy 直传，不依赖 ffmpeg）"""
    _lazy_init()
    if not is_whisper_ready() or not audio_bytes:
        return ""

    audio = _pcm_to_numpy(audio_bytes)
    try:
        if _WHISPER_BACKEND == "faster":
            segments, _ = _transcriber.transcribe(audio, language="zh", vad_filter=True)
            return _fix_common_errors("".join(seg.text.strip() for seg in segments))
        else:
            result = _transcriber.transcribe(audio, language="zh", fp16=False)
            return _fix_common_errors(result.get("text", "").strip())
    except Exception as e:
        print(f"[audio] 同步转录失败: {e}")
        return ""


# ---- Edge-TTS 语音合成 ----
_TTS_LOCK = asyncio.Lock()

async def speak(text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> bool:
    """
    使用 edge-tts 朗读文本，返回是否成功
    复用锁防止并发冲突
    """
    try:
        import edge_tts
        async with _TTS_LOCK:
            tts = edge_tts.Communicate(text, voice=voice)
            # 输出到临时文件然后播放
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp_path = f.name
            await tts.save(tmp_path)
            # 返回音频路径让前端播放
            return tmp_path
    except ImportError:
        # edge-tts 没安装，返回 None 表示不朗读
        print("[audio] edge-tts 未安装，跳过朗读")
        return None
    except Exception as e:
        print(f"[audio] 朗读失败: {e}")
        return None
