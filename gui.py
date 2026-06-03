import json
import ctypes
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QParallelAnimationGroup, QProcess, QProcessEnvironment, QPropertyAnimation, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
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


ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
DOWNLOAD_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")
TASK_DIR_LINE = re.compile(r"任务目录[:：]\s*(.+)")


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

        title = StrongBodyLabel("拖拽本地音视频文件到这里", self)
        hint = BodyLabel("也可以在上方输入 URL、BV 号，或点击选择文件。", self)
        hint.setObjectName("mutedLabel")
        layout.addWidget(title)
        layout.addWidget(hint)

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

        mark = QLabel("VS", self)
        mark.setObjectName("titleMark")
        mark.setFixedSize(24, 24)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self.current_job_dir: Path | None = None
        self.log_text = ""
        self.last_error = ""

        self.setWindowTitle("Video Sift")
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
        self.browse_btn = PushButton("选择文件", input_panel)
        self.browse_btn.setIcon(FIF.FOLDER)
        self.browse_btn.clicked.connect(self.browse_file)
        self.browse_btn.setToolTip("从本地选择 mp4、mkv、mp3、wav 等媒体文件。")
        row.addWidget(self.source_input, 1)
        row.addWidget(self.browse_btn)
        input_layout.addLayout(row)
        input_layout.addWidget(DropZone(self.set_source_path, input_panel))
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
        self.history_list = QListWidget(left_panel)
        self.history_list.setMinimumWidth(220)
        self.history_list.itemSelectionChanged.connect(self.show_selected_history)
        left_layout.addWidget(self.history_list)

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
        layout.addWidget(self.section_header("设置", "管理运行环境和默认参数。第一版先展示关键配置入口与依赖状态。"))

        panel = self.panel()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(12)
        panel_layout.addWidget(StrongBodyLabel("配置文件", panel))
        # panel_layout.addWidget(BodyLabel(f"本地配置：{self.project_dir / 'settings.py'}", panel))
        panel_layout.addWidget(BodyLabel("可在 settings.py 中管理 DeepSeek、代理、cookies、默认模型和默认语言。", panel))
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
        return page

    def build_about_page(self) -> QWidget:
        page, layout = self.scroll_page()
        layout.addWidget(self.section_header("关于", "Video Sift 用于将音视频转写并总结成结构化 Markdown。"))

        panel = self.panel()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(10)
        panel_layout.addWidget(SubtitleLabel("Video Sift", panel))
        
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

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(self.project_dir))
        self.process.start(sys.executable, args)

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
        else:
            message = self.last_error or f"进程异常退出，退出码 {exit_code}。"
            self.set_status("处理失败", f"{message} 点击展开详细日志可查看完整输出。", self.progress_bar.value())
            self.append_log(f"\n进程异常退出，退出码 {exit_code}。\n")
        self.load_history()

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
        if not hasattr(self, "history_list"):
            return
        current = self.selected_job_dir()
        self.history_list.clear()
        if not self.outputs_dir.exists():
            return

        jobs = sorted(
            [path for path in self.outputs_dir.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for job_dir in jobs:
            item = QListWidgetItem(self.history_item_title(job_dir))
            item.setSizeHint(QSize(220, 58))
            item.setToolTip(str(job_dir))
            item.setData(Qt.ItemDataRole.UserRole, str(job_dir))
            self.history_list.addItem(item)
            if current and job_dir == current:
                item.setSelected(True)
            elif self.current_job_dir and job_dir == self.current_job_dir:
                item.setSelected(True)

        if self.history_list.count() and not self.history_list.selectedItems():
            self.history_list.setCurrentRow(0)

    def history_item_title(self, job_dir: Path) -> str:
        metadata = self.read_metadata(job_dir)
        title = metadata.get("source_label") or metadata.get("video_info", {}).get("title") or job_dir.name
        status = self.job_status(job_dir)
        model = metadata.get("whisper_model", "-")
        language = metadata.get("language", "-")
        updated_at = datetime.fromtimestamp(job_dir.stat().st_mtime).strftime("%m-%d %H:%M")
        return f"{status}  {title}\nWhisper {model} · {language} · {updated_at}"

    def job_status(self, job_dir: Path) -> str:
        if (job_dir / "summary.md").exists():
            return "完成"
        if (job_dir / "transcript.txt").exists():
            return "已转写"
        if (job_dir / "audio.mp3").exists():
            return "已下载"
        return "未完成"

    def selected_job_dir(self) -> Path | None:
        if not hasattr(self, "history_list"):
            return None
        items = self.history_list.selectedItems()
        if not items:
            return None
        return Path(items[0].data(Qt.ItemDataRole.UserRole))

    def show_selected_history(self):
        job_dir = self.selected_job_dir()
        if not job_dir:
            return

        metadata = self.read_metadata(job_dir)
        title = metadata.get("source_label") or metadata.get("video_info", {}).get("title") or job_dir.name
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

    def dependency_status(self) -> dict[str, bool]:
        return {
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "yt-dlp": shutil.which("yt-dlp") is not None,
            "outputs 目录": self.outputs_dir.exists(),
            "Whisper 缓存目录": (Path.home() / ".cache" / "whisper").exists(),
        }

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
            QTextBrowser#markdownView, QListWidget, QTabWidget::pane {
                background: #20242a;
                border: 1px solid #3a3f48;
                border-radius: 6px;
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
            QListWidget::item {
                padding: 9px 10px;
                border-bottom: 1px solid #33363c;
            }
            QListWidget::item:hover {
                background: #2b3038;
            }
            QListWidget::item:selected {
                background: #303641;
                border-left: 3px solid #27dbe1;
            }
            QSplitter::handle {
                background: #181b20;
            }
            QSplitter::handle:hover {
                background: #27dbe1;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: #1e2126;
                border: none;
                margin: 0;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #4a505c;
                border-radius: 4px;
                min-height: 24px;
                min-width: 24px;
            }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                background: #606978;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0;
                height: 0;
            }
            """
        )


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    window = VideoSiftGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
