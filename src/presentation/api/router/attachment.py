from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.attachment_service import AttachmentService
from src.presentation.middleware.auth import CurrentUser, get_current_user
from src.presentation.utils.to_object_id import ObjectIdParam

# Todo /attachments exige Bearer válido
router = APIRouter(
  prefix="/attachments", tags=["attachments"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[AttachmentService, Depends(Provide[Container.attachment_service])]

@router.get("/{id}/download")
@inject
async def download_attachment(id: ObjectIdParam, service: Service, current: CurrentUser):
  """Redirige a una URL firmada: el fichero baja del bucket, no por la API."""
  attachment = await service.get(id, current.id)
  if not attachment:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "attachment not found")
  return RedirectResponse(await service.download_url(attachment))
