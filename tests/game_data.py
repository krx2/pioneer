"""Game data many tests share, hand-written in the contracts' shapes: the iron chain from ore to
plates and rods (with its alternate ingot), refinery fuel, the buildings they run in, and a save's
factory making plates. The rates are the game's own."""

from pioneer.contracts import Building, ItemAmount, ProductionGraph, ProductionNode, Recipe

IRON_INGOT = Recipe(
    recipe_id="Recipe_IngotIron_C",
    name="Iron Ingot",
    building_ids=("Build_SmelterMk1_C",),
    inputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
)
PURE_IRON_INGOT = Recipe(
    recipe_id="Recipe_Alternate_PureIronIngot_C",
    name="Alternate: Pure Iron Ingot",
    building_ids=("Build_OilRefinery_C",),
    inputs=(
        ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=35),
        ItemAmount(item_id="Desc_Water_C", amount_per_minute=20),
    ),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=65),),
    is_alternate=True,
)
IRON_PLATE = Recipe(
    recipe_id="Recipe_IronPlate_C",
    name="Iron Plate",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=20),),
)
IRON_ROD = Recipe(
    recipe_id="Recipe_IronRod_C",
    name="Iron Rod",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=15),),
    outputs=(ItemAmount(item_id="Desc_IronRod_C", amount_per_minute=15),),
)
FUEL = Recipe(
    recipe_id="Recipe_LiquidFuel_C",
    name="Fuel",
    building_ids=("Build_OilRefinery_C",),
    inputs=(ItemAmount(item_id="Desc_LiquidOil_C", amount_per_minute=60),),
    outputs=(
        ItemAmount(item_id="Desc_LiquidFuel_C", amount_per_minute=40),
        ItemAmount(item_id="Desc_PolymerResin_C", amount_per_minute=30),
    ),
)

SMELTER = Building(
    building_id="Build_SmelterMk1_C",
    name="Smelter",
    power_consumption_mw=4,
    input_slots=1,
    output_slots=1,
)
CONSTRUCTOR = Building(
    building_id="Build_ConstructorMk1_C",
    name="Constructor",
    power_consumption_mw=4,
    input_slots=1,
    output_slots=1,
)


def iron_factory(*, smelters: float, constructors: float) -> ProductionGraph:
    """A save's factory making iron plates: its smelters and constructors, clock-scaled."""
    return ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="save_Recipe_IngotIron_C",
                recipe_id="Recipe_IngotIron_C",
                building_id="Build_SmelterMk1_C",
                machine_count=smelters,
                is_existing=True,
            ),
            ProductionNode(
                node_id="save_Recipe_IronPlate_C",
                recipe_id="Recipe_IronPlate_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=constructors,
                is_existing=True,
            ),
        ),
        flows=(),
    )
