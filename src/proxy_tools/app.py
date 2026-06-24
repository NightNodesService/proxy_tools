from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .categories import SITE_CATEGORIES, normalize_site_category
from .checker import run_proxy_check
from .config import SETTINGS_PATH, app_root, bundled_root, load_settings, load_targets, save_settings, save_targets
from .i18n import LANGUAGE_OPTIONS, SUPPORTED_LANGUAGES, translate
from .models import AppSettings, ProxyCheckResult, TargetSite
from .proxy_latency import ProxyLatencyResult, test_proxy_latency, unique_proxies
from .third_party import PROVIDERS, ThirdPartyResult, run_third_party_checks, validate_ip


class CheckWorker(QThread):
    finished = Signal(object)

    def __init__(
        self,
        proxy: str,
        proxy_type: str,
        target: TargetSite,
        mode: str,
        settings: AppSettings,
    ) -> None:
        super().__init__()
        self.proxy = proxy
        self.proxy_type = proxy_type
        self.target = target
        self.mode = mode
        self.settings = settings

    def run(self) -> None:
        self.finished.emit(
            run_proxy_check(
                raw_proxy=self.proxy,
                proxy_type=self.proxy_type,
                target=self.target,
                mode=self.mode,
                settings=self.settings,
            )
        )


class ThirdPartyWorker(QThread):
    finished = Signal(object)

    def __init__(self, ip: str, providers: list[str], settings: AppSettings) -> None:
        super().__init__()
        self.ip = ip
        self.providers = providers
        self.settings = settings

    def run(self) -> None:
        self.finished.emit(run_third_party_checks(self.ip, self.providers, self.settings))


class ProxyLatencyWorker(QThread):
    progress = Signal(int, object)
    finished = Signal(object)

    def __init__(
        self,
        proxies: list[str],
        proxy_type: str,
        target: TargetSite,
        settings: AppSettings,
        attempts: int,
        concurrency: int,
    ) -> None:
        super().__init__()
        self.proxies = proxies
        self.proxy_type = proxy_type
        self.target = target
        self.settings = settings
        self.attempts = attempts
        self.concurrency = concurrency

    def run(self) -> None:
        results: list[ProxyLatencyResult] = []
        normalized_attempts = max(1, min(5, self.attempts))
        workers = max(1, min(20, self.concurrency, len(self.proxies) or 1))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    test_proxy_latency,
                    raw_proxy=proxy,
                    proxy_type=self.proxy_type,
                    target=self.target,
                    settings=self.settings,
                    attempts=normalized_attempts,
                ): row
                for row, proxy in enumerate(self.proxies)
            }
            for future in as_completed(futures):
                row = futures[future]
                result = future.result()
                results.append(result)
                self.progress.emit(row, result)
        self.finished.emit(results)


class ProxyLatencyRetryWorker(QThread):
    finished = Signal(int, object)

    def __init__(
        self,
        row: int,
        proxy: str,
        proxy_type: str,
        target: TargetSite,
        settings: AppSettings,
        attempts: int,
    ) -> None:
        super().__init__()
        self.row = row
        self.proxy = proxy
        self.proxy_type = proxy_type
        self.target = target
        self.settings = settings
        self.attempts = attempts

    def run(self) -> None:
        result = test_proxy_latency(
            raw_proxy=self.proxy,
            proxy_type=self.proxy_type,
            target=self.target,
            settings=self.settings,
            attempts=max(1, min(5, self.attempts)),
        )
        self.finished.emit(self.row, result)


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "--") -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("mutedLabel")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.value_label.setWordWrap(True)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("proxy_tools")
        logo_path = self.logo_path()
        if logo_path:
            self.setWindowIcon(QIcon(str(logo_path)))
        self.resize(1180, 760)

        self.all_targets = load_targets()
        self.targets = [target for target in self.all_targets if target.enabled]
        self.settings = load_settings()
        self.language = self.settings.language if self.settings.language in SUPPORTED_LANGUAGES else "zh"
        self.theme = self.settings.theme if self.settings.theme in {"tech_dark", "light"} else "tech_dark"
        self.worker: CheckWorker | None = None
        self.third_party_worker: ThirdPartyWorker | None = None
        self.proxy_latency_worker: ProxyLatencyWorker | None = None
        self.proxy_latency_retry_workers: list[ProxyLatencyRetryWorker] = []
        self.proxy_test_context: dict[str, object] = {}

        self.nav_buttons: list[QPushButton] = []
        self.detail_labels: dict[str, QLabel] = {}
        self.last_result: ProxyCheckResult | None = None

        self.setCentralWidget(self.build_shell())
        self.apply_style()
        self.refresh_language()

    def t(self, key: str) -> str:
        return translate(self.language, key)

    def build_shell(self) -> QWidget:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 22, 18, 18)
        sidebar_layout.setSpacing(18)

        brand = QFrame()
        brand.setObjectName("brand")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(12)
        brand_logo = QLabel("N")
        brand_logo.setObjectName("brandLogo")
        brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_path = self.logo_path()
        if logo_path:
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                brand_logo.setPixmap(
                    pixmap.scaled(
                        36,
                        36,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        brand_text = QFrame()
        brand_text_layout = QVBoxLayout(brand_text)
        brand_text_layout.setContentsMargins(0, 0, 0, 0)
        brand_text_layout.setSpacing(2)
        brand_name = QLabel("proxy_tools")
        brand_name.setObjectName("brandName")
        self.brand_tag = QLabel()
        self.brand_tag.setObjectName("brandTag")
        brand_text_layout.addWidget(brand_name)
        brand_text_layout.addWidget(self.brand_tag)
        brand_layout.addWidget(brand_logo)
        brand_layout.addWidget(brand_text, 1)
        nav_box = QFrame()
        nav_box.setObjectName("navBox")
        nav_layout = QVBoxLayout(nav_box)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(10)

        for index, key in enumerate(("nav_check", "nav_proxy_test", "nav_targets", "nav_third_party", "nav_settings")):
            button = QPushButton(self.t(key))
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setProperty("textKey", key)
            button.clicked.connect(lambda checked=False, page_index=index: self.switch_page(page_index))
            nav_layout.addWidget(button)
            self.nav_buttons.append(button)
        nav_layout.addStretch(1)

        self.sidebar_hint = QLabel()
        self.sidebar_hint.setObjectName("sidebarHint")
        self.sidebar_hint.setWordWrap(True)
        self.support_hint = QLabel()
        self.support_hint.setObjectName("sidebarHint")
        self.support_hint.setWordWrap(True)
        self.sidebar_status = QLabel()
        self.sidebar_status.setObjectName("sidebarStatus")

        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(nav_box, 1)
        sidebar_layout.addStretch(1)
        sidebar_layout.addWidget(self.sidebar_hint)
        sidebar_layout.addWidget(self.support_hint)
        sidebar_layout.addWidget(self.sidebar_status)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.build_check_page())
        self.stack.addWidget(self.build_proxy_test_page())
        self.stack.addWidget(self.build_targets_page())
        self.stack.addWidget(self.build_third_party_page())
        self.stack.addWidget(self.build_settings_page())
        self.switch_page(0)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stack, 1)
        return root

    def logo_path(self):
        for root in (app_root(), bundled_root()):
            candidate = root / "assets" / "nightnodes_logo.svg"
            if candidate.exists():
                return candidate
        return None

    def build_page_frame(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("content")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)
        return page, layout

    def build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)

        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        self.hero_title = QLabel()
        self.hero_title.setObjectName("pageTitle")
        self.hero_subtitle = QLabel()
        self.hero_subtitle.setObjectName("pageSubtitle")
        self.hero_subtitle.setWordWrap(True)
        title_block.addWidget(self.hero_title)
        title_block.addWidget(self.hero_subtitle)

        self.language_input = QComboBox()
        self.language_input.setObjectName("languagePicker")
        self.populate_language_combo(self.language_input)
        self.language_input.setCurrentIndex(max(0, self.language_input.findData(self.language)))
        self.language_input.currentIndexChanged.connect(self.change_language)

        layout.addLayout(title_block, 1)
        layout.addWidget(self.language_input)
        return header

    def build_check_page(self) -> QWidget:
        page, layout = self.build_page_frame()
        layout.addWidget(self.build_header())

        workbench = QFrame()
        workbench.setObjectName("panel")
        workbench_layout = QGridLayout(workbench)
        workbench_layout.setContentsMargins(18, 18, 18, 18)
        workbench_layout.setHorizontalSpacing(14)
        workbench_layout.setVerticalSpacing(12)

        self.proxy_type = QComboBox()
        self.proxy_type.addItems(["HTTP", "HTTPS"])

        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("ip:port:user:pass")

        self.mode_input = QComboBox()
        self.mode_input.addItem(self.t("mode_light"), "Lightweight HTTP")
        self.mode_input.addItem(self.t("mode_browser"), "Browser Simulation")
        self.mode_input.currentIndexChanged.connect(self.update_browser_options_visibility)

        self.target_input = QComboBox()
        for target in self.targets:
            self.target_input.addItem(f"{target.name} - {target.url}", target)

        self.local_chrome_options = QFrame()
        self.local_chrome_options.setObjectName("subPanel")
        local_chrome_layout = QHBoxLayout(self.local_chrome_options)
        local_chrome_layout.setContentsMargins(12, 10, 12, 10)
        local_chrome_layout.setSpacing(12)
        self.local_chrome_caption = QLabel()
        self.local_chrome_yes = QRadioButton()
        self.local_chrome_no = QRadioButton()
        self.local_chrome_no.setChecked(True)
        local_chrome_layout.addWidget(self.local_chrome_caption)
        local_chrome_layout.addWidget(self.local_chrome_no)
        local_chrome_layout.addWidget(self.local_chrome_yes)
        local_chrome_layout.addStretch(1)

        self.check_button = QPushButton()
        self.check_button.setObjectName("primaryButton")
        self.check_button.clicked.connect(self.start_check)
        self.clear_check_button = QPushButton()
        self.clear_check_button.setObjectName("secondaryButton")
        self.clear_check_button.clicked.connect(self.clear_check_page)

        self.proxy_type_caption = QLabel()
        self.proxy_caption = QLabel()
        self.mode_caption = QLabel()
        self.target_caption = QLabel()

        workbench_layout.addWidget(self.proxy_type_caption, 0, 0)
        workbench_layout.addWidget(self.proxy_caption, 0, 1)
        workbench_layout.addWidget(self.proxy_type, 1, 0)
        workbench_layout.addWidget(self.proxy_input, 1, 1)
        workbench_layout.addWidget(self.mode_caption, 2, 0)
        workbench_layout.addWidget(self.target_caption, 2, 1)
        workbench_layout.addWidget(self.mode_input, 3, 0)
        workbench_layout.addWidget(self.target_input, 3, 1)
        workbench_layout.addWidget(self.check_button, 3, 2)
        workbench_layout.addWidget(self.clear_check_button, 3, 3)
        workbench_layout.addWidget(self.local_chrome_options, 4, 0, 1, 3)
        workbench_layout.setColumnStretch(0, 1)
        workbench_layout.setColumnStretch(1, 4)
        self.update_browser_options_visibility()

        self.check_log_frame = QFrame()
        self.check_log_frame.setObjectName("toastLog")
        check_log_layout = QHBoxLayout(self.check_log_frame)
        check_log_layout.setContentsMargins(14, 10, 10, 10)
        check_log_layout.setSpacing(10)
        self.check_log_title = QLabel()
        self.check_log_title.setObjectName("toastTitle")
        self.check_log_body = QLabel()
        self.check_log_body.setObjectName("toastBody")
        self.check_log_body.setWordWrap(True)
        self.check_log_close = QPushButton("×")
        self.check_log_close.setObjectName("iconButton")
        self.check_log_close.setFixedSize(28, 28)
        self.check_log_close.clicked.connect(self.hide_check_log)
        check_log_layout.addWidget(self.check_log_title)
        check_log_layout.addWidget(self.check_log_body, 1)
        check_log_layout.addWidget(self.check_log_close)
        self.check_log_frame.hide()
        self.check_log_timer = QTimer(self)
        self.check_log_timer.setSingleShot(True)
        self.check_log_timer.timeout.connect(self.hide_check_log)

        metrics = QFrame()
        metrics.setObjectName("metricsRow")
        metrics_layout = QGridLayout(metrics)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setHorizontalSpacing(12)

        self.status_card = MetricCard("")
        self.score_card = MetricCard("")
        self.latency_card = MetricCard("")
        self.risk_card = MetricCard("")
        for index, card in enumerate((self.status_card, self.score_card, self.latency_card, self.risk_card)):
            metrics_layout.addWidget(card, 0, index)

        details = QFrame()
        details.setObjectName("panel")
        details_layout = QGridLayout(details)
        details_layout.setContentsMargins(18, 18, 18, 18)
        details_layout.setHorizontalSpacing(18)
        details_layout.setVerticalSpacing(12)

        self.details_title = QLabel()
        self.details_title.setObjectName("sectionTitle")
        details_layout.addWidget(self.details_title, 0, 0, 1, 4)
        self.ip_profile_title = QLabel()
        self.ip_profile_title.setObjectName("subsectionTitle")
        self.reachability_title = QLabel()
        self.reachability_title.setObjectName("subsectionTitle")
        details_layout.addWidget(self.ip_profile_title, 1, 0, 1, 4)

        ip_profile_keys = [
            ("exit_ip", "exit_ip"),
            ("ip_native", "ip_native"),
            ("operator_type", "operator_type"),
            ("ip_type", "ip_type"),
            ("human_traffic", "human_traffic"),
            ("country_region", "country_region"),
            ("coordinates", "coordinates"),
            ("abuse_level", "abuse_level"),
            ("risk_signals", "risk_signals"),
            ("asn", "asn"),
            ("company_info", "company_info"),
        ]
        reachability_keys = [
            ("target", "target"),
            ("blocked", "blocked"),
            ("captcha", "captcha"),
            ("estimated_bandwidth", "estimated_bandwidth"),
        ]
        self.detail_caption_labels: dict[str, QLabel] = {}
        ip_profile_rows = (len(ip_profile_keys) + 1) // 2
        for index, (caption_key, value_key) in enumerate(ip_profile_keys):
            row = 2 + (index % ip_profile_rows)
            col = 0 if index < ip_profile_rows else 2
            self.add_detail_field(details_layout, caption_key, value_key, row, col)
        reachability_title_row = 3 + ip_profile_rows
        reachability_row_start = reachability_title_row + 1
        details_layout.addWidget(self.reachability_title, reachability_title_row, 0, 1, 4)
        for index, (caption_key, value_key) in enumerate(reachability_keys):
            row = reachability_row_start + (index % 2)
            col = 0 if index < 2 else 2
            self.add_detail_field(details_layout, caption_key, value_key, row, col)

        self.global_latency_panel = QFrame()
        self.global_latency_panel.setObjectName("latencyStrip")
        global_latency_layout = QGridLayout(self.global_latency_panel)
        global_latency_layout.setContentsMargins(14, 12, 14, 12)
        global_latency_layout.setHorizontalSpacing(12)
        global_latency_layout.setVerticalSpacing(8)
        self.global_latency_title = QLabel()
        self.global_latency_title.setObjectName("subsectionTitle")
        global_latency_layout.addWidget(self.global_latency_title, 0, 0, 1, 8)
        self.global_latency_region_labels: dict[str, QLabel] = {}
        self.global_latency_value_labels: dict[str, QLabel] = {}
        for column, (region_key, flag) in enumerate(self.global_latency_regions()):
            region_label = QLabel()
            region_label.setObjectName("latencyRegion")
            region_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value_label = QLabel("--")
            value_label.setObjectName("latencyValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.global_latency_region_labels[region_key] = region_label
            self.global_latency_value_labels[region_key] = value_label
            global_latency_layout.addWidget(region_label, 1, column)
            global_latency_layout.addWidget(value_label, 2, column)
            global_latency_layout.setColumnStretch(column, 1)
        details_layout.addWidget(self.global_latency_panel, reachability_row_start + 3, 0, 1, 4)
        details_layout.setColumnStretch(1, 1)
        details_layout.setColumnStretch(3, 1)

        layout.addWidget(workbench)
        layout.addWidget(self.check_log_frame)
        layout.addWidget(metrics)
        layout.addWidget(details)
        layout.addStretch(1)
        return page

    def add_detail_field(
        self,
        layout: QGridLayout,
        caption_key: str,
        value_key: str,
        row: int,
        col: int,
    ) -> None:
        caption = QLabel()
        caption.setObjectName("mutedLabel")
        value = QLabel("--")
        value.setObjectName("detailValue")
        value.setWordWrap(True)
        self.detail_caption_labels[caption_key] = caption
        self.detail_labels[value_key] = value
        layout.addWidget(caption, row, col)
        layout.addWidget(value, row, col + 1)

    def global_latency_regions(self) -> tuple[tuple[str, str], ...]:
        return (
            ("Shanghai", "\U0001f1e8\U0001f1f3"),
            ("Hong Kong", "\U0001f1ed\U0001f1f0"),
            ("Tokyo", "\U0001f1ef\U0001f1f5"),
            ("Singapore", "\U0001f1f8\U0001f1ec"),
            ("Los Angeles", "\U0001f1fa\U0001f1f8"),
            ("Vancouver", "\U0001f1e8\U0001f1e6"),
            ("Frankfurt", "\U0001f1e9\U0001f1ea"),
            ("Paris", "\U0001f1eb\U0001f1f7"),
        )

    def build_proxy_test_page(self) -> QWidget:
        page, layout = self.build_page_frame()
        self.proxy_test_title = QLabel()
        self.proxy_test_title.setObjectName("pageTitle")
        self.proxy_test_subtitle = QLabel()
        self.proxy_test_subtitle.setObjectName("pageSubtitle")
        self.proxy_test_subtitle.setWordWrap(True)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setHorizontalSpacing(12)
        panel_layout.setVerticalSpacing(12)

        self.proxy_test_type_caption = QLabel()
        self.proxy_test_type = QComboBox()
        self.proxy_test_type.addItems(["HTTP", "HTTPS"])

        self.proxy_test_attempts_caption = QLabel()
        self.proxy_test_attempts = QSpinBox()
        self.proxy_test_attempts.setRange(1, 5)
        self.proxy_test_attempts.setValue(1)

        self.proxy_test_concurrency_caption = QLabel()
        self.proxy_test_concurrency = QSpinBox()
        self.proxy_test_concurrency.setRange(1, 20)
        self.proxy_test_concurrency.setValue(1)

        self.proxy_test_target_caption = QLabel()
        self.proxy_test_target = QComboBox()
        for target in self.targets:
            self.proxy_test_target.addItem(f"{target.name} - {target.url}", target)

        self.proxy_test_input_caption = QLabel()
        self.proxy_test_input = QTextEdit()
        self.proxy_test_input.setMinimumHeight(150)
        self.proxy_test_input.setMaximumHeight(190)
        self.proxy_test_input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.proxy_test_input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.proxy_test_input.setPlaceholderText("ip:port:user:pass")

        self.run_proxy_test_button = QPushButton()
        self.run_proxy_test_button.setObjectName("primaryButton")
        self.run_proxy_test_button.clicked.connect(self.start_proxy_latency_test)
        self.clear_proxy_test_button = QPushButton()
        self.clear_proxy_test_button.setObjectName("secondaryButton")
        self.clear_proxy_test_button.clicked.connect(self.clear_proxy_latency_page)

        self.proxy_test_table = QTableWidget(0, 8)
        self.proxy_test_table.setMinimumHeight(330)
        self.proxy_test_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.proxy_test_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.proxy_test_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.proxy_test_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.proxy_test_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.proxy_test_table.setColumnWidth(6, 132)
        self.proxy_test_table.verticalHeader().setVisible(False)
        self.proxy_test_table.verticalHeader().setDefaultSectionSize(30)
        self.proxy_test_table.setAlternatingRowColors(True)
        self.proxy_test_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.proxy_test_table.itemDoubleClicked.connect(self.copy_table_item_text)

        self.proxy_test_message = QLabel()
        self.proxy_test_message.setObjectName("messageLabel")

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        button_row.addStretch(1)
        button_row.addWidget(self.clear_proxy_test_button)
        button_row.addWidget(self.run_proxy_test_button)

        panel_layout.addWidget(self.proxy_test_type_caption, 0, 0)
        panel_layout.addWidget(self.proxy_test_attempts_caption, 0, 1)
        panel_layout.addWidget(self.proxy_test_concurrency_caption, 0, 2)
        panel_layout.addWidget(self.proxy_test_target_caption, 0, 3)
        panel_layout.addWidget(self.proxy_test_type, 1, 0)
        panel_layout.addWidget(self.proxy_test_attempts, 1, 1)
        panel_layout.addWidget(self.proxy_test_concurrency, 1, 2)
        panel_layout.addWidget(self.proxy_test_target, 1, 3)
        panel_layout.addLayout(button_row, 1, 4)
        panel_layout.addWidget(self.proxy_test_input_caption, 2, 0, 1, 5)
        panel_layout.addWidget(self.proxy_test_input, 3, 0, 1, 5)
        panel_layout.addWidget(self.proxy_test_table, 4, 0, 1, 5)
        panel_layout.addWidget(self.proxy_test_message, 5, 0, 1, 5)
        panel_layout.setColumnStretch(3, 1)
        panel_layout.setRowStretch(4, 1)

        layout.addWidget(self.proxy_test_title)
        layout.addWidget(self.proxy_test_subtitle)
        layout.addWidget(panel, 1)
        return page

    def build_targets_page(self) -> QWidget:
        page, layout = self.build_page_frame()
        self.targets_title = QLabel()
        self.targets_title.setObjectName("pageTitle")
        self.targets_subtitle = QLabel()
        self.targets_subtitle.setObjectName("pageSubtitle")
        self.targets_subtitle.setWordWrap(True)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)

        self.targets_table = QTableWidget(len(self.all_targets), 4)
        self.targets_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.targets_table.verticalHeader().setVisible(False)
        self.targets_table.setAlternatingRowColors(True)
        self.targets_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.targets_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.targets_table.itemSelectionChanged.connect(self.load_selected_target_into_editor)
        self.populate_targets_table()

        editor = QFrame()
        editor.setObjectName("subPanel")
        editor_layout = QGridLayout(editor)
        editor_layout.setContentsMargins(14, 14, 14, 14)
        editor_layout.setHorizontalSpacing(12)
        editor_layout.setVerticalSpacing(10)

        self.site_editor_title = QLabel()
        self.site_editor_title.setObjectName("sectionTitle")
        self.site_name_caption = QLabel()
        self.site_url_caption = QLabel()
        self.site_category_caption = QLabel()
        self.site_enabled_caption = QLabel()
        self.site_name_input = QLineEdit()
        self.site_url_input = QLineEdit()
        self.site_category_input = QComboBox()
        self.site_category_input.addItems(SITE_CATEGORIES)
        self.site_enabled_input = QComboBox()
        self.site_enabled_input.addItem("Yes", True)
        self.site_enabled_input.addItem("No", False)

        self.add_site_button = QPushButton()
        self.update_site_button = QPushButton()
        self.delete_site_button = QPushButton()
        self.save_sites_button = QPushButton()
        for button in (self.add_site_button, self.update_site_button, self.save_sites_button):
            button.setObjectName("primaryButton")
        self.delete_site_button.setObjectName("dangerButton")

        self.add_site_button.clicked.connect(self.add_target_site)
        self.update_site_button.clicked.connect(self.update_target_site)
        self.delete_site_button.clicked.connect(self.delete_target_site)
        self.save_sites_button.clicked.connect(self.save_target_sites)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        button_row.addWidget(self.add_site_button)
        button_row.addWidget(self.update_site_button)
        button_row.addWidget(self.delete_site_button)
        button_row.addStretch(1)
        button_row.addWidget(self.save_sites_button)

        editor_layout.addWidget(self.site_editor_title, 0, 0, 1, 4)
        editor_layout.addWidget(self.site_name_caption, 1, 0)
        editor_layout.addWidget(self.site_name_input, 1, 1)
        editor_layout.addWidget(self.site_url_caption, 1, 2)
        editor_layout.addWidget(self.site_url_input, 1, 3)
        editor_layout.addWidget(self.site_category_caption, 2, 0)
        editor_layout.addWidget(self.site_category_input, 2, 1)
        editor_layout.addWidget(self.site_enabled_caption, 2, 2)
        editor_layout.addWidget(self.site_enabled_input, 2, 3)
        editor_layout.addLayout(button_row, 3, 0, 1, 4)
        editor_layout.setColumnStretch(1, 1)
        editor_layout.setColumnStretch(3, 1)

        panel_layout.addWidget(self.targets_table)
        panel_layout.addWidget(editor)
        self.targets_message = QLabel()
        self.targets_message.setObjectName("messageLabel")
        panel_layout.addWidget(self.targets_message)
        layout.addWidget(self.targets_title)
        layout.addWidget(self.targets_subtitle)
        layout.addWidget(panel, 1)
        return page

    def build_third_party_page(self) -> QWidget:
        page, layout = self.build_page_frame()
        self.third_party_title = QLabel()
        self.third_party_title.setObjectName("pageTitle")
        self.third_party_subtitle = QLabel()
        self.third_party_subtitle.setObjectName("pageSubtitle")
        self.third_party_subtitle.setWordWrap(True)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setHorizontalSpacing(12)
        panel_layout.setVerticalSpacing(12)

        self.third_party_ip_caption = QLabel()
        self.third_party_ip_input = QLineEdit()
        self.third_party_ip_input.setPlaceholderText("1.1.1.1")
        self.use_last_ip_button = QPushButton()
        self.use_last_ip_button.setObjectName("primaryButton")
        self.use_last_ip_button.clicked.connect(self.use_last_exit_ip)
        self.run_third_party_button = QPushButton()
        self.run_third_party_button.setObjectName("primaryButton")
        self.run_third_party_button.clicked.connect(self.start_third_party_checks)
        self.clear_third_party_button = QPushButton()
        self.clear_third_party_button.setObjectName("secondaryButton")
        self.clear_third_party_button.clicked.connect(self.clear_third_party_page)

        self.provider_checks: dict[str, QCheckBox] = {}
        providers_box = QFrame()
        providers_box.setObjectName("subPanel")
        providers_layout = QHBoxLayout(providers_box)
        providers_layout.setContentsMargins(12, 10, 12, 10)
        providers_layout.setSpacing(12)
        for provider in PROVIDERS:
            checkbox = QCheckBox(provider)
            checkbox.setChecked(provider == "proxycheck.io")
            self.provider_checks[provider] = checkbox
            providers_layout.addWidget(checkbox)
        providers_layout.addStretch(1)

        self.third_party_table = QTableWidget(0, 7)
        self.third_party_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.third_party_table.verticalHeader().setVisible(False)
        self.third_party_table.setAlternatingRowColors(True)

        self.third_party_message = QLabel()
        self.third_party_message.setObjectName("messageLabel")

        panel_layout.addWidget(self.third_party_ip_caption, 0, 0)
        panel_layout.addWidget(self.third_party_ip_input, 0, 1)
        panel_layout.addWidget(self.use_last_ip_button, 0, 2)
        panel_layout.addWidget(self.run_third_party_button, 0, 3)
        panel_layout.addWidget(self.clear_third_party_button, 0, 4)
        panel_layout.addWidget(providers_box, 1, 0, 1, 5)
        panel_layout.addWidget(self.third_party_table, 2, 0, 1, 5)
        panel_layout.addWidget(self.third_party_message, 3, 0, 1, 5)
        panel_layout.setColumnStretch(1, 1)
        panel_layout.setRowStretch(2, 1)

        layout.addWidget(self.third_party_title)
        layout.addWidget(self.third_party_subtitle)
        layout.addWidget(panel, 1)
        return page

    def build_settings_page(self) -> QWidget:
        page, layout = self.build_page_frame()
        self.settings_title = QLabel()
        self.settings_title.setObjectName("pageTitle")
        self.settings_subtitle = QLabel()
        self.settings_subtitle.setObjectName("pageSubtitle")
        self.settings_subtitle.setWordWrap(True)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setHorizontalSpacing(16)
        panel_layout.setVerticalSpacing(14)

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(3, 60)
        self.timeout_input.setValue(int(self.settings.timeout_seconds))

        self.settings_language_input = QComboBox()
        self.populate_language_combo(self.settings_language_input)
        self.settings_language_input.setCurrentIndex(max(0, self.settings_language_input.findData(self.language)))
        self.settings_language_input.currentIndexChanged.connect(self.change_language_from_settings)

        self.theme_input = QComboBox()
        self.theme_input.addItem(self.t("theme_tech_dark"), "tech_dark")
        self.theme_input.addItem(self.t("theme_light"), "light")
        self.theme_input.setCurrentIndex(0 if self.theme == "tech_dark" else 1)
        self.theme_input.currentIndexChanged.connect(self.change_theme)

        self.user_agent_input = QLineEdit(self.settings.user_agent)
        self.ipinfo_token_input = QLineEdit(self.settings.ipinfo_token)
        self.proxycheck_key_input = QLineEdit(self.settings.proxycheck_key)
        self.abuseipdb_key_input = QLineEdit(self.settings.abuseipdb_key)
        self.ipqualityscore_key_input = QLineEdit(self.settings.ipqualityscore_key)
        self.config_path_label = QLabel(str(SETTINGS_PATH))
        self.config_path_label.setWordWrap(True)
        self.save_button = QPushButton()
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save_current_settings)
        self.reset_settings_button = QPushButton()
        self.reset_settings_button.setObjectName("secondaryButton")
        self.reset_settings_button.clicked.connect(self.reset_settings)

        self.timeout_caption = QLabel()
        self.settings_language_caption = QLabel()
        self.theme_caption = QLabel()
        self.user_agent_caption = QLabel()
        self.config_path_caption = QLabel()
        self.api_keys_caption = QLabel()
        self.ipinfo_token_caption = QLabel()
        self.proxycheck_key_caption = QLabel()
        self.abuseipdb_key_caption = QLabel()
        self.ipqualityscore_key_caption = QLabel()

        panel_layout.addWidget(self.timeout_caption, 0, 0)
        panel_layout.addWidget(self.timeout_input, 0, 1)
        panel_layout.addWidget(self.settings_language_caption, 1, 0)
        panel_layout.addWidget(self.settings_language_input, 1, 1)
        panel_layout.addWidget(self.theme_caption, 2, 0)
        panel_layout.addWidget(self.theme_input, 2, 1)
        panel_layout.addWidget(self.user_agent_caption, 3, 0)
        panel_layout.addWidget(self.user_agent_input, 3, 1)
        panel_layout.addWidget(self.api_keys_caption, 4, 0, 1, 2)
        panel_layout.addWidget(self.ipinfo_token_caption, 5, 0)
        panel_layout.addWidget(self.ipinfo_token_input, 5, 1)
        panel_layout.addWidget(self.proxycheck_key_caption, 6, 0)
        panel_layout.addWidget(self.proxycheck_key_input, 6, 1)
        panel_layout.addWidget(self.abuseipdb_key_caption, 7, 0)
        panel_layout.addWidget(self.abuseipdb_key_input, 7, 1)
        panel_layout.addWidget(self.ipqualityscore_key_caption, 8, 0)
        panel_layout.addWidget(self.ipqualityscore_key_input, 8, 1)
        panel_layout.addWidget(self.config_path_caption, 9, 0)
        panel_layout.addWidget(self.config_path_label, 9, 1)
        settings_button_row = QHBoxLayout()
        settings_button_row.setSpacing(12)
        settings_button_row.addStretch(1)
        settings_button_row.addWidget(self.reset_settings_button)
        settings_button_row.addWidget(self.save_button)
        panel_layout.addLayout(settings_button_row, 10, 1)
        panel_layout.setColumnStretch(1, 1)

        layout.addWidget(self.settings_title)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def switch_page(self, index: int) -> None:
        if not hasattr(self, "stack"):
            return
        safe_index = min(max(0, index), self.stack.count() - 1)
        self.stack.setCurrentIndex(safe_index)
        for button_index, button in enumerate(self.nav_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == safe_index)
            button.blockSignals(False)

    def change_language(self) -> None:
        new_language = self.language_input.currentData()
        if new_language:
            self.set_language(str(new_language))

    def change_language_from_settings(self) -> None:
        new_language = self.settings_language_input.currentData()
        if new_language:
            self.set_language(str(new_language))

    def change_theme(self) -> None:
        new_theme = self.theme_input.currentData()
        if new_theme in {"tech_dark", "light"}:
            self.theme = str(new_theme)
            self.apply_style()

    def populate_language_combo(self, combo: QComboBox) -> None:
        combo.clear()
        for code, label in LANGUAGE_OPTIONS:
            combo.addItem(label, code)

    def set_language(self, language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            return
        self.language = language
        self.language_input.blockSignals(True)
        self.settings_language_input.blockSignals(True)
        self.language_input.setCurrentIndex(max(0, self.language_input.findData(language)))
        self.settings_language_input.setCurrentIndex(max(0, self.settings_language_input.findData(language)))
        self.language_input.blockSignals(False)
        self.settings_language_input.blockSignals(False)
        self.refresh_language()

    def refresh_language(self) -> None:
        self.hero_title.setText(self.t("hero_title"))
        self.hero_subtitle.setText(self.t("hero_subtitle"))
        self.sidebar_hint.setText(self.t("hero_subtitle"))
        self.support_hint.setText(self.t("support_hint"))
        self.brand_tag.setText(self.t("version_badge"))
        for button in self.nav_buttons:
            button.setText(self.t(str(button.property("textKey"))))

        self.proxy_type_caption.setText(self.t("proxy_type"))
        self.proxy_caption.setText(self.t("proxy"))
        self.mode_caption.setText(self.t("mode"))
        self.target_caption.setText(self.t("target"))
        self.proxy_input.setPlaceholderText(self.t("proxy_placeholder"))
        self.check_button.setText(self.t("run_check"))
        self.clear_check_button.setText(self.t("clear"))
        self.local_chrome_caption.setText(self.t("local_chrome_test"))
        self.local_chrome_no.setText(self.t("no"))
        self.local_chrome_yes.setText(self.t("yes"))

        current_mode = self.mode_input.currentData()
        self.mode_input.blockSignals(True)
        self.mode_input.setItemText(0, self.t("mode_light"))
        self.mode_input.setItemText(1, self.t("mode_browser"))
        self.mode_input.setCurrentIndex(0 if current_mode == "Lightweight HTTP" else 1)
        self.mode_input.blockSignals(False)
        self.update_browser_options_visibility()

        self.status_card.set_title(self.t("status"))
        self.score_card.set_title(self.t("cleanliness"))
        self.latency_card.set_title(self.t("latency"))
        self.risk_card.set_title(self.t("risk"))
        if self.last_result is None:
            self.status_card.set_value(self.t("ready"))
            self.score_card.set_value("--")
            self.latency_card.set_value("--")
            self.risk_card.set_value("--")
        else:
            self.display_result(self.last_result, update_notes=True)

        self.details_title.setText(self.t("details"))
        self.ip_profile_title.setText(self.t("ip_profile"))
        self.reachability_title.setText(self.t("reachability"))
        for key, label in self.detail_caption_labels.items():
            label.setText(self.t(key))
        self.global_latency_title.setText(self.t("global_latency"))
        for region_key, flag in self.global_latency_regions():
            self.global_latency_region_labels[region_key].setText(f"{flag} {self.t(f'ping_region_{region_key}')}")
        self.check_log_title.setText(self.t("check_log"))

        self.proxy_test_title.setText(self.t("proxy_test_title"))
        self.proxy_test_subtitle.setText(self.t("proxy_test_subtitle"))
        self.proxy_test_type_caption.setText(self.t("proxy_type"))
        self.proxy_test_attempts_caption.setText(self.t("proxy_test_attempts"))
        self.proxy_test_concurrency_caption.setText(self.t("proxy_test_concurrency"))
        self.proxy_test_target_caption.setText(self.t("target"))
        self.proxy_test_input_caption.setText(self.t("proxy_test_list"))
        self.proxy_test_input.setPlaceholderText(self.t("proxy_test_placeholder"))
        self.run_proxy_test_button.setText(self.t("run_proxy_test"))
        self.clear_proxy_test_button.setText(self.t("clear"))
        self.populate_proxy_test_headers()

        self.targets_title.setText(self.t("target_sites"))
        self.targets_subtitle.setText(self.t("target_subtitle"))
        self.site_editor_title.setText(self.t("site_editor"))
        self.site_name_caption.setText(self.t("name"))
        self.site_url_caption.setText(self.t("url"))
        self.site_category_caption.setText(self.t("category"))
        self.site_enabled_caption.setText(self.t("enabled"))
        self.site_name_input.setPlaceholderText(self.t("site_name_placeholder"))
        self.site_url_input.setPlaceholderText(self.t("site_url_placeholder"))
        self.add_site_button.setText(self.t("add_site"))
        self.update_site_button.setText(self.t("update_site"))
        self.delete_site_button.setText(self.t("delete_site"))
        self.save_sites_button.setText(self.t("save_sites"))
        self.site_enabled_input.setItemText(0, self.t("yes"))
        self.site_enabled_input.setItemText(1, self.t("no"))
        self.populate_targets_table()
        if hasattr(self, "targets_message") and self.targets_message.text() == "":
            self.targets_message.setText("")

        self.third_party_title.setText(self.t("third_party_title"))
        self.third_party_subtitle.setText(self.t("third_party_subtitle"))
        self.third_party_ip_caption.setText(self.t("ip_address"))
        self.use_last_ip_button.setText(self.t("use_last_exit_ip"))
        self.run_third_party_button.setText(self.t("run_third_party"))
        self.clear_third_party_button.setText(self.t("clear"))
        self.populate_third_party_headers()

        self.settings_title.setText(self.t("settings"))
        self.settings_subtitle.setText("")
        self.timeout_caption.setText(self.t("timeout_seconds"))
        self.settings_language_caption.setText(self.t("language"))
        self.theme_caption.setText(self.t("theme"))
        self.theme_input.blockSignals(True)
        self.theme_input.setItemText(0, self.t("theme_tech_dark"))
        self.theme_input.setItemText(1, self.t("theme_light"))
        self.theme_input.setCurrentIndex(0 if self.theme == "tech_dark" else 1)
        self.theme_input.blockSignals(False)
        self.user_agent_caption.setText(self.t("user_agent"))
        self.api_keys_caption.setText(self.t("api_keys"))
        self.ipinfo_token_caption.setText(self.t("ipinfo_token"))
        self.proxycheck_key_caption.setText(self.t("proxycheck_key"))
        self.abuseipdb_key_caption.setText(self.t("abuseipdb_key"))
        self.ipqualityscore_key_caption.setText(self.t("ipqualityscore_key"))
        self.config_path_caption.setText(self.t("settings_file"))
        self.config_path_label.setText(str(app_root() / "config" / "settings.json"))
        self.save_button.setText(self.t("save_settings"))
        self.reset_settings_button.setText(self.t("reset_settings"))
        self.sidebar_status.setText(self.t("api_online"))
        self.refresh_proxy_latency_translations()

    def populate_third_party_headers(self) -> None:
        if not hasattr(self, "third_party_table"):
            return
        self.third_party_table.setHorizontalHeaderLabels(
            [
                self.t("provider"),
                self.t("provider_status"),
                self.t("provider_risk"),
                self.t("provider_proxy"),
                self.t("provider_country"),
                self.t("provider_asn"),
                self.t("provider_summary"),
            ]
        )

    def populate_proxy_test_headers(self) -> None:
        if not hasattr(self, "proxy_test_table"):
            return
        self.proxy_test_table.setHorizontalHeaderLabels(
            [
                self.t("proxy"),
                self.t("status"),
                self.t("proxy_test_success_rate"),
                self.t("proxy_test_median"),
                self.t("proxy_test_average"),
                self.t("proxy_test_min_max"),
                self.t("proxy_test_http_codes"),
                self.t("proxy_test_error"),
            ]
        )

    def populate_targets_table(self) -> None:
        if not hasattr(self, "targets_table"):
            return
        self.targets_table.setHorizontalHeaderLabels(
            [self.t("name"), self.t("url"), self.t("category"), self.t("enabled")]
        )
        self.targets_table.setRowCount(len(self.all_targets))
        for row, target in enumerate(self.all_targets):
            self.targets_table.setItem(row, 0, QTableWidgetItem(target.name))
            self.targets_table.setItem(row, 1, QTableWidgetItem(target.url))
            self.targets_table.setItem(row, 2, QTableWidgetItem(target.category))
            self.targets_table.setItem(row, 3, QTableWidgetItem(self.t("yes") if target.enabled else self.t("no")))

    def refresh_target_choices(self) -> None:
        self.targets = [target for target in self.all_targets if target.enabled]
        current_name = self.target_input.currentData().name if self.target_input.currentData() else None
        self.target_input.blockSignals(True)
        self.target_input.clear()
        for target in self.targets:
            self.target_input.addItem(f"{target.name} - {target.url}", target)
        if current_name:
            for index, target in enumerate(self.targets):
                if target.name == current_name:
                    self.target_input.setCurrentIndex(index)
                    break
        self.target_input.blockSignals(False)
        if hasattr(self, "proxy_test_target"):
            current_test_name = (
                self.proxy_test_target.currentData().name if self.proxy_test_target.currentData() else None
            )
            self.proxy_test_target.blockSignals(True)
            self.proxy_test_target.clear()
            for target in self.targets:
                self.proxy_test_target.addItem(f"{target.name} - {target.url}", target)
            if current_test_name:
                for index, target in enumerate(self.targets):
                    if target.name == current_test_name:
                        self.proxy_test_target.setCurrentIndex(index)
                        break
            self.proxy_test_target.blockSignals(False)

    def update_browser_options_visibility(self) -> None:
        if hasattr(self, "local_chrome_options"):
            self.local_chrome_options.setVisible(self.mode_input.currentData() == "Browser Simulation")

    def selected_target_row(self) -> int:
        rows = self.targets_table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def load_selected_target_into_editor(self) -> None:
        row = self.selected_target_row()
        if row < 0 or row >= len(self.all_targets):
            return
        target = self.all_targets[row]
        self.site_name_input.setText(target.name)
        self.site_url_input.setText(target.url)
        category = normalize_site_category(target.category)
        self.site_category_input.setCurrentIndex(SITE_CATEGORIES.index(category))
        self.site_enabled_input.setCurrentIndex(0 if target.enabled else 1)

    def editor_target(self) -> TargetSite | None:
        name = self.site_name_input.text().strip()
        url = self.site_url_input.text().strip()
        category = str(self.site_category_input.currentData() or self.site_category_input.currentText())
        if not name or not url or not url.startswith(("http://", "https://")):
            self.show_target_message(self.t("invalid_site"))
            return None
        return TargetSite(
            name=name,
            url=url,
            category=category,
            enabled=bool(self.site_enabled_input.currentData()),
        )

    def add_target_site(self) -> None:
        target = self.editor_target()
        if target is None:
            return
        self.all_targets.append(target)
        self.populate_targets_table()
        self.refresh_target_choices()
        self.targets_table.selectRow(len(self.all_targets) - 1)
        self.show_target_message(self.t("site_added"))

    def update_target_site(self) -> None:
        row = self.selected_target_row()
        if row < 0:
            self.show_target_message(self.t("select_site_first"))
            return
        target = self.editor_target()
        if target is None:
            return
        self.all_targets[row] = target
        self.populate_targets_table()
        self.refresh_target_choices()
        self.targets_table.selectRow(row)
        self.show_target_message(self.t("site_updated"))

    def delete_target_site(self) -> None:
        row = self.selected_target_row()
        if row < 0:
            self.show_target_message(self.t("select_site_first"))
            return
        del self.all_targets[row]
        self.populate_targets_table()
        self.refresh_target_choices()
        self.site_name_input.clear()
        self.site_url_input.clear()
        self.site_category_input.setCurrentIndex(SITE_CATEGORIES.index("Other"))
        self.show_target_message(self.t("site_deleted"))

    def save_target_sites(self) -> None:
        save_targets(self.all_targets)
        self.refresh_target_choices()
        self.show_target_message(self.t("site_saved"))

    def show_target_message(self, message: str) -> None:
        if hasattr(self, "targets_message"):
            self.targets_message.setText(message)

    def use_last_exit_ip(self) -> None:
        if self.last_result and self.last_result.exit_ip != "Unknown":
            self.third_party_ip_input.setText(self.last_result.exit_ip)
            return
        self.third_party_message.setText(self.t("no_exit_ip"))

    def start_third_party_checks(self) -> None:
        providers = [
            provider for provider, checkbox in self.provider_checks.items() if checkbox.isChecked()
        ]
        if not providers:
            self.third_party_message.setText(self.t("select_provider"))
            return
        try:
            ip = validate_ip(self.third_party_ip_input.text())
        except ValueError:
            self.third_party_message.setText(self.t("invalid_ip"))
            return

        self.third_party_message.setText(self.t("third_party_running"))
        self.run_third_party_button.setEnabled(False)
        self.third_party_worker = ThirdPartyWorker(ip, providers, self.current_settings())
        self.third_party_worker.finished.connect(self.display_third_party_results)
        self.third_party_worker.start()

    def display_third_party_results(self, results: list[ThirdPartyResult]) -> None:
        self.run_third_party_button.setEnabled(True)
        self.third_party_table.setRowCount(len(results))
        for row, result in enumerate(results):
            values = [
                result.provider,
                result.status,
                result.risk,
                result.proxy,
                result.country,
                result.asn,
                result.summary,
            ]
            for column, value in enumerate(values):
                self.third_party_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.third_party_message.setText("")

    def start_proxy_latency_test(self) -> None:
        if not self.targets:
            self.proxy_test_message.setText(self.t("no_targets"))
            return
        proxies = unique_proxies(self.proxy_test_input.toPlainText().splitlines())
        if not proxies:
            self.proxy_test_message.setText(self.t("proxy_test_empty"))
            return

        target = self.proxy_test_target.currentData()
        if target is None:
            self.proxy_test_message.setText(self.t("no_targets"))
            return

        self.proxy_test_context = {
            "proxy_type": self.proxy_test_type.currentText(),
            "target": target,
            "settings": self.current_settings(),
            "attempts": self.proxy_test_attempts.value(),
            "concurrency": self.proxy_test_concurrency.value(),
            "running": True,
        }
        self.prepare_proxy_latency_rows(proxies)
        self.proxy_test_message.setText(self.t("proxy_test_running"))
        self.run_proxy_test_button.setEnabled(False)
        self.proxy_latency_worker = ProxyLatencyWorker(
            proxies=proxies,
            proxy_type=str(self.proxy_test_context["proxy_type"]),
            target=target,
            settings=self.proxy_test_context["settings"],
            attempts=int(self.proxy_test_context["attempts"]),
            concurrency=int(self.proxy_test_context["concurrency"]),
        )
        self.proxy_latency_worker.progress.connect(self.update_proxy_latency_row)
        self.proxy_latency_worker.finished.connect(self.finish_proxy_latency_results)
        self.proxy_latency_worker.start()

    def prepare_proxy_latency_rows(self, proxies: list[str]) -> None:
        self.proxy_test_table.setRowCount(len(proxies))
        for row, proxy in enumerate(proxies):
            values = [proxy, self.t("proxy_test_status_Pending"), "--", "--", "--", "--", "--", "--"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, "Pending")
                self.proxy_test_table.setItem(row, column, item)
            self.proxy_test_table.setCellWidget(row, 6, None)

    def update_proxy_latency_row(self, row: int, result: ProxyLatencyResult) -> None:
        if row < 0 or row >= self.proxy_test_table.rowCount():
            return
        self.proxy_test_table.setCellWidget(row, 6, None)
        values = [
            result.proxy,
            self.t(f"proxy_test_status_{result.status_label}"),
            f"{result.success_rate}% ({result.success_count}/{result.attempts})",
            self.format_latency(result.median_ms),
            self.format_latency(result.average_ms),
            self.format_latency_range(result.min_ms, result.max_ms),
            self.latest_http_status(result.status_codes),
            self.compact_errors(result.errors),
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column == 1:
                item.setData(Qt.ItemDataRole.UserRole, result.status_label)
            self.proxy_test_table.setItem(row, column, item)
        if not bool(self.proxy_test_context.get("running")):
            self.add_proxy_retry_button(row)
        self.proxy_test_table.scrollToItem(self.proxy_test_table.item(row, 0))

    def finish_proxy_latency_results(self, results: list[ProxyLatencyResult]) -> None:
        self.run_proxy_test_button.setEnabled(True)
        self.proxy_test_context["running"] = False
        for row in range(self.proxy_test_table.rowCount()):
            self.add_proxy_retry_button(row)
        self.proxy_test_message.setText(self.t("proxy_test_done"))

    def add_proxy_retry_button(self, row: int) -> None:
        status_item = self.proxy_test_table.item(row, 6)
        status_text = str(status_item.data(Qt.ItemDataRole.UserRole) or status_item.text()) if status_item else "--"
        if status_item is None:
            status_item = QTableWidgetItem("")
            self.proxy_test_table.setItem(row, 6, status_item)
        status_item.setData(Qt.ItemDataRole.UserRole, status_text)
        status_item.setText("")
        container = QWidget()
        container.setObjectName("retryCell")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        label = QLabel(status_text)
        label.setObjectName("detailValue")
        label.setMinimumWidth(34)
        button = QPushButton("⟳")
        button.setObjectName("retryIconButton")
        button.setFixedSize(20, 20)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(self.t("proxy_test_retry"))
        button.clicked.connect(lambda checked=False, retry_row=row: self.retry_proxy_latency_row(retry_row))
        layout.addWidget(label, 1)
        layout.addWidget(button)
        self.proxy_test_table.setCellWidget(row, 6, container)

    def retry_proxy_latency_row(self, row: int) -> None:
        if bool(self.proxy_test_context.get("running")):
            return
        item = self.proxy_test_table.item(row, 0)
        if item is None:
            return
        target = self.proxy_test_context.get("target")
        settings = self.proxy_test_context.get("settings")
        if not isinstance(target, TargetSite) or not isinstance(settings, AppSettings):
            target = self.proxy_test_target.currentData()
            settings = self.current_settings()
        if target is None:
            return
        self.proxy_test_table.setCellWidget(row, 6, None)
        status_item = QTableWidgetItem(self.t("proxy_test_status_Running"))
        status_item.setData(Qt.ItemDataRole.UserRole, "Running")
        self.proxy_test_table.setItem(row, 1, status_item)
        worker = ProxyLatencyRetryWorker(
            row=row,
            proxy=item.text(),
            proxy_type=str(self.proxy_test_context.get("proxy_type") or self.proxy_test_type.currentText()),
            target=target,
            settings=settings,
            attempts=int(self.proxy_test_context.get("attempts") or self.proxy_test_attempts.value()),
        )
        self.proxy_latency_retry_workers.append(worker)
        worker.finished.connect(self.finish_proxy_latency_retry)
        worker.start()

    def finish_proxy_latency_retry(self, row: int, result: ProxyLatencyResult) -> None:
        self.update_proxy_latency_row(row, result)
        sender = self.sender()
        if isinstance(sender, ProxyLatencyRetryWorker) and sender in self.proxy_latency_retry_workers:
            self.proxy_latency_retry_workers.remove(sender)

    def refresh_proxy_latency_translations(self) -> None:
        if not hasattr(self, "proxy_test_table"):
            return
        for row in range(self.proxy_test_table.rowCount()):
            status_item = self.proxy_test_table.item(row, 1)
            if status_item is not None:
                status_key = status_item.data(Qt.ItemDataRole.UserRole)
                if status_key:
                    status_item.setText(self.t(f"proxy_test_status_{status_key}"))
            if not bool(self.proxy_test_context.get("running")) and self.proxy_test_table.cellWidget(row, 6):
                self.add_proxy_retry_button(row)

    def copy_table_item_text(self, item: QTableWidgetItem) -> None:
        QApplication.clipboard().setText(item.text())
        self.proxy_test_message.setText(self.t("copied"))

    def clear_proxy_latency_page(self) -> None:
        self.proxy_test_input.clear()
        self.proxy_test_table.setRowCount(0)
        self.proxy_test_message.setText("")
        self.run_proxy_test_button.setEnabled(True)

    def format_latency(self, value: int | None) -> str:
        return "--" if value is None else f"{value} ms"

    def format_latency_range(self, min_value: int | None, max_value: int | None) -> str:
        if min_value is None or max_value is None:
            return "--"
        return f"{min_value}-{max_value} ms"

    def latest_http_status(self, status_codes: list[int]) -> str:
        return str(status_codes[-1]) if status_codes else "--"

    def compact_errors(self, errors: list[str]) -> str:
        if not errors:
            return "--"
        unique_errors = []
        for error in errors:
            if error not in unique_errors:
                unique_errors.append(error)
        return " | ".join(unique_errors)

    def start_check(self) -> None:
        if not self.targets:
            self.show_check_log(self.t("no_targets"))
            return

        target = self.target_input.currentData()
        self.status_card.set_value(self.t("checking"))
        self.check_button.setEnabled(False)
        self.show_check_log(self.t("running"), timeout_ms=0)

        self.worker = CheckWorker(
            proxy=self.proxy_input.text(),
            proxy_type=self.proxy_type.currentText(),
            target=target,
            mode=self.mode_input.currentData(),
            settings=self.current_settings(),
        )
        self.worker.finished.connect(self.display_result)
        self.worker.start()

    def clear_check_page(self) -> None:
        self.last_result = None
        self.proxy_input.clear()
        self.status_card.set_value(self.t("ready"))
        self.score_card.set_value("--")
        self.latency_card.set_value("--")
        self.risk_card.set_value("--")
        for label in self.detail_labels.values():
            label.setText("--")
        for value_label in self.global_latency_value_labels.values():
            value_label.setText("--")
        self.hide_check_log()
        self.check_button.setEnabled(True)

    def display_result(self, result: ProxyCheckResult, update_notes: bool = False) -> None:
        self.last_result = result
        self.check_button.setEnabled(True)
        self.status_card.set_value(self.t(f"status_{result.status_label}"))
        self.score_card.set_value(f"{result.cleanliness_score}/100")
        self.latency_card.set_value("--" if result.latency_ms is None else f"{result.latency_ms} ms")
        self.risk_card.set_value(f"{result.risk_score}/100")
        self.detail_labels["exit_ip"].setText(result.exit_ip)
        self.detail_labels["country_region"].setText(f"{result.country} / {result.region}")
        self.detail_labels["coordinates"].setText(result.coordinates)
        self.detail_labels["asn"].setText(result.asn)
        self.detail_labels["company_info"].setText(result.company_info)
        self.detail_labels["ip_type"].setText(self.format_ip_type(result.ip_type))
        self.detail_labels["ip_native"].setText(self.format_native_status(result.ip_native))
        self.detail_labels["operator_type"].setText(self.format_operator_type(result.operator_type))
        self.detail_labels["human_traffic"].setText(self.format_human_traffic(result.human_traffic))
        self.detail_labels["abuse_level"].setText(self.format_abuse_level(result.abuse_level))
        self.detail_labels["risk_signals"].setText(self.format_risk_signals(result.tags))
        self.detail_labels["target"].setText(result.target_name)
        self.detail_labels["blocked"].setText(self.format_boolean_signal(result.blocked))
        self.detail_labels["captcha"].setText(self.format_boolean_signal(result.captcha))
        self.detail_labels["estimated_bandwidth"].setText(result.estimated_bandwidth)
        self.update_global_latency_display(result.global_latencies)

        notes = [
            f"{self.t('tags')}: {', '.join(result.tags) if result.tags else self.t('none')}",
            *result.notes,
        ]
        self.show_check_log("  ·  ".join(note for note in notes if note), timeout_ms=30000)

    def show_check_log(self, message: str, timeout_ms: int = 30000) -> None:
        self.check_log_title.setText(self.t("check_log"))
        self.check_log_body.setText(message)
        self.check_log_frame.show()
        self.check_log_timer.stop()
        if timeout_ms > 0:
            self.check_log_timer.start(timeout_ms)

    def hide_check_log(self) -> None:
        self.check_log_timer.stop()
        self.check_log_frame.hide()

    def update_global_latency_display(self, latencies: dict[str, str]) -> None:
        for region_key, _flag in self.global_latency_regions():
            latency = latencies.get(region_key, "--")
            if latency == "Timeout":
                latency = self.t("ping_timeout")
            self.global_latency_value_labels[region_key].setText(latency)

    def format_chip(self, text: str, tone: str = "neutral") -> str:
        dot_colors = {
            "ok": "#22c55e",
            "info": "#38bdf8",
            "warn": "#f59e0b",
            "bad": "#ef4444",
            "neutral": "#94a3b8",
        }
        text_color = "#d7ddf4" if self.theme == "tech_dark" else "#334155"
        dot_color = dot_colors.get(tone, dot_colors["neutral"])
        return (
            f"<span style='color:{dot_color}; font-weight:900;'>●</span>"
            f" <span style='color:{text_color}; font-weight:700; white-space:nowrap;'>{text}</span>"
        )

    def format_ip_type(self, value: str) -> str:
        tone = "warn" if value == "Datacenter" else "ok" if value == "Residential-like" else "neutral"
        key = {
            "Datacenter": "ip_type_datacenter",
            "Residential-like": "ip_type_residential_like",
            "Unknown": "ip_type_unknown",
        }.get(value)
        return self.format_chip(self.t(key) if key else value, tone)

    def format_native_status(self, value: str) -> str:
        tone = "ok" if value == "native" else "warn" if value in {"datacenter", "non_native"} else "neutral"
        return self.format_chip(self.t(f"ip_native_{value}"), tone)

    def format_operator_type(self, value: str) -> str:
        tone = "warn" if value in {"hosting", "cdn"} else "ok" if value in {"isp", "residential", "mobile"} else "neutral"
        return self.format_chip(self.t(f"operator_type_{value}"), tone)

    def format_human_traffic(self, value: str) -> str:
        tone = "warn" if value == "crawler_heavy" else "ok" if value == "human_heavy" else "neutral"
        return self.format_chip(self.t(f"human_traffic_{value}"), tone)

    def format_abuse_level(self, value: str) -> str:
        tone = {
            "clean": "ok",
            "low": "warn",
            "elevated": "warn",
            "high": "bad",
            "very_high": "bad",
        }.get(value, "neutral")
        return self.format_chip(self.t(f"abuse_level_{value}"), tone)

    def format_boolean_signal(self, value: bool) -> str:
        return self.format_chip(self.t("yes") if value else self.t("no"), "bad" if value else "ok")

    def format_risk_signals(self, tags: list[str]) -> str:
        if not tags:
            return self.format_chip(self.t("none"), "ok")
        warn_tags = {"datacenter", "proxy", "vpn", "tor", "abuser", "crawler", "blocked", "captcha"}
        return " ".join(self.format_chip(tag, "warn" if tag in warn_tags else "ok") for tag in tags)

    def current_settings(self) -> AppSettings:
        return AppSettings(
            timeout_seconds=float(self.timeout_input.value()),
            language=self.language,
            theme=self.theme,
            local_chrome_test=self.local_chrome_yes.isChecked()
            if self.mode_input.currentData() == "Browser Simulation"
            else False,
            ipinfo_token=self.ipinfo_token_input.text().strip(),
            proxycheck_key=self.proxycheck_key_input.text().strip(),
            abuseipdb_key=self.abuseipdb_key_input.text().strip(),
            ipqualityscore_key=self.ipqualityscore_key_input.text().strip(),
            user_agent=self.user_agent_input.text().strip() or AppSettings().user_agent,
        )

    def save_current_settings(self) -> None:
        self.settings = self.current_settings()
        save_settings(self.settings)
        self.show_check_log(self.t("settings_saved"))

    def clear_third_party_page(self) -> None:
        self.third_party_ip_input.clear()
        self.third_party_table.setRowCount(0)
        self.third_party_message.setText("")
        self.run_third_party_button.setEnabled(True)

    def reset_settings(self) -> None:
        defaults = AppSettings()
        self.timeout_input.setValue(int(defaults.timeout_seconds))
        self.set_language(defaults.language)
        self.theme = defaults.theme
        self.theme_input.setCurrentIndex(0)
        self.user_agent_input.setText(defaults.user_agent)
        self.ipinfo_token_input.clear()
        self.proxycheck_key_input.clear()
        self.abuseipdb_key_input.clear()
        self.ipqualityscore_key_input.clear()
        self.local_chrome_no.setChecked(True)
        self.apply_style()
        self.refresh_language()
        self.show_check_log(self.t("settings_reset"))

    def apply_style(self) -> None:
        if self.theme == "light":
            self.setStyleSheet(
                """
                QMainWindow { background: #f4f7fb; }
                QWidget {
                    font-family: "Inter", "Segoe UI", "Microsoft YaHei UI", Arial;
                    font-size: 13px;
                    color: #142033;
                }
                #sidebar {
                    background: #ffffff;
                    border-right: 1px solid #dbe4ef;
                }
                #brand {
                    border-bottom: 1px solid #e7edf5;
                    padding-bottom: 16px;
                }
                #brandLogo {
                    min-width: 36px;
                    max-width: 36px;
                    min-height: 36px;
                    max-height: 36px;
                    border-radius: 9px;
                    background: #eef6ff;
                }
                #brandName {
                    color: #0f172a;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                }
                #brandTag {
                    color: #64748b;
                    font-size: 10px;
                    font-weight: 700;
                }
                #sidebarHint { color: #64748b; line-height: 1.4; }
                #sidebarStatus {
                    color: #059669;
                    border-top: 1px solid #e7edf5;
                    padding-top: 12px;
                    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                    font-size: 11px;
                }
                #navBox { background: transparent; border: 0; }
                QPushButton#navButton {
                    border: 1px solid transparent;
                    border-radius: 8px;
                    padding: 11px 12px;
                    background: transparent;
                    color: #334155;
                    text-align: left;
                    font-weight: 600;
                }
                QPushButton#navButton:checked {
                    background: #eef2ff;
                    border: 1px solid #a5b4fc;
                    color: #312e81;
                }
                QPushButton#navButton:hover { background: #f1f5f9; color: #0f172a; }
                #content { background: #f4f7fb; }
                #pageTitle { font-size: 26px; font-weight: 700; color: #0f172a; }
                #pageSubtitle, #mutedLabel { color: #64748b; }
                #sectionTitle { font-size: 16px; font-weight: 700; color: #0f172a; }
                #panel, #metricCard {
                    background: #ffffff;
                    border: 1px solid #dbe4ef;
                    border-radius: 14px;
                }
                #panel:hover, #metricCard:hover { border: 1px solid #818cf8; }
                #metricValue {
                    font-size: 23px;
                    font-weight: 700;
                    color: #0f172a;
                    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                }
                QLineEdit, QComboBox, QSpinBox, QTextEdit {
                    border: 1px solid #cbd5e1;
                    border-radius: 8px;
                    padding: 9px 11px;
                    padding-right: 40px;
                    background: #ffffff;
                    color: #0f172a;
                    selection-background-color: #818cf8;
                }
                QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {
                    border: 1px solid #6366f1;
                }
                QComboBox::drop-down {
                    subcontrol-origin: padding;
                    subcontrol-position: top right;
                    width: 34px;
                    border-left: 1px solid #dbe4ef;
                    border-top-right-radius: 8px;
                    border-bottom-right-radius: 8px;
                    background: #f8fafc;
                }
                QComboBox QAbstractItemView {
                    background: #ffffff;
                    color: #0f172a;
                    selection-background-color: #eef2ff;
                    border: 1px solid #cbd5e1;
                }
                QCheckBox, QRadioButton { color: #334155; spacing: 8px; }
                QRadioButton::indicator {
                    width: 16px;
                    height: 16px;
                    border-radius: 8px;
                    border: 2px solid #64748b;
                    background: #ffffff;
                }
                QRadioButton::indicator:checked {
                    border: 2px solid #4f46e5;
                    background: #4f46e5;
                }
                QPushButton#primaryButton {
                    min-width: 86px;
                    border: 0;
                    border-radius: 8px;
                    padding: 11px 18px;
                    background: #6366f1;
                    color: #ffffff;
                    font-weight: 700;
                }
                QPushButton#primaryButton:hover { background: #4f46e5; }
                QPushButton#primaryButton:disabled { background: #94a3b8; color: #e2e8f0; }
                QPushButton#secondaryButton {
                    min-width: 72px;
                    border: 1px solid #cbd5e1;
                    border-radius: 8px;
                    padding: 11px 18px;
                    background: #ffffff;
                    color: #334155;
                    font-weight: 700;
                }
                QPushButton#secondaryButton:hover { background: #f1f5f9; border-color: #818cf8; }
                QPushButton#iconButton {
                    border: 1px solid #cbd5e1;
                    border-radius: 7px;
                    background: #ffffff;
                    color: #64748b;
                    font-size: 16px;
                    font-weight: 700;
                    padding: 0;
                }
                QPushButton#iconButton:hover {
                    background: #f1f5f9;
                    color: #0f172a;
                }
                QPushButton#retryIconButton {
                    min-width: 20px;
                    max-width: 20px;
                    min-height: 20px;
                    max-height: 20px;
                    border: 1px solid #cbd5e1;
                    border-radius: 6px;
                    padding: 0;
                    background: #ffffff;
                    color: #4f46e5;
                    font-size: 12px;
                    font-weight: 800;
                }
                QPushButton#retryIconButton:hover {
                    background: #eef2ff;
                    border-color: #6366f1;
                }
                QPushButton#dangerButton {
                    min-width: 86px;
                    border: 0;
                    border-radius: 8px;
                    padding: 11px 18px;
                    background: #ef4444;
                    color: #ffffff;
                    font-weight: 700;
                }
                QPushButton#dangerButton:hover { background: #dc2626; }
                QTableWidget {
                    border: 1px solid #dbe4ef;
                    border-radius: 10px;
                    background: #ffffff;
                    alternate-background-color: #f8fafc;
                    color: #0f172a;
                    gridline-color: #e2e8f0;
                }
                QHeaderView::section {
                    background: #eef2f7;
                    border: 0;
                    border-bottom: 1px solid #dbe4ef;
                    padding: 9px;
                    font-weight: 700;
                    color: #0f172a;
                }
                QTableWidget::item:selected { background: #eef2ff; color: #312e81; }
                QScrollBar:vertical {
                    background: #eef2f7;
                    width: 12px;
                    margin: 2px;
                    border-radius: 6px;
                }
                QScrollBar::handle:vertical {
                    background: #94a3b8;
                    min-height: 28px;
                    border-radius: 6px;
                }
                QScrollBar::handle:vertical:hover { background: #6366f1; }
                QScrollBar:horizontal {
                    background: #eef2f7;
                    height: 12px;
                    margin: 2px;
                    border-radius: 6px;
                }
                QScrollBar::handle:horizontal {
                    background: #94a3b8;
                    min-width: 28px;
                    border-radius: 6px;
                }
                QScrollBar::handle:horizontal:hover { background: #6366f1; }
                QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
                #subsectionTitle { color: #0f766e; font-size: 14px; font-weight: 700; padding-top: 4px; }
                #subPanel { background: #f8fafc; border: 1px solid #dbe4ef; border-radius: 10px; }
                #toastLog {
                    background: #ffffff;
                    border: 1px solid #cbd5e1;
                    border-left: 3px solid #6366f1;
                    border-radius: 10px;
                }
                #toastTitle {
                    color: #334155;
                    font-weight: 800;
                    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                }
                #toastBody {
                    color: #475569;
                    line-height: 1.35;
                }
                #latencyStrip {
                    background: #f8fafc;
                    border: 1px solid #dbe4ef;
                    border-radius: 10px;
                }
                #latencyRegion {
                    color: #334155;
                    font-weight: 700;
                }
                #latencyValue {
                    color: #0f766e;
                    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                    font-weight: 800;
                }
                #messageLabel { color: #059669; font-weight: 600; padding: 4px 2px; }
                #detailValue {
                    color: #0f172a;
                    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                    font-weight: 650;
                }
                """
            )
            return
        self.setStyleSheet(
            """
            QMainWindow {
                background: #07080d;
            }
            QWidget {
                font-family: "Inter", "Segoe UI", "Microsoft YaHei UI", Arial;
                font-size: 13px;
                color: #f0f3ff;
            }
            #sidebar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #0d1018, stop:1 #07080d);
                border-right: 1px solid #1f2742;
            }
            #brand {
                border-bottom: 1px solid #161c30;
                padding-bottom: 16px;
            }
            #brandLogo {
                min-width: 36px;
                max-width: 36px;
                min-height: 36px;
                max-height: 36px;
                border-radius: 9px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #7c5cff, stop:1 #00e0ff);
                color: #07080d;
                font-weight: 800;
                font-size: 16px;
            }
            #brandName {
                color: #f0f3ff;
                font-size: 15px;
                font-weight: 700;
                font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
            }
            #brandTag {
                color: #6e7896;
                font-size: 10px;
                font-weight: 700;
            }
            #sidebarHint {
                color: #6e7896;
                line-height: 1.4;
            }
            #sidebarStatus {
                color: #2ee6a8;
                border-top: 1px solid #161c30;
                padding-top: 12px;
                font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                font-size: 11px;
            }
            #navBox {
                background: transparent;
                border: 0;
            }
            QPushButton#navButton {
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 11px 12px;
                background: transparent;
                color: #b6bdd8;
                text-align: left;
                font-weight: 600;
            }
            QPushButton#navButton:checked {
                background: rgba(124, 92, 255, 0.18);
                border: 1px solid rgba(124, 92, 255, 0.45);
                color: #f0f3ff;
            }
            QPushButton#navButton:hover {
                background: rgba(124, 92, 255, 0.08);
                color: #f0f3ff;
            }
            #content {
                background: #07080d;
            }
            #pageTitle {
                font-size: 26px;
                font-weight: 700;
                color: #f0f3ff;
            }
            #pageSubtitle, #mutedLabel {
                color: #6e7896;
            }
            #sectionTitle {
                font-size: 16px;
                font-weight: 700;
                color: #f0f3ff;
            }
            #panel, #metricCard {
                background: #0d1018;
                border: 1px solid #1f2742;
                border-radius: 14px;
            }
            #panel:hover, #metricCard:hover {
                border: 1px solid rgba(124, 92, 255, 0.62);
            }
            #metricValue {
                font-size: 23px;
                font-weight: 700;
                color: #f0f3ff;
                font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
            }
            QLineEdit, QComboBox, QSpinBox, QTextEdit {
                border: 1px solid #1f2742;
                border-radius: 8px;
                padding: 9px 11px;
                padding-right: 40px;
                background: #07080d;
                color: #f0f3ff;
                selection-background-color: #7c5cff;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {
                border: 1px solid #7c5cff;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 34px;
                border-left: 1px solid #1f2742;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                background: #0d1018;
            }
            QComboBox QAbstractItemView {
                background: #131826;
                color: #f0f3ff;
                selection-background-color: #7c5cff;
                border: 1px solid #1f2742;
            }
            QCheckBox, QRadioButton {
                color: #b6bdd8;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #4a5273;
                background: #07080d;
            }
            QCheckBox::indicator:checked {
                background: #7c5cff;
                border: 1px solid #00e0ff;
            }
            QRadioButton::indicator {
                width: 17px;
                height: 17px;
                border-radius: 9px;
                border: 2px solid #91a0cc;
                background: #07080d;
            }
            QRadioButton::indicator:hover {
                border: 2px solid #00e0ff;
            }
            QRadioButton::indicator:checked {
                border: 2px solid #00e0ff;
                background: #7c5cff;
            }
            QPushButton#primaryButton {
                min-width: 86px;
                border: 0;
                border-radius: 8px;
                padding: 11px 18px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #7c5cff, stop:1 #5e3eff);
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton#primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8b73ff, stop:1 #00a8ff);
            }
            QPushButton#primaryButton:disabled {
                background: #4a5273;
                color: #b6bdd8;
            }
            QPushButton#secondaryButton {
                min-width: 72px;
                border: 1px solid #2f395c;
                border-radius: 8px;
                padding: 11px 18px;
                background: #111827;
                color: #d7ddf4;
                font-weight: 700;
            }
            QPushButton#secondaryButton:hover {
                border: 1px solid #00e0ff;
                color: #ffffff;
            }
            QPushButton#iconButton {
                border: 1px solid #2f395c;
                border-radius: 7px;
                background: #0d1320;
                color: #8f9abf;
                font-size: 16px;
                font-weight: 700;
                padding: 0;
            }
            QPushButton#iconButton:hover {
                border-color: #00e0ff;
                color: #ffffff;
                background: rgba(0, 224, 255, 0.10);
            }
            QPushButton#retryIconButton {
                min-width: 20px;
                max-width: 20px;
                min-height: 20px;
                max-height: 20px;
                border: 1px solid #2f395c;
                border-radius: 6px;
                padding: 0;
                background: #0d1320;
                color: #00e0ff;
                font-size: 12px;
                font-weight: 800;
            }
            QPushButton#retryIconButton:hover {
                border: 1px solid #00e0ff;
                background: rgba(0, 224, 255, 0.12);
                color: #ffffff;
            }
            QPushButton#dangerButton {
                min-width: 86px;
                border: 0;
                border-radius: 8px;
                padding: 11px 18px;
                background: #ff5e6c;
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton#dangerButton:hover {
                background: #ff4052;
            }
            QTableWidget {
                border: 1px solid #1f2742;
                border-radius: 10px;
                background: #07080d;
                alternate-background-color: #0d1018;
                color: #f0f3ff;
                gridline-color: #1f2742;
            }
            QHeaderView::section {
                background: #131826;
                border: 0;
                border-bottom: 1px solid #1f2742;
                padding: 9px;
                font-weight: 700;
                color: #f0f3ff;
            }
            QTableWidget::item:selected {
                background: rgba(124, 92, 255, 0.28);
                color: #ffffff;
            }
            QScrollBar:vertical {
                background: #0b0f18;
                width: 12px;
                margin: 2px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #37415f;
                min-height: 28px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #7c5cff;
            }
            QScrollBar:horizontal {
                background: #0b0f18;
                height: 12px;
                margin: 2px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background: #37415f;
                min-width: 28px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #7c5cff;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0;
                height: 0;
            }
            #subsectionTitle {
                color: #00e0ff;
                font-size: 14px;
                font-weight: 700;
                padding-top: 4px;
            }
            #subPanel {
                background: #07080d;
                border: 1px solid #1f2742;
                border-radius: 10px;
            }
            #toastLog {
                background: #0d1018;
                border: 1px solid #27314f;
                border-left: 3px solid #7c5cff;
                border-radius: 10px;
            }
            #toastTitle {
                color: #d7ddf4;
                font-weight: 800;
                font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
            }
            #toastBody {
                color: #aeb8dc;
                line-height: 1.35;
            }
            #latencyStrip {
                background: #07080d;
                border: 1px solid #1f2742;
                border-radius: 10px;
            }
            #latencyRegion {
                color: #d7ddf4;
                font-weight: 700;
            }
            #latencyValue {
                color: #2ee6a8;
                font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                font-weight: 800;
            }
            #messageLabel {
                color: #2ee6a8;
                font-weight: 600;
                padding: 4px 2px;
            }
            #detailValue {
                color: #f0f3ff;
                font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
                font-weight: 650;
            }
            """
        )


def run() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
