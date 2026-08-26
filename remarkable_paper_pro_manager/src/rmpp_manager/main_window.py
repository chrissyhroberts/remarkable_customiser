from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSettings, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .device import (
    DeploymentPlan,
    DeploymentSpec,
    DeviceConfig,
    build_plan,
    deploy,
    restore_backup,
    test_connection,
)
from .editor import TemplateEditor


class Worker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    log = Signal(str)
    finished = Signal()

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class PlanDialog(QDialog):
    def __init__(self, plan: DeploymentPlan, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Deployment preview")
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        intro = QLabel("The live templates.json was fetched from the device. No changes have been made.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        lines: list[str] = []
        lines.append("PLANNED ACTIONS")
        if plan.actions:
            lines.extend(f"  • {action}" for action in plan.actions)
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("WARNINGS")
        if plan.warnings:
            lines.extend(f"  • {warning}" for warning in plan.warnings)
        else:
            lines.append("  (none)")

        merge = plan.registry_merge
        if merge is not None:
            lines.append("")
            lines.append("REGISTRY MERGE")
            lines.append(f"  Add: {len(merge.added)}")
            lines.append(f"  Already identical: {len(merge.unchanged)}")
            lines.append(f"  Conflicts: {len(merge.conflicts)}")
            lines.append(f"  Name collisions: {len(merge.name_collisions)}")
            if merge.added:
                lines.append("")
                lines.append("Entries to add:")
                for entry in merge.added:
                    lines.append(
                        f"  + {entry.get('name')} → {entry.get('filename')}"
                        + (" [landscape]" if entry.get("landscape") else "")
                    )
            if merge.conflicts:
                lines.append("")
                lines.append("Conflicts:")
                for remote, local in merge.conflicts:
                    lines.append(
                        f"  ! {local.get('filename')}: device='{remote.get('name')}', "
                        f"local='{local.get('name')}'"
                    )

        text.setPlainText("\n".join(lines))
        layout.addWidget(text, 1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)


class DeployTab(QWidget):
    def __init__(self, settings: QSettings, library_root: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.library_root = library_root
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        connection = QGroupBox("Device connection")
        form = QFormLayout(connection)
        self.host = QLineEdit()
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Not saved; leave blank to use SSH key/agent")
        self.key_file = QLineEdit()
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_file, 1)
        key_browse = QPushButton("Browse…")
        key_browse.clicked.connect(self._browse_key)
        key_row.addWidget(key_browse)
        form.addRow("Host", self.host)
        form.addRow("User", self.user)
        form.addRow("Password", self.password)
        form.addRow("SSH key", key_row)

        test_button = QPushButton("Test connection")
        test_button.clicked.connect(self.test_device)
        form.addRow(test_button)
        layout.addWidget(connection)

        sleep_group = QGroupBox("Suspended screen")
        sleep_form = QFormLayout(sleep_group)
        self.enable_suspend = QCheckBox("Replace suspended.png")
        self.suspend_path = QLineEdit()
        suspend_row = QHBoxLayout()
        suspend_row.addWidget(self.suspend_path, 1)
        suspend_browse = QPushButton("Choose PNG…")
        suspend_browse.clicked.connect(self._browse_suspend)
        suspend_row.addWidget(suspend_browse)
        sleep_form.addRow(self.enable_suspend)
        sleep_form.addRow("Image", suspend_row)
        layout.addWidget(sleep_group)

        carousel = QGroupBox("Sleep carousel")
        carousel_layout = QVBoxLayout(carousel)
        self.carousel_leave = QRadioButton("Leave unchanged")
        self.carousel_blank = QRadioButton("Blank sleep_Illustration_*.png files")
        self.carousel_custom = QRadioButton("Upload PNG files from custom folder")
        self.carousel_blank.setChecked(True)
        carousel_layout.addWidget(self.carousel_leave)
        carousel_layout.addWidget(self.carousel_blank)
        carousel_layout.addWidget(self.carousel_custom)
        carousel_row = QHBoxLayout()
        self.carousel_path = QLineEdit()
        carousel_row.addWidget(self.carousel_path, 1)
        carousel_browse = QPushButton("Choose folder…")
        carousel_browse.clicked.connect(self._browse_carousel)
        carousel_row.addWidget(carousel_browse)
        carousel_layout.addLayout(carousel_row)
        layout.addWidget(carousel)

        templates = QGroupBox("Native templates")
        templates_form = QFormLayout(templates)
        template_row = QHBoxLayout()
        self.template_path = QLineEdit()
        template_row.addWidget(self.template_path, 1)
        template_browse = QPushButton("Choose folder…")
        template_browse.clicked.connect(self._browse_templates)
        template_row.addWidget(template_browse)
        self.replace_conflicts = QCheckBox("Replace registry entries with same filename + orientation")
        self.replace_conflicts.setToolTip(
            "Off is safer: conflicting entries in the live device templates.json are preserved."
        )
        templates_form.addRow("Folder", template_row)
        templates_form.addRow(self.replace_conflicts)
        layout.addWidget(templates)

        action_row = QHBoxLayout()
        self.preview_button = QPushButton("Preview changes")
        self.preview_button.clicked.connect(self.preview)
        self.push_button = QPushButton("Push to reMarkable")
        self.push_button.clicked.connect(self.push)
        self.restore_button = QPushButton("Restore backup…")
        self.restore_button.clicked.connect(self.restore)
        action_row.addWidget(self.preview_button)
        action_row.addWidget(self.push_button)
        action_row.addWidget(self.restore_button)
        layout.addLayout(action_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Connection and deployment log")
        layout.addWidget(self.log, 1)

    def _load_settings(self) -> None:
        self.host.setText(str(self.settings.value("device/host", "192.168.86.87")))
        self.user.setText(str(self.settings.value("device/user", "root")))
        self.key_file.setText(str(self.settings.value("device/key_file", "")))
        self.template_path.setText(str(self.settings.value("deploy/template_folder", "")))
        self.suspend_path.setText(str(self.settings.value("deploy/suspend_path", "")))
        self.carousel_path.setText(str(self.settings.value("deploy/carousel_folder", "")))

    def save_settings(self) -> None:
        self.settings.setValue("device/host", self.host.text().strip())
        self.settings.setValue("device/user", self.user.text().strip())
        self.settings.setValue("device/key_file", self.key_file.text().strip())
        self.settings.setValue("deploy/template_folder", self.template_path.text().strip())
        self.settings.setValue("deploy/suspend_path", self.suspend_path.text().strip())
        self.settings.setValue("deploy/carousel_folder", self.carousel_path.text().strip())

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose private SSH key", str(Path.home()))
        if path:
            self.key_file.setText(path)

    def _browse_suspend(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose suspended screen", str(self.library_root), "PNG images (*.png)"
        )
        if path:
            self.suspend_path.setText(path)
            self.enable_suspend.setChecked(True)

    def _browse_carousel(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose carousel folder", str(self.library_root))
        if path:
            self.carousel_path.setText(path)
            self.carousel_custom.setChecked(True)

    def _browse_templates(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose native template folder", str(self.library_root / "templates")
        )
        if path:
            self.template_path.setText(path)

    def set_template_folder(self, path: str) -> None:
        self.template_path.setText(path)

    def _config(self) -> DeviceConfig:
        return DeviceConfig(
            host=self.host.text().strip() or "192.168.86.87",
            user=self.user.text().strip() or "root",
            password=self.password.text(),
            key_file=self.key_file.text().strip(),
        )

    def _spec(self) -> DeploymentSpec:
        suspend = None
        if self.enable_suspend.isChecked() and self.suspend_path.text().strip():
            suspend = Path(self.suspend_path.text().strip()).expanduser()

        if self.carousel_blank.isChecked():
            carousel_mode = "blank"
        elif self.carousel_custom.isChecked():
            carousel_mode = "custom"
        else:
            carousel_mode = "leave"

        carousel_folder = (
            Path(self.carousel_path.text().strip()).expanduser()
            if self.carousel_path.text().strip()
            else None
        )
        template_folder = (
            Path(self.template_path.text().strip()).expanduser()
            if self.template_path.text().strip()
            else None
        )
        return DeploymentSpec(
            suspend_screen=suspend,
            carousel_mode=carousel_mode,
            carousel_folder=carousel_folder,
            template_folder=template_folder,
            replace_registry_conflicts=self.replace_conflicts.isChecked(),
            backup_root=self.library_root / "backups",
        )

    def _set_busy(self, busy: bool) -> None:
        self.preview_button.setEnabled(not busy)
        self.push_button.setEnabled(not busy)
        self.restore_button.setEnabled(not busy)

    def _start(
        self,
        fn: Callable[[], object],
        *,
        success: Callable[[object], None] | None = None,
    ) -> None:
        if self.thread is not None:
            return
        self._set_busy(True)
        self.save_settings()
        thread = QThread(self)
        worker = Worker(fn)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.failed.connect(self._operation_failed)
        if success is not None:
            worker.succeeded.connect(success)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._thread_done)
        self.thread = thread
        self.worker = worker
        thread.start()

    @Slot()
    def _thread_done(self) -> None:
        self.thread = None
        self.worker = None
        self._set_busy(False)

    @Slot(str)
    def _append_log(self, message: str) -> None:
        self.log.appendPlainText(message)

    @Slot(str)
    def _operation_failed(self, detail: str) -> None:
        self.log.appendPlainText(detail)
        headline = detail.splitlines()[0] if detail else "Operation failed."
        QMessageBox.critical(self, "Operation failed", headline)

    def test_device(self) -> None:
        self._append_log("Testing SSH connection…")
        self._start(
            lambda: test_connection(self._config()),
            success=lambda result: self._connection_ok(str(result)),
        )

    def _connection_ok(self, result: str) -> None:
        self._append_log(result)
        QMessageBox.information(self, "Connected", result[:1000])

    def preview(self) -> None:
        self._append_log("Fetching live template registry and building preview…")
        self._start(
            lambda: build_plan(self._config(), self._spec()),
            success=lambda result: PlanDialog(result, self).exec(),
        )

    def push(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Push customisations?",
            "The app will back up affected files, remount / read-write, upload the selected "
            "customisations, merge the live templates.json, restart xochitl, then attempt to "
            "remount / read-only.\n\nContinue?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        config = self._config()
        spec = self._spec()

        def work():
            logger = self.worker.log.emit if self.worker is not None else None
            return deploy(config, spec, log=logger)

        self._append_log("Starting deployment…")
        self._start(
            work,
            success=lambda result: QMessageBox.information(
                self, "Deployment complete", f"Backup saved to:\n{result}"
            ),
        )

    def restore(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose a backup timestamp folder",
            str(self.library_root / "backups" / (self.host.text().strip() or "device")),
        )
        if not path:
            return
        confirm = QMessageBox.warning(
            self,
            "Restore backup?",
            "This will overwrite the device files contained in the selected backup and restart xochitl.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        config = self._config()
        backup = Path(path)

        def work():
            logger = self.worker.log.emit if self.worker is not None else None
            restore_backup(config, backup, log=logger)
            return backup

        self._append_log("Starting restore…")
        self._start(
            work,
            success=lambda _result: QMessageBox.information(self, "Restore complete", "Backup restored."),
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("reMarkable Paper Pro Manager")
        self.resize(1180, 850)

        self.settings = QSettings()
        saved_root = str(
            self.settings.value(
                "library/root",
                str(Path.home() / "Documents" / "reMarkable Templates"),
            )
        )
        self.library_root = Path(saved_root).expanduser()
        self.library_root.mkdir(parents=True, exist_ok=True)

        tabs = QTabWidget()
        self.deploy_tab = DeployTab(self.settings, self.library_root)
        self.editor = TemplateEditor(self.library_root)
        self.editor.template_saved.connect(self.deploy_tab.set_template_folder)
        tabs.addTab(self.deploy_tab, "Device & deploy")
        tabs.addTab(self.editor, "Template designer")
        self.setCentralWidget(tabs)

        self.statusBar().showMessage(
            "Firmware updates may overwrite files in /usr/share/remarkable; keep local backups."
        )

    def closeEvent(self, event) -> None:
        self.deploy_tab.save_settings()
        self.settings.setValue("library/root", str(self.library_root))
        super().closeEvent(event)
