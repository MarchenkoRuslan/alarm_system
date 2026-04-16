from __future__ import annotations

import unittest
from unittest.mock import patch

from alarm_system import run_api


class RunApiTests(unittest.TestCase):
    def test_main_parses_env_and_calls_uvicorn(self) -> None:
        app_obj = object()
        with patch.dict(
            "os.environ",
            {"ALARM_API_HOST": "127.0.0.1", "ALARM_API_PORT": "9090"},
            clear=False,
        ), patch("alarm_system.run_api.create_app", return_value=app_obj), patch(
            "alarm_system.run_api.uvicorn.run"
        ) as uvicorn_run:
            run_api.main()

        uvicorn_run.assert_called_once_with(app_obj, host="127.0.0.1", port=9090)

    def test_main_uses_default_host_port(self) -> None:
        app_obj = object()
        with patch.dict(
            "os.environ",
            {},
            clear=True,
        ), patch("alarm_system.run_api.create_app", return_value=app_obj), patch(
            "alarm_system.run_api.uvicorn.run"
        ) as uvicorn_run:
            run_api.main()

        uvicorn_run.assert_called_once_with(app_obj, host="0.0.0.0", port=8000)
