"""前置机 /stop：缺服务算成功，systemctl 真失败必须让调用方看见。"""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "switch_server.py"
_spec = importlib.util.spec_from_file_location("switch_server", _PATH)
switch_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(switch_server)


def test_missing_unit_is_success():
    calls = []

    def run(cmd):
        calls.append(cmd)
        return 1, "Failed to disable unit: Unit file gost-relay-r1.service does not exist."

    result = switch_server.stop_relay("r1", run=run)
    assert result["ok"] is True
    assert result["error"] == ""
    assert switch_server.stop_http_status(result) == 200
    assert len(calls) == 2


def test_systemctl_failure_is_not_http_success():
    def run(cmd):
        svc = cmd[-1]
        if svc.startswith("gost-"):
            return 1, "Job for gost-relay-r1.service failed because the control process exited with error code."
        return 0, ""

    result = switch_server.stop_relay("r1", run=run)
    assert result["ok"] is False
    assert "gost-relay-r1" in result["error"]
    assert switch_server.stop_http_status(result) == 500


def test_not_loaded_unit_is_success():
    def run(_cmd):
        return 5, "Unit sing-box-relay-r1.service not loaded."

    result = switch_server.stop_relay("r9", run=run)
    assert result["ok"] is True
    assert switch_server.stop_http_status(result) == 200
