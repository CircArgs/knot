"""Games domain — Studio, Platform, Game, Release, GameCredit.

Shares Person with every other domain (a person who designed a
game can also direct a movie). Adds three game-catalog sources:
igdb (deepest), giant_bomb (editorial), steam (PC-skewed,
shallowest credits).
"""

from knot import types
from movies_spec.base import person, spec

studio = spec.add_class("Studio")
studio.slot("name", types.TEXT, required=True)
studio.slot("country", types.TEXT)
studio.slot("founded_year", types.INTEGER)

platform = spec.add_class("Platform")
platform.slot("name", types.TEXT, required=True)
platform.slot("manufacturer", types.TEXT)

game = spec.add_class("Game")
game.slot("title", types.TEXT, required=True)
game.slot("release_year", types.INTEGER)
game.slot("studio", studio)
game.slot("genre", types.TEXT)
game.slot("platform_summary", types.TEXT)
game.slot("title_embedding", types.VECTOR(384))

release = spec.add_class("Release")
release.slot("game", game)
release.slot("platform", platform)
release.slot("release_date", types.DATE)
release.slot("region", types.TEXT)

game_credit = spec.add_class("GameCredit")
game_credit.slot("role", types.TEXT, required=True)
game_credit.slot("game", game)
game_credit.slot("person", person)

_GAME_CLASSES = [studio, platform, game, release, person, game_credit]
igdb = spec.add_source("igdb")
giant_bomb = spec.add_source("giant_bomb")
steam = spec.add_source("steam")
for _src, _w in [(igdb, 0.85), (giant_bomb, 0.80), (steam, 0.65)]:
    for _cls in _GAME_CLASSES:
        _src.bind(_cls).set_default_weight(_w)
