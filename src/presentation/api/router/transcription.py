from typing import Annotated

from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from src.container import Container
from src.application.use_cases.transcription_service import TranscriptionService
from src.presentation.middleware.auth import ProUser

router = APIRouter(prefix="/transcription", tags=["transcription"])

Service = Annotated[TranscriptionService, Depends(Provide[Container.transcription_service])]


@router.websocket("/ws")
@inject
async def transcribe(websocket: WebSocket, current: ProUser, service: Service):
  """El cliente manda frames binarios PCM16 mono 24 kHz y recibe JSON:
  {"delta": "..."} según habla, {"text": "..."} por frase cerrada, {"error": "..."}.
  Cierra el socket cuando termina de grabar. La cookie de sesión viaja en el handshake."""
  await websocket.accept()

  async def audio():
    try:
      while True:
        yield await websocket.receive_bytes()
    except WebSocketDisconnect:
      return

  try:
    async for event in service.stream(audio(), current.id, current.email):
      await websocket.send_json(event)
  except WebSocketDisconnect:
    return
