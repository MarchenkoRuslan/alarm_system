from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from alarm_system.alert_store import (
    AlertStore,
    InMemoryAlertStore,
    build_cached_alert_store,
)
from alarm_system.api.migrations import (
    apply_sql_migrations,
    should_auto_apply_sql_migrations,
)
from alarm_system.api.routes import build_alerts_router, build_telegram_router
from alarm_system.api.telegram_client import TelegramApiClient
from alarm_system.rules_dsl import AlertRuleV1

logger = logging.getLogger(__name__)


def create_app(
    *,
    store: AlertStore | None = None,
    telegram_client: TelegramApiClient | None = None,
) -> FastAPI:
    resolved_store = store or _store_from_env()
    resolved_telegram_client = telegram_client or _telegram_client_from_env()
    rule_identities = _load_rule_identities_from_env()
    webhook_url = _optional_env("ALARM_TELEGRAM_WEBHOOK_URL")
    webhook_secret = _optional_env("ALARM_TELEGRAM_WEBHOOK_SECRET")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if webhook_url is not None:
            try:
                await resolved_telegram_client.set_webhook(
                    webhook_url=webhook_url,
                    secret_token=webhook_secret,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "telegram_webhook_registration_failed",
                    extra={"url": webhook_url, "error": str(exc)},
                )
            else:
                logger.info(
                    "telegram_webhook_registered",
                    extra={"url": webhook_url},
                )
        yield

    app = FastAPI(
        title="Alarm System Internal API",
        description=(
            "Interactive Telegram webhook and internal CRUD API for alerts."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        logger.warning(
            "request_validation_error",
            extra={
                "path": request.url.path,
                "method": request.method,
                "query": dict(request.query_params),
                "has_body": request.headers.get("content-length", "0") != "0",
                "errors": exc.errors(),
            },
        )
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    app.include_router(
        build_alerts_router(
            resolved_store,
            rule_identities=rule_identities,
        )
    )
    app.include_router(
        build_telegram_router(
            store=resolved_store,
            telegram_client=resolved_telegram_client,
            secret_token=webhook_secret,
        )
    )
    # TODO(security): add auth on /internal/*.
    return app


def _store_from_env() -> AlertStore:
    alarm_env = _read_alarm_env()
    postgres_dsn = os.getenv("ALARM_POSTGRES_DSN")
    redis_url = os.getenv("ALARM_REDIS_URL")
    if not postgres_dsn or not postgres_dsn.strip():
        if alarm_env in {"dev", "test"}:
            return InMemoryAlertStore()
        raise RuntimeError(
            "ALARM_POSTGRES_DSN is required when ALARM_ENV is staging/prod."
        )
    if should_auto_apply_sql_migrations():
        # TODO(migrations): replace auto-SQL bootstrap with Alembic versioned migrations.
        apply_sql_migrations(postgres_dsn=postgres_dsn.strip())
    if not redis_url or not redis_url.strip():
        return build_cached_alert_store(
            postgres_dsn=postgres_dsn.strip(),
            redis_client=_build_noop_redis(),
        )
    cache_ttl_seconds = _parse_int_env(
        "ALARM_CONFIG_CACHE_TTL_SECONDS",
        default=30,
    )
    return build_cached_alert_store(
        postgres_dsn=postgres_dsn.strip(),
        redis_client=_build_redis_client(redis_url.strip()),
        cache_ttl_seconds=cache_ttl_seconds,
    )


def _telegram_client_from_env() -> TelegramApiClient:
    bot_token = os.getenv("ALARM_TELEGRAM_BOT_TOKEN")
    if bot_token is None or not bot_token.strip():
        raise RuntimeError(
            "ALARM_TELEGRAM_BOT_TOKEN is required for Telegram webhook API."
        )
    return TelegramApiClient(bot_token=bot_token.strip())


def _build_redis_client(redis_url: str) -> Any:
    try:
        import redis
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'redis' package is required for API cache integration."
        ) from exc
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _build_noop_redis() -> Any:
    class _NoopRedis:
        def get(self, key: str) -> None:
            return None

        def set(
            self,
            key: str,
            value: str,
            ex: int | None = None,
            nx: bool = False,
        ) -> bool:
            return True

        def delete(self, key: str) -> int:
            return 0

    return _NoopRedis()


def _parse_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value.strip())


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _read_alarm_env() -> str:
    value = os.getenv("ALARM_ENV", "dev").strip().lower()
    if value in {"dev", "test", "staging", "prod"}:
        return value
    raise ValueError(
        "Invalid ALARM_ENV value. Use one of dev/test/staging/prod."
    )


def _load_rule_identities_from_env() -> set[tuple[str, int]] | None:
    rules_path = _optional_env("ALARM_RULES_PATH")
    if rules_path is None:
        return None
    try:
        content = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"ALARM_RULES_PATH does not exist: {rules_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"ALARM_RULES_PATH contains invalid JSON: {rules_path}"
        ) from exc
    if not isinstance(content, list):
        raise RuntimeError(
            "ALARM_RULES_PATH must contain a JSON array of rules."
        )
    identities: set[tuple[str, int]] = set()
    for raw_rule in content:
        rule = AlertRuleV1.model_validate(raw_rule)
        identities.add((rule.rule_id, rule.version))
    return identities
