from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .design_model import (
    ELEMENT_TYPES,
    compile_design,
    default_element,
    element_display_name,
)
from .registry import safe_filename, update_local_manifest
from .template_engine import TemplatePreview, load_template


PARAMETERS: dict[str, list[tuple[Any, ...]]] = {
    "Horizontal line": [
        ("x", "X", "float", 0, 5000, 1),
        ("y", "Y", "float", 0, 5000, 1),
        ("length", "Length", "float", 1, 5000, 5),
        ("stroke", "Stroke", "float", 0.1, 20, 0.25),
        ("scale", "Scale", "float", 0.05, 20, 0.05),
    ],
    "Vertical line": [
        ("x", "X", "float", 0, 5000, 1),
        ("y", "Y", "float", 0, 5000, 1),
        ("length", "Length", "float", 1, 5000, 5),
        ("stroke", "Stroke", "float", 0.1, 20, 0.25),
        ("scale", "Scale", "float", 0.05, 20, 0.05),
    ],
    "Rectangle": [
        ("x", "X", "float", 0, 5000, 1),
        ("y", "Y", "float", 0, 5000, 1),
        ("width", "Width", "float", 1, 5000, 5),
        ("height", "Height", "float", 1, 5000, 5),
        ("stroke", "Stroke", "float", 0.1, 20, 0.25),
        ("scale_x", "Scale X", "float", 0.05, 20, 0.05),
        ("scale_y", "Scale Y", "float", 0.05, 20, 0.05),
    ],
    "Circle": [
        ("x", "Centre X", "float", 0, 5000, 1),
        ("y", "Centre Y", "float", 0, 5000, 1),
        ("radius", "Radius", "float", 1, 2500, 2),
        ("stroke", "Stroke", "float", 0.1, 20, 0.25),
        ("scale_x", "Scale X", "float", 0.05, 20, 0.05),
        ("scale_y", "Scale Y", "float", 0.05, 20, 0.05),
    ],
    "Text": [
        ("x", "X", "float", 0, 5000, 1),
        ("y", "Y", "float", 0, 5000, 1),
        ("text", "Text", "text"),
        ("font_family", "Font", "font"),
        ("font_size", "Font size", "float", 4, 300, 1),
        ("bold", "Bold", "bool"),
        ("italic", "Italic", "bool"),
        ("scale", "Scale", "float", 0.05, 20, 0.05),
    ],
    "Ruled lines": [
        ("x", "X", "float", 0, 5000, 1),
        ("y", "Y", "float", 0, 5000, 1),
        ("width", "Width", "float", 1, 5000, 5),
        ("count", "Lines", "int", 1, 200, 1),
        ("spacing", "Spacing", "float", 1, 1000, 1),
        ("stroke", "Stroke", "float", 0.1, 20, 0.25),
        ("scale_x", "Scale X", "float", 0.05, 20, 0.05),
        ("scale_y", "Scale Y", "float", 0.05, 20, 0.05),
    ],
    "Grid": [
        ("x", "X", "float", 0, 5000, 1),
        ("y", "Y", "float", 0, 5000, 1),
        ("width", "Width", "float", 1, 5000, 5),
        ("height", "Height", "float", 1, 5000, 5),
        ("row_spacing", "Row spacing", "float", 1, 1000, 1),
        ("column_spacing", "Column spacing", "float", 1, 1000, 1),
        ("stroke", "Stroke", "float", 0.1, 20, 0.25),
        ("scale_x", "Scale X", "float", 0.05, 20, 0.05),
        ("scale_y", "Scale Y", "float", 0.05, 20, 0.05),
    ],
    "Dot grid": [
        ("x", "X", "float", 0, 5000, 1),
        ("y", "Y", "float", 0, 5000, 1),
        ("width", "Width", "float", 1, 5000, 5),
        ("height", "Height", "float", 1, 5000, 5),
        ("row_spacing", "Row spacing", "float", 1, 1000, 1),
        ("column_spacing", "Column spacing", "float", 1, 1000, 1),
        ("dot_radius", "Dot radius", "float", 0.5, 30, 0.5),
        ("stroke", "Dot stroke", "float", 0.1, 20, 0.25),
        ("scale_x", "Scale X", "float", 0.05, 20, 0.05),
        ("scale_y", "Scale Y", "float", 0.05, 20, 0.05),
    ],
    "Checklist": [
        ("x", "X", "float", 0, 5000, 1),
        ("y", "Y", "float", 0, 5000, 1),
        ("width", "Width", "float", 1, 5000, 5),
        ("count", "Rows", "int", 1, 200, 1),
        ("spacing", "Row spacing", "float", 1, 1000, 1),
        ("box_size", "Checkbox size", "float", 4, 300, 1),
        ("line_offset", "Text-line offset", "float", 0, 1000, 1),
        ("stroke", "Stroke", "float", 0.1, 20, 0.25),
        ("scale_x", "Scale X", "float", 0.05, 20, 0.05),
        ("scale_y", "Scale Y", "float", 0.05, 20, 0.05),
    ],
}


class TemplateEditor(QWidget):
    template_saved = Signal(str)

    def __init__(self, library_root: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.library_root = library_root
        self.metadata: dict[str, Any] = {}
        self.elements: list[dict[str, Any]] = []
        self.base_items: list[dict[str, Any]] = []
        self._loading_properties = False
        self._build_ui()
        self.new_template()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMaximumWidth(430)

        left_widget = QWidget()
        controls = QVBoxLayout(left_widget)
        controls.setContentsMargins(4, 4, 4, 4)

        meta = QGroupBox("Template")
        meta_form = QFormLayout(meta)
        self.name_edit = QLineEdit("Custom Template")
        self.orientation = QComboBox()
        self.orientation.addItems(["portrait", "landscape"])
        self.categories = QLineEdit("Creative")
        self.device = QComboBox()
        self.device.addItems(["Paper Pro", "reMarkable 2"])
        self.device.currentTextChanged.connect(self._device_changed)
        self.name_edit.textChanged.connect(self._metadata_changed)
        self.orientation.currentTextChanged.connect(self._metadata_changed)
        self.categories.textChanged.connect(self._metadata_changed)
        meta_form.addRow("Name", self.name_edit)
        meta_form.addRow("Orientation", self.orientation)
        meta_form.addRow("Categories", self.categories)
        meta_form.addRow("Preview device", self.device)
        controls.addWidget(meta)

        add_group = QGroupBox("Add element")
        add_layout = QHBoxLayout(add_group)
        self.element_type = QComboBox()
        self.element_type.addItems(ELEMENT_TYPES)
        add_button = QPushButton("Add")
        add_button.clicked.connect(self.add_element)
        add_layout.addWidget(self.element_type, 1)
        add_layout.addWidget(add_button)
        controls.addWidget(add_group)

        elements_group = QGroupBox("Elements")
        elements_layout = QVBoxLayout(elements_group)
        self.element_list = QListWidget()
        self.element_list.setMinimumHeight(180)
        self.element_list.currentRowChanged.connect(self._selection_changed)
        elements_layout.addWidget(self.element_list)

        row1 = QHBoxLayout()
        duplicate_button = QPushButton("Duplicate")
        duplicate_button.clicked.connect(self.duplicate_selected)
        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self.delete_selected)
        row1.addWidget(duplicate_button)
        row1.addWidget(delete_button)
        elements_layout.addLayout(row1)

        row2 = QHBoxLayout()
        up_button = QPushButton("Move up")
        up_button.clicked.connect(lambda: self.move_selected(-1))
        down_button = QPushButton("Move down")
        down_button.clicked.connect(lambda: self.move_selected(1))
        row2.addWidget(up_button)
        row2.addWidget(down_button)
        elements_layout.addLayout(row2)
        controls.addWidget(elements_group)

        self.properties_group = QGroupBox("Selected element")
        self.properties_form = QFormLayout(self.properties_group)
        controls.addWidget(self.properties_group)

        file_group = QGroupBox("File")
        file_layout = QVBoxLayout(file_group)
        file_row = QHBoxLayout()
        new_button = QPushButton("New")
        new_button.clicked.connect(self.new_template)
        open_button = QPushButton("Open…")
        open_button.clicked.connect(self.open_template)
        save_button = QPushButton("Save to library")
        save_button.clicked.connect(self.save_template)
        file_row.addWidget(new_button)
        file_row.addWidget(open_button)
        file_row.addWidget(save_button)
        file_layout.addLayout(file_row)

        clear_button = QPushButton("Clear editable elements")
        clear_button.clicked.connect(self.clear_elements)
        file_layout.addWidget(clear_button)
        controls.addWidget(file_group)
        controls.addStretch(1)

        left_scroll.setWidget(left_widget)
        outer.addWidget(left_scroll)

        self.preview = TemplatePreview()
        outer.addWidget(self.preview, 1)

    def _metadata_dict(self) -> dict[str, Any]:
        categories = [x.strip() for x in self.categories.text().split(",") if x.strip()]
        return {
            "name": self.name_edit.text().strip() or "Custom Template",
            "author": self.metadata.get("author", "Custom"),
            "templateVersion": self.metadata.get("templateVersion", "1.0.0"),
            "formatVersion": self.metadata.get("formatVersion", 1),
            "categories": categories or ["Creative"],
            "orientation": self.orientation.currentText(),
        }

    def _metadata_changed(self, _value: object = None) -> None:
        if not hasattr(self, "preview"):
            return
        self.metadata = self._metadata_dict()
        self._refresh_preview()

    def _device_changed(self, name: str) -> None:
        self.preview.set_device(name)

    def _refresh_preview(self) -> None:
        if not hasattr(self, "preview"):
            return
        self.metadata = self._metadata_dict()
        self.preview.set_template(
            compile_design(
                self.metadata,
                self.elements,
                base_items=self.base_items,
                preview=True,
            )
        )

    def _refresh_element_list(self, select_row: int | None = None) -> None:
        current = self.element_list.currentRow() if select_row is None else select_row
        self.element_list.blockSignals(True)
        self.element_list.clear()
        for index, element in enumerate(self.elements):
            self.element_list.addItem(element_display_name(element, index))
        self.element_list.blockSignals(False)

        if self.elements:
            current = min(max(current, 0), len(self.elements) - 1)
            self.element_list.setCurrentRow(current)
        else:
            self._selection_changed(-1)

    def add_element(self) -> None:
        element = default_element(self.element_type.currentText())
        if self.elements:
            element["x"] = float(self.elements[-1].get("x", 80.0)) + 20.0
            element["y"] = float(self.elements[-1].get("y", 100.0)) + 20.0
        self.elements.append(element)
        self._refresh_element_list(len(self.elements) - 1)
        self._refresh_preview()

    def duplicate_selected(self) -> None:
        row = self.element_list.currentRow()
        if not 0 <= row < len(self.elements):
            return
        duplicate = copy.deepcopy(self.elements[row])
        duplicate["x"] = float(duplicate.get("x", 0.0)) + 20.0
        duplicate["y"] = float(duplicate.get("y", 0.0)) + 20.0
        self.elements.insert(row + 1, duplicate)
        self._refresh_element_list(row + 1)
        self._refresh_preview()

    def delete_selected(self) -> None:
        row = self.element_list.currentRow()
        if not 0 <= row < len(self.elements):
            return
        del self.elements[row]
        self._refresh_element_list(min(row, len(self.elements) - 1))
        self._refresh_preview()

    def move_selected(self, direction: int) -> None:
        row = self.element_list.currentRow()
        target = row + direction
        if not (0 <= row < len(self.elements) and 0 <= target < len(self.elements)):
            return
        self.elements[row], self.elements[target] = (
            self.elements[target],
            self.elements[row],
        )
        self._refresh_element_list(target)
        self._refresh_preview()

    def clear_elements(self) -> None:
        self.elements = []
        self._refresh_element_list(-1)
        self._refresh_preview()

    def _clear_property_form(self) -> None:
        while self.properties_form.rowCount():
            self.properties_form.removeRow(0)

    def _selection_changed(self, row: int) -> None:
        self._clear_property_form()
        if not 0 <= row < len(self.elements):
            self.properties_form.addRow(QLabel("Select an element to edit it."))
            return

        element = self.elements[row]
        kind = str(element["kind"])
        self._loading_properties = True
        try:
            for spec in PARAMETERS[kind]:
                key, label, widget_type, *limits = spec
                widget = self._property_widget(
                    key, widget_type, element.get(key), limits
                )
                self.properties_form.addRow(label, widget)
        finally:
            self._loading_properties = False

    def _property_widget(
        self,
        key: str,
        widget_type: str,
        value: Any,
        limits: list[Any],
    ) -> QWidget:
        if widget_type == "text":
            widget = QLineEdit(str(value or ""))
            widget.textChanged.connect(
                lambda new, k=key: self._set_property(k, new)
            )
            return widget

        if widget_type == "font":
            widget = QComboBox()
            widget.setEditable(True)
            widget.addItems(
                [
                    "Sans Serif",
                    "Helvetica",
                    "Arial",
                    "Avenir",
                    "Georgia",
                    "Times New Roman",
                    "Courier New",
                    "Menlo",
                ]
            )
            widget.setCurrentText(str(value or "Sans Serif"))
            widget.currentTextChanged.connect(
                lambda new, k=key: self._set_property(k, new)
            )
            return widget

        if widget_type == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            widget.toggled.connect(
                lambda new, k=key: self._set_property(k, bool(new))
            )
            return widget

        if widget_type == "int":
            minimum, maximum, step = limits
            widget = QSpinBox()
            widget.setRange(int(minimum), int(maximum))
            widget.setSingleStep(int(step))
            widget.setValue(int(value))
            widget.valueChanged.connect(
                lambda new, k=key: self._set_property(k, int(new))
            )
            return widget

        minimum, maximum, step = limits
        widget = QDoubleSpinBox()
        widget.setDecimals(2)
        widget.setRange(float(minimum), float(maximum))
        widget.setSingleStep(float(step))
        widget.setValue(float(value))
        widget.valueChanged.connect(
            lambda new, k=key: self._set_property(k, float(new))
        )
        return widget

    def _set_property(self, key: str, value: Any) -> None:
        if self._loading_properties:
            return
        row = self.element_list.currentRow()
        if not 0 <= row < len(self.elements):
            return
        self.elements[row][key] = value
        self.element_list.item(row).setText(
            element_display_name(self.elements[row], row)
        )
        self._refresh_preview()

    def new_template(self) -> None:
        self.metadata = {
            "name": "Custom Template",
            "author": "Custom",
            "templateVersion": "1.0.0",
            "formatVersion": 1,
            "categories": ["Creative"],
            "orientation": "portrait",
        }
        self.elements = []
        self.base_items = []

        if hasattr(self, "name_edit"):
            self.name_edit.setText("Custom Template")
            self.orientation.setCurrentText("portrait")
            self.categories.setText("Creative")
            self._refresh_element_list(-1)
            self._refresh_preview()

    def _load_design(self, design: dict[str, Any]) -> None:
        self.metadata = dict(design.get("template", {}))
        self.elements = [dict(item) for item in design.get("elements", [])]
        self.base_items = [dict(item) for item in design.get("base_items", [])]

        self.name_edit.setText(
            str(self.metadata.get("name", "Custom Template"))
        )
        self.orientation.setCurrentText(
            str(self.metadata.get("orientation", "portrait"))
        )
        self.categories.setText(
            ", ".join(self.metadata.get("categories", ["Creative"]))
        )
        self._refresh_element_list(0 if self.elements else -1)
        self._refresh_preview()

    def open_template(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "Open template or editable design",
            str(self.library_root),
            (
                "Editable designs (*.design.json);;"
                "reMarkable templates (*.template);;"
                "JSON (*.json);;"
                "All files (*)"
            ),
        )
        if not path_text:
            return

        path = Path(path_text)
        try:
            if path.name.endswith(".design.json"):
                with path.open("r", encoding="utf-8") as handle:
                    self._load_design(json.load(handle))
                return

            sidecar = path.with_suffix(".design.json")
            if sidecar.is_file():
                with sidecar.open("r", encoding="utf-8") as handle:
                    self._load_design(json.load(handle))
                return

            template = load_template(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open template", str(exc))
            return

        self.metadata = {k: v for k, v in template.items() if k != "items"}
        self.elements = []
        self.base_items = [
            dict(item) for item in template.get("items", [])
        ]

        self.name_edit.setText(str(template.get("name", "Custom Template")))
        self.orientation.setCurrentText(
            str(template.get("orientation", "portrait"))
        )
        self.categories.setText(
            ", ".join(template.get("categories", ["Creative"]))
        )
        self._refresh_element_list(-1)
        self._refresh_preview()

        QMessageBox.information(
            self,
            "Native template opened",
            (
                "This template has no .design.json sidecar, so its existing "
                "native items are preserved as a non-editable base layer. "
                "New elements that you add here remain fully editable."
            ),
        )

    def save_template(self) -> None:
        self.metadata = self._metadata_dict()
        folder = self.library_root / "templates"
        folder.mkdir(parents=True, exist_ok=True)

        stem = safe_filename(self.metadata["name"])
        template_path = folder / f"{stem}.template"
        design_path = folder / f"{stem}.design.json"

        native_template = compile_design(
            self.metadata,
            self.elements,
            base_items=self.base_items,
            preview=False,
        )
        design = {
            "schema": "remarkable-customiser-design",
            "schemaVersion": 1,
            "template": self.metadata,
            "elements": self.elements,
            "base_items": self.base_items,
        }

        try:
            with template_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    native_template, handle, ensure_ascii=False, indent=2
                )
                handle.write("\n")

            with design_path.open("w", encoding="utf-8") as handle:
                json.dump(design, handle, ensure_ascii=False, indent=2)
                handle.write("\n")

            update_local_manifest(
                folder,
                name=self.metadata["name"],
                filename=stem,
                categories=self.metadata["categories"],
                landscape=self.metadata["orientation"] == "landscape",
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Could not save template", str(exc)
            )
            return

        self.template_saved.emit(str(folder))
        QMessageBox.information(
            self,
            "Template saved",
            (
                f"Deployable template:\n{template_path}\n\n"
                f"Editable design:\n{design_path}\n\n"
                "The local templates.json manifest was updated too."
            ),
        )
