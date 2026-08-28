from packaging.utils import canonicalize_name
from qgis.PyQt.QtCore import QProcess, Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .pip_progress import PipProgressParser, requirement_name


class PipInstallProgressDialog(QDialog):
    """Run one pip resolver process and expose progress for every dependency."""

    COMPLETE_STATUSES = {"Already installed", "Completed"}

    def __init__(self, args, requirements, description, log_callback, parent=None):
        super().__init__(parent)
        self.args = [str(arg) for arg in args]
        self.description = description
        self.log_callback = log_callback
        self.parser = PipProgressParser(requirements)
        self.rows = {}
        self.output_buffer = ""
        self.full_output = ""
        self.cancelled = False
        self.exit_code = None

        self.setWindowTitle("QPIP - Python dependency installation")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(720, 430)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(description.capitalize()))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Dependency", "Status", "Progress"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        layout.addWidget(QLabel("Overall progress"))
        self.overall_progress = QProgressBar()
        layout.addWidget(self.overall_progress)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        self.details.setMaximumBlockCount(2000)
        layout.addWidget(self.details)

        buttons = QHBoxLayout()
        self.details_button = QPushButton("Show details")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        buttons.addWidget(self.details_button)
        buttons.addStretch()
        self.abort_button = QPushButton("Abort")
        self.abort_button.clicked.connect(self._abort)
        buttons.addWidget(self.abort_button)
        layout.addLayout(buttons)

        for requirement in requirements:
            self._ensure_row(requirement_name(requirement), "Pending")

        self.process = QProcess(self)
        channel_mode = getattr(QProcess, "MergedChannels", None)
        if channel_mode is None:
            channel_mode = QProcess.ProcessChannelMode.MergedChannels
        self.process.setProcessChannelMode(channel_mode)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)

    def execute(self):
        self.process.start(self.args[0], self.args[1:])
        self.exec()
        return self.exit_code, self.cancelled, self.full_output

    def _ensure_row(self, package, status="Pending"):
        key = canonicalize_name(package)
        if key in self.rows:
            return self.rows[key]

        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(package))
        self.table.setItem(row, 1, QTableWidgetItem(status))
        progress = QProgressBar()
        progress.setRange(0, 0 if status in {"Resolving", "Installing"} else 100)
        progress.setValue(0)
        progress.setTextVisible(status not in {"Resolving", "Installing"})
        self.table.setCellWidget(row, 2, progress)
        self.rows[key] = (row, progress)
        self.table.resizeColumnToContents(0)
        self._update_overall()
        return row, progress

    def _apply_update(self, update):
        if update.package is None:
            return

        row, progress = self._ensure_row(update.package, update.status)
        self.table.item(row, 1).setText(update.status)

        if update.total:
            progress.setRange(0, update.total)
            progress.setValue(min(update.current or 0, update.total))
            progress.setTextVisible(True)
        elif update.status in {"Resolving", "Downloading", "Installing", "Using cache"}:
            progress.setRange(0, 0)
            progress.setTextVisible(False)

        if update.status in self.COMPLETE_STATUSES:
            progress.setRange(0, 1)
            progress.setValue(1)
            progress.setTextVisible(True)

        self._update_overall()

    def _update_overall(self):
        total = len(self.rows)
        completed = 0
        for row, _progress in self.rows.values():
            if self.table.item(row, 1).text() in self.COMPLETE_STATUSES:
                completed += 1
        self.overall_progress.setRange(0, max(total, 1))
        self.overall_progress.setValue(completed)
        self.overall_progress.setFormat(
            f"{completed} of {total} dependencies completed"
        )

    def _read_output(self):
        chunk = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if not chunk:
            return
        self.full_output += chunk
        self.output_buffer += chunk.replace("\r", "\n")
        lines = self.output_buffer.split("\n")
        self.output_buffer = lines.pop()
        for line in lines:
            self._consume_line(line)

    def _consume_line(self, line):
        line = line.strip()
        if not line:
            return
        self.details.appendPlainText(line)
        self.log_callback(line)
        for update in self.parser.parse_line(line):
            self._apply_update(update)

    def _finished(self, exit_code, _exit_status):
        self._read_output()
        if self.output_buffer.strip():
            self._consume_line(self.output_buffer)
            self.output_buffer = ""
        self.exit_code = exit_code
        self.abort_button.setEnabled(False)
        if exit_code == 0:
            for row, progress in self.rows.values():
                self.table.item(row, 1).setText("Completed")
                progress.setRange(0, 1)
                progress.setValue(1)
                progress.setTextVisible(True)
            self._update_overall()
            self.accept()
        else:
            self.reject()

    def _process_error(self, _error):
        error = self.process.errorString()
        if error:
            self.full_output += error
            self.details.appendPlainText(error)
            self.log_callback(error)
        self.abort_button.setEnabled(False)
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.exit_code = -1
            self.reject()

    def _abort(self):
        self.cancelled = True
        self.abort_button.setEnabled(False)
        self.abort_button.setText("Aborting...")
        self.process.kill()

    def _toggle_details(self, visible):
        self.details.setVisible(visible)
        self.details_button.setText("Hide details" if visible else "Show details")

    def reject(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self._abort()
            return
        super().reject()
