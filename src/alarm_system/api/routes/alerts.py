from __future__ import annotations

import logging
from typing import NoReturn

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

logger = logging.getLogger(__name__)


def _raise_backend_unavailable(exc: AlertStoreBackendError) -> NoReturn:
    logger.error("alert_store_backend_error", exc_info=exc)
    raise HTTPException(
        status_code=503,
        detail="alert store temporarily unavailable",
    ) from exc


def _validate_alert_rule_identity(
    *,
    rule_id: str,
    rule_version: int,
    rule_identities: set[tuple[str, int]] | None,
) -> None:
    if rule_identities is None:
        return
    if (rule_id, rule_version) not in rule_identities:
        raise HTTPException(
            status_code=422,
            detail=(
                "unknown rule identity for alert: "
                f"{rule_id}#{rule_version}"
            ),
        )


def _list_alerts(
    store: AlertStore,
    user_id: str | None,
    include_disabled: bool,
) -> AlertListResponse:
    try:
        return AlertListResponse(
            alerts=store.list_alerts(user_id=user_id, include_disabled=include_disabled)
        )
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)


def _get_alert(store: AlertStore, alert_id: str) -> AlertResponse:
    try:
        alert = store.get_alert(alert_id)
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    return AlertResponse(alert=alert)


def _create_alert(
    store: AlertStore,
    payload: AlertCreateRequest,
    *,
    rule_identities: set[tuple[str, int]] | None,
) -> AlertResponse:
    _validate_alert_rule_identity(
        rule_id=payload.rule_id,
        rule_version=payload.rule_version,
        rule_identities=rule_identities,
    )
    alert = payload.to_alert()
    try:
        existing = store.get_alert(alert.alert_id)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"alert {alert.alert_id} already exists",
            )
        saved = store.upsert_alert(alert, expected_version=0)
    except AlertStoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AlertStoreContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)
    return AlertResponse(alert=saved)


def _update_alert(
    store: AlertStore,
    alert_id: str,
    payload: AlertUpdateRequest,
    *,
    rule_identities: set[tuple[str, int]] | None,
) -> AlertResponse:
    _validate_alert_rule_identity(
        rule_id=payload.rule_id,
        rule_version=payload.rule_version,
        rule_identities=rule_identities,
    )
    try:
        existing = store.get_alert(alert_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="alert not found")
        alert = payload.to_alert(alert_id=alert_id, created_at=existing.created_at)
        saved = store.upsert_alert(alert, expected_version=payload.expected_version)
    except AlertStoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AlertStoreContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)
    return AlertResponse(alert=saved)


def _delete_alert(store: AlertStore, alert_id: str) -> dict[str, bool]:
    try:
        return {"deleted": store.delete_alert(alert_id)}
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)


def _list_bindings(
    store: AlertStore,
    user_id: str | None,
    channel: DeliveryChannel | None,
) -> ChannelBindingListResponse:
    try:
        return ChannelBindingListResponse(
            bindings=store.list_bindings(user_id=user_id, channel=channel)
        )
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)


def _get_binding(store: AlertStore, binding_id: str) -> ChannelBindingResponse:
    try:
        binding = store.get_binding(binding_id)
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)
    if binding is None:
        raise HTTPException(status_code=404, detail="binding not found")
    return ChannelBindingResponse(binding=binding)


def _upsert_binding(
    store: AlertStore,
    payload: ChannelBindingUpsertRequest,
) -> ChannelBindingResponse:
    try:
        saved = store.upsert_binding(payload.to_binding())
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)
    return ChannelBindingResponse(binding=saved)


def _delete_binding(store: AlertStore, binding_id: str) -> dict[str, bool]:
    try:
        return {"deleted": store.delete_binding(binding_id)}
    except AlertStoreBackendError as exc:
        _raise_backend_unavailable(exc)


def build_alerts_router(
    store: AlertStore,
    *,
    rule_identities: set[tuple[str, int]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/internal", tags=["internal-alerts"])

    @router.get("/alerts", response_model=AlertListResponse)
    def list_alerts(
        user_id: str | None = Query(default=None),
        include_disabled: bool = Query(default=False),
    ) -> AlertListResponse:
        return _list_alerts(store, user_id, include_disabled)

    @router.get("/alerts/{alert_id}", response_model=AlertResponse)
    def get_alert(alert_id: str) -> AlertResponse:
        return _get_alert(store, alert_id)

    @router.post("/alerts", response_model=AlertResponse)
    def create_alert(payload: AlertCreateRequest) -> AlertResponse:
        return _create_alert(
            store,
            payload,
            rule_identities=rule_identities,
        )

    @router.put("/alerts/{alert_id}", response_model=AlertResponse)
    def update_alert(alert_id: str, payload: AlertUpdateRequest) -> AlertResponse:
        return _update_alert(
            store,
            alert_id,
            payload,
            rule_identities=rule_identities,
        )

    @router.delete("/alerts/{alert_id}")
    def delete_alert(alert_id: str) -> dict[str, bool]:
        return _delete_alert(store, alert_id)

    @router.get("/channel-bindings", response_model=ChannelBindingListResponse)
    def list_channel_bindings(
        user_id: str | None = Query(default=None),
        channel: DeliveryChannel | None = Query(default=None),
    ) -> ChannelBindingListResponse:
        return _list_bindings(store, user_id, channel)

    @router.get(
        "/channel-bindings/{binding_id}",
        response_model=ChannelBindingResponse,
    )
    def get_channel_binding(binding_id: str) -> ChannelBindingResponse:
        return _get_binding(store, binding_id)

    @router.post("/channel-bindings", response_model=ChannelBindingResponse)
    def upsert_channel_binding(
        payload: ChannelBindingUpsertRequest,
    ) -> ChannelBindingResponse:
        return _upsert_binding(store, payload)

    @router.delete("/channel-bindings/{binding_id}")
    def delete_channel_binding(binding_id: str) -> dict[str, bool]:
        return _delete_binding(store, binding_id)

    return router
