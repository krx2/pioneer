"""Tests for the icon import, against a hand-made Docs fragment and a fake FModel export of a few
generated PNGs — none of the game's own files."""

from pathlib import Path

from PIL import Image

from pioneer.icons import icon_textures, import_icons, texture_for

_DOCS = [
    {
        "NativeClass": "/Script/CoreUObject.Class'/Script/FactoryGame.FGItemDescriptor'",
        "Classes": [
            {
                "ClassName": "Desc_IronPlate_C",
                "mSmallIcon": "Texture2D /Game/FactoryGame/Resource/Parts/IronPlate/UI/"
                "IconDesc_IronPlates_256.IconDesc_IronPlates_256",
            },
            {"ClassName": "Desc_Nothing_C", "mSmallIcon": "None"},
        ],
    },
    {
        "NativeClass": "/Script/CoreUObject.Class'/Script/FactoryGame.FGBuildingDescriptor'",
        "Classes": [
            {
                "ClassName": "Desc_SmelterMk1_C",
                "mSmallIcon": "None",
                "mPersistentBigIcon": "Texture2D /Game/FactoryGame/Buildable/Factory/SmelterMk1/"
                "UI/Smelter_512.Smelter_512",
            }
        ],
    },
]


def _png(path: Path, size: int = 256) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), (200, 120, 40, 255)).save(path)
    return path


def test_icon_textures_are_game_paths_by_class_id() -> None:
    textures = icon_textures(_DOCS)

    assert textures == {
        "Desc_IronPlate_C": "FactoryGame/Resource/Parts/IronPlate/UI/IconDesc_IronPlates_256",
        "Desc_SmelterMk1_C": "FactoryGame/Buildable/Factory/SmelterMk1/UI/Smelter_512",
    }


def test_a_placed_building_takes_its_descriptors_icon() -> None:
    textures = icon_textures(_DOCS)

    assert texture_for("Build_SmelterMk1_C", textures) == textures["Desc_SmelterMk1_C"]
    assert texture_for("Build_Unknown_C", textures) is None
    assert texture_for("Desc_Nothing_C", textures) is None


def test_import_scales_each_icon_down_and_names_it_by_class_id(tmp_path: Path) -> None:
    export = tmp_path / "Exports" / "FactoryGame" / "Content"
    _png(export / "FactoryGame/Resource/Parts/IronPlate/UI/IconDesc_IronPlates_256.png")
    _png(export / "FactoryGame/Buildable/Factory/SmelterMk1/UI/Smelter_512.png", size=512)
    out = tmp_path / "icons"

    report = import_icons(
        tmp_path / "Exports",
        ["Desc_IronPlate_C", "Build_SmelterMk1_C", "Desc_Nothing_C"],
        icon_textures(_DOCS),
        out,
        size=32,
    )

    assert report.imported == ("Desc_IronPlate_C", "Build_SmelterMk1_C")
    assert report.missing == (("Desc_Nothing_C", None),)
    for class_id in report.imported:
        with Image.open(out / f"{class_id}.png") as icon:
            assert icon.size == (32, 32)


def test_a_texture_is_found_by_name_and_its_folder_settles_a_tie(tmp_path: Path) -> None:
    right = _png(tmp_path / "Parts/IronPlate/UI/IconDesc_IronPlates_256.png", size=64)
    _png(tmp_path / "Somewhere/Else/IconDesc_IronPlates_256.png", size=16)

    import_icons(tmp_path, ["Desc_IronPlate_C"], icon_textures(_DOCS), tmp_path / "out", size=48)

    with Image.open(tmp_path / "out" / "Desc_IronPlate_C.png") as icon:
        assert icon.size == (48, 48)  # from the 64px one: the 16px one isn't scaled up
    assert right.exists()


def test_a_missing_texture_is_reported_with_the_path_it_wanted(tmp_path: Path) -> None:
    report = import_icons(tmp_path, ["Desc_IronPlate_C"], icon_textures(_DOCS), tmp_path / "out")

    assert report.imported == ()
    assert report.missing == (
        ("Desc_IronPlate_C", "FactoryGame/Resource/Parts/IronPlate/UI/IconDesc_IronPlates_256"),
    )
