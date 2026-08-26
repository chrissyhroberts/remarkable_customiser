from __future__ import annotations

import ast
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


DEVICE_SIZES = {
    "Paper Pro": (1620.0, 2160.0),
    "reMarkable 2": (1404.0, 1872.0),
}


_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}
_ALLOWED_UNARY = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
    ast.Not: lambda a: not a,
}
_ALLOWED_COMPARE = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}


def _split_ternary(expr: str) -> tuple[str, str, str] | None:
    depth = 0
    q_index = -1
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "?" and depth == 0:
            q_index = i
            break
    if q_index < 0:
        return None

    depth = 0
    nested = 0
    for i in range(q_index + 1, len(expr)):
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch == "?":
            nested += 1
        elif depth == 0 and ch == ":":
            if nested:
                nested -= 1
            else:
                return expr[:q_index], expr[q_index + 1:i], expr[i + 1:]
    raise ValueError(f"Malformed ternary expression: {expr}")


def safe_eval(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, (int, float, bool)):
        return value
    if not isinstance(value, str):
        return value

    expression = value.strip()
    ternary = _split_ternary(expression)
    if ternary:
        condition, when_true, when_false = ternary
        return safe_eval(when_true if safe_eval(condition, variables) else when_false, variables)

    expression = expression.replace("&&", " and ").replace("||", " or ")
    node = ast.parse(expression, mode="eval").body

    def visit(current: ast.AST) -> Any:
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float, bool)):
            return current.value
        if isinstance(current, ast.Name):
            if current.id not in variables:
                raise ValueError(f"Unknown template variable: {current.id}")
            return variables[current.id]
        if isinstance(current, ast.BinOp) and type(current.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(current.op)](visit(current.left), visit(current.right))
        if isinstance(current, ast.UnaryOp) and type(current.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(current.op)](visit(current.operand))
        if isinstance(current, ast.BoolOp):
            values = [bool(visit(v)) for v in current.values]
            if isinstance(current.op, ast.And):
                return all(values)
            if isinstance(current.op, ast.Or):
                return any(values)
        if isinstance(current, ast.Compare):
            left = visit(current.left)
            for op, comparator in zip(current.ops, current.comparators):
                right = visit(comparator)
                fn = _ALLOWED_COMPARE.get(type(op))
                if fn is None or not fn(left, right):
                    return False
                left = right
            return True
        raise ValueError(f"Unsupported template expression: {ast.dump(current)}")

    return visit(node)


def template_variables(template: dict[str, Any], width: float, height: float) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "templateWidth": width,
        "templateHeight": height,
    }
    for constant in template.get("constants", []):
        if not isinstance(constant, dict):
            continue
        for name, value in constant.items():
            variables[name] = safe_eval(value, variables)
    return variables


def _path_from_data(data: list[Any], variables: dict[str, Any], dx: float = 0.0, dy: float = 0.0) -> QPainterPath:
    path = QPainterPath()
    index = 0
    start: QPointF | None = None
    while index < len(data):
        command = data[index]
        if command == "M" and index + 2 < len(data):
            x = float(safe_eval(data[index + 1], variables)) + dx
            y = float(safe_eval(data[index + 2], variables)) + dy
            point = QPointF(x, y)
            path.moveTo(point)
            start = point
            index += 3
        elif command == "L" and index + 2 < len(data):
            x = float(safe_eval(data[index + 1], variables)) + dx
            y = float(safe_eval(data[index + 2], variables)) + dy
            path.lineTo(x, y)
            index += 3
        elif command == "Z":
            path.closeSubpath()
            index += 1
        else:
            index += 1
    return path


def _draw_item(
    painter: QPainter,
    item: dict[str, Any],
    variables: dict[str, Any],
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    limit_height: float,
) -> None:
    kind = item.get("type")
    if kind == "path":
        pen = QPen(QColor(item.get("strokeColor", "#000000")))
        pen.setWidthF(float(item.get("strokeWidth", 1.0)))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_path_from_data(item.get("data", []), variables, dx, dy))
        return

    if kind == "text":
        position = item.get("position", {})
        x = float(safe_eval(position.get("x", 0), variables)) + dx
        y = float(safe_eval(position.get("y", 0), variables)) + dy
        font = QFont()
        font.setPointSizeF(max(1.0, float(item.get("fontSize", 24)) * 0.72))
        painter.setFont(font)
        painter.setPen(QColor(item.get("color", "#000000")))
        painter.drawText(QPointF(x, y + font.pointSizeF()), str(item.get("text", "")))
        return

    if kind == "group":
        box = item.get("boundingBox", {})
        base_x = float(safe_eval(box.get("x", 0), variables)) + dx
        base_y = float(safe_eval(box.get("y", 0), variables)) + dy
        width = float(safe_eval(box.get("width", variables["templateWidth"]), variables))
        height = float(safe_eval(box.get("height", 0), variables))
        children = item.get("children", [])
        repeat = item.get("repeat", {})

        if repeat.get("rows") == "down" and height > 0:
            row_y = base_y
            while row_y <= limit_height:
                for child in children:
                    _draw_item(
                        painter,
                        child,
                        variables,
                        dx=base_x,
                        dy=row_y,
                        limit_height=limit_height,
                    )
                row_y += height
        else:
            for child in children:
                _draw_item(
                    painter,
                    child,
                    variables,
                    dx=base_x,
                    dy=base_y,
                    limit_height=limit_height,
                )


class TemplatePreview(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.template: dict[str, Any] = {
            "name": "Blank",
            "orientation": "portrait",
            "items": [],
        }
        self.device_name = "Paper Pro"
        self.setMinimumSize(420, 560)

    def set_template(self, template: dict[str, Any]) -> None:
        self.template = template
        self.update()

    def set_device(self, device_name: str) -> None:
        if device_name in DEVICE_SIZES:
            self.device_name = device_name
            self.update()

    def logical_size(self) -> tuple[float, float]:
        width, height = DEVICE_SIZES[self.device_name]
        if str(self.template.get("orientation", "portrait")).lower() == "landscape":
            return height, width
        return width, height

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        width, height = self.logical_size()
        margin = 18.0
        sx = max(0.01, (self.width() - margin * 2) / width)
        sy = max(0.01, (self.height() - margin * 2) / height)
        scale = min(sx, sy)
        draw_w, draw_h = width * scale, height * scale
        ox = (self.width() - draw_w) / 2
        oy = (self.height() - draw_h) / 2

        painter.fillRect(self.rect(), QColor("#ececec"))
        painter.save()
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        painter.fillRect(QRectF(0, 0, width, height), QColor("white"))
        painter.setPen(QPen(QColor("#b0b0b0"), 1.0 / scale))
        painter.drawRect(QRectF(0, 0, width, height))

        variables = template_variables(self.template, width, height)
        for item in self.template.get("items", []):
            try:
                _draw_item(painter, item, variables, limit_height=height)
            except Exception:
                pen = QPen(QColor("#aa0000"))
                pen.setWidthF(2)
                painter.setPen(pen)
                painter.drawLine(20, 20, 100, 100)
                painter.drawLine(100, 20, 20, 100)
        painter.restore()


def load_template(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def polygon_path(cx: float, cy: float, radius: float, sides: int = 32) -> list[Any]:
    sides = max(8, sides)
    data: list[Any] = []
    for i in range(sides):
        angle = 2 * math.pi * i / sides
        x = cx + math.cos(angle) * radius
        y = cy + math.sin(angle) * radius
        data.extend(["M" if i == 0 else "L", round(x, 2), round(y, 2)])
    data.append("Z")
    return data
