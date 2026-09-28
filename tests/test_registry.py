import json

from rmpp_manager.registry import discover_local_templates, merge_registry


def test_new_custom_entry_is_inserted_without_losing_device_data():
    remote = {
        "vendorKey": "keep-me",
        "templates": [
            {
                "name": "Blank",
                "filename": "Blank",
                "iconCode": "\\ue9fe",
                "categories": ["Creative"],
                "supportedScreens": ["rmPP"],
            },
            {
                "name": "Blank",
                "filename": "Blank",
                "iconCode": "\\ue9fd",
                "landscape": True,
                "categories": ["Creative"],
            },
            {
                "name": "Stock",
                "filename": "Stock",
                "iconCode": "\\ue9aa",
                "categories": ["Lines"],
            },
        ],
    }

    local = [
        {
            "name": "Chrissy Notes",
            "filename": "Chrissy_Notes",
            "iconCode": "\\ue9ab",
            "landscape": False,
            "categories": ["Life/organize"],
        }
    ]

    result = merge_registry(remote, local)

    assert result.merged["vendorKey"] == "keep-me"
    assert (
        result.merged["templates"][0]["supportedScreens"]
        == ["rmPP"]
    )

    assert len(result.added) == 1
    assert not result.updated

    # Custom entries go immediately after the Blank variants.
    assert (
        result.merged["templates"][2]["filename"]
        == "Chrissy_Notes"
    )
    assert (
        result.merged["templates"][3]["filename"]
        == "Stock"
    )


def test_exact_duplicate_is_unchanged():
    entry = {
        "name": "Dots",
        "filename": "Dots",
        "iconCode": "\\ue9b9",
        "categories": ["Grids"],
    }

    result = merge_registry(
        {"templates": [entry]},
        [entry],
    )

    assert not result.added
    assert not result.updated
    assert len(result.unchanged) == 1


def test_existing_custom_entry_is_updated_in_place():
    remote = {
        "templates": [
            {
                "name": "Old display name",
                "filename": "Chrissy_Notes",
                "iconCode": "\\ue9b9",
                "categories": ["Lines"],
                "supportedScreens": ["rmPP"],
                "firmwareField": "preserve-me",
            }
        ]
    }

    local = [
        {
            "name": "Chrissy Notes",
            "filename": "Chrissy_Notes",
            "iconCode": "\\ue9ab",
            "landscape": False,
            "categories": ["Life/organize"],
        }
    ]

    result = merge_registry(remote, local)

    assert not result.added
    assert len(result.updated) == 1

    merged = result.merged["templates"][0]

    assert merged["name"] == "Chrissy Notes"
    assert merged["iconCode"] == "\\ue9ab"
    assert merged["categories"] == ["Life/organize"]
    assert merged["landscape"] is False

    # Device-only data survives the custom metadata update.
    assert merged["supportedScreens"] == ["rmPP"]
    assert merged["firmwareField"] == "preserve-me"


def test_landscape_variant_is_distinct():
    remote = {
        "templates": [
            {
                "name": "Dots",
                "filename": "Dots",
                "iconCode": "\\ue9b9",
                "categories": ["Grids"],
            }
        ]
    }

    local = [
        {
            "name": "Dots landscape",
            "filename": "Dots",
            "iconCode": "\\ue9f9",
            "landscape": True,
            "categories": ["Grids"],
        }
    ]

    result = merge_registry(remote, local)

    assert len(result.added) == 1
    assert len(result.merged["templates"]) == 2


def test_original_remote_registry_is_not_mutated():
    remote = {
        "templates": [
            {
                "name": "Stock",
                "filename": "Stock",
                "iconCode": "\\ue9aa",
                "categories": ["Lines"],
            }
        ]
    }

    local = [
        {
            "name": "Custom",
            "filename": "Custom",
            "iconCode": "\\ue9ab",
            "categories": ["Life/organize"],
        }
    ]

    merge_registry(remote, local)

    assert len(remote["templates"]) == 1
    assert remote["templates"][0]["filename"] == "Stock"



def test_fallback_registry_name_comes_from_filename(tmp_path):
    template = tmp_path / "Chrissy_Notes.template"

    template.write_text(
        json.dumps(
            {
                "name": "Weekplanner 1",
                "author": "reMarkable",
                "templateVersion": "1.0.0",
                "formatVersion": 1,
                "categories": ["Creative"],
                "orientation": "portrait",
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    assets, entries = discover_local_templates(tmp_path)

    assert assets == [template]
    assert len(entries) == 1

    # The native template may retain the name of the stock template it was
    # derived from. Registry discovery must instead generate a unique,
    # human-readable display name from the custom filename.
    assert entries[0]["name"] == "Chrissy Notes"
    assert entries[0]["filename"] == "Chrissy_Notes"
