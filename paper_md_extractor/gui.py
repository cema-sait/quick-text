from __future__ import annotations

from importlib import resources
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .converter import ConversionOptions, ConversionResult, convert_pdf_to_markdown


def app_icon() -> QIcon:
    source = resources.files("paper_md_extractor").joinpath("assets", "app-icon.svg")
    with resources.as_file(source) as icon_path:
        return QIcon(str(icon_path))


class ConversionWorker(QObject):
    progress = Signal(str, int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, pdf_path: str, output_dir: str, options: ConversionOptions) -> None:
        super().__init__()
        self.pdf_path = pdf_path
        self.output_dir = output_dir
        self.options = options

    @Slot()
    def run(self) -> None:
        try:
            result = convert_pdf_to_markdown(
                self.pdf_path,
                self.output_dir,
                self.options,
                lambda message, page, total: self.progress.emit(message, page, total),
            )
        except Exception as exc:
            details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.failed.emit(details)
        else:
            self.finished.emit(result)


class PaperMarkdownWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quick Text")
        self.setWindowIcon(app_icon())
        self.resize(1120, 700)
        self.setMinimumSize(880, 560)

        self.result_path: Path | None = None
        self.worker_thread: QThread | None = None
        self.worker: ConversionWorker | None = None

        self.pdf_input = QLineEdit()
        self.pdf_input.setPlaceholderText("Choose an academic paper PDF")
        self.output_input = QLineEdit()
        self.output_input.setPlaceholderText("Choose an output folder")

        self.images_check = QCheckBox("Images")
        self.images_check.setChecked(True)
        self.tables_check = QCheckBox("Tables")
        self.tables_check.setChecked(True)
        self.ocr_check = QCheckBox("OCR scanned pages")
        self.equations_check = QCheckBox("Equations as images")
        self.equations_check.setChecked(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel("Choose a PDF to begin.")
        self.status.setWordWrap(True)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Conversion status will appear here.")
        self.log.setMinimumHeight(130)

        self.convert_button = QPushButton("Convert")
        self.open_markdown_button = QPushButton("Open Markdown")
        self.open_button = QPushButton("Open Output Folder")
        self.open_button.setEnabled(False)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.preview.setReadOnly(True)
        self.preview.setHtml("<h2>Markdown Preview</h2><p>Convert a PDF or open a Markdown file to preview it here.</p>")

        self._build_ui()
        self._build_menu()
        self._connect_signals()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        header = QLabel("PDF to Markdown")
        header.setObjectName("Header")
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(header)

        form_frame = QFrame()
        form_frame.setObjectName("Panel")
        form_layout = QFormLayout(form_frame)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(12)

        pdf_row = QHBoxLayout()
        pdf_row.addWidget(self.pdf_input, 1)
        pdf_browse = QPushButton("Browse")
        pdf_browse.clicked.connect(self.choose_pdf)
        pdf_row.addWidget(pdf_browse)
        form_layout.addRow("PDF", pdf_row)

        output_row = QHBoxLayout()
        output_row.addWidget(self.output_input, 1)
        output_browse = QPushButton("Browse")
        output_browse.clicked.connect(self.choose_output)
        output_row.addWidget(output_browse)
        form_layout.addRow("Output", output_row)

        options = QHBoxLayout()
        options.addWidget(self.images_check)
        options.addWidget(self.tables_check)
        options.addWidget(self.equations_check)
        options.addWidget(self.ocr_check)
        options.addStretch(1)
        form_layout.addRow("Extraction", options)
        root.addWidget(form_frame)

        progress_grid = QGridLayout()
        progress_grid.setHorizontalSpacing(12)
        progress_grid.setVerticalSpacing(8)
        progress_grid.addWidget(self.progress, 0, 0, 1, 2)
        progress_grid.addWidget(self.status, 1, 0, 1, 2)
        root.addLayout(progress_grid)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_label = QLabel("Conversion Log")
        log_label.setObjectName("SectionLabel")
        log_layout.addWidget(log_label)
        log_layout.addWidget(self.log)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_label = QLabel("Markdown Preview")
        preview_label.setObjectName("SectionLabel")
        preview_layout.addWidget(preview_label)
        preview_layout.addWidget(self.preview)

        content_splitter.addWidget(log_panel)
        content_splitter.addWidget(preview_panel)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 2)
        root.addWidget(content_splitter, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.open_markdown_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.convert_button)
        root.addLayout(actions)

        self.setStyleSheet(
            """
            QMainWindow { background: #f7f7f5; }
            QLabel#Header { font-size: 28px; font-weight: 700; color: #1f2933; }
            QLabel#SectionLabel { font-weight: 700; color: #344054; }
            QFrame#Panel { background: #ffffff; border: 1px solid #d8d8d2; border-radius: 8px; }
            QLineEdit, QTextEdit, QTextBrowser { background: #ffffff; border: 1px solid #c8c8c0; border-radius: 6px; padding: 8px; }
            QPushButton { padding: 8px 14px; border-radius: 6px; border: 1px solid #9aa0a6; background: #ffffff; }
            QPushButton:hover { background: #f0f4f8; }
            QPushButton:pressed { background: #e2e8f0; }
            QPushButton:disabled { color: #8d949e; background: #ececea; }
            QProgressBar { border: 1px solid #c8c8c0; border-radius: 6px; text-align: center; background: #ffffff; height: 18px; }
            QProgressBar::chunk { background: #2f6f73; border-radius: 5px; }
            """
        )

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        choose_action = QAction("Choose PDF...", self)
        choose_action.triggered.connect(self.choose_pdf)
        file_menu.addAction(choose_action)

        output_action = QAction("Choose Output Folder...", self)
        output_action.triggered.connect(self.choose_output)
        file_menu.addAction(output_action)

        markdown_action = QAction("Open Markdown Preview...", self)
        markdown_action.triggered.connect(self.choose_markdown_preview)
        file_menu.addAction(markdown_action)

        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _connect_signals(self) -> None:
        self.convert_button.clicked.connect(self.convert)
        self.open_markdown_button.clicked.connect(self.choose_markdown_preview)
        self.open_button.clicked.connect(self.open_output_folder)

    @Slot()
    def choose_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose PDF", "", "PDF files (*.pdf);;All files (*)")
        if not path:
            return
        self.pdf_input.setText(path)
        if not self.output_input.text().strip():
            self.output_input.setText(str(Path(path).with_suffix("")))

    @Slot()
    def choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if path:
            self.output_input.setText(path)

    @Slot()
    def choose_markdown_preview(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Markdown", "", "Markdown files (*.md *.markdown);;All files (*)")
        if path:
            self.load_markdown_preview(Path(path))

    def load_markdown_preview(self, markdown_path: Path) -> None:
        try:
            markdown = markdown_path.read_text(encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, "Preview failed", str(exc))
            return

        self.preview.document().setBaseUrl(QUrl.fromLocalFile(str(markdown_path.parent) + "/"))
        self.preview.setMarkdown(markdown)
        self._append_log(f"Previewing {markdown_path.name}")

    @Slot()
    def convert(self) -> None:
        pdf = self.pdf_input.text().strip()
        output = self.output_input.text().strip()
        if not pdf or not Path(pdf).exists():
            QMessageBox.critical(self, "Missing PDF", "Choose an existing PDF file.")
            return
        if not output:
            QMessageBox.critical(self, "Missing output", "Choose an output folder.")
            return

        self.result_path = None
        self.progress.setValue(0)
        self.log.clear()
        self.preview.setHtml("<h2>Markdown Preview</h2><p>Preview will update when conversion finishes.</p>")
        self.open_button.setEnabled(False)
        self.convert_button.setEnabled(False)
        self.status.setText("Starting conversion...")
        self._append_log("Starting conversion")

        options = ConversionOptions(
            extract_images=self.images_check.isChecked(),
            extract_tables=self.tables_check.isChecked(),
            ocr_scanned_pages=self.ocr_check.isChecked(),
            extract_equations_as_images=self.equations_check.isChecked(),
        )

        self.worker_thread = QThread(self)
        self.worker = ConversionWorker(pdf, output, options)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self._clear_worker_refs)
        self.worker_thread.start()

    @Slot(str, int, int)
    def on_progress(self, message: str, page: int, total: int) -> None:
        self.status.setText(message)
        self.progress.setValue(round((page - 1) / max(total, 1) * 100))
        self._append_log(message)

    @Slot(object)
    def on_finished(self, result: ConversionResult) -> None:
        self.result_path = result.markdown_path
        self.progress.setValue(100)
        self.convert_button.setEnabled(True)
        self.open_button.setEnabled(True)
        message = (
            f"Done in {result.elapsed_seconds:.1f}s: {result.page_count} pages, "
            f"{result.table_count} tables, {result.image_count} images."
        )
        if result.equation_image_count:
            message += f" {result.equation_image_count} equations captured."
        if result.ocr_page_count:
            message += f" OCR used on {result.ocr_page_count} pages."
        self.status.setText(message)
        self._append_log(message)
        self.load_markdown_preview(result.markdown_path)

    @Slot(str)
    def on_failed(self, error: str) -> None:
        self.convert_button.setEnabled(True)
        self.status.setText("Conversion failed.")
        self._append_log(f"Conversion failed: {error}")
        QMessageBox.critical(self, "Conversion failed", error)

    @Slot()
    def _clear_worker_refs(self) -> None:
        self.worker = None
        self.worker_thread = None

    @Slot()
    def open_output_folder(self) -> None:
        if not self.result_path:
            return
        QDesktopServices.openUrl(QUrl(self.result_path.parent.as_uri()))

    def _append_log(self, message: str) -> None:
        self.log.append(message)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Conversion running", "Wait for the current conversion to finish before closing.")
            event.ignore()
            return
        event.accept()


def main() -> None:  # pragma: no cover
    app = QApplication(sys.argv)
    app.setApplicationName("Quick Text")
    app.setWindowIcon(app_icon())
    window = PaperMarkdownWindow()
    window.show()
    sys.exit(app.exec())
