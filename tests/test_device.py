import json
import uuid

from rmpp_manager.device import (
    _template_deployments,
    is_template_type_firmware,
    parse_firmware_version,
    _template_metadata,
)


def test_firmware_boundary_selects_template_type_mode():
    assert parse_firmware_version('VERSION_ID="3.30.0.108"') == (3, 30, 0, 108)
    assert not is_template_type_firmware((3, 29, 3))
    assert is_template_type_firmware((3, 30))


def test_template_type_deployment_uses_stable_uuid_mapping(tmp_path):
    asset = tmp_path / "Chrissy_Notes.template"
    asset.write_text(
        json.dumps({"orientation": "portrait", "items": []}),
        encoding="utf-8",
    )
    entry = {
        "name": "Chrissy Notes",
        "filename": "Chrissy_Notes",
        "categories": ["Life/organize"],
    }

    first = _template_deployments(tmp_path, [asset], [entry])
    mapping = json.loads((tmp_path / ".rmpp-template-uuids.json").read_text())
    first_uuid = first[0].uuid

    second = _template_deployments(tmp_path, [asset], [entry])

    assert uuid.UUID(first_uuid)
    assert second[0].uuid == first_uuid
    assert mapping["templates"]["Chrissy_Notes|portrait"] == first_uuid


def test_template_type_metadata_has_no_icon_data():
    metadata = json.loads(
        _template_metadata({"name": "Chrissy Notes", "filename": "Chrissy_Notes"})
    )

    assert metadata["type"] == "TemplateType"
    assert metadata["visibleName"] == "Chrissy Notes"
    assert "iconData" not in metadata
