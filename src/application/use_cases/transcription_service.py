import asyncio
import base64
from collections.abc import AsyncIterator
from decimal import Decimal

import logfire
from bson import ObjectId
from openai import AsyncOpenAI

from src.application.use_cases.usage_service import UsageService

# ponytail: modelo fijo; a env si se quiere comparar con gpt-transcribe
MODEL = "gpt-live-transcribe"
# el único rate que admite la Realtime API para pcm16
SAMPLE_RATE = 24000
# ponytail: precio a mano. openai no expone precios y genai-prices no conoce el modelo
PRICE_PER_MINUTE = Decimal("0.017")


class TranscriptionService:
  """Voz a texto en directo: el audio entra en trozos y el texto sale según se habla."""

  def __init__(self, usage_service: UsageService):
    self.client = AsyncOpenAI()  # OPENAI_API_KEY del entorno
    self.usage_service = usage_service

  async def stream(
    self, audio: AsyncIterator[bytes], user_id: ObjectId, email: str
  ) -> AsyncIterator[dict]:
    """audio: PCM16 mono a 24 kHz, en trozos. Emite {"delta": "..."} mientras habla y
    {"text": "..."} con cada frase cerrada (el corte lo decide el propio modelo).
    Termina cuando se acaba el audio o falla la sesión ({"error": "..."})."""
    async with self.client.realtime.connect(extra_query={"intent": "transcription"}) as conn:
      await conn.session.update(session={
        "type": "transcription",
        "audio": {"input": {
          "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
          # sin language: el modelo detecta el idioma en cada frase, el usuario habla en el que sea
          "transcription": {"model": MODEL},
          # gpt-live-transcribe corta las frases él solo: con VAD del servidor la sesión falla
          "turn_detection": None,
        }},
      })

      # sin VAD nadie cierra el buffer: el `completed` (y con él el usage) solo llega
      # tras un commit, así que al acabar el audio se commitea y se espera ese evento
      audio_done = asyncio.Event()

      async def pump() -> None:
        async for chunk in audio:
          await conn.input_audio_buffer.append(audio=base64.b64encode(chunk).decode())
        audio_done.set()
        await conn.input_audio_buffer.commit()
        # ponytail: si el completed no llega en 5 s se cierra igual y esa frase no se factura
        await asyncio.sleep(5)
        await conn.close()

      sender = asyncio.create_task(pump())
      # el usage llega por frase, en tokens o en segundos según facture el modelo
      input_tokens = output_tokens = 0
      seconds = 0.0
      try:
        async for event in conn:
          if event.type == "conversation.item.input_audio_transcription.delta":
            yield {"delta": event.delta}
          elif event.type == "conversation.item.input_audio_transcription.completed":
            usage = event.usage
            if usage is not None and usage.type == "tokens":
              input_tokens += usage.input_tokens
              output_tokens += usage.output_tokens
            elif usage is not None:
              seconds += usage.seconds
            yield {"text": event.transcript}
            if audio_done.is_set():
              break  # el `async with` cierra la sesión
          elif event.type == "error":
            logfire.error("Transcription error: {message}", message=event.error.message)
            yield {"error": event.error.message}
      finally:
        sender.cancel()
        logfire.info(
          "Transcription for {email} used {seconds}s / {input_tokens} tokens",
          email=email, seconds=seconds, input_tokens=input_tokens,
        )
        if seconds or input_tokens:
          await self.usage_service.record_transcription(
            user_id=user_id,
            email=email,
            model=MODEL,
            seconds=seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            price_per_minute=PRICE_PER_MINUTE,
          )
