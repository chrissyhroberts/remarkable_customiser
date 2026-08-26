from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

from .registry import safe_filename, update_local_manifest
from .template_engine import TemplatePreview, load_template, polygon_path


class TemplateEditor(QWidget):
    template_saved = Signal(str)

    def __init__(self, library_root: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.library_root = library_root
        self.template: dict = {}
        self._build_ui()
        self.new_template()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)

        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)

        meta = QGroupBox("Template")
        meta_form = QFormLayout(meta)
        self.name_edit = QLineEdit("Custom Template")
        self.orientation = QComboBox()
        self.orientation.addItems(["portrait", "landscape"])
        self.categories = QLineEdit("Creative")
        self.device = QComboBox()
        self.device.addItems(["Paper Pro", "reMarkable 2"])
        self.device.currentTextChanged.connect(self._device_changed)
        meta_form.addRow("Name", self.name_edit)
        meta_form.addRow("Orientation", self.orientation)
        meta_form.addRow("Categories", self.categories)
        meta_form.addRow("Preview device", self.device)
        controls.addWidget(meta)

        primitive = QGroupBox("Add element")
        primitive_form = QFormLayout(primitive)
        self.element_type = QComboBox()
        self.element_type.addItems([
            "Horizontal line",
            "Vertical line",
            "Rectangle",
            "Circle",
            "Text",
            "Ruled lines",
            "Grid",
            "Dot grid",
            "Checklist",
        ])
        self.x = self._spin(0, 5000, 80)
        self.y = self._spin(0, 5000, 100)
        self.width_value = self._spin(1, 5000, 1000)
        self.height_value = self._spin(1, 5000, 600)
        self.spacing = self._spin(2, 1000, 70)
        self.count = self._spin(1, 100, 10)
        self.stroke = QDoubleSpinBox()
        self.stroke.setRange(0.1, 20.0)
        self.stroke.setValue(1.5)
        self.stroke.setSingleStep(0.25)
        self.text_value = QLineEdit("Heading")
        primitive_form.addRow("Type", self.element_type)
        primitive_form.addRow("X", self.x)
        primitive_form.addRow("Y", self.y)
        primitive_form.addRow("Width", self.width_value)
        primitive_form.addRow("Height / radius", self.height_value)
        primitive_form.addRow("Spacing", self.spacing)
        primitive_form.addRow("Count", self.count)
        primitive_form.addRow("Stroke", self.stroke)
        primitive_form.addRow("Text", self.text_value)
        add_button = QPushButton("Add")
        add_button.clicked.connect(self.add_element)
        primitive_form.addRow(add_button)
        controls.addWidget(primitive)

        file_group = QGroupBox("File")
        file_layout = QVBoxLayout(file_group)
        row = QHBoxLayout()
        new_button = QPushButton("New")
        new_button.clicked.connect(self.new_template)
        load_button = QPushButton("Open…")
        load_button.clicked.connect(self.open_template)
        save_button = QPushButton("Save to library")
        save_button.clicked.connect(self.save_template)
        row.addWidget(new_button)
        row.addWidget(load_button)
        row.addWidget(save_button)
        file_layout.addLayout(row)

        clear_button = QPushButton("Clear elements")
        clear_button.clicked.connect(self.clear_elements)
        file_layout.addWidget(clear_button)
        controls.addWidget(file_group)
        controls.addStretch(1)

        left = QWidget()
        left.setLayout(controls)
        left.setMaximumWidth(390)
        outer.addWidget(left)

        self.preview = TemplatePreview()
        outer.addWidget(self.preview, 1)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _device_changed(self, name: str) -> None:
        self.preview.set_device(name)

    def _sync_metadata(self) -> None:
        self.template["name"] = self.name_edit.text().strip() or "Custom Template"
        self.template["orientation"] = self.orientation.currentText()
        categories = [x.strip() for x in self.categories.text().split(",") if x.strip()]
        self.template["categories"] = categories or ["Creative"]

    def new_template(self) -> None:
        self.template = {
            "name": "Custom Template",
            "author": "Custom",
            "templateVersion": "1.0.0",
            "formatVersion": 1,
            "categories": ["Creative"],
            "orientation": "portrait",
            "items": [],
        }
        if hasattr(self, "name_edit"):
            self.name_edit.setText("Custom Template")
            self.orientation.setCurrentText("portrait")
            self.categories.setText("Creative")
            self.preview.set_template(self.template)

    def clear_elements(self) -> None:
        self.template["items"] = []
        self.preview.set_template(self.template)

    def add_element(self) -> None:
        self._sync_metadata()
        kind = self.element_type.currentText()
        x, y = self.x.value(), self.y.value()
        width, height = self.width_value.value(), self.height_value.value()
        spacing, count = self.spacing.value(), self.count.value()
        stroke = self.stroke.value()
        items = self.template.setdefault("items", [])

        def path(data):
            return {"type": "path", "strokeWidth": stroke, "data": data}

        if kind == "Horizontal line":
            items.append(path(["M", x, y, "L", x + width, y]))
        elif kind == "Vertical line":
            items.append(path(["M", x, y, "L", x, y + height]))
        elif kind == "Rectangle":
            items.append(path([
                "M", x, y,
                "L", x + width, y,
                "L", x + width, y + height,
                "L", x, y + height,
                "Z",
            ]))
        elif kind == "Circle":
            items.append(path(polygon_path(x, y, max(1, height), 32)))
        elif kind == "Text":
            items.append({
                "type": "text",
                "text": self.text_value.text(),
                "fontSize": max(8, int(height / 2)),
                "position": {"x": x, "y": y},
            })
        elif kind == "Ruled lines":
            data = []
            for row in range(count):
                yy = y + row * spacing
                data += ["M", x, yy, "L", x + width, yy]
            items.append(path(data))
        elif kind == "Grid":
            data = []
            for row in range(count + 1):
                yy = y + row * spacing
                data += ["M", x, yy, "L", x + width, yy]
            columns = max(1, width // spacing)
            for col in range(columns + 1):
                xx = x + col * spacing
                data += ["M", xx, y, "L", xx, y + count * spacing]
            items.append(path(data))
        elif kind == "Dot grid":
            data = []
            radius = max(1.5, stroke * 1.5)
            columns = max(1, width // spacing)
            for row in range(count):
                for col in range(columns + 1):
                    cx, cy = x + col * spacing, y + row * spacing
                    data += polygon_path(cx, cy, radius, 8)
            items.append(path(data))
        elif kind == "Checklist":
            data = []
            box = min(50, max(16, spacing - 12))
            for row in range(count):
                yy = y + row * spacing
                data += [
                    "M", x, yy,
                    "L", x + box, yy,
                    "L", x + box, yy + box,
                    "L", x, yy + box,
                    "Z",
                    "M", x + box + 25, yy + box,
                    "L", x + width, yy + box,
                ]
            items.append(path(data))

        self.preview.set_template(self.template)

    def open_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open native template",
            str(self.library_root),
            "reMarkable templates (*.template);;JSON (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            template = load_template(Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Could not open template", str(exc))
            return
        self.template = template
        self.name_edit.setText(str(template.get("name", "Custom Template")))
        self.orientation.setCurrentText(str(template.get("orientation", "portrait")))
        self.categories.setText(", ".join(template.get("categories", ["Creative"])))
        self.preview.set_template(self.template)

    def save_template(self) -> None:
        self._sync_metadata()
        folder = self.library_root / "templates"
        folder.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(self.template["name"])
        path = folder / f"{stem}.template"
        try:
            with path.open("w", encoding="utf-8") as handle:
                json.dump(self.template, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            update_local_manifest(
                folder,
                name=self.template["name"],
                filename=stem,
                categories=self.template["categories"],
                landscape=self.template["orientation"] == "landscape",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not save template", str(exc))
            return

        self.template_saved.emit(str(folder))
        QMessageBox.information(
            self,
            "Template saved",
            f"Saved:\n{path}\n\nThe local templates.json manifest was updated too.",
        )
