#!/usr/bin/env python3
"""
fast_ai_bridge.py — Fixed version
Proper FreeSWITCH Outbound ESL + OpenAI Realtime duplex bridge.
Fixes: ESL handshake, audio sampling rates, failover, NAT handling.
"""
import asyncio, base64, json, logging, os, signal, audioop, time
from dotenv import load_dotenv
import websockets

load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log = logging.getLogger("ai_bridge")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-realtime-preview-2024-12-17")
OPENAI_WS_URL  = f"wss://api.openai.com/v1/realtime?model={OPENAI_MODEL}"
AI_VOICE       = os.getenv("AI_VOICE", "alloy")
VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", 600))
VAD_THRESHOLD  = float(os.getenv("VAD_THRESHOLD", 0.5))
BRIDGE_HOST    = os.getenv("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT    = int(os.getenv("BRIDGE_PORT", 5000))
N8N_WEBHOOK    = os.getenv("N8N_WEBHOOK_URL", "http://127.0.0.1:3000/webhook")
AGENT_EXT      = os.getenv("AGENT_EXTENSION", "9000")

try:
    SYSTEM_PROMPT = open(os.path.join(os.path.dirname(__file__), "../prompts/sales_agent.md")).read()
except FileNotFoundError:
    SYSTEM_PROMPT = "You are Aman, a professional sales executive. Keep replies very short."

try:
    from metrics import BARGE_IN_COUNTER, ACTIVE_CALLS_GAUGE
    METRICS = True
except ImportError:
    METRICS = False


# ── Fix #2: Correct audio pipeline ───────────────────────────────────────────
# FS out  → PCMU 8kHz  → upsample  → PCM16 16kHz → OpenAI input
# OpenAI  → PCM16 24kHz → downsample → PCMU 8kHz  → FS in

def pcmu_to_pcm16_16k(data: bytes) -> bytes:
    pcm8k = audioop.ulaw2lin(data, 2)
    pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, None)
    return pcm16k

def pcm16_24k_to_pcmu(data: bytes) -> bytes:
    pcm8k, _ = audioop.ratecv(data, 2, 1, 24000, 8000, None)
    return audioop.lin2ulaw(pcm8k, 2)


# ── Fix #1: Proper Outbound ESL handler ──────────────────────────────────────
class ESLSession:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.uuid   = None

    async def _read_packet(self, timeout=10.0):
        headers = {}
        while True:
            line = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
            if not line:
                raise ConnectionError("ESL closed")
            line = line.decode(errors="ignore").rstrip("\r\n")
            if line == "":
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        body = b""
        if "content-length" in headers:
            body = await asyncio.wait_for(
                self.reader.readexactly(int(headers["content-length"])), timeout=timeout)
        return {"headers": headers, "body": body}

    async def _send(self, cmd):
        self.writer.write((cmd + "\n\n").encode())
        await self.writer.drain()

    async def handshake(self):
        # FS outbound: FS connects to us and sends connect event first
        pkt  = await self._read_packet(timeout=10)
        await self._send("connect")
        pkt  = await self._read_packet(timeout=10)
        body = pkt["body"].decode(errors="ignore")
        for line in body.splitlines():
            if line.lower().startswith("unique-id:"):
                self.uuid = line.split(":", 1)[1].strip()
                break
        if not self.uuid:
            self.uuid = f"call-{int(time.time())}"
        log.info(f"[{self.uuid}] ESL handshake OK")
        await self._send("myevents")
        await self._read_packet(timeout=5)
        await self._send("linger")
        await self._read_packet(timeout=5)
        return True

    async def send_cmd(self, cmd):
        self.writer.write(f"api {cmd}\n\n".encode())
        await self.writer.drain()
        try:
            pkt = await asyncio.wait_for(self._read_packet(timeout=3), timeout=3)
            return pkt["body"].decode(errors="ignore").strip()
        except asyncio.TimeoutError:
            return ""

    async def execute(self, app, arg=""):
        msg = f"sendmsg {self.uuid}\ncall-command: execute\nexecute-app-name: {app}\n"
        if arg:
            msg += f"execute-app-arg: {arg}\n"
        self.writer.write((msg + "\n").encode())
        await self.writer.drain()
        try:
            await asyncio.wait_for(self._read_packet(timeout=5), timeout=5)
        except asyncio.TimeoutError:
            pass

    async def answer(self):
        await self.execute("answer")
        await asyncio.sleep(0.3)

    async def hangup(self, cause="NORMAL_CLEARING"):
        await self.execute("hangup", cause)

    async def break_audio(self):
        await self.send_cmd(f"uuid_break {self.uuid} all")

    async def transfer(self, ext, ctx="default"):
        await self.send_cmd(f"uuid_transfer {self.uuid} {ext} XML {ctx}")

    async def read_event(self, timeout=0.05):
        try:
            return await asyncio.wait_for(self._read_packet(timeout=timeout), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            return None


async def notify_n8n(outcome, uuid, extra=None):
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            await s.post(N8N_WEBHOOK + "/call-outcome",
                json={"uuid": uuid, "outcome": outcome, **(extra or {})},
                timeout=aiohttp.ClientTimeout(total=5))
        log.info(f"[{uuid}] n8n: {outcome}")
    except Exception as e:
        log.warning(f"[{uuid}] n8n failed (non-fatal): {e}")


async def run_bridge(esl: ESLSession):
    uuid    = esl.uuid
    active  = True
    outcome = None
    barges  = 0
    t_start = time.time()
    if METRICS:
        ACTIVE_CALLS_GAUGE.inc()

    ws_headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}

    try:
        async with websockets.connect(OPENAI_WS_URL, additional_headers=ws_headers,
                                      ping_interval=20, open_timeout=10) as ws:
            log.info(f"[{uuid}] OpenAI WS connected")

            await ws.send(json.dumps({"type": "session.update", "session": {
                "modalities": ["audio","text"],
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
                    {"type":"function","name":"transfer_to_agent",
                     "description":"Transfer to human agent when user is interested",
                     "parameters":{"type":"object","properties":{}}},
                    {"type":"function","name":"hangup_call",
                     "description":"End call when user not interested",
                     "parameters":{"type":"object","properties":{}}},
                ],
                "tool_choice": "auto",
            }}))

            audio_q = asyncio.Queue(maxsize=300)

            async def esl_reader():
                nonlocal active
                while active:
                    evt = await esl.read_event(timeout=0.02)
                    if not evt:
                        continue
                    body  = evt.get("body", b"")
                    ctype = evt["headers"].get("content-type", "")
                    if ctype == "text/event-plain":
                        text = body.decode(errors="ignore")
                        for line in text.splitlines():
                            if "Event-Name: CHANNEL_HANGUP" in line:
                                log.info(f"[{uuid}] Channel hangup")
                                active = False
                    elif body:
                        try:
                            audio_q.put_nowait(body)
                        except asyncio.QueueFull:
                            pass

            async def send_audio():
                while active:
                    try:
                        pcmu  = await asyncio.wait_for(audio_q.get(), timeout=0.5)
                        pcm16 = pcmu_to_pcm16_16k(pcmu)
                        await ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm16).decode(),
                        }))
                    except asyncio.TimeoutError:
                        pass

            async def recv_ai():
                nonlocal active, outcome, barges
                async for raw in ws:
                    if not active:
                        break
                    try:
                        ev = json.loads(raw)
                    except Exception:
                        continue
                    t = ev.get("type", "")

                    if t == "response.audio.delta":
                        pcmu = pcm16_24k_to_pcmu(base64.b64decode(ev["delta"]))
                        await esl.send_cmd(f"uuid_audio_play {uuid} base64://{base64.b64encode(pcmu).decode()}")

                    elif t == "input_speech_started":
                        barges += 1
                        await esl.break_audio()
                        if METRICS: BARGE_IN_COUNTER.inc()
                        log.info(f"[{uuid}] Barge-in #{barges}")

                    elif t == "response.function_call_arguments.done":
                        fn = ev.get("name","")
                        if fn == "transfer_to_agent":
                            outcome = "TRANSFER"
                            await notify_n8n("TRANSFER", uuid)
                            await esl.transfer(AGENT_EXT)
                            active = False
                        elif fn == "hangup_call":
                            outcome = "NOT_INTERESTED"
                            await notify_n8n("NOT_INTERESTED", uuid)
                            await esl.hangup()
                            active = False

                    elif t == "conversation.item.input_audio_transcription.completed":
                        txt = ev.get("transcript","")
                        log.info(f"[{uuid}] User: {txt}")
                        if any(w in txt.lower() for w in ["interested","haan","zaroor","yes","batao"]):
                            outcome = "INTERESTED"
                            await notify_n8n("INTERESTED", uuid, {"transcript": txt})

                    elif t == "error":
                        log.error(f"[{uuid}] OpenAI error: {ev.get('error')}")

            await asyncio.gather(esl_reader(), send_audio(), recv_ai())

    except (websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as e:
        # Fix #9: Failover — AI down → transfer to agent queue
        log.error(f"[{uuid}] AI unavailable: {e} — failover to agent")
        outcome = "AI_FAILURE"
        await notify_n8n("AI_FAILURE", uuid, {"error": str(e)})
        try:
            await esl.transfer(AGENT_EXT)
        except Exception:
            await esl.hangup("SERVICE_UNAVAILABLE")

    finally:
        duration = round(time.time() - t_start, 1)
        if METRICS: ACTIVE_CALLS_GAUGE.dec()
        log.info(f"[{uuid}] Done. outcome={outcome} barges={barges} duration={duration}s")


async def handle_call(reader, writer):
    peer = writer.get_extra_info("peername")
    log.info(f"ESL inbound from {peer}")
    esl = ESLSession(reader, writer)
    try:
        if await esl.handshake():
            await esl.answer()
            await run_bridge(esl)
    except Exception as e:
        log.error(f"handle_call: {e}", exc_info=True)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    log.info(f"AI Bridge listening on {BRIDGE_HOST}:{BRIDGE_PORT}")
    server = await asyncio.start_server(handle_call, BRIDGE_HOST, BRIDGE_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
