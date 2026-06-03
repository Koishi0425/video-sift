import sys
import os
from pathlib import Path
from PySide6.QtCore import Qt, QProcess, QProcessEnvironment
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog
from qfluentwidgets import (
    setTheme, Theme, LineEdit, PrimaryPushButton, PushButton, 
    TextEdit, ComboBox, CheckBox, SubtitleLabel, BodyLabel
)

class DropLineEdit(LineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
    
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.setText(path)

class VideoSiftGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Sift - 视频内容提取与总结")
        self.resize(800, 600)
        self.setAcceptDrops(True)
        
        self.setup_ui()
        self.setup_process()
        
    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(16)
        
        # 标题
        title = SubtitleLabel("Video Sift 视频处理", self)
        main_layout.addWidget(title)
        
        # 输入区（包含拖放功能）
        input_layout = QHBoxLayout()
        self.source_input = DropLineEdit(self)
        self.source_input.setPlaceholderText("请输入网址、BV号，或拖拽本地音视频文件到此处...")
        self.source_input.setClearButtonEnabled(True)
        self.source_input.setToolTip("输入哔哩哔哩链接、BV号，或者直接拖放本地媒体文件到当前输入框。")
        
        self.browse_btn = PushButton("浏览文件", self)
        self.browse_btn.clicked.connect(self.browse_file)
        
        input_layout.addWidget(self.source_input, 1)
        input_layout.addWidget(self.browse_btn)
        main_layout.addLayout(input_layout)
        
        # 选项区
        options_layout = QHBoxLayout()
        options_layout.setSpacing(24)
        
        # 语言选择
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(BodyLabel("转写语言:", self))
        self.lang_cb = ComboBox(self)
        self.lang_cb.addItems(["auto", "zh", "en", "ja"])
        self.lang_cb.setToolTip("选择视频的语言以提升转写准确率：\n- auto: 自动检测前段音频语言后进行转写\n- zh: 中文\n- en: 英文\n- ja: 日文")
        lang_layout.addWidget(self.lang_cb)
        options_layout.addLayout(lang_layout)
        
        # 模型选择
        model_layout = QHBoxLayout()
        model_layout.addWidget(BodyLabel("Whisper 模型:", self))
        self.model_cb = ComboBox(self)
        self.model_cb.addItems(["tiny", "base", "small", "medium", "large"])
        self.model_cb.setCurrentIndex(1) # 默认base
        self.model_cb.setToolTip("选择本地 Whisper 转写模型：\n- tiny/base: 速度极快但精度较低\n- small: 日常推荐，精度稍好\n- medium: 速度与精度的良好平衡\n- large: 精度最高但速度最慢，需要更好硬件\n注意第一次使用时会自动下载所选模型文件，下载完成后会缓存到本地供后续使用。")
        model_layout.addWidget(self.model_cb)
        options_layout.addLayout(model_layout)
        
        # 处理模式选择
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(BodyLabel("处理模式:", self))
        self.mode_cb = ComboBox(self)
        self.mode_cb.addItems(["完整处理", "仅下载/提取音频", "仅语音转文字", "仅重新总结"])
        self.mode_cb.setToolTip(
            "选择执行哪些步骤：\n"
            "- 完整处理: 包含下载、提取、转写和AI总结\n"
            "- 仅下载/提取音频: 提取出mp3音频后即停止\n"
            "- 仅语音转文字: 生成转写文稿后即停止\n"
            "- 仅重新总结: 跳过下载和转写，使用已有文稿重新归纳"
        )
        mode_layout.addWidget(self.mode_cb)
        options_layout.addLayout(mode_layout)
        
        options_layout.addStretch(1)
        main_layout.addLayout(options_layout)
        
        # 开关选项
        checkbox_layout = QHBoxLayout()
        self.chk_force = CheckBox("强制重新处理", self)
        self.chk_force.setToolTip("无视任务缓存（如已经存在的音频或文稿），强制重新执行所有需要处理的流程。")
        
        checkbox_layout.addWidget(self.chk_force)
        checkbox_layout.addStretch(1)
        main_layout.addLayout(checkbox_layout)
        
        # 启动按钮
        self.start_btn = PrimaryPushButton("开始处理", self)
        self.start_btn.clicked.connect(self.start_task)
        main_layout.addWidget(self.start_btn)
        
        # 日志输出区
        self.log_output = TextEdit(self)
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("运行日志将在这里显示...")
        main_layout.addWidget(self.log_output, 1)

    def browse_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择本地媒体文件", "", "所有文件 (*.*);;音视频文件 (*.mp4 *.mkv *.mp3 *.wav *.flac)"
        )
        if filepath:
            self.source_input.setText(filepath)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.source_input.setText(path)

    def setup_process(self):
        self.process = QProcess(self)
        # 合并输出流（方便按发生顺序查看）
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.finished.connect(self.process_finished)

    def start_task(self):
        source = self.source_input.text().strip()
        if not source:
            self.log_output.append("错误：请输入处理源（URL、BV号或文件路径）")
            return
            
        args = ["main.py", source]
        
        args.extend(["--language", self.lang_cb.currentText()])
        args.extend(["--whisper-model", self.model_cb.currentText()])
        
        mode = self.mode_cb.currentText()
        if mode == "仅下载/提取音频":
            args.append("--download-only")
        elif mode == "仅语音转文字":
            args.append("--transcript-only")
        elif mode == "仅重新总结":
            args.append("--summary-only")
            
        if self.chk_force.isChecked():
            args.append("--force")
            
        self.log_output.clear()
        self.log_output.append(f"执行命令: python {' '.join(args)}\n")
        self.start_btn.setEnabled(False)
        self.start_btn.setText("处理中...")
        
        # 传递环境变量，确保Python输出不被缓冲，并强制使用UTF-8编码
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.process.setProcessEnvironment(env)
        
        # 假设当前工作目录为 main.py 所在的文件夹
        main_py_path = Path(__file__).resolve().parent / "main.py"
        self.process.setWorkingDirectory(str(main_py_path.parent))
        
        self.process.start(sys.executable, args)

    def handle_stdout(self):
        data = self.process.readAllStandardOutput()
        # 解码并处理click库的ANSI颜色字符
        text = data.data().decode("utf-8", errors="replace")
        
        # 简单的去除 ANSI 转义字符
        import re
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        text = ansi_escape.sub('', text)
        
        # 将 Windows 的 \r\n 统一替换为 \n，避免普通的换行被按 \r 截断导致正文被错误擦除而变成空行
        text = text.replace('\r\n', '\n')
        
        # 滚动到底部
        scrollbar = self.log_output.verticalScrollBar()
        is_at_bottom = scrollbar.value() == scrollbar.maximum()
        
        # 处理 \r (回车符)，在GUI的 TextEdit 中实现刷新当前行的效果
        cursor = self.log_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        
        parts = text.split('\r')
        for i, part in enumerate(parts):
            if i > 0:
                # 遇到 \r 时，清除当前行的内容
                cursor.movePosition(cursor.MoveOperation.StartOfLine)
                cursor.movePosition(cursor.MoveOperation.EndOfLine, cursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            if part:
                cursor.insertText(part)
        
        if is_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def process_finished(self, exit_code, exit_status):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("开始处理")
        if exit_code == 0:
            self.log_output.append("\n✅ 处理完成！")
        else:
            self.log_output.append(f"\n❌ 进程异常退出 (退出码 {exit_code})")

def main():
    # 启用高DPI缩放
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    # set theme
    setTheme(Theme.AUTO)

    app = QApplication(sys.argv)
    window = VideoSiftGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
