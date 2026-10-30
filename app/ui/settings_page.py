"""设置页：Vultr API 密钥 + 代理凭据 + 本地端口 + 区域/套餐。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..vultr import VultrAPI, VultrError
from .workers import CallWorker


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[CallWorker] = []
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
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
        form.addRow("实例标签", self.label_edit)

        self.regions_edit = QLineEdit(",".join(config.get("regions", [])))
        self.regions_edit.setPlaceholderText("逗号分隔，如 nrt,osa,sin,sjc")
        form.addRow("区域", self.regions_edit)

        self.plans_edit = QLineEdit(",".join(config.get("plans", [])))
        self.plans_edit.setPlaceholderText("逗号分隔，如 vc2-1c-1gb,vhf-1c-1gb,vhp-1c-2gb")
        form.addRow("套餐", self.plans_edit)

        vc.addLayout(form)
        root.addWidget(vultr_card)

        # Webshare 卡片
        ws_card = QFrame()
        ws_card.setProperty("card", True)
        wc = QVBoxLayout(ws_card)
        wc.setContentsMargins(20, 16, 20, 16)
        wc.setSpacing(12)
        wsec = QLabel("Webshare")
        wsec.setProperty("sectionTitle", True)
        wc.addWidget(wsec)
        wform = QFormLayout()
        wform.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.ws_api_key = QLineEdit(config.get("webshare_api_key", ""))
        self.ws_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ws_api_key.setPlaceholderText("webshare API 密钥（dashboard → Account → API）")
        self.ws_api_key.setMinimumWidth(360)
        wform.addRow("API 密钥", self.ws_api_key)
        wc.addLayout(wform)
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
        dform.addRow("主域名", self.domain_edit)
        tip = QLabel("⚠️ 这里填主域名（zone），如 yduas.cc。每台服务器的子域名在「服务器」列表的 ⚙设置 里单独选择。")
        tip.setStyleSheet("color: #98989d; font-size: 11px;")
        tip.setWordWrap(True)
        dform.addRow("", tip)
        self.cf_token = QLineEdit(config.get("cf_token", ""))
        self.cf_token.setEchoMode(QLineEdit.EchoMode.Password)
        dform.addRow("CF 令牌", self.cf_token)
        self.zone_edit = QLineEdit(config.get("zone_id", ""))
        self.zone_edit.setPlaceholderText("留空自动识别")
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
        config.update(
            vultr_api_key=self.api_key.text().strip(),
            label=self.label_edit.text().strip() or "vultr-proxy-node",
            regions=[s.strip() for s in self.regions_edit.text().split(",") if s.strip()],
            plans=[s.strip() for s in self.plans_edit.text().split(",") if s.strip()],
            local_port=self.local_port.value(),
            domain_name=self.domain_edit.text().strip(),
            cf_token=self.cf_token.text().strip(),
            zone_id=self.zone_edit.text().strip(),
            webshare_api_key=self.ws_api_key.text().strip(),
        )
        self.save_btn.setText("已保存 ✓")
        self.save_btn.setEnabled(False)

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
