from rmpp_manager.design_model import (
    compile_design,
    compile_element,
    default_element,
)


def test_default_rectangle_is_compilable():
    element = default_element("Rectangle")
    item = compile_element(element)
    assert item["type"] == "path"
    assert item["strokeWidth"] == 1.5
    assert item["data"][0] == "M"
    assert item["data"][-1] == "Z"


def test_scaling_changes_rectangle_geometry():
    element = default_element("Rectangle")
    element["x"] = 10
    element["y"] = 20
    element["width"] = 100
    element["height"] = 50
    element["scale_x"] = 2
    element["scale_y"] = 3

    item = compile_element(element)
    assert item["data"][:6] == [
        "M", 10.0, 20.0, "L", 210.0, 20.0
    ]


def test_text_font_metadata_is_preview_only():
    element = default_element("Text")
    element["font_family"] = "Georgia"
    element["bold"] = True

    native = compile_element(element, preview=False)
    preview = compile_element(element, preview=True)

    assert "_fontFamily" not in native
    assert "_bold" not in native
    assert preview["_fontFamily"] == "Georgia"
    assert preview["_bold"] is True


def test_compile_design_preserves_base_items_and_order():
    metadata = {
        "name": "Test",
        "orientation": "portrait",
        "categories": ["Creative"],
    }
    base = [
        {
            "type": "text",
            "text": "base",
            "fontSize": 20,
            "position": {"x": 1, "y": 2},
        }
    ]
    first = default_element("Horizontal line")
    second = default_element("Text")
    second["text"] = "second"

    result = compile_design(metadata, [first, second], base_items=base)
    assert result["items"][0]["text"] == "base"
    assert result["items"][1]["type"] == "path"
    assert result["items"][2]["text"] == "second"
