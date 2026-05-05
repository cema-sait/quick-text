from __future__ import annotations

from pathlib import Path
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from paper_md_extractor import gui
from paper_md_extractor.converter import ConversionOptions, ConversionResult
from paper_md_extractor.gui import ConversionWorker, PaperMarkdownWindow, app_icon


@pytest.fixture(scope='session')
def app() -> QApplication:
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


@pytest.fixture
def window(app: QApplication) -> PaperMarkdownWindow:
    win = PaperMarkdownWindow()
    yield win
    win.close()


def test_window_initial_state_and_log(window: PaperMarkdownWindow) -> None:
    assert window.windowTitle() == 'Quick Text'
    assert not app_icon().isNull()
    assert not window.windowIcon().isNull()
    assert window.convert_button.isEnabled()
    assert not window.open_button.isEnabled()
    assert window.images_check.isChecked()
    assert window.tables_check.isChecked()
    assert not window.ocr_check.isChecked()
    window._append_log('hello')
    assert 'hello' in window.log.toPlainText()


def test_choose_pdf_sets_pdf_and_default_output(monkeypatch: pytest.MonkeyPatch, window: PaperMarkdownWindow, tmp_path: Path) -> None:
    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'%PDF-1.7')
    monkeypatch.setattr(gui.QFileDialog, 'getOpenFileName', lambda *args, **kwargs: (str(pdf), 'PDF files (*.pdf)'))

    window.choose_pdf()

    assert window.pdf_input.text() == str(pdf)
    assert window.output_input.text() == str(pdf.with_suffix(''))


def test_choose_pdf_keeps_existing_output_and_cancel_is_noop(monkeypatch: pytest.MonkeyPatch, window: PaperMarkdownWindow, tmp_path: Path) -> None:
    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'%PDF-1.7')
    window.output_input.setText(str(tmp_path / 'custom'))
    monkeypatch.setattr(gui.QFileDialog, 'getOpenFileName', lambda *args, **kwargs: (str(pdf), 'PDF files (*.pdf)'))
    window.choose_pdf()
    assert window.output_input.text() == str(tmp_path / 'custom')

    monkeypatch.setattr(gui.QFileDialog, 'getOpenFileName', lambda *args, **kwargs: ('', ''))
    window.pdf_input.clear()
    window.choose_pdf()
    assert window.pdf_input.text() == ''


def test_choose_output_sets_folder_and_cancel_is_noop(monkeypatch: pytest.MonkeyPatch, window: PaperMarkdownWindow, tmp_path: Path) -> None:
    monkeypatch.setattr(gui.QFileDialog, 'getExistingDirectory', lambda *args, **kwargs: str(tmp_path))
    window.choose_output()
    assert window.output_input.text() == str(tmp_path)

    monkeypatch.setattr(gui.QFileDialog, 'getExistingDirectory', lambda *args, **kwargs: '')
    window.choose_output()
    assert window.output_input.text() == str(tmp_path)


def test_convert_validates_inputs(monkeypatch: pytest.MonkeyPatch, window: PaperMarkdownWindow, tmp_path: Path) -> None:
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(gui.QMessageBox, 'critical', lambda parent, title, text: messages.append((title, text)))

    window.convert()
    assert messages[-1] == ('Missing PDF', 'Choose an existing PDF file.')

    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'%PDF-1.7')
    window.pdf_input.setText(str(pdf))
    window.output_input.clear()
    window.convert()
    assert messages[-1] == ('Missing output', 'Choose an output folder.')


def test_convert_starts_worker_with_selected_options(monkeypatch: pytest.MonkeyPatch, window: PaperMarkdownWindow, tmp_path: Path) -> None:
    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'%PDF-1.7')
    output = tmp_path / 'out'
    window.pdf_input.setText(str(pdf))
    window.output_input.setText(str(output))
    window.images_check.setChecked(False)
    window.tables_check.setChecked(True)
    window.ocr_check.setChecked(True)

    captured: dict[str, object] = {}

    class FakeThread:
        def __init__(self, parent=None):
            self.started = FakeSignal()
            self.finished = FakeSignal()
        def start(self):
            captured['started'] = True
        def quit(self):
            captured['quit'] = True
        def deleteLater(self):
            captured['thread_deleted'] = True
        def isRunning(self):
            return False

    class FakeSignal:
        def __init__(self):
            self.callbacks = []
        def connect(self, callback):
            self.callbacks.append(callback)

    class FakeWorker:
        def __init__(self, pdf_path, output_dir, options):
            captured['pdf'] = pdf_path
            captured['output'] = output_dir
            captured['options'] = options
            self.progress = FakeSignal()
            self.finished = FakeSignal()
            self.failed = FakeSignal()
        def moveToThread(self, thread):
            captured['thread'] = thread
        def run(self):
            captured['run_connected'] = True
        def deleteLater(self):
            captured['worker_deleted'] = True

    monkeypatch.setattr(gui, 'QThread', FakeThread)
    monkeypatch.setattr(gui, 'ConversionWorker', FakeWorker)

    window.convert()

    assert captured['pdf'] == str(pdf)
    assert captured['output'] == str(output)
    assert captured['started'] is True
    options = captured['options']
    assert isinstance(options, ConversionOptions)
    assert not options.extract_images
    assert options.extract_tables
    assert options.ocr_scanned_pages
    assert not window.convert_button.isEnabled()
    assert 'Starting conversion' in window.log.toPlainText()


def test_progress_finished_failed_and_clear_worker_refs(monkeypatch: pytest.MonkeyPatch, window: PaperMarkdownWindow, tmp_path: Path) -> None:
    window.convert_button.setEnabled(False)
    result = ConversionResult(tmp_path / 'paper.md', tmp_path / 'images', 3, 1.25, 2, 1, 1)

    window.on_progress('Reading page 2 of 3', 2, 3)
    assert window.progress.value() == 33
    assert 'Reading page 2 of 3' in window.log.toPlainText()

    window.on_finished(result)
    assert window.result_path == result.markdown_path
    assert window.progress.value() == 100
    assert window.convert_button.isEnabled()
    assert window.open_button.isEnabled()
    assert 'OCR used on 1 pages' in window.status.text()

    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(gui.QMessageBox, 'critical', lambda parent, title, text: errors.append((title, text)))
    window.convert_button.setEnabled(False)
    window.on_failed('boom')
    assert window.convert_button.isEnabled()
    assert errors == [('Conversion failed', 'boom')]

    window.worker = object()
    window.worker_thread = object()
    window._clear_worker_refs()
    assert window.worker is None
    assert window.worker_thread is None


def test_open_output_folder_uses_desktop_services(monkeypatch: pytest.MonkeyPatch, window: PaperMarkdownWindow, tmp_path: Path) -> None:
    opened: list[str] = []
    monkeypatch.setattr(gui.QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()))

    window.open_output_folder()
    assert opened == []

    path = tmp_path / 'paper.md'
    window.result_path = path
    window.open_output_folder()
    assert opened == [tmp_path.as_uri()]


def test_close_event_ignores_when_worker_running_and_accepts_otherwise(monkeypatch: pytest.MonkeyPatch, window: PaperMarkdownWindow) -> None:
    info: list[str] = []
    monkeypatch.setattr(gui.QMessageBox, 'information', lambda parent, title, text: info.append(text))

    class Thread:
        def __init__(self, running: bool):
            self.running = running
        def isRunning(self):
            return self.running

    class Event:
        def __init__(self):
            self.ignored = False
            self.accepted = False
        def ignore(self):
            self.ignored = True
        def accept(self):
            self.accepted = True

    event = Event()
    window.worker_thread = Thread(True)
    window.closeEvent(event)
    assert event.ignored
    assert info

    event = Event()
    window.worker_thread = Thread(False)
    window.closeEvent(event)
    assert event.accepted


def test_conversion_worker_emits_finished_and_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = ConversionResult(tmp_path / 'out.md', tmp_path / 'images', 1, 0.1, 0, 0, 0)
    monkeypatch.setattr(gui, 'convert_pdf_to_markdown', lambda pdf, out, options, progress: result)
    worker = ConversionWorker('in.pdf', 'out', ConversionOptions())
    finished: list[object] = []
    failed: list[str] = []
    worker.finished.connect(lambda payload: finished.append(payload))
    worker.failed.connect(lambda error: failed.append(error))
    worker.run()
    assert finished == [result]
    assert failed == []

    def fail(pdf, out, options, progress):
        raise ValueError('bad pdf')

    monkeypatch.setattr(gui, 'convert_pdf_to_markdown', fail)
    worker = ConversionWorker('in.pdf', 'out', ConversionOptions())
    worker.failed.connect(lambda error: failed.append(error))
    worker.run()
    assert any('ValueError: bad pdf' in error for error in failed)
