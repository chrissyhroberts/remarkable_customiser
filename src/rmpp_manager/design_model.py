from __future__ import annotations

import copy
import math
from typing import Any


ELEMENT_TYPES = (
    "Horizontal line",
    "Vertical line",
    "Rectangle",
    "Circle",
    "Text",
    "Ruled lines",
    "Grid",
    "Dot grid",
    "Checklist",
)


def default_element(kind: str) -> dict[str, Any]:
    common = {"kind": kind, "x": 80.0, "y": 100.0}

    defaults: dict[str, dict[str, Any]] = {
        "Horizontal line": {"length": 1000.0, "stroke": 1.5, "scale": 1.0},
        "Vertical line": {"length": 600.0, "stroke": 1.5, "scale": 1.0},
        "Rectangle": {
            "width": 1000.0,
            "height": 600.0,
            "stroke": 1.5,
            "scale_x": 1.0,
            "scale_y": 1.0,
        },
        "Circle": {
            "radius": 80.0,
            "stroke": 1.5,
            "scale_x": 1.0,
            "scale_y": 1.0,
        },
        "Text": {
            "text": "Heading",
            "font_family": "Sans Serif",
            "font_size": 36.0,
            "bold": False,
            "italic": False,
            "scale": 1.0,
        },
        "Ruled lines": {
            "width": 1200.0,
            "count": 12,
            "spacing": 70.0,
            "stroke": 1.25,
            "scale_x": 1.0,
            "scale_y": 1.0,
        },
        "Grid": {
            "width": 1200.0,
            "height": 1200.0,
            "row_spacing": 70.0,
            "column_spacing": 70.0,
            "stroke": 1.0,
            "scale_x": 1.0,
            "scale_y": 1.0,
        },
        "Dot grid": {
            "width": 1200.0,
            "height": 1200.0,
            "row_spacing": 70.0,
            "column_spacing": 70.0,
            "dot_radius": 2.5,
            "stroke": 1.0,
            "scale_x": 1.0,
            "scale_y": 1.0,
        },
        "Checklist": {
            "width": 1200.0,
            "count": 12,
            "spacing": 80.0,
            "box_size": 34.0,
            "line_offset": 28.0,
            "stroke": 1.25,
            "scale_x": 1.0,
            "scale_y": 1.0,
        },
    }

    if kind not in defaults:
        raise ValueError(f"Unknown element type: {kind}")

    return {**common, **copy.deepcopy(defaults[kind])}


def element_display_name(element: dict[str, Any], index: int | None = None) -> str:
    kind = str(element.get("kind", "Element"))
    if kind == "Text":
        text = str(element.get("text", "")).strip()
        label = f'Text: "{text[:24]}"' if text else "Text"
    else:
        label = kind
    return f"{index + 1}. {label}" if index is not None else label


def _path(data: list[Any], stroke: float) -> dict[str, Any]:
    return {"type": "path", "strokeWidth": float(stroke), "data": data}


def _ellipse_path(
    cx: float, cy: float, rx: float, ry: float, sides: int = 40
) -> list[Any]:
    data: list[Any] = []
    sides = max(12, sides)
    for i in range(sides):
        angle = 2.0 * math.pi * i / sides
        x = cx + math.cos(angle) * rx
        y = cy + math.sin(angle) * ry
        data.extend(["M" if i == 0 else "L", round(x, 2), round(y, 2)])
    data.append("Z")
    return data


def _grid_positions(limit: float, spacing: float) -> list[float]:
    spacing = max(1.0, float(spacing))
    limit = max(0.0, float(limit))
    values: list[float] = []
    current = 0.0
    while current <= limit + 1e-9 and len(values) < 5000:
        values.append(current)
        current += spacing
    if not values or values[-1] < limit:
        values.append(limit)
    return values


def compile_element(
    element: dict[str, Any], *, preview: bool = False
) -> dict[str, Any]:
    kind = str(element["kind"])
    x = float(element.get("x", 0.0))
    y = float(element.get("y", 0.0))

    if kind == "Horizontal line":
        length = float(element["length"]) * float(element.get("scale", 1.0))
        return _path(["M", x, y, "L", x + length, y], element["stroke"])

    if kind == "Vertical line":
        length = float(element["length"]) * float(element.get("scale", 1.0))
        return _path(["M", x, y, "L", x, y + length], element["stroke"])

    if kind == "Rectangle":
        width = float(element["width"]) * float(element.get("scale_x", 1.0))
        height = float(element["height"]) * float(element.get("scale_y", 1.0))
        return _path(
            [
                "M", x, y,
                "L", x + width, y,
                "L", x + width, y + height,
                "L", x, y + height,
                "Z",
            ],
            element["stroke"],
        )

    if kind == "Circle":
        rx = float(element["radius"]) * float(element.get("scale_x", 1.0))
        ry = float(element["radius"]) * float(element.get("scale_y", 1.0))
        return _path(_ellipse_path(x, y, rx, ry), element["stroke"])

    if kind == "Text":
        item: dict[str, Any] = {
            "type": "text",
            "text": str(element.get("text", "")),
            "fontSize": max(
                1.0,
                float(element.get("font_size", 36.0))
                * float(element.get("scale", 1.0)),
            ),
            "position": {"x": x, "y": y},
        }
        if preview:
            item["_fontFamily"] = str(element.get("font_family", "Sans Serif"))
            item["_bold"] = bool(element.get("bold", False))
            item["_italic"] = bool(element.get("italic", False))
        return item

    if kind == "Ruled lines":
        width = float(element["width"]) * float(element.get("scale_x", 1.0))
        spacing = float(element["spacing"]) * float(element.get("scale_y", 1.0))
        count = max(1, int(element["count"]))
        data: list[Any] = []
        for row in range(count):
            yy = y + row * spacing
            data += ["M", x, yy, "L", x + width, yy]
        return _path(data, element["stroke"])

    if kind == "Grid":
        width = float(element["width"]) * float(element.get("scale_x", 1.0))
        height = float(element["height"]) * float(element.get("scale_y", 1.0))
        row_spacing = float(element["row_spacing"]) * float(
            element.get("scale_y", 1.0)
        )
        col_spacing = float(element["column_spacing"]) * float(
            element.get("scale_x", 1.0)
        )
        data: list[Any] = []
        for offset in _grid_positions(height, row_spacing):
            data += ["M", x, y + offset, "L", x + width, y + offset]
        for offset in _grid_positions(width, col_spacing):
            data += ["M", x + offset, y, "L", x + offset, y + height]
        return _path(data, element["stroke"])

    if kind == "Dot grid":
        width = float(element["width"]) * float(element.get("scale_x", 1.0))
        height = float(element["height"]) * float(element.get("scale_y", 1.0))
        row_spacing = float(element["row_spacing"]) * float(
            element.get("scale_y", 1.0)
        )
        col_spacing = float(element["column_spacing"]) * float(
            element.get("scale_x", 1.0)
        )
        dot_radius = float(element["dot_radius"])
        data: list[Any] = []
        for yy in _grid_positions(height, row_spacing):
            for xx in _grid_positions(width, col_spacing):
                data += _ellipse_path(
                    x + xx, y + yy, dot_radius, dot_radius, sides=10
                )
        return _path(data, element["stroke"])

    if kind == "Checklist":
        width = float(element["width"]) * float(element.get("scale_x", 1.0))
        spacing = float(element["spacing"]) * float(element.get("scale_y", 1.0))
        box = float(element["box_size"]) * min(
            float(element.get("scale_x", 1.0)),
            float(element.get("scale_y", 1.0)),
        )
        line_offset = float(element["line_offset"]) * float(
            element.get("scale_x", 1.0)
        )
        count = max(1, int(element["count"]))
        data: list[Any] = []
        for row in range(count):
            yy = y + row * spacing
            data += [
                "M", x, yy,
                "L", x + box, yy,
                "L", x + box, yy + box,
                "L", x, yy + box,
                "Z",
                "M", x + box + line_offset, yy + box,
                "L", x + width, yy + box,
            ]
        return _path(data, element["stroke"])

    raise ValueError(f"Unknown element type: {kind}")


def compile_design(
    metadata: dict[str, Any],
    elements: list[dict[str, Any]],
    *,
    base_items: list[dict[str, Any]] | None = None,
    preview: bool = False,
) -> dict[str, Any]:
    template = copy.deepcopy(metadata)
    template["items"] = copy.deepcopy(base_items or [])
    template["items"].extend(
        compile_element(element, preview=preview) for element in elements
    )
    return template
