#!/usr/bin/env python3
"""
fast_ai_bridge.py — FreeSWITCH ESL <-> OpenAI Realtime WS bridge
Audio pipeline: PCMU 8kHz <-> PCM16 24kHz <-> OpenAI duplex WebSocket
"""
import asyncio, base64, json, logging, os, signal, audioop
from dotenv import load_dotenv
import websockets

load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

log = logging.getLogger("bridge")
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-realtime-preview-2024-12-17")
OPENAI_WS_URL  = f"wss://api.openai.com/v1/realtime?model={OPENAI_MODEL}"
AI_VOICE       = os.getenv("AI_VOICE", "alloy")
VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", 600))
VAD_THRESHOLD  = float(os.getenv("VAD_THRESHOLD", 0.5))
ESL_HOST       = os.getenv("ESL_HOST", "127.0.0.1")
ESL_PORT       = int(os.getenv("ESL_PORT", 8021))
ESL_PASSWORD   = os.getenv("ESL_PASSWORD", "ClueCon")
BRIDGE_HOST    = os.getenv("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT    = int(os.getenv("BRIDGE_PORT", 5000))
N8N_WEBHOOK    = os.getenv("N8N_WEBHOOK_URL", "http://127.0.0.1:3000/webhook")

_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "../prompts/sales_agent.md")
try:
    SYSTEM_PROMPT = open(_PROMPT_FILE).read()
except FileNotFoundError:
    SYSTEM_PROMPT = "You are Aman, a professional sales executive. Keep replies very short."

try:
    from metrics import BARGE_IN_COUNTER, ACTIVE_CALLS_GAUGE
    METRICS = True
except ImportError:
    METRICS = False


# ── Audio conversion helpers ──────────────────────────────────────────────────
def pcmu_to_pcm16_16k(pcmu: bytes) -> bytes:
    pcm8k = audioop.ulaw2lin(pcmu, 2)
    pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, None)
    return pcm16k

def pcm16_24k_to_pcmu(pcm24k: bytes) -> bytes:
    pcm8k, _ = audioop.ratecv(pcm24k, 2, 1, 24000, 8000, None)
    return audioop.lin2ulaw(pcm8k, 2)


# ── n8n notification ──────────────────────────────────────────────────────────
async def notify_n8n(outcome: str, uuid: str, extra: dict = None):
    import aiohttp
    payload = {"uuid": uuid, "outcome": outcome, **(extra or {})}
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.post(N8N_WEBHOOK + "/call-outcome", json=payload, timeout=aiohttp.ClientTimeout(total=5))
            log.info(f"n8n [{outcome}] → {r.status}")
    except Exception as e:
        log.warning(f"n8n notify failed: {e}")


# ── ESL helper: send command, get reply ──────────────────────────────────────
async def esl_cmd(writer, reader, cmd: str) -> str:
    writer.write((cmd + "\n\n").encode())
    await writer.drain()
    try:
        data = await asyncio.wait_for(reader.read(4096), timeout=3)
        return data.decode(errors="ignore")
    except asyncio.TimeoutError:
        return ""


# ── Core bridge coroutine ─────────────────────────────────────────────────────
async def bridge_call(uuid: str, esl_writer: asyncio.StreamWriter, esl_reader: asyncio.StreamReader):
    log.info(f"[{uuid}] Starting AI bridge")
    if METRICS:
        ACTIVE_CALLS_GAUGE.inc()

    active   = True
    outcome  = None
    barges   = 0
    audio_q  = asyncio.Queue(maxsize=200)   # inbound audio chunks from FS

    ws_headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    try:
        async with websockets.connect(OPENAI_WS_URL, additional_headers=ws_headers,
                                      ping_interval=20) as ws:
            # Configure session
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "modalities": ["audio", "text"],
                    "instructions": SYSTEM_PROMPT,
                    "voice": AI_VOICE,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {"model": "whisper-1"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": VAD_THRESHOLD,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": VAD_SILENCE_MS,
                    },
                    "tools": [
                        {"type": "function", "name": "transfer_to_agent",
                         "description": "Transfer call to human agent when user is highly interested",
                         "parameters": {"type": "object", "properties": {}}},
                        {"type": "function", "name": "hangup_call",
                         "description": "End the call when user is not interested",
                         "parameters": {"type": "object", "properties": {}}},
                    ],
                    "tool_choice": "auto",
                },
            }))
            log.info(f"[{uuid}] OpenAI session configured")

            # ── FS inbound audio reader ────────────────────────────────
            async def read_fs_audio():
                while active:
                    try:
                        raw = await asyncio.wait_for(esl_reader.read(4096), timeout=0.1)
                        if raw:
                            await audio_q.put(raw)
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        break

            # ── FS audio → OpenAI ──────────────────────────────────────
            async def fs_to_openai():
                while active:
                    try:
                        pcmu = await asyncio.wait_for(audio_q.get(), timeout=0.5)
                        pcm16 = pcmu_to_pcm16_16k(pcmu)
                        await ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm16).decode(),
                        }))
                    except asyncio.TimeoutError:
                        pass
                    except Exception as e:
                        log.debug(f"fs_to_openai: {e}")

            # ── OpenAI → FS ────────────────────────────────────────────
            async def openai_to_fs():
                nonlocal active, outcome, barges
                async for raw in ws:
                    if not active:
                        break
                    ev = json.loads(raw)
                    t  = ev.get("type", "")

                    if t == "response.audio.delta":
                        pcm24k = base64.b64decode(ev["delta"])
                        pcmu   = pcm16_24k_to_pcmu(pcm24k)
                        # Inject audio back to FreeSWITCH channel
                        cmd = f"api uuid_audio_play {uuid} base64://{base64.b64encode(pcmu).decode()}"
                        await esl_cmd(esl_writer, esl_reader, cmd)

                    elif t == "input_speech_started":
                        barges += 1
                        await esl_cmd(esl_writer, esl_reader, f"api uuid_break {uuid} all")
                        if METRICS:
                            BARGE_IN_COUNTER.inc()
                        log.info(f"[{uuid}] Barge-in #{barges}")

                    elif t == "response.function_call_arguments.done":
                        fn = ev.get("name")
                        if fn == "transfer_to_agent":
                            outcome = "TRANSFER"
                            await notify_n8n("TRANSFER", uuid)
                            await esl_cmd(esl_writer, esl_reader, f"api uuid_transfer {uuid} 9000 XML default")
                            active = False
                        elif fn == "hangup_call":
                            outcome = "NOT_INTERESTED"
                            await notify_n8n("NOT_INTERESTED", uuid)
                            await esl_cmd(esl_writer, esl_reader, f"api uuid_kill {uuid}")
                            active = False

                    elif t == "conversation.item.input_audio_transcription.completed":
                        txt = ev.get("transcript", "")
                        log.info(f"[{uuid}] User said: {txt}")
                        low = txt.lower()
                        if any(w in low for w in ["interested", "haan", "zaroor", "bata", "yes"]):
                            outcome = "INTERESTED"
                            await notify_n8n("INTERESTED", uuid, {"transcript": txt})

                    elif t == "error":
                        log.error(f"[{uuid}] OpenAI error: {ev.get('error')}")

            await asyncio.gather(
                read_fs_audio(),
                fs_to_openai(),
                openai_to_fs(),
            )

    except Exception as e:
        log.error(f"[{uuid}] Bridge exception: {e}", exc_info=True)
    finally:
        if METRICS:
            ACTIVE_CALLS_GAUGE.dec()
        log.info(f"[{uuid}] Done. outcome={outcome} barges={barges}")


# ── TCP server: one task per inbound call ─────────────────────────────────────
async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer = writer.get_extra_info("peername")
    log.info(f"Connection from {peer}")
    try:
        # ESL auth handshake
        data = await asyncio.wait_for(reader.read(512), timeout=5)
        if b"auth/request" in data:
            writer.write(b"auth " + ESL_PASSWORD.encode() + b"\n\n")
            await writer.drain()
            await asyncio.wait_for(reader.read(512), timeout=5)

        # Subscribe to channel events
        writer.write(b"myevents\n\n")
        await writer.drain()

        # Get channel UUID
        writer.write(b"api uuid_list\n\n")
        await writer.drain()
        resp = await asyncio.wait_for(reader.read(2048), timeout=3)
        lines = resp.decode(errors="ignore").strip().splitlines()
        uuid = next((l.strip() for l in lines if len(l.strip()) == 36), f"call-{id(writer)}")

        await bridge_call(uuid, writer, reader)
    except Exception as e:
        log.error(f"Connection error {peer}: {e}")
    finally:
        writer.close()


async def main():
    log.info(f"AI Bridge listening on {BRIDGE_HOST}:{BRIDGE_PORT}")
    server = await asyncio.start_server(handle_connection, BRIDGE_HOST, BRIDGE_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
