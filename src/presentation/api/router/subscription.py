from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.subscription_service import SubscriptionService
from src.domain.user import Role
from src.presentation.api.schemas.subscription import PlanUpdate, SubscriptionOut
from src.presentation.middleware.auth import CurrentUser, get_current_user, AdminUser
from src.presentation.utils.to_object_id import ObjectIdParam

router = APIRouter(
  prefix="/subscriptions",
  tags=["subscriptions"],
  dependencies=[Depends(get_current_user)],
)

Service = Annotated[
  SubscriptionService, Depends(Provide[Container.subscription_service])
]


@router.get("/me", response_model=SubscriptionOut)
@inject
async def my_subscription(service: Service, current: CurrentUser):
  subscription = await service.get_by_user(current.id)
  if not subscription:
    # cuentas anteriores al trial: se les abre aquí en vez de migrar a mano
    subscription = await service.start_trial(current.id)
  return subscription


@router.patch("/{user_id}", response_model=SubscriptionOut)
@inject
async def set_plan(
  user_id: ObjectIdParam,
  payload: PlanUpdate,
  service: Service,
  admin: AdminUser,
):
  """Cambiar de plan es cosa del admin: mientras no haya pasarela, no hay
  ninguna otra forma legítima de que un usuario se suba a pro."""
  subscription = await service.set_plan(user_id, payload.plan, payload.days)
  if not subscription:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
  return subscription


@router.delete("/{user_id}/cancel", response_model=SubscriptionOut)
@inject
async def cancel(user_id: ObjectIdParam, service: Service, current: CurrentUser):
  if user_id != current.id and current.role != Role.ADMIN:
    raise HTTPException(status.HTTP_403_FORBIDDEN, "not your subscription")
  subscription = await service.cancel(user_id)
  if not subscription:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
  return subscription
