from __future__ import annotations

import os

import uvicorn

from alarm_system.api.app import create_app


def main() -> None:
    host = os.getenv("ALARM_API_HOST", "0.0.0.0")
    port = int(os.getenv("ALARM_API_PORT", "8000"))
    app = create_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
