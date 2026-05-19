"""Games domain — Studio + Platform + Game + Release + GameCredit.

Self-contained sub-spec (``part``). Shares ``person`` from
person.py via cross-domain import.
"""

from knot import Spec, types
from media_spec.person import person

part = Spec(identifier_slot_name="canonical_id")

studio = part.add_class("Studio")
studio.slot("name", types.TEXT, required=True)
studio.slot("country", types.TEXT)
studio.slot("founded_year", types.INTEGER)

platform = part.add_class("Platform")
platform.slot("name", types.TEXT, required=True)
platform.slot("manufacturer", types.TEXT)

game = part.add_class("Game")
game.slot("title", types.TEXT, required=True)
game.slot("release_year", types.INTEGER)
game.slot("studio", studio)
game.slot("genre", types.TEXT)
game.slot("platform_summary", types.TEXT)
game.slot("title_embedding", types.VECTOR(384))

release = part.add_class("Release")
release.slot("game", game)
release.slot("platform", platform)
release.slot("release_date", types.DATE)
release.slot("region", types.TEXT)

game_credit = part.add_class("GameCredit")
game_credit.slot("role", types.TEXT, required=True)
game_credit.slot("game", game)
game_credit.slot("person", person)

_GAME_CLASSES = [studio, platform, game, release, person, game_credit]
igdb = part.add_source("igdb")
giant_bomb = part.add_source("giant_bomb")
steam = part.add_source("steam")
for _src in [igdb, giant_bomb, steam]:
    for _cls in _GAME_CLASSES:
        _src.bind(_cls)
