import json
import ctypes
import importlib.util
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QEasingCurve, QEvent, QParallelAnimationGroup, QProcess, QProcessEnvironment, QPropertyAnimation, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QAbstractItemView,
    QHeaderView,
    QProgressBar,
    QLineEdit,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    NavigationInterface,
    NavigationDisplayMode,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TextEdit,
    Theme,
    setTheme,
)
from config_utils import DEFAULT_SETTINGS, find_executable, load_settings, save_user_settings


ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
DOWNLOAD_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")
TASK_DIR_LINE = re.compile(r"任务目录[:：]\s*(.+)")
BILIBILI_BVID_PATTERN = re.compile(r"(?i)(?<![0-9a-z])BV[0-9a-z]{10}(?![0-9a-z])")
APP_VERSION = "1.2.0"
APP_DIR = Path(__file__).resolve().parent
APP_ICON_PATH = APP_DIR / "assets" / "app_icon.png"


class DropLineEdit(LineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.setText(urls[0].toLocalFile())
            event.acceptProposedAction()


class DropZone(QFrame):
    def __init__(self, on_path, parent=None):
        super().__init__(parent)
        self.on_path = on_path
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setMinimumHeight(92)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)

        self.loading_ring = IndeterminateProgressRing(self)
        self.loading_ring.setFixedSize(26, 26)
        self.loading_ring.setTextVisible(False)
        self.loading_ring.hide()
        self.title = StrongBodyLabel("拖拽本地音视频文件到这里", self)
        self.hint = BodyLabel("也可以在上方输入 URL、BV 号，或点击选择文件。", self)
        self.hint.setObjectName("mutedLabel")
        self.title.setWordWrap(True)
        self.hint.setWordWrap(True)
        layout.addStretch(1)
        layout.addWidget(self.loading_ring, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def set_preview(self, title: str, hint: str):
        self.loading_ring.hide()
        self.title.show()
        self.hint.show()
        self.title.setText(title)
        self.hint.setText(hint)

    def set_loading(self):
        self.title.hide()
        self.hint.hide()
        self.loading_ring.show()

    def reset_preview(self):
        self.set_preview("拖拽本地音视频文件到这里", "也可以在上方输入 URL、BV 号，或点击选择文件。")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.on_path(urls[0].toLocalFile())
            event.acceptProposedAction()


class WindowTitleBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_position = None
        self.setObjectName("windowTitleBar")
        self.setFixedHeight(42)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(8)

        mark = QLabel(self)
        mark.setObjectName("titleMark")
        mark.setFixedSize(24, 24)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if APP_ICON_PATH.exists():
            mark.setPixmap(QPixmap(str(APP_ICON_PATH)).scaled(
                22,
                22,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            mark.setText("VS")
        layout.addWidget(mark)

        title = QLabel("Video Sift", self)
        title.setObjectName("windowTitle")
        layout.addWidget(title)
        layout.addStretch(1)

        self.minimize_btn = self.window_button("—")
        self.maximize_btn = self.window_button("□")
        self.close_btn = self.window_button("×", "closeButton")
        layout.addWidget(self.minimize_btn)
        layout.addWidget(self.maximize_btn)
        layout.addWidget(self.close_btn)

        self.minimize_btn.clicked.connect(lambda: self.window().showMinimized())
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        self.close_btn.clicked.connect(lambda: self.window().close())

    def window_button(self, text: str, object_name: str = "windowButton") -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setObjectName(object_name)
        button.setFixedSize(36, 30)
        return button

    def toggle_maximize(self):
        window = self.window()
        if window.isMaximized():
            window.showNormal()
            self.maximize_btn.setText("□")
        else:
            window.showMaximized()
            self.maximize_btn.setText("❐")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_position is not None:
            if self.window().isMaximized():
                return
            self.window().move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_position = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize()
            event.accept()


class VideoSiftGUI(QWidget):
    def __init__(self):
        super().__init__()
        setTheme(Theme.DARK)
        self.project_dir = Path(__file__).resolve().parent
        self.outputs_dir = self.project_dir / "outputs"
        self.app_settings = load_settings(self.project_dir)
        if not self.app_settings.USER_SETTINGS_PATH.exists():
            save_user_settings({
                key: getattr(self.app_settings, key)
                for key in DEFAULT_SETTINGS
            }, fallback_dir=self.project_dir)
            self.app_settings = load_settings(self.project_dir)
        self.current_job_dir: Path | None = None
        self.log_text = ""
        self.last_error = ""
        self.source_preview_value = ""
        self.source_preview_process: QProcess | None = None
        self.source_preview_kind = ""
        self.source_preview_info: dict | None = None
        self.source_preview_timer = QTimer(self)
        self.source_preview_timer.setSingleShot(True)
        self.source_preview_timer.setInterval(700)
        self.source_preview_timer.timeout.connect(self.start_source_preview)
        self.source_preview_timeout_timer = QTimer(self)
        self.source_preview_timeout_timer.setSingleShot(True)
        self.source_preview_timeout_timer.timeout.connect(self.source_preview_timed_out)

        self.setWindowTitle(f"Video Sift v{APP_VERSION}")
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setObjectName("appRoot")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.resize(1120, 760)
        self.setMinimumSize(960, 620)
        self.setAcceptDrops(True)

        self.setup_ui()
        self.setup_process()
        self.load_history()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = WindowTitleBar(self)
        body = QWidget(self)
        body.setObjectName("appBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.nav = self.build_nav()
        self.stack = QStackedWidget(self)
        self.stack.setObjectName("contentStack")
        self.stack.addWidget(self.build_task_page())
        self.stack.addWidget(self.build_history_page())
        self.stack.addWidget(self.build_settings_page())
        self.stack.addWidget(self.build_about_page())

        body_layout.addWidget(self.nav)
        body_layout.addWidget(self.stack, 1)
        root.addWidget(self.title_bar)
        root.addWidget(body, 1)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 4, 4)
        grip_row.addStretch(1)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setObjectName("sizeGrip")
        grip_row.addWidget(self.size_grip)
        root.addLayout(grip_row)
        self.apply_styles()

    def showEvent(self, event):
        super().showEvent(event)
        self.enable_dark_title_bar()
        self.hook_nav_menu_button()

    def build_nav(self) -> QFrame:
        nav = QFrame(self)
        nav.setObjectName("sideNav")
        self.nav_expanded_width = 188
        self.nav_collapsed_width = 69
        nav.setFixedWidth(self.nav_expanded_width)
        self.nav_width_animation = None
        self.nav_is_expanded = True
        self.nav_menu_button = None

        layout = QVBoxLayout(nav)
        layout.setContentsMargins(10, 14, 10, 12)
        layout.setSpacing(10)

        self.nav_interface = NavigationInterface(nav, showMenuButton=True, showReturnButton=False, collapsible=True)
        self.nav_interface.setObjectName("navInterface")
        self.nav_interface.setExpandWidth(168)
        self.nav_interface.setMinimumExpandWidth(0)
        self.nav_interface.displayModeChanged.connect(self.animate_nav_width)
        self.nav_routes = {
            "task": 0,
            "history": 1,
            "settings": 2,
            "about": 3,
        }
        self.nav_interface.addItem("task", FIF.PLAY, "处理任务", lambda: self.switch_page(0), tooltip="处理任务")
        self.nav_interface.addItem("history", FIF.HISTORY, "历史任务", lambda: self.switch_page(1), tooltip="历史任务")
        self.nav_interface.addItem("settings", FIF.SETTING, "设置", lambda: self.switch_page(2), tooltip="设置")
        self.nav_interface.addItem(
            "about",
            FIF.INFO,
            "关于",
            lambda: self.switch_page(3),
            position=NavigationItemPosition.BOTTOM,
            tooltip="关于",
        )
        self.nav_interface.setCurrentItem("task")
        self.nav_interface.expand(useAni=False)
        layout.addWidget(self.nav_interface, 1)
        self.hook_nav_menu_button()
        return nav

    def build_task_page(self) -> QWidget:
        page, layout = self.scroll_page()

        header = self.section_header("处理任务", "输入视频来源，选择处理参数，然后开始提取、转写和总结。")
        layout.addWidget(header)

        input_panel = self.panel()
        input_layout = QVBoxLayout(input_panel)
        input_layout.setContentsMargins(16, 14, 16, 14)
        input_layout.setSpacing(10)

        input_layout.addWidget(StrongBodyLabel("输入来源", input_panel))
        row = QHBoxLayout()
        self.source_input = DropLineEdit(input_panel)
        self.source_input.setPlaceholderText("输入网址、BV 号，或选择本地音视频文件")
        self.source_input.setClearButtonEnabled(True)
        self.source_input.setToolTip("支持完整视频链接、Bilibili BV 号，或本地音视频文件路径。")
        self.source_input.textChanged.connect(self.schedule_source_preview)
        self.browse_btn = PushButton("选择文件", input_panel)
        self.browse_btn.setIcon(FIF.FOLDER)
        self.browse_btn.clicked.connect(self.browse_file)
        self.browse_btn.setToolTip("从本地选择 mp4、mkv、mp3、wav 等媒体文件。")
        row.addWidget(self.source_input, 1)
        row.addWidget(self.browse_btn)
        input_layout.addLayout(row)
        self.source_drop_zone = DropZone(self.set_source_path, input_panel)
        input_layout.addWidget(self.source_drop_zone)
        layout.addWidget(input_panel)

        options_panel = self.panel()
        options_layout = QGridLayout(options_panel)
        options_layout.setContentsMargins(16, 14, 16, 14)
        options_layout.setHorizontalSpacing(18)
        options_layout.setVerticalSpacing(10)

        self.lang_cb = ComboBox(options_panel)
        self.lang_cb.addItems(["auto", "zh", "en", "ja"])
        self.lang_cb.setToolTip(
            "选择视频语言以提升转写准确率。\n"
            "auto: 自动检测首段音频语言后转写\n"
            "zh: 中文\n"
            "en: 英文\n"
            "ja: 日文"
        )
        self.model_cb = ComboBox(options_panel)
        self.model_cb.addItems(["tiny", "base", "small", "medium", "large"])
        self.model_cb.setCurrentIndex(1)
        self.model_cb.setToolTip(
            "选择本地 Whisper 转写模型。\n"
            "tiny/base: 速度快，精度较低\n"
            "small: 日常推荐\n"
            "medium: 速度与精度更均衡\n"
            "large: 精度最高，但更慢且占用更多资源"
        )
        self.mode_cb = ComboBox(options_panel)
        self.mode_cb.addItems(["完整处理", "仅下载音频", "仅语音转文字", "仅重新总结"])
        self.mode_cb.setToolTip(
            "选择本次任务执行到哪个阶段。\n"
            "完整处理: 下载、转写并总结\n"
            "仅下载音频: 只生成 audio.mp3\n"
            "仅语音转文字: 生成转写文本后停止\n"
            "仅重新总结: 使用已有 transcript.txt 重新生成 summary.md"
        )
        self.chk_force = CheckBox("强制重新处理", options_panel)
        self.chk_force.setToolTip("忽略已有音频、转写和总结缓存，重新执行所选流程。")

        options_layout.addWidget(StrongBodyLabel("处理参数", options_panel), 0, 0, 1, 4)
        self.add_field(options_layout, "转写语言", self.lang_cb, 1, 0)
        self.add_field(options_layout, "Whisper 模型", self.model_cb, 1, 1)
        self.add_field(options_layout, "处理模式", self.mode_cb, 1, 2)
        options_layout.addWidget(self.chk_force, 2, 0, 1, 2)
        layout.addWidget(options_panel)

        status_panel = self.panel()
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setSpacing(10)

        status_header = QHBoxLayout()
        status_header.addWidget(StrongBodyLabel("任务状态", status_panel))
        status_header.addStretch(1)
        self.start_btn = PrimaryPushButton("开始处理", status_panel)
        self.start_btn.setIcon(FIF.PLAY)
        self.start_btn.clicked.connect(self.start_task)
        self.start_btn.setToolTip("开始执行当前视频处理任务。")
        self.cancel_btn = PushButton("取消", status_panel)
        self.cancel_btn.setIcon(FIF.CANCEL)
        self.cancel_btn.clicked.connect(self.cancel_task)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip("停止正在运行的任务。")
        status_header.addWidget(self.start_btn)
        status_header.addWidget(self.cancel_btn)
        status_layout.addLayout(status_header)

        self.stage_label = SubtitleLabel("等待开始", status_panel)
        self.status_label = BodyLabel("准备好后点击开始处理。", status_panel)
        self.status_label.setObjectName("mutedLabel")
        self.progress_bar = QProgressBar(status_panel)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        status_layout.addWidget(self.stage_label)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)

        log_row = QHBoxLayout()
        self.log_toggle_btn = PushButton("展开详细日志", status_panel)
        self.log_toggle_btn.setIcon(FIF.DOCUMENT)
        self.log_toggle_btn.clicked.connect(self.toggle_log_panel)
        self.log_toggle_btn.setToolTip("显示或隐藏完整命令行输出。")
        self.copy_log_btn = PushButton("复制日志", status_panel)
        self.copy_log_btn.setIcon(FIF.COPY)
        self.copy_log_btn.clicked.connect(self.copy_log)
        self.copy_log_btn.setToolTip("复制当前任务的完整日志。")
        self.clear_log_btn = PushButton("清空日志", status_panel)
        self.clear_log_btn.setIcon(FIF.DELETE)
        self.clear_log_btn.clicked.connect(self.clear_log)
        self.clear_log_btn.setToolTip("清空界面中的日志显示，不会删除任务文件。")
        log_row.addWidget(self.log_toggle_btn)
        log_row.addWidget(self.copy_log_btn)
        log_row.addWidget(self.clear_log_btn)
        log_row.addStretch(1)
        status_layout.addLayout(log_row)

        self.log_output = TextEdit(status_panel)
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(260)
        self.log_output.setVisible(False)
        status_layout.addWidget(self.log_output)
        layout.addWidget(status_panel)

        layout.addStretch(1)
        return page

    def build_history_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("contentPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(self.section_header("历史任务", "查看已处理任务，并在界面中直接阅读总结。"))

        splitter = QSplitter(Qt.Orientation.Horizontal, page)
        splitter.setObjectName("historySplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        left_panel = self.panel(expand_vertical=True)
        left_panel.setMinimumWidth(240)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)
        list_header = QHBoxLayout()
        list_header.addWidget(StrongBodyLabel("历史任务", left_panel))
        list_header.addStretch(1)
        refresh_btn = PushButton("刷新", left_panel)
        refresh_btn.setIcon(FIF.SYNC)
        refresh_btn.clicked.connect(self.load_history)
        refresh_btn.setToolTip("重新扫描 outputs 目录中的任务。")
        list_header.addWidget(refresh_btn)
        left_layout.addLayout(list_header)
        self.history_table = QTableWidget(left_panel)
        self.history_table.setColumnCount(2)
        self.history_table.setHorizontalHeaderLabels(["状态", "标题"])
        self.history_table.setMinimumWidth(280)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setShowGrid(False)
        self.history_table.verticalHeader().hide()
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.history_table.itemSelectionChanged.connect(self.show_selected_history)
        left_layout.addWidget(self.history_table)

        right_panel = self.panel(expand_vertical=True)
        right_panel.setMinimumWidth(520)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)

        self.history_title = SubtitleLabel("选择一个任务", right_panel)
        self.history_title.setMaximumHeight(54)
        self.history_meta = BodyLabel("完成任务后，这里会显示 summary.md、转写文本和日志。", right_panel)
        self.history_meta.setObjectName("mutedLabel")
        self.history_meta.setWordWrap(True)
        self.history_meta.setMaximumHeight(44)
        right_layout.addWidget(self.history_title)
        right_layout.addWidget(self.history_meta)

        action_row = QHBoxLayout()
        self.copy_summary_btn = PushButton("复制总结", right_panel)
        self.copy_summary_btn.setIcon(FIF.COPY)
        self.copy_summary_btn.clicked.connect(self.copy_current_summary)
        self.copy_summary_btn.setToolTip("复制当前任务的 summary.md 内容。")
        self.open_file_btn = PushButton("打开文件", right_panel)
        self.open_file_btn.setIcon(FIF.DOCUMENT)
        self.open_file_btn.clicked.connect(self.open_current_file)
        self.open_file_btn.setToolTip("打开当前选中的结果文件。")
        self.open_dir_btn = PushButton("打开目录", right_panel)
        self.open_dir_btn.setIcon(FIF.FOLDER)
        self.open_dir_btn.clicked.connect(self.open_current_dir)
        self.open_dir_btn.setToolTip("打开当前任务的输出目录。")
        self.rerun_summary_btn = PushButton("重新总结", right_panel)
        self.rerun_summary_btn.setIcon(FIF.SYNC)
        self.rerun_summary_btn.clicked.connect(self.rerun_current_summary)
        self.rerun_summary_btn.setToolTip("把当前历史任务带回处理页，并切换到仅重新总结模式。")
        action_row.addWidget(self.copy_summary_btn)
        action_row.addWidget(self.open_file_btn)
        action_row.addWidget(self.open_dir_btn)
        action_row.addWidget(self.rerun_summary_btn)
        action_row.addStretch(1)
        right_layout.addLayout(action_row)

        self.result_tabs = QTabWidget(right_panel)
        self.result_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.result_tabs.setMinimumHeight(360)
        self.summary_view = self.markdown_view()
        self.transcript_view = self.markdown_view()
        self.timestamp_view = self.markdown_view()
        self.runlog_view = self.markdown_view()
        self.result_tabs.addTab(self.summary_view, "总结")
        self.result_tabs.addTab(self.transcript_view, "转写文本")
        self.result_tabs.addTab(self.timestamp_view, "带时间戳转写")
        self.result_tabs.addTab(self.runlog_view, "日志")
        right_layout.addWidget(self.result_tabs, 1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([280, 780])
        layout.addWidget(splitter, 1)
        return page

    def build_settings_page(self) -> QWidget:
        page, layout = self.scroll_page()
        layout.addWidget(self.section_header("设置", "管理 API、下载参数和默认处理选项。保存后会写入用户本地配置。"))

        panel = self.panel()
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setHorizontalSpacing(18)
        panel_layout.setVerticalSpacing(10)
        panel_layout.addWidget(StrongBodyLabel("用户配置", panel), 0, 0, 1, 2)

        self.config_path_label = BodyLabel(f"配置文件：{self.current_settings_path()}", panel)
        self.config_path_label.setObjectName("mutedLabel")
        panel_layout.addWidget(self.config_path_label, 1, 0, 1, 2)

        self.api_key_input = LineEdit(panel)
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setClearButtonEnabled(True)
        self.api_key_input.setPlaceholderText("DeepSeek API Key")
        self.api_key_input.setToolTip("保存在用户本机配置文件中，不会进入仓库或发行包。")

        self.base_url_input = LineEdit(panel)
        self.base_url_input.setPlaceholderText("https://api.deepseek.com")

        self.llm_model_input = LineEdit(panel)
        self.llm_model_input.setPlaceholderText("deepseek-chat")

        self.settings_whisper_cb = ComboBox(panel)
        self.settings_whisper_cb.addItems(["tiny", "base", "small", "medium", "large"])

        self.settings_language_cb = ComboBox(panel)
        self.settings_language_cb.addItems(["auto", "zh", "en", "ja"])

        self.proxy_input = LineEdit(panel)
        self.proxy_input.setPlaceholderText("例如 http://127.0.0.1:7890，可留空")

        self.cookies_browser_input = LineEdit(panel)
        self.cookies_browser_input.setPlaceholderText("chrome / edge / firefox，可留空")

        self.cookies_file_input = LineEdit(panel)
        self.cookies_file_input.setPlaceholderText("cookies.txt 路径，可留空")

        self.ffmpeg_path_input = LineEdit(panel)
        self.ffmpeg_path_input.setPlaceholderText("ffmpeg.exe 路径，可留空")
        self.ffmpeg_path_input.setToolTip("如果发行版检测不到 PATH 中的 ffmpeg，可在这里填写 ffmpeg.exe 的完整路径。")

        self.ffprobe_path_input = LineEdit(panel)
        self.ffprobe_path_input.setPlaceholderText("ffprobe.exe 路径，可留空")
        self.ffprobe_path_input.setToolTip("通常和 ffmpeg.exe 在同一个 bin 目录。用于本地文件预览和音频格式检测。")

        self.add_field(panel_layout, "DeepSeek API Key", self.api_key_input, 2, 0)
        self.add_field(panel_layout, "DeepSeek Base URL", self.base_url_input, 2, 1)
        self.add_field(panel_layout, "默认 LLM 模型", self.llm_model_input, 3, 0)
        self.add_field(panel_layout, "默认 Whisper 模型", self.settings_whisper_cb, 3, 1)
        self.add_field(panel_layout, "默认转写语言", self.settings_language_cb, 4, 0)
        self.add_field(panel_layout, "yt-dlp 代理", self.proxy_input, 4, 1)
        self.add_field(panel_layout, "浏览器 Cookies", self.cookies_browser_input, 5, 0)
        self.add_field(panel_layout, "Cookies 文件", self.cookies_file_input, 5, 1)
        self.add_field(panel_layout, "ffmpeg 路径", self.ffmpeg_path_input, 6, 0)
        self.add_field(panel_layout, "ffprobe 路径", self.ffprobe_path_input, 6, 1)

        action_row = QHBoxLayout()
        self.save_settings_btn = PrimaryPushButton("保存设置", panel)
        self.save_settings_btn.setIcon(FIF.SAVE)
        self.save_settings_btn.clicked.connect(self.save_settings_form)
        self.open_settings_dir_btn = PushButton("打开配置目录", panel)
        self.open_settings_dir_btn.setIcon(FIF.FOLDER)
        self.open_settings_dir_btn.clicked.connect(self.open_settings_dir)
        self.settings_status_label = BodyLabel("设置会自动保存到用户本机配置目录。", panel)
        self.settings_status_label.setObjectName("mutedLabel")
        action_row.addWidget(self.save_settings_btn)
        action_row.addWidget(self.open_settings_dir_btn)
        action_row.addWidget(self.settings_status_label, 1)
        panel_layout.addLayout(action_row, 7, 0, 1, 2)
        layout.addWidget(panel)

        deps = self.panel()
        deps_layout = QVBoxLayout(deps)
        deps_layout.setContentsMargins(18, 18, 18, 18)
        deps_layout.setSpacing(8)
        deps_layout.addWidget(StrongBodyLabel("依赖状态", deps))
        for name, ok in self.dependency_status().items():
            deps_layout.addWidget(BodyLabel(f"{'已找到' if ok else '未找到'}：{name}", deps))
        layout.addWidget(deps)
        layout.addStretch(1)
        self.load_settings_form()
        return page

    def build_about_page(self) -> QWidget:
        page, layout = self.scroll_page()
        layout.addWidget(self.section_header("关于", "Video Sift 用于将音视频转写并总结成结构化 Markdown。"))

        panel = self.panel()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(10)
        panel_layout.addWidget(SubtitleLabel(f"Video Sift v{APP_VERSION}", panel))
        
        # GitHub Repo Link
        github_link = BodyLabel('<a href="https://github.com/Koishi0425/video-sift">Koishi0425/video-sift: used to summarize video content that is full of empty talk.</a>', panel)
        github_link.setOpenExternalLinks(True)
        panel_layout.addWidget(github_link)
        
        panel_layout.addWidget(BodyLabel("当前 GUI 正在向工作台形态演进：处理任务、阅读结果、管理配置会逐步整合到一个窗口内。", panel))
        panel_layout.addWidget(BodyLabel("发行方向：PyAppify + Python GUI。", panel))
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def setup_process(self):
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.finished.connect(self.process_finished)

    def scroll_page(self) -> tuple[QWidget, QVBoxLayout]:
        wrapper = QWidget(self)
        wrapper.setObjectName("contentPage")
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(wrapper)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(scroll)
        content.setObjectName("contentPage")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(16)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return wrapper, layout

    def panel(self, expand_vertical: bool = False) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("panel")
        vertical = QSizePolicy.Policy.Expanding if expand_vertical else QSizePolicy.Policy.Maximum
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, vertical)
        return frame

    def section_header(self, title: str, subtitle: str) -> QWidget:
        frame = QFrame(self)
        frame.setObjectName("sectionHeader")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        frame.setMaximumHeight(72)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)
        layout.addWidget(SubtitleLabel(title, frame))
        label = BodyLabel(subtitle, frame)
        label.setObjectName("mutedLabel")
        layout.addWidget(label)
        return frame

    def enable_dark_title_bar(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            value = ctypes.c_int(1)
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_uint(attribute),
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
                if result == 0:
                    break
        except Exception:
            return

    def add_field(self, layout: QGridLayout, label: str, widget: QWidget, row: int, col: int):
        box = QVBoxLayout()
        box.setSpacing(6)
        box.addWidget(BodyLabel(label, self))
        box.addWidget(widget)
        layout.addLayout(box, row, col)

    def markdown_view(self) -> QTextBrowser:
        view = QTextBrowser(self)
        view.setOpenExternalLinks(True)
        view.setObjectName("markdownView")
        return view

    def switch_page(self, index: int):
        self.stack.setCurrentIndex(index)
        if hasattr(self, "nav_interface"):
            route_keys = ["task", "history", "settings", "about"]
            self.nav_interface.setCurrentItem(route_keys[index])
        if index == 1:
            self.load_history()

    def hook_nav_menu_button(self):
        if getattr(self, "nav_menu_button", None) is not None:
            return
        panel = getattr(self.nav_interface, "panel", None)
        if panel is None:
            return
        buttons = [
            child for child in panel.findChildren(QWidget)
            if type(child).__name__ == "NavigationToolButton" and child.isVisible()
        ]
        if buttons:
            menu_button = min(buttons, key=lambda child: child.geometry().y())
            self.nav_menu_button = menu_button
            menu_button.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "nav_menu_button", None) and event.type() == QEvent.Type.MouseButtonPress:
            self.prepare_nav_width_toggle()
        return super().eventFilter(watched, event)

    def prepare_nav_width_toggle(self):
        self.nav_is_expanded = not self.nav_is_expanded
        mode = NavigationDisplayMode.EXPAND if self.nav_is_expanded else NavigationDisplayMode.COMPACT
        self.animate_nav_width(mode)

    def animate_nav_width(self, display_mode):
        if not hasattr(self, "nav"):
            return
        target = self.nav_expanded_width if display_mode == NavigationDisplayMode.EXPAND else self.nav_collapsed_width
        self.nav_is_expanded = target == self.nav_expanded_width
        start = self.nav.width()
        if start == target:
            return
        if self.nav_width_animation is not None:
            self.nav_width_animation.stop()

        group = QParallelAnimationGroup(self)
        for prop in (b"minimumWidth", b"maximumWidth"):
            animation = QPropertyAnimation(self.nav, prop, group)
            animation.setStartValue(start)
            animation.setEndValue(target)
            animation.setDuration(180)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(animation)

        self.nav_width_animation = group
        group.finished.connect(lambda: self.nav.setFixedWidth(target))
        group.start()

    def browse_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "选择本地媒体文件",
            "",
            "媒体文件 (*.mp4 *.mkv *.mov *.avi *.mp3 *.wav *.flac);;所有文件 (*.*)",
        )
        if filepath:
            self.set_source_path(filepath)

    def set_source_path(self, path: str):
        self.source_input.setText(path)

    def schedule_source_preview(self, value: str):
        value = value.strip()
        self.source_preview_value = value
        self.source_preview_info = None
        self.source_preview_timer.stop()
        self.cancel_source_preview_process()

        if not value:
            self.source_drop_zone.reset_preview()
            return

        local_path = self.preview_local_path(value)
        if local_path:
            self.source_drop_zone.set_loading()
            self.source_preview_timer.setInterval(120)
            self.source_preview_timer.start()
            return

        normalized = self.normalize_preview_source(value)
        if self.is_preview_url(normalized):
            self.source_drop_zone.set_loading()
            self.source_preview_timer.setInterval(700)
            self.source_preview_timer.start()
            return

        self.source_drop_zone.set_preview("等待有效来源", "请输入完整视频链接、Bilibili BV 号，或拖入本地媒体文件。")

    def start_source_preview(self):
        value = self.source_preview_value
        if not value:
            self.source_drop_zone.reset_preview()
            return

        local_path = self.preview_local_path(value)
        if local_path:
            self.start_local_duration_preview(local_path, value)
            return

        normalized = self.normalize_preview_source(value)
        if self.is_preview_url(normalized):
            self.start_url_info_preview(normalized, value)
            return

        self.source_drop_zone.set_preview("无法识别来源", "请检查链接、BV 号或本地文件路径是否完整。")

    def start_local_duration_preview(self, path: Path, value: str):
        ffprobe = self.resolve_executable("ffprobe", "FFPROBE_PATH")
        if ffprobe is None:
            self.show_local_source_preview(path, duration=None)
            return

        self.source_preview_kind = "file"
        process = QProcess(self)
        process.finished.connect(
            lambda exit_code, exit_status, proc=process, source_value=value: self.source_preview_finished(
                proc, source_value, "file", exit_code, exit_status
            )
        )
        self.source_preview_process = process
        self.source_preview_timeout_timer.start(5000)
        process.setProcessEnvironment(self.tool_process_environment())
        process.start(
            ffprobe,
            [
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
        )

    def start_url_info_preview(self, source: str, value: str):
        if not self.ytdlp_module_available():
            self.source_drop_zone.set_preview("未找到 yt-dlp 模块", "无法预览在线视频信息；安装依赖后可解析标题和时长。")
            return

        self.source_drop_zone.set_loading()
        self.source_preview_kind = "url"
        process = QProcess(self)
        process.finished.connect(
            lambda exit_code, exit_status, proc=process, source_value=value: self.source_preview_finished(
                proc, source_value, "url", exit_code, exit_status
            )
        )
        self.source_preview_process = process
        self.source_preview_timeout_timer.start(20000)
        process.setWorkingDirectory(str(self.project_dir))
        process.setProcessEnvironment(self.tool_process_environment())
        process.start(
            self.silent_python_command(),
            [
                "-m",
                "yt_dlp",
                "--dump-single-json",
                "--no-playlist",
                "--skip-download",
                *self.ytdlp_preview_args(source),
                source,
            ],
        )

    def source_preview_finished(self, process: QProcess, source_value: str, kind: str, exit_code: int, exit_status):
        stdout = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        process.deleteLater()

        if process is not self.source_preview_process or source_value != self.source_preview_value:
            return

        self.source_preview_process = None
        self.source_preview_timeout_timer.stop()

        if kind == "file":
            path = self.preview_local_path(source_value)
            if not path:
                self.source_drop_zone.set_preview("文件不存在", "请重新选择本地音视频文件。")
                return
            duration = self.parse_duration(stdout) if exit_code == 0 else None
            self.show_local_source_preview(path, duration=duration)
            return

        if exit_code != 0 or not stdout:
            detail = self.short_error(stderr) or "暂时无法解析链接信息，仍可直接开始处理。"
            self.source_drop_zone.set_preview("链接信息暂不可用", detail)
            return

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            self.source_drop_zone.set_preview("链接信息暂不可用", "返回信息无法读取，仍可直接开始处理。")
            return

        info = {
            "kind": "url",
            "title": data.get("title"),
            "uploader": data.get("uploader"),
            "duration": data.get("duration"),
            "extractor": data.get("extractor_key") or data.get("extractor"),
            "view_count": data.get("view_count"),
            "webpage_url": data.get("webpage_url"),
        }
        self.source_preview_info = info
        self.show_url_source_preview(info)

    def source_preview_timed_out(self):
        process = self.source_preview_process
        if not process:
            return

        kind = self.source_preview_kind
        self.source_preview_process = None
        process.kill()
        process.deleteLater()
        if kind == "file":
            path = self.preview_local_path(self.source_preview_value)
            if path:
                self.show_local_source_preview(path, duration=None)
            return

        self.source_drop_zone.set_preview("解析耗时较长", "可以先开始处理；任务运行时会再次获取视频标题和信息。")

    def cancel_source_preview_process(self):
        self.source_preview_timeout_timer.stop()
        process = self.source_preview_process
        self.source_preview_process = None
        if process and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
        if process:
            process.deleteLater()

    def show_local_source_preview(self, path: Path, duration: float | None):
        meta = [f"大小 {self.format_file_size(path.stat().st_size)}"]
        if duration:
            meta.append(f"时长 {self.format_duration(duration)}")
            estimate = self.estimate_processing_time(duration)
            if estimate:
                meta.append(f"粗略预计 {estimate}")
        else:
            meta.append("时长暂不可用")
        self.source_preview_info = {
            "kind": "file",
            "path": str(path),
            "duration": duration,
            "size": path.stat().st_size,
        }
        self.source_drop_zone.set_preview(f"已选择：{path.name}", " · ".join(meta))

    def show_url_source_preview(self, info: dict):
        title = str(info.get("title") or "已识别在线视频")
        meta = []
        if info.get("uploader"):
            meta.append(f"作者 {info['uploader']}")
        duration = self.parse_duration(info.get("duration"))
        if duration:
            meta.append(f"时长 {self.format_duration(duration)}")
            estimate = self.estimate_processing_time(duration)
            if estimate:
                meta.append(f"粗略预计 {estimate}")
        if info.get("view_count") is not None:
            meta.append(f"播放 {self.format_count(info['view_count'])}")
        if info.get("extractor"):
            meta.append(str(info["extractor"]))
        self.source_drop_zone.set_preview(title, " · ".join(meta) or "已获取视频基础信息。")

    def preview_local_path(self, value: str) -> Path | None:
        if self.is_preview_url(value):
            return None
        candidate = value.strip().strip('"')
        if not candidate:
            return None
        try:
            path = Path(candidate).expanduser()
            if path.exists() and path.is_file():
                return path.resolve()
        except OSError:
            return None
        return None

    def normalize_preview_source(self, value: str) -> str:
        source = value.strip()
        if self.is_preview_url(source):
            return source
        match = BILIBILI_BVID_PATTERN.search(source)
        if match:
            bvid = match.group(0)
            return f"https://www.bilibili.com/video/BV{bvid[2:]}"
        return source

    def is_preview_url(self, value: str) -> bool:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def ytdlp_preview_args(self, source: str) -> list[str]:
        args = []
        proxy = getattr(self.app_settings, "YTDLP_PROXY", "")
        if proxy and ("youtube.com" in source or "youtu.be" in source):
            args.extend(["--proxy", proxy])

        user_agent = getattr(self.app_settings, "YTDLP_USER_AGENT", "")
        if user_agent:
            args.extend(["--user-agent", user_agent])

        if urlparse(source).netloc.lower().endswith("bilibili.com"):
            headers = getattr(self.app_settings, "YTDLP_BILIBILI_HEADERS", {}) or {}
            for name, value in headers.items():
                if value:
                    args.extend(["--add-headers", f"{name}:{value}"])

        cookies_file = getattr(self.app_settings, "YTDLP_COOKIES_FILE", "")
        if cookies_file:
            args.extend(["--cookies", str(Path(cookies_file).expanduser())])

        cookies_from_browser = getattr(self.app_settings, "YTDLP_COOKIES_FROM_BROWSER", "")
        if cookies_from_browser:
            args.extend(["--cookies-from-browser", cookies_from_browser])
        return args

    def parse_duration(self, value) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return None
        return seconds if seconds > 0 else None

    def format_duration(self, seconds: float) -> str:
        total = int(seconds)
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def format_file_size(self, size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{value:.1f} GB"

    def format_count(self, value) -> str:
        try:
            count = int(value)
        except (TypeError, ValueError):
            return str(value)
        if count >= 10000:
            return f"{count / 10000:.1f} 万"
        return str(count)

    def estimate_processing_time(self, duration: float) -> str:
        mode = self.mode_cb.currentText() if hasattr(self, "mode_cb") else "完整处理"
        model = self.model_cb.currentText() if hasattr(self, "model_cb") else "base"
        duration_minutes = duration / 60
        model_factor = {
            "tiny": 0.25,
            "base": 0.4,
            "small": 0.75,
            "medium": 1.2,
            "large": 1.8,
        }.get(model, 0.5)

        if mode == "仅下载音频":
            estimate = max(1, duration_minutes * 0.08)
        elif mode == "仅重新总结":
            return "取决于已有转写长度"
        elif mode == "仅语音转文字":
            estimate = max(1, duration_minutes * model_factor)
        else:
            estimate = max(1, duration_minutes * model_factor) + max(1, duration_minutes * 0.08)

        low = max(1, int(estimate * 0.8))
        high = max(low + 1, int(estimate * 1.5) + 1)
        if high < 60:
            return f"{low}-{high} 分钟"
        return f"{high // 60} 小时内"

    def short_error(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        return lines[-1][:160]

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.set_source_path(urls[0].toLocalFile())
            event.acceptProposedAction()

    def start_task(self):
        source = self.source_input.text().strip()
        if not source:
            self.set_status("缺少输入", "请输入 URL、BV 号或本地文件路径。", 0)
            return

        if self.process.state() != QProcess.ProcessState.NotRunning:
            return

        args = ["main.py", source, "--language", self.lang_cb.currentText(), "--whisper-model", self.model_cb.currentText()]
        mode = self.mode_cb.currentText()
        if mode == "仅下载音频":
            args.append("--download-only")
        elif mode == "仅语音转文字":
            args.append("--transcript-only")
        elif mode == "仅重新总结":
            args.append("--summary-only")
        if self.chk_force.isChecked():
            args.append("--force")

        self.current_job_dir = None
        self.last_error = ""
        self.log_text = ""
        self.log_output.clear()
        self.append_log(f"执行命令: python {' '.join(args)}\n")
        self.set_status("准备启动", "正在创建任务进程。", 3)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        env = self.tool_process_environment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(self.project_dir))
        self.process.start(self.silent_python_command(), args)

    def cancel_task(self):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.set_status("正在取消", "正在停止当前任务。", self.progress_bar.value())
        self.process.kill()

    def handle_stdout(self):
        data = self.process.readAllStandardOutput()
        text = data.data().decode("utf-8", errors="replace")
        text = ANSI_ESCAPE.sub("", text).replace("\r\n", "\n")
        self.append_log(text)
        self.update_progress_from_text(text)
        self.capture_current_job_dir(text)

    def append_log(self, text: str):
        self.log_text += text
        scrollbar = self.log_output.verticalScrollBar()
        is_at_bottom = scrollbar.value() == scrollbar.maximum()

        cursor = self.log_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        for index, part in enumerate(text.split("\r")):
            if index > 0:
                cursor.movePosition(cursor.MoveOperation.StartOfLine)
                cursor.movePosition(cursor.MoveOperation.EndOfLine, cursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            if part:
                cursor.insertText(part)

        if is_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def update_progress_from_text(self, text: str):
        plain = text.replace("\r", "\n")
        if "正在提取音频" in plain or "Extracting URL" in plain or "Downloading webpage" in plain:
            self.set_status("下载音频", "正在获取视频信息并提取音频。", max(self.progress_bar.value(), 12))
        if "download:" in plain or "[download]" in plain:
            match = DOWNLOAD_PERCENT.search(plain)
            if match:
                percent = min(35, 8 + int(float(match.group(1)) * 0.27))
                self.set_status("下载音频", "正在下载媒体内容。", percent)
        if "音频提取完成" in plain or "音频已准备好" in plain:
            self.set_status("音频已准备", "音频文件已生成。", max(self.progress_bar.value(), 38))
        if "加载语言检测模型" in plain:
            self.set_status("检测语言", "正在加载语言检测模型。", max(self.progress_bar.value(), 42))
        if "正在进行语音转文字" in plain or "加载转写 Whisper 模型" in plain:
            self.set_status("语音转写", "正在加载模型并转写音频。", max(self.progress_bar.value(), 55))
        if "转写已完成" in plain or "语音转文字完成" in plain:
            self.set_status("转写完成", "转写文本已生成。", max(self.progress_bar.value(), 72))
        if "正在总结" in plain or "DeepSeek" in plain:
            self.set_status("AI 总结", "正在生成结构化总结。", max(self.progress_bar.value(), 84))
        if "ERROR:" in plain or "Traceback" in plain or "CalledProcessError" in plain:
            self.last_error = self.extract_error_line(plain)
            self.set_status("任务异常", self.last_error or "任务运行中出现错误，可展开日志查看详情。", self.progress_bar.value())

    def capture_current_job_dir(self, text: str):
        for line in text.splitlines():
            match = TASK_DIR_LINE.search(line)
            if match:
                raw = match.group(1).strip()
                path = Path(raw)
                if not path.is_absolute():
                    path = self.project_dir / path
                self.current_job_dir = path

    def extract_error_line(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            if "ERROR:" in line:
                return line
        for line in reversed(lines):
            if "CalledProcessError" in line or "Traceback" in line:
                return line
        return "任务失败，可展开日志查看详情。"

    def process_finished(self, exit_code, exit_status):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if exit_code == 0:
            self.set_status("处理完成", "任务已完成，可在历史任务中查看结果。", 100)
            self.append_log("\n处理完成。\n")
            self.notify_task_finished(True, "任务已完成", "可以在历史任务中查看总结结果。")
        else:
            message = self.last_error or f"进程异常退出，退出码 {exit_code}。"
            self.set_status("处理失败", f"{message} 点击展开详细日志可查看完整输出。", self.progress_bar.value())
            self.append_log(f"\n进程异常退出，退出码 {exit_code}。\n")
            self.notify_task_finished(False, "任务处理失败", message)
        self.load_history()

    def notify_task_finished(self, success: bool, title: str, message: str):
        QApplication.alert(self, 8000)
        if success:
            InfoBar.success(
                title=title,
                content=message,
                duration=5000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )
        else:
            InfoBar.error(
                title=title,
                content=message,
                duration=7000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )

    def set_status(self, stage: str, message: str, progress: int):
        self.stage_label.setText(stage)
        self.status_label.setText(message)
        self.progress_bar.setValue(max(0, min(100, progress)))

    def toggle_log_panel(self):
        visible = not self.log_output.isVisible()
        self.log_output.setVisible(visible)
        self.log_toggle_btn.setText("收起详细日志" if visible else "展开详细日志")

    def copy_log(self):
        QApplication.clipboard().setText(self.log_text)

    def clear_log(self):
        self.log_text = ""
        self.log_output.clear()

    def load_history(self):
        if not hasattr(self, "history_table"):
            return
        current = self.selected_job_dir()
        self.history_table.setRowCount(0)
        if not self.outputs_dir.exists():
            return

        jobs = sorted(
            [path for path in self.outputs_dir.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for job_dir in jobs:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)

            status_item = QTableWidgetItem(self.history_status_text(job_dir))
            title_item = QTableWidgetItem(self.history_display_title(job_dir))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(self.history_status_color(job_dir))
            status_item.setToolTip(self.history_meta_text(job_dir))
            title_item.setToolTip(f"{self.history_meta_text(job_dir)}\n{job_dir}")
            title_item.setData(Qt.ItemDataRole.UserRole, str(job_dir))

            self.history_table.setItem(row, 0, status_item)
            self.history_table.setItem(row, 1, title_item)
            self.history_table.setRowHeight(row, 46)
            if current and job_dir == current:
                self.history_table.selectRow(row)
            elif self.current_job_dir and job_dir == self.current_job_dir:
                self.history_table.selectRow(row)

        if self.history_table.rowCount() and not self.history_table.selectedItems():
            self.history_table.selectRow(0)

    def history_display_title(self, job_dir: Path) -> str:
        metadata = self.read_metadata(job_dir)
        return metadata.get("video_info", {}).get("title") or metadata.get("source_label") or job_dir.name

    def history_meta_text(self, job_dir: Path) -> str:
        metadata = self.read_metadata(job_dir)
        model = metadata.get("whisper_model", "-")
        language = metadata.get("language", "-")
        updated_at = datetime.fromtimestamp(job_dir.stat().st_mtime).strftime("%m-%d %H:%M")
        return f"{self.job_status(job_dir)} · Whisper {model} · {language} · {updated_at}"

    def history_status_text(self, job_dir: Path) -> str:
        return f"● {self.job_status(job_dir)}"

    def history_status_color(self, job_dir: Path):
        status = self.job_status(job_dir)
        if status == "完成":
            return QColor("#35d07f")
        if status == "已转写":
            return QColor("#49c6f5")
        if status == "已下载":
            return QColor("#f3bd45")
        return QColor("#ff6b6b")

    def job_status(self, job_dir: Path) -> str:
        if (job_dir / "summary.md").exists():
            return "完成"
        if (job_dir / "transcript.txt").exists():
            return "已转写"
        if (job_dir / "audio.mp3").exists():
            return "已下载"
        return "未完成"

    def selected_job_dir(self) -> Path | None:
        if not hasattr(self, "history_table"):
            return None
        row = self.history_table.currentRow()
        if row < 0:
            return None
        item = self.history_table.item(row, 1)
        if item is None:
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def show_selected_history(self):
        job_dir = self.selected_job_dir()
        if not job_dir:
            return

        metadata = self.read_metadata(job_dir)
        title = self.history_display_title(job_dir)
        model = metadata.get("whisper_model", "-")
        language = metadata.get("language", "-")
        self.history_title.setText(title)
        self.history_meta.setText(f"{self.job_status(job_dir)} · Whisper {model} · 语言 {language} · {job_dir}")

        self.set_markdown_file(self.summary_view, job_dir / "summary.md", "暂无总结。")
        self.set_plain_file(self.transcript_view, job_dir / "transcript.txt", "暂无转写文本。")
        self.set_markdown_file(self.timestamp_view, job_dir / "transcript_with_timestamps.md", "暂无带时间戳转写。")
        self.set_plain_file(self.runlog_view, job_dir / "run.log", "暂无日志。")

    def read_metadata(self, job_dir: Path) -> dict:
        path = job_dir / "metadata.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def set_markdown_file(self, view: QTextBrowser, path: Path, empty: str):
        if path.exists():
            view.setMarkdown(path.read_text(encoding="utf-8", errors="replace"))
        else:
            view.setPlainText(empty)

    def set_plain_file(self, view: QTextBrowser, path: Path, empty: str):
        if path.exists():
            view.setPlainText(path.read_text(encoding="utf-8", errors="replace"))
        else:
            view.setPlainText(empty)

    def copy_current_summary(self):
        job_dir = self.selected_job_dir()
        if not job_dir:
            return
        path = job_dir / "summary.md"
        if path.exists():
            QApplication.clipboard().setText(path.read_text(encoding="utf-8", errors="replace"))

    def open_current_file(self):
        job_dir = self.selected_job_dir()
        if not job_dir:
            return
        paths = [
            job_dir / "summary.md",
            job_dir / "transcript.txt",
            job_dir / "transcript_with_timestamps.md",
            job_dir / "run.log",
        ]
        path = paths[self.result_tabs.currentIndex()]
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_current_dir(self):
        job_dir = self.selected_job_dir()
        if job_dir and job_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(job_dir)))

    def rerun_current_summary(self):
        job_dir = self.selected_job_dir()
        if not job_dir:
            return
        metadata = self.read_metadata(job_dir)
        source = metadata.get("source")
        if not source:
            return
        self.source_input.setText(source)
        self.mode_cb.setCurrentText("仅重新总结")
        self.switch_page(0)

    def load_settings_form(self):
        settings = self.app_settings
        self.config_path_label.setText(f"配置文件：{self.current_settings_path()}")
        self.api_key_input.setText(settings.DEEPSEEK_API_KEY)
        self.base_url_input.setText(settings.DEEPSEEK_BASE_URL)
        self.llm_model_input.setText(settings.DEFAULT_LLM_MODEL)
        self.settings_whisper_cb.setCurrentText(settings.DEFAULT_WHISPER_MODEL)
        self.settings_language_cb.setCurrentText(settings.DEFAULT_TRANSCRIBE_LANGUAGE)
        self.proxy_input.setText(settings.YTDLP_PROXY)
        self.cookies_browser_input.setText(settings.YTDLP_COOKIES_FROM_BROWSER)
        self.cookies_file_input.setText(settings.YTDLP_COOKIES_FILE)
        self.ffmpeg_path_input.setText(getattr(settings, "FFMPEG_PATH", ""))
        self.ffprobe_path_input.setText(getattr(settings, "FFPROBE_PATH", ""))
        self.model_cb.setCurrentText(settings.DEFAULT_WHISPER_MODEL)
        self.lang_cb.setCurrentText(settings.DEFAULT_TRANSCRIBE_LANGUAGE)

    def save_settings_form(self):
        values = {
            "DEEPSEEK_API_KEY": self.api_key_input.text().strip(),
            "DEEPSEEK_BASE_URL": self.base_url_input.text().strip() or "https://api.deepseek.com",
            "DEFAULT_LLM_MODEL": self.llm_model_input.text().strip() or "deepseek-chat",
            "DEFAULT_DETECT_WHISPER_MODEL": "tiny",
            "DEFAULT_WHISPER_MODEL": self.settings_whisper_cb.currentText(),
            "DEFAULT_TRANSCRIBE_LANGUAGE": self.settings_language_cb.currentText(),
            "DEFAULT_WORKDIR": "outputs",
            "MAX_CHUNK_MINUTES": 25,
            "SUMMARY_CHUNK_CHARS": 12000,
            "YTDLP_PROXY": self.proxy_input.text().strip(),
            "YTDLP_COOKIES_FROM_BROWSER": self.cookies_browser_input.text().strip(),
            "YTDLP_COOKIES_FILE": self.cookies_file_input.text().strip(),
            "FFMPEG_PATH": self.ffmpeg_path_input.text().strip(),
            "FFPROBE_PATH": self.ffprobe_path_input.text().strip(),
        }
        path = save_user_settings(values, fallback_dir=self.project_dir)
        self.app_settings = load_settings(self.project_dir)
        self.load_settings_form()
        self.settings_status_label.setText(f"已保存：{path}")

    def open_settings_dir(self):
        path = self.current_settings_path().parent
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def current_settings_path(self) -> Path:
        return self.app_settings.CONFIG_SOURCE_PATH or self.app_settings.USER_SETTINGS_PATH

    def dependency_status(self) -> dict[str, bool]:
        return {
            "ffmpeg": self.resolve_executable("ffmpeg", "FFMPEG_PATH") is not None,
            "ffprobe": self.resolve_executable("ffprobe", "FFPROBE_PATH") is not None,
            "yt-dlp Python 模块": self.ytdlp_module_available(),
            "outputs 目录（首次运行会自动创建）": True,
            "Whisper 缓存目录": (Path.home() / ".cache" / "whisper").exists(),
        }

    def resolve_executable(self, command: str, setting_name: str | None = None) -> str | None:
        configured = getattr(self.app_settings, setting_name, "") if setting_name else ""
        return find_executable(command, configured)

    def python_command(self) -> str:
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            console_python = executable.with_name("python.exe")
            if console_python.exists():
                return str(console_python)
        return sys.executable

    def silent_python_command(self) -> str:
        executable = Path(sys.executable)
        if executable.name.lower() == "python.exe":
            windowless_python = executable.with_name("pythonw.exe")
            if windowless_python.exists():
                return str(windowless_python)
        return sys.executable

    def tool_process_environment(self) -> QProcessEnvironment:
        environment = QProcessEnvironment.systemEnvironment()
        tool_dirs = []
        for command, setting_name in (("ffmpeg", "FFMPEG_PATH"), ("ffprobe", "FFPROBE_PATH")):
            path = self.resolve_executable(command, setting_name)
            if path:
                tool_dirs.append(str(Path(path).parent))

        if tool_dirs:
            current_path = environment.value("PATH")
            environment.insert("PATH", ";".join([*dict.fromkeys(tool_dirs), current_path]))

        return environment

    def ytdlp_module_available(self) -> bool:
        return importlib.util.find_spec("yt_dlp") is not None

    def apply_styles(self):
        self.setStyleSheet(
            """
            QWidget {
                color: #f2f4f7;
                font-size: 14px;
            }
            QWidget#appRoot {
                background: #181b20;
            }
            QWidget#appBody, QStackedWidget#contentStack, QWidget#contentPage {
                background: #181b20;
            }
            QFrame#windowTitleBar {
                background: #11151a;
                border-bottom: 1px solid #252930;
            }
            QLabel#titleMark {
                background: #26313a;
                color: #27dbe1;
                border: 1px solid #3b4a55;
                border-radius: 5px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#windowTitle {
                color: #f2f4f7;
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }
            QToolButton#windowButton, QToolButton#closeButton {
                background: transparent;
                color: #d8dde6;
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }
            QToolButton#windowButton:hover {
                background: #282d35;
            }
            QToolButton#closeButton:hover {
                background: #c42b1c;
                color: white;
            }
            QSizeGrip#sizeGrip {
                width: 14px;
                height: 14px;
                background: transparent;
            }
            QLabel, BodyLabel, StrongBodyLabel, SubtitleLabel {
                background: transparent;
            }
            QScrollArea {
                border: none;
                background: #1b1d21;
            }
            QFrame#sideNav {
                background: #11151a;
                border-right: 1px solid #272c34;
            }
            QFrame#brandBlock {
                background: #171b21;
                border: 1px solid #242a32;
                border-radius: 6px;
            }
            QLabel#mutedLabel, BodyLabel#mutedLabel {
                color: #a9adb6;
            }
            QFrame#panel {
                background: #25282e;
                border: 1px solid #363c45;
                border-radius: 8px;
            }
            QFrame#dropZone {
                background: #20242a;
                border: 1px dashed #4f5d6b;
                border-radius: 8px;
            }
            PushButton, PrimaryPushButton {
                border-radius: 6px;
                padding: 7px 12px;
                min-height: 24px;
            }
            NavigationInterface#navInterface {
                background: transparent;
            }
            PushButton#navButton {
                text-align: left;
                padding: 9px 12px;
                border-radius: 6px;
                color: #e9edf3;
                background: transparent;
            }
            PushButton#navButton:hover {
                background: #242830;
            }
            PushButton#navButton:checked {
                background: #2c3038;
                border-left: 3px solid #27dbe1;
            }
            LineEdit, ComboBox, TextEdit {
                background: #20242a;
                border: 1px solid #3c424d;
                border-radius: 6px;
                color: #f2f4f7;
            }
            QProgressBar {
                background: #1d2127;
                border: 1px solid #383b42;
                border-radius: 4px;
                min-height: 10px;
                max-height: 10px;
            }
            QProgressBar::chunk {
                background: #27dbe1;
                border-radius: 4px;
            }
            QTextBrowser#markdownView, QTableWidget, QTabWidget::pane {
                background: #20242a;
                border: 1px solid #3a3f48;
                border-radius: 6px;
            }
            QTableWidget {
                gridline-color: transparent;
                selection-background-color: #303641;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background: #25282e;
                color: #cbd1da;
                border: none;
                border-bottom: 1px solid #3a3f48;
                padding: 7px 8px;
                font-weight: 600;
            }
            QTabWidget::pane {
                top: -1px;
            }
            QTabBar::tab {
                background: #25282e;
                color: #cbd1da;
                border: 1px solid #3a3f48;
                border-bottom: none;
                padding: 8px 14px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #20242a;
                color: #ffffff;
                border-top: 2px solid #27dbe1;
            }
            QTabBar::tab:hover {
                background: #30343c;
            }
            QTableWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #33363c;
            }
            QTableWidget::item:selected {
                background: #303641;
            }
            QSplitter::handle {
                background: #181b20;
            }
            QSplitter::handle:hover {
                background: #27dbe1;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: transparent;
                border: none;
                margin: 0;
            }
            QScrollBar:vertical {
                width: 10px;
            }
            QScrollBar:horizontal {
                height: 10px;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #3d444e;
                border: 2px solid transparent;
                border-radius: 5px;
                background-clip: padding;
                min-height: 30px;
                min-width: 30px;
            }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                background: #5a6573;
            }
            QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {
                background: #6b7686;
            }
            QScrollBar::add-line, QScrollBar::sub-line,
            QScrollBar::add-page, QScrollBar::sub-page {
                background: transparent;
                border: none;
                width: 0;
                height: 0;
            }
            """
        )


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    window = VideoSiftGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
