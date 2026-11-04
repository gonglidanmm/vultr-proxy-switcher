"""用户页回归：列表要显示最近到期，拖拽要写回 user_order，详情要能改每天 IP 上限。"""

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QSpinBox, QWidget  # noqa: E402

from app.ui import users_page  # noqa: E402
from app.ui.users_page import UsersPage, _UserLinesDialog  # noqa: E402
from app.user_directory import order_users  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def test_user_row_shows_soonest_expiry():
    """在用/已到期旁边要带「最近 X」，和 line_overview 的 soon_text 一致。"""
    _app()
    overview = {
        "text": "在用 1 · 已到期 1 · 最近 5天",
        "active": 1,
        "expired": 1,
        "soon_text": "5天",
    }
    page = UsersPage.__new__(UsersPage)
    page._detail = lambda _user: None
    page._toggle_status = lambda _user: None
    page._delete_user = lambda _user: None
    row = page._user_row(
        {"user_id": 1, "username": "mix", "status": "active"},
        overview,
        "1.00G",
        False,
    )
    labels = [w.text() for w in row.findChildren(QLabel)]
    assert any("最近 5天" in text for text in labels)
    assert any("已到期 1" in text for text in labels)


def _mouse(kind, local, buttons):
    return QMouseEvent(
        kind,
        QPointF(*local),
        QPointF(*local),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_row_click_opens_detail_drag_reorders_instead():
    _app()
    page = UsersPage.__new__(UsersPage)
    picked = []
    dropped = []
    page._detail = lambda user: picked.append(user["user_id"])
    page._drop_row = lambda user, _pos: dropped.append(user["user_id"])
    page._toggle_status = lambda _user: None
    page._delete_user = lambda _user: None
    overview = {"active": 0, "expired": 0, "soon_text": ""}
    row = page._user_row({"user_id": 4, "username": "d", "status": "active"}, overview, "0", False)

    row.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, (10, 10), Qt.MouseButton.LeftButton))
    row.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, (12, 12), Qt.MouseButton.NoButton))
    assert picked == [4]
    assert dropped == []

    picked.clear()
    row.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, (10, 10), Qt.MouseButton.LeftButton))
    row.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, (40, 10), Qt.MouseButton.LeftButton))
    row.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, (40, 10), Qt.MouseButton.NoButton))
    assert picked == []
    assert dropped == [4]


def test_row_drop_persists_full_list_order(monkeypatch):
    saved = {}
    monkeypatch.setattr(users_page.config, "set_value", lambda key, value: saved.__setitem__(key, value))
    page = UsersPage.__new__(UsersPage)
    original = [
        {"user_id": 1, "username": "a"},
        {"user_id": 2, "username": "b"},
        {"user_id": 3, "username": "c"},
        {"user_id": 4, "username": "d"},
    ]
    page._users = [dict(user) for user in original]
    page._visible = [page._users[0], page._users[2]]
    applied = []
    page._apply_view = lambda: applied.append(True)
    page._on_row_dropped(0, 1)
    assert [u["username"] for u in page._users] == ["b", "c", "a", "d"]
    assert saved["user_order"] == [2, 3, 1, 4]
    assert applied == [True]
    restored = order_users(original, saved["user_order"])
    assert [u["username"] for u in restored] == ["b", "c", "a", "d"]


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.logs = []

    def _tint_scroll(self, _scroll, _color):
        return None

    def _log(self, msg):
        self.logs.append(msg)

    def refresh(self):
        return None


def _ann():
    return {
        "user_id": 7,
        "username": "ann",
        "status": "active",
        "note": "",
        "switch_limit": 5,
        "lines": [{
            "user_id": 7,
            "resource_type": "vultr",
            "resource_id": "r1",
            "node_switch_limit": 3,
            "user_switch_limit": 5,
            "node_data": {"name": "节点", "domain": "a.example", "ip": "1.1.1.1"},
            "quota_gb": 0,
            "used_gb": 0,
            "hist_gb": 0,
        }],
    }


def test_detail_panel_saves_daily_ip_limit_and_resets_the_line(monkeypatch):
    """详情里能改用户每天 IP 上限，也能按旧对话框把本线路上限写回去并重置今天的次数。"""
    _app()
    today = datetime.now().strftime("%Y-%m-%d")
    calls = []

    def get_user(_uid):
        return {
            "user": {"switch_limit": 8},
            "assignments": [{"resource_id": "r1", "switch_count": 1, "switch_date": today}],
        }

    monkeypatch.setattr(users_page.api_client, "get_user", get_user)
    monkeypatch.setattr(
        users_page.api_client, "update_user",
        lambda uid, **fields: calls.append(("update", uid, fields)) or {},
    )
    monkeypatch.setattr(
        users_page.api_client, "reset_user_switch",
        lambda uid, resource_id=None: calls.append(("reset", uid, resource_id)) or {},
    )
    monkeypatch.setattr(
        users_page.QMessageBox, "question",
        lambda *_a, **_k: QMessageBox.StandardButton.Yes,
    )

    panel = _UserLinesDialog(_Host(), _ann())
    assert any(w.text() == "每天 IP 上限" for w in panel.findChildren(QLabel))
    assert panel.switch_spin.value() == 8
    assert panel.findChild(QSpinBox, "lineSwitchSpin").value() == 3
    assert panel.findChild(QLabel, "switchCount").text() == "已用 IP：2/3"

    panel.switch_spin.setValue(9)
    panel.findChild(QPushButton, "saveUserSwitch").click()
    assert ("update", 7, {"switch_limit": 9}) in calls

    panel.findChild(QSpinBox, "lineSwitchSpin").setValue(4)
    panel.findChild(QPushButton, "saveLineSwitch").click()
    assert ("update", 7, {"switch_limit": 4, "resource_id": "r1"}) in calls

    panel.findChild(QPushButton, "resetSwitch").click()
    assert ("reset", 7, "r1") in calls
    assert any("已重置节点 a.example" in msg for msg in panel._page.logs)
