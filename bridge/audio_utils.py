"""Audio conversion utilities"""
import audioop

def pcmu_to_pcm16_16k(data: bytes) -> bytes:
    """G.711 PCMU 8kHz -> PCM16 signed 16kHz"""
    pcm8 = audioop.ulaw2lin(data, 2)
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)
    return pcm16

def pcm16_24k_to_pcmu(data: bytes) -> bytes:
    """PCM16 24kHz (OpenAI output) -> G.711 PCMU 8kHz"""
    pcm8, _ = audioop.ratecv(data, 2, 1, 24000, 8000, None)
    return audioop.lin2ulaw(pcm8, 2)

def pcm16_16k_to_pcmu(data: bytes) -> bytes:
    """PCM16 16kHz -> G.711 PCMU 8kHz"""
    pcm8, _ = audioop.ratecv(data, 2, 1, 16000, 8000, None)
    return audioop.lin2ulaw(pcm8, 2)

def normalize_volume(data: bytes, factor: float = 1.2) -> bytes:
    return audioop.mul(data, 2, factor)
