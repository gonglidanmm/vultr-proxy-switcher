"""设置页：Vultr API 密钥 + 代理凭据 + 本地端口 + 区域/套餐。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..vultr import VultrAPI, VultrError
from .workers import CallWorker


class _WsAccountDialog(QDialog):
    """添加 webshare 账号：名称 + API key。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加 webshare 账号")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("如：主账号 / 新账号（用于下拉框区分）")
        self.name_edit.setMinimumWidth(320)
        form.addRow("账号名", self.name_edit)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("webshare API 密钥（dashboard → Account → API）")
        self.key_edit.setMinimumWidth(320)
        form.addRow("API 密钥", self.key_edit)
        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_ok(self) -> None:
        if not self.key_edit.text().strip():
            QMessageBox.warning(self, "提示", "API 密钥不能为空")
            return
        self.accept()

    def name(self) -> str:
        return self.name_edit.text().strip()

    def key(self) -> str:
        return self.key_edit.text().strip()


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[CallWorker] = []
        self._ws_suppress = False
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("设置")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(title)

        # Vultr 卡片
        vultr_card = QFrame()
        vultr_card.setProperty("card", True)
        vc = QVBoxLayout(vultr_card)
        vc.setContentsMargins(20, 16, 20, 16)
        vc.setSpacing(12)

        sec = QLabel("Vultr")
        sec.setProperty("sectionTitle", True)
        vc.addWidget(sec)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.api_key = QLineEdit(config.get("vultr_api_key", ""))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Vultr API 密钥（在 my.vultr.com → Account → API 获取）")
        self.api_key.setMinimumWidth(360)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key, 1)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_api)
        key_row.addWidget(self.test_btn)
        form.addRow("API 密钥", key_row)

        self.api_status = QLabel("")
        form.addRow("", self.api_status)

        self.label_edit = QLineEdit(config.get("label", "vultr-proxy-node"))
        self.label_edit.setMinimumWidth(360)
        form.addRow("实例标签", self.label_edit)

        self.regions_edit = QLineEdit(",".join(config.get("regions", [])))
        self.regions_edit.setPlaceholderText("逗号分隔，如 nrt,osa,sin,sjc")
        self.regions_edit.setMinimumWidth(360)
        form.addRow("区域", self.regions_edit)

        self.plans_edit = QLineEdit(",".join(config.get("plans", [])))
        self.plans_edit.setPlaceholderText("逗号分隔，如 vc2-1c-1gb,vhf-1c-1gb,vhp-1c-2gb")
        self.plans_edit.setMinimumWidth(360)
        form.addRow("套餐", self.plans_edit)

        vc.addLayout(form)
        root.addWidget(vultr_card)

        # Webshare 卡片（多账号）
        ws_card = QFrame()
        ws_card.setProperty("card", True)
        wc = QVBoxLayout(ws_card)
        wc.setContentsMargins(20, 16, 20, 16)
        wc.setSpacing(12)
        wsec = QLabel("Webshare 账号（多 key）")
        wsec.setProperty("sectionTitle", True)
        wc.addWidget(wsec)
        ws_tip = QLabel("每个 webshare 账号一个 API key（独立 IP 池 + 独立流量额度）。在「Webshare-IP」页顶部下拉框切换加载。")
        ws_tip.setStyleSheet("color: #98989d; font-size: 11px;")
        ws_tip.setWordWrap(True)
        wc.addWidget(ws_tip)
        self.ws_accounts_table = QTableWidget(0, 2)
        self.ws_accounts_table.setHorizontalHeaderLabels(["账号名", "API 密钥"])
        self.ws_accounts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.ws_accounts_table.horizontalHeader().setStretchLastSection(True)
        self.ws_accounts_table.verticalHeader().setVisible(False)
        self.ws_accounts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ws_accounts_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.ws_accounts_table.setColumnWidth(0, 140)
        self.ws_accounts_table.setMinimumHeight(140)
        self.ws_accounts_table.setMinimumWidth(480)
        self.ws_accounts_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.ws_accounts_table.itemChanged.connect(self._on_ws_item_changed)
        wc.addWidget(self.ws_accounts_table)
        ws_btn_row = QHBoxLayout()
        add_ws_btn = QPushButton("添加账号")
        add_ws_btn.clicked.connect(self._add_ws_account)
        ws_btn_row.addWidget(add_ws_btn)
        del_ws_btn = QPushButton("删除选中")
        del_ws_btn.clicked.connect(self._del_ws_account)
        ws_btn_row.addWidget(del_ws_btn)
        ws_btn_row.addStretch(1)
        wc.addLayout(ws_btn_row)
        self._load_ws_accounts()
        root.addWidget(ws_card)

        # 代理卡片
        proxy_card = QFrame()
        proxy_card.setProperty("card", True)
        pc = QVBoxLayout(proxy_card)
        pc.setContentsMargins(20, 16, 20, 16)
        pc.setSpacing(12)

        sec2 = QLabel("本机代理")
        sec2.setProperty("sectionTitle", True)
        pc.addWidget(sec2)
        tip2 = QLabel("每台服务器的 SOCKS5/SS/Reality/Hysteria2 凭据，在「服务器」列表的 ⚙设置 里单独管理。")
        tip2.setStyleSheet("color: #98989d; font-size: 11px;")
        tip2.setWordWrap(True)
        pc.addWidget(tip2)

        pform = QFormLayout()
        pform.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.local_port = QSpinBox()
        self.local_port.setRange(1, 65535)
        self.local_port.setValue(int(config.get("local_port", 1080)))
        pform.addRow("本地监听端口", self.local_port)
        pc.addLayout(pform)
        root.addWidget(proxy_card)

        # 域名卡片
        domain_card = QFrame()
        domain_card.setProperty("card", True)
        dc = QVBoxLayout(domain_card)
        dc.setContentsMargins(20, 16, 20, 16)
        dc.setSpacing(12)
        sec3 = QLabel("主域名（Cloudflare）")
        sec3.setProperty("sectionTitle", True)
        dc.addWidget(sec3)
        dform = QFormLayout()
        dform.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.domain_edit = QLineEdit(config.get("domain_name", ""))
        self.domain_edit.setPlaceholderText("如 yduas.cc（子域名自动分配 node1.yduas.cc）")
        self.domain_edit.setMinimumWidth(360)
        dform.addRow("主域名", self.domain_edit)
        tip = QLabel("⚠️ 这里填主域名（zone），如 yduas.cc。每台服务器的子域名在「服务器」列表的 ⚙设置 里单独选择。")
        tip.setStyleSheet("color: #98989d; font-size: 11px;")
        tip.setWordWrap(True)
        dform.addRow("", tip)
        self.cf_token = QLineEdit(config.get("cf_token", ""))
        self.cf_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.cf_token.setMinimumWidth(360)
        dform.addRow("CF 令牌", self.cf_token)
        self.zone_edit = QLineEdit(config.get("zone_id", ""))
        self.zone_edit.setPlaceholderText("留空自动识别")
        self.zone_edit.setMinimumWidth(360)
        dform.addRow("Zone ID", self.zone_edit)
        dc.addLayout(dform)

        test_row = QHBoxLayout()
        self.cf_test_btn = QPushButton("测试连接")
        self.cf_test_btn.clicked.connect(self._test_cf)
        test_row.addWidget(self.cf_test_btn)
        self.cf_status = QLabel("")
        test_row.addWidget(self.cf_status, 1)
        test_row.addStretch(1)
        dc.addLayout(test_row)
        root.addWidget(domain_card)

        # 保存
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.save_btn = QPushButton("保存")
        self.save_btn.setProperty("primary", True)
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)
        root.addStretch(1)

    def _save(self) -> None:
        accounts = self._collect_ws_accounts()
        config.update(
            vultr_api_key=self.api_key.text().strip(),
            label=self.label_edit.text().strip() or "vultr-proxy-node",
            regions=[s.strip() for s in self.regions_edit.text().split(",") if s.strip()],
            plans=[s.strip() for s in self.plans_edit.text().split(",") if s.strip()],
            local_port=self.local_port.value(),
            domain_name=self.domain_edit.text().strip(),
            cf_token=self.cf_token.text().strip(),
            zone_id=self.zone_edit.text().strip(),
            webshare_accounts=accounts,
        )
        # 向下兼容：保留第一个账号的 key 到旧字段，老代码仍可读
        config.set_value("webshare_api_key", accounts[0]["api_key"] if accounts else "")
        # 若当前选中账号被改名（编辑账号名），同步 active_account 到新名字；被删则回退第一个
        active = config.get("webshare_active_account", "")
        if active:
            matched = False
            for row in range(self.ws_accounts_table.rowCount()):
                it = self.ws_accounts_table.item(row, 0)
                if it and it.data(Qt.ItemDataRole.UserRole) == active:
                    new_name = it.text().strip()
                    config.set_value("webshare_active_account", new_name or active)
                    matched = True
                    break
            if not matched and not any(a["name"] == active for a in accounts):
                config.set_value("webshare_active_account", accounts[0]["name"] if accounts else "")
        self.save_btn.setText("已保存 ✓")
        self.save_btn.setEnabled(False)

    # ---------- webshare 多账号管理 ----------
    def _collect_ws_accounts(self) -> list[dict]:
        """从表格读所有账号 [{name, api_key}]（跳过空 key）。"""
        out = []
        for row in range(self.ws_accounts_table.rowCount()):
            name = (self.ws_accounts_table.item(row, 0).text().strip() if self.ws_accounts_table.item(row, 0) else "")
            key_item = self.ws_accounts_table.item(row, 1)
            key = key_item.data(Qt.ItemDataRole.UserRole) if key_item else ""
            if key:
                out.append({"name": name or f"账号{row + 1}", "api_key": key})
        return out

    def _persist_ws_accounts(self) -> None:
        """立即保存账号列表到 config（添加/删除/改名后即时生效，不必点保存按钮）。"""
        accounts = self._collect_ws_accounts()
        config.set_value("webshare_accounts", accounts)
        config.set_value("webshare_api_key", accounts[0]["api_key"] if accounts else "")

    def _load_ws_accounts(self) -> None:
        """填充账号表格（从 config.webshare_accounts 或旧 webshare_api_key）。"""
        from .. import webshare
        accounts = webshare.list_accounts()
        self._ws_suppress = True
        self.ws_accounts_table.setRowCount(0)
        for a in accounts:
            row = self.ws_accounts_table.rowCount()
            self.ws_accounts_table.insertRow(row)
            name_item = QTableWidgetItem(a.get("name", ""))
            name_item.setData(Qt.ItemDataRole.UserRole, a.get("name", ""))
            self.ws_accounts_table.setItem(row, 0, name_item)
            key = a.get("api_key", "")
            masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("•" * min(len(key), 8))
            key_item = QTableWidgetItem(masked)
            key_item.setData(Qt.ItemDataRole.UserRole, key)
            key_item.setToolTip("密钥不可直接编辑；如需修改请删除后重新添加")
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.ws_accounts_table.setItem(row, 1, key_item)
        self._ws_suppress = False

    def _on_ws_item_changed(self, item: QTableWidgetItem) -> None:
        """账号名双击编辑后立即保存（列 0 = 账号名）。"""
        if self._ws_suppress:
            return
        if item.column() == 0:
            self._persist_ws_accounts()

    def _add_ws_account(self) -> None:
        dlg = _WsAccountDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.name().strip() or f"账号{self.ws_accounts_table.rowCount() + 1}"
        key = dlg.key().strip()
        if not key:
            return

        def _mk_key_item(k: str) -> QTableWidgetItem:
            it = QTableWidgetItem((k[:6] + "…" + k[-4:]) if len(k) > 12 else "•" * min(len(k), 8))
            it.setData(Qt.ItemDataRole.UserRole, k)
            it.setToolTip("密钥不可直接编辑；如需修改请删除后重新添加")
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            return it

        self._ws_suppress = True
        # 重名则覆盖
        for row in range(self.ws_accounts_table.rowCount()):
            if self.ws_accounts_table.item(row, 0) and self.ws_accounts_table.item(row, 0).text() == name:
                self.ws_accounts_table.setItem(row, 1, _mk_key_item(key))
                self._ws_suppress = False
                self._persist_ws_accounts()
                return
        row = self.ws_accounts_table.rowCount()
        self.ws_accounts_table.insertRow(row)
        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.ItemDataRole.UserRole, name)
        self.ws_accounts_table.setItem(row, 0, name_item)
        self.ws_accounts_table.setItem(row, 1, _mk_key_item(key))
        self._ws_suppress = False
        self._persist_ws_accounts()

    def _del_ws_account(self) -> None:
        row = self.ws_accounts_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选中要删除的账号行")
            return
        name = self.ws_accounts_table.item(row, 0).text() if self.ws_accounts_table.item(row, 0) else ""
        if QMessageBox.question(self, "删除账号", f"确定删除账号「{name}」吗？") != QMessageBox.StandardButton.Yes:
            return
        self.ws_accounts_table.removeRow(row)
        self._persist_ws_accounts()

    def _test_cf(self) -> None:
        from ..cloudflare import test_connection
        token = self.cf_token.text().strip()
        domain = self.domain_edit.text().strip()
        zone_id = self.zone_edit.text().strip()
        self.cf_status.setText('<span style="color:#0a84ff;">测试中…</span>')
        self.cf_test_btn.setEnabled(False)

        def _do():
            return test_connection(token, domain, zone_id)

        w = CallWorker(_do)
        w.done.connect(self._on_cf_ok)
        w.failed.connect(lambda m: self._on_cf_fail(str(m)))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_cf_ok(self, result: dict) -> None:
        self.cf_test_btn.setEnabled(True)
        if result.get("ok"):
            self.cf_status.setText(f'<span style="color:#32d74b;">{result.get("message", "连接成功")}</span>')
            if result.get("zone_id"):
                self.zone_edit.setText(result["zone_id"])
        else:
            self.cf_status.setText(f'<span style="color:#ff453a;">{result.get("message", "失败")}</span>')

    def _on_cf_fail(self, msg: str) -> None:
        self.cf_test_btn.setEnabled(True)
        self.cf_status.setText(f'<span style="color:#ff453a;">失败：{msg}</span>')

    def _test_api(self) -> None:
        key = self.api_key.text().strip()
        if not key:
            self.api_status.setText('<span style="color:#ff9f0a;">请先输入 API 密钥</span>')
            return
        self.api_status.setText('<span style="color:#0a84ff;">测试中…</span>')
        self.test_btn.setEnabled(False)

        def _do():
            api = VultrAPI(key)
            return api.list_instances()

        w = CallWorker(_do)
        w.done.connect(self._on_api_ok)
        w.failed.connect(self._on_api_fail)
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_api_ok(self, instances) -> None:
        self.test_btn.setEnabled(True)
        n = len(instances) if isinstance(instances, list) else 0
        self.api_status.setText(f'<span style="color:#32d74b;">连接成功（现有 {n} 台实例）</span>')

    def _on_api_fail(self, msg: str) -> None:
        self.test_btn.setEnabled(True)
        hint = ""
        if "not enabled" in msg.lower() or "unauthorized ip" in msg.lower():
            hint = "（去 my.vultr.com → Account → API 打开 Enable API 并清空 IP 白名单）"
        self.api_status.setText(f'<span style="color:#ff453a;">失败：{msg}{hint}</span>')
