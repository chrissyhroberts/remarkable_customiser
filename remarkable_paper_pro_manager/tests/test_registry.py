from pathlib import Path

from rmpp_manager.registry import merge_registry


def test_merge_appends_new_entry_without_dropping_remote_fields():
    remote = {
        "vendorKey": "keep-me",
        "templates": [
            {
                "name": "Blank",
                "filename": "Blank",
                "iconCode": "\ue9fe",
                "categories": ["Creative"],
                "supportedScreens": ["rmPP"],
            }
        ],
    }
    local = [
        {
            "name": "Chrissy Notes",
            "filename": "Chrissy_Notes",
            "iconCode": "\ue9b9",
            "categories": ["Lines"],
        }
    ]

    result = merge_registry(remote, local)

    assert result.merged["vendorKey"] == "keep-me"
    assert result.merged["templates"][0]["supportedScreens"] == ["rmPP"]
    assert result.merged["templates"][-1]["filename"] == "Chrissy_Notes"
    assert len(result.added) == 1


def test_exact_duplicate_is_unchanged():
    entry = {
        "name": "Dots",
        "filename": "Dots",
        "iconCode": "\ue9b9",
        "categories": ["Grids"],
    }
    result = merge_registry({"templates": [entry]}, [entry])
    assert not result.added
    assert len(result.unchanged) == 1
    assert not result.conflicts


def test_same_filename_and_orientation_is_conflict_by_default():
    remote = {
        "templates": [
            {
                "name": "Old",
                "filename": "Notes",
                "iconCode": "\ue9b9",
                "categories": ["Lines"],
            }
        ]
    }
    local = [
        {
            "name": "New",
            "filename": "Notes",
            "iconCode": "\ue9b9",
            "categories": ["Creative"],
        }
    ]

    result = merge_registry(remote, local)
    assert len(result.conflicts) == 1
    assert result.merged["templates"][0]["name"] == "Old"


def test_landscape_variant_is_distinct():
    remote = {
        "templates": [
            {
                "name": "Dots",
                "filename": "Dots",
                "iconCode": "\ue9b9",
                "categories": ["Grids"],
            }
        ]
    }
    local = [
        {
            "name": "Dots",
            "filename": "Dots",
            "iconCode": "\ue9b9",
            "landscape": True,
            "categories": ["Grids"],
        }
    ]

    result = merge_registry(remote, local)
    assert len(result.added) == 1
    assert len(result.merged["templates"]) == 2
