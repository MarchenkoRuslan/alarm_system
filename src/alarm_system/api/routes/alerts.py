from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from alarm_system.alert_store import (
    AlertStore,
    AlertStoreBackendError,
    AlertStoreContractError,
    AlertStoreConflictError,
)
from alarm_system.api.schemas import (
    AlertCreateRequest,
    AlertListResponse,
    AlertResponse,
    AlertUpdateRequest,
    ChannelBindingListResponse,
    ChannelBindingResponse,
    ChannelBindingUpsertRequest,
)
from alarm_system.entities import DeliveryChannel


def build_alerts_router(store: AlertStore) -> APIRouter:
    router = APIRouter(prefix="/internal", tags=["internal-alerts"])

    @router.get("/alerts", response_model=AlertListResponse)
    def list_alerts(
        user_id: str | None = Query(default=None),
        include_disabled: bool = Query(default=False),
    ) -> AlertListResponse:
        try:
            return AlertListResponse(
                alerts=store.list_alerts(
                    user_id=user_id,
                    include_disabled=include_disabled,
                )
            )
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/alerts/{alert_id}", response_model=AlertResponse)
    def get_alert(alert_id: str) -> AlertResponse:
        try:
            alert = store.get_alert(alert_id)
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        return AlertResponse(alert=alert)

    @router.post("/alerts", response_model=AlertResponse)
    def create_alert(payload: AlertCreateRequest) -> AlertResponse:
        alert = payload.to_alert()
        try:
            existing = store.get_alert(alert.alert_id)
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"alert {alert.alert_id} already exists",
                )
            saved = store.upsert_alert(
                alert,
                expected_version=0,
            )
        except AlertStoreConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AlertStoreContractError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return AlertResponse(alert=saved)

    @router.put("/alerts/{alert_id}", response_model=AlertResponse)
    def update_alert(
        alert_id: str,
        payload: AlertUpdateRequest,
    ) -> AlertResponse:
        try:
            existing = store.get_alert(alert_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="alert not found")
            alert = payload.to_alert(
                alert_id=alert_id,
                created_at=existing.created_at,
            )
            saved = store.upsert_alert(
                alert,
                expected_version=payload.expected_version,
            )
        except AlertStoreConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AlertStoreContractError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return AlertResponse(alert=saved)

    @router.delete("/alerts/{alert_id}")
    def delete_alert(alert_id: str) -> dict[str, bool]:
        try:
            return {"deleted": store.delete_alert(alert_id)}
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/channel-bindings", response_model=ChannelBindingListResponse)
    def list_channel_bindings(
        user_id: str | None = Query(default=None),
        channel: DeliveryChannel | None = Query(default=None),
    ) -> ChannelBindingListResponse:
        try:
            return ChannelBindingListResponse(
                bindings=store.list_bindings(user_id=user_id, channel=channel)
            )
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get(
        "/channel-bindings/{binding_id}",
        response_model=ChannelBindingResponse,
    )
    def get_channel_binding(binding_id: str) -> ChannelBindingResponse:
        try:
            binding = store.get_binding(binding_id)
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if binding is None:
            raise HTTPException(status_code=404, detail="binding not found")
        return ChannelBindingResponse(binding=binding)

    @router.post("/channel-bindings", response_model=ChannelBindingResponse)
    def upsert_channel_binding(
        payload: ChannelBindingUpsertRequest,
    ) -> ChannelBindingResponse:
        try:
            saved = store.upsert_binding(payload.to_binding())
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return ChannelBindingResponse(binding=saved)

    @router.delete("/channel-bindings/{binding_id}")
    def delete_channel_binding(binding_id: str) -> dict[str, bool]:
        try:
            return {"deleted": store.delete_binding(binding_id)}
        except AlertStoreBackendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
