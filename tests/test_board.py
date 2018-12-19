import asyncio
import tempfile
from collections import Counter
from itertools import combinations, product
from os.path import dirname, join, realpath
from random import choice, randint

import faker
import mock
import pytest

import lycanthrope
from lycanthrope.game import get_scenario

# Persistent file for IRC-base interactions
MOCK_IRC_FILE = tempfile.NamedTemporaryFile(prefix="lycanthrope-").name
CONFIG_FILE = join(
    dirname(realpath(__file__)), "../src/lycanthrope/roles-scenario.yaml"
)
MAX_ROLE_NB = get_scenario(CONFIG_FILE)["loups-garous contre village"][
    "Classique"
]["max_nb"]


def test_init():
    lycanthrope.Game()


@pytest.fixture
async def game():
    """Return a game."""
    game = lycanthrope.Game()
    yield game
    await game.clean_up()


def iter_name_lists(min_len=3, max_len=10):
    """Yield set of names.

    Args:
        max_len (int): maximal list length
    """
    names_gen = faker.Faker()
    for length in range(min_len, max_len):
        yield {names_gen.first_name() for _ in range(length)}


async def mock_get_choice(player, choices, bot):
    """Simulate the get_choice()

    Args:
        player (string): unsused
        choices (list or tuple or set): available choices
    Return:
        A random choice
    """
    await asyncio.sleep(randint(0, 10) / 100)
    cho = choice(list(choices))
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("- {} \tchoose \t{} \t({})\n".format(player, cho, choices))
    return cho


async def mock_notify_player(player, msg, bot):
    """Simulate notify_player.

    Args:
        player (string): unused
        msg (string): message to deliver
    """
    await asyncio.sleep(randint(0, 10) / 100)
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write(" --> [{}]\t{}\n".format(player, msg))


def test_add_duplicate_player(game):
    """Test addition of dupplicate players raise exception."""
    players = ["a", "a"]
    with pytest.raises(ValueError):
        for player in players:
            game.add_player(player)


def test_more_player(game):
    """Test there cannot be more than 16 players."""
    players = [str(i) for i in range(4, 26)]
    with pytest.raises(RuntimeWarning):
        for player in players:
            game.add_player(player)
            assert len(game.players) < 17


@pytest.mark.parametrize("players", iter_name_lists())
def test_remove_player(players, game):
    """Test player removal."""
    for player in players:
        game.add_player(player)
    nick = choice(game.players)
    assert nick in game.players
    for _ in range(2):
        game.remove_player(nick)
        assert nick not in game.players


@pytest.mark.parametrize("players", iter_name_lists())
def test_remove_player_failure(players, game):
    """Test player removal after game start."""
    for player in players:
        game.add_player(player)
    nick = choice(game.players)
    assert nick in game.players
    game.in_progress = True
    with pytest.raises(RuntimeError):
        game.remove_player(nick)
    assert nick in game.players


@pytest.mark.parametrize("players", iter_name_lists())
def test_deal_role(players, game):
    """Deal roles among players and perform checks.

    Args:
        players (iterable): list/tuple/set of player's nick.
    """
    # Add players
    for player in players:
        game.add_player(player)
    assert len(game.players) == len(players) + 3

    # Roles exist
    game.deal_roles()
    dealt_roles = game.initial_roles
    for role in dealt_roles.values():
        assert role in MAX_ROLE_NB

    # Role number's constraints are respected
    dealt_roles_distr = Counter(dealt_roles.values())
    for role, nb in dealt_roles_distr.items():
        assert nb <= MAX_ROLE_NB[role]


def test_unknown_scenario(game):
    with pytest.raises(ValueError):
        game.set_scenario("unknown")


SCENARIO = {
    "Anarchie": {"min_players": 3, "max_player": 10},
    "Classique": {"min_players": 3, "max_player": 10},
}


@pytest.mark.parametrize("scenario,constraints", tuple(SCENARIO.items()))
def test_deal_scenario(scenario, constraints, game):
    """Test scenario deal.
    """
    for players in iter_name_lists(
        constraints.get("min_players", 3), constraints.get("max_players", 10)
    ):
        game.players = [0, 1, 2]
        for player in players:
            game.add_player(player)
        game.set_scenario(scenario)
        game.deal_roles()

        try:
            for role in constraints["mandatory"]:
                assert role in game.current_roles
        except KeyError:
            # no explicit mandatory role
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("players", iter_name_lists(9, 10))
async def test_notify_roles(players):
    """Test initial notification of players.

    Deal roles among players and then notitfy them.

    Args:
        players (string): players
    """
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("\n===== TEST test_notify_roles =====\n")

    with mock.patch("lycanthrope.game.notify_player", new=mock_notify_player):
        game = lycanthrope.Game()
        for player in players:
            game.add_player(player)
        game.deal_roles()
        await game.notify_player_roles()
        await game.notify_player_roles(False)
        if game.tasks:
            await asyncio.wait(game.tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("players", iter_name_lists())
@pytest.mark.parametrize("run", range(5))
async def test_turns(players, run):
    """Test player turn."""
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("\n===== TEST test_turns =====\n")

    with mock.patch("lycanthrope.game.notify_player", new=mock_notify_player):
        with mock.patch("lycanthrope.game.get_choice", new=mock_get_choice):

            # init game
            game = lycanthrope.Game()
            for player in players:
                game.add_player(player)
            game.deal_roles()
            with open(MOCK_IRC_FILE, "a") as fd:
                fd.write(
                    "Initial distribution: {}\n".format(
                        str(game.initial_roles)
                    )
                )

            # execute turns
            turn_names = [
                name for name in game.__dir__() if name.endswith("_turn")
            ]
            for turn_name in turn_names:
                with open(MOCK_IRC_FILE, "a") as fd:
                    fd.write("----- TEST {} -----\n".format(turn_name))
                ret = await getattr(game, turn_name)()
                if ret:
                    with open(MOCK_IRC_FILE, "a") as fd:
                        fd.write("- switches: {}\n".format(str(ret)))
                if game.tasks:
                    await asyncio.wait(game.tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("players", iter_name_lists())
@pytest.mark.parametrize("run", range(5))
async def test_night(players, run):
    """Test night."""
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("\n===== TEST night =====\n")

    with mock.patch("lycanthrope.game.notify_player", new=mock_notify_player):
        with mock.patch("lycanthrope.game.get_choice", new=mock_get_choice):

            # init game
            game = lycanthrope.Game()
            for player in players:
                game.add_player(player)
            game.deal_roles()
            with open(MOCK_IRC_FILE, "a") as fd:
                fd.write(
                    "Initial distribution: {}\n".format(
                        str(game.initial_roles)
                    )
                )

            await game.night()
            with open(MOCK_IRC_FILE, "a") as fd:
                fd.write(
                    "Finale distribution: {}\n".format(str(game.current_roles))
                )

            # clean up
            if game.tasks:
                await asyncio.wait(game.tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("players", iter_name_lists())
@pytest.mark.parametrize("run", range(5))
async def test_votes(players, run):
    """Test votes."""
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("\n===== TEST votes =====\n")

    with mock.patch("lycanthrope.game.notify_player", new=mock_notify_player):
        with mock.patch("lycanthrope.game.get_choice", new=mock_get_choice):

            # init game
            game = lycanthrope.Game()
            for player in players:
                game.add_player(player)
            game.deal_roles()

            await game.collect_votes(timeout=run / 10)

            with open(MOCK_IRC_FILE, "a") as fd:
                fd.write("votes: {}\n".format(str(game.votes)))
                fd.write("dead: {}\n".format(game.dead))

            # clean up
            if game.tasks:
                await asyncio.wait(game.tasks)


def distributions():
    """Yield interesting roles distributions."""
    center = {str(i): "villageois" for i in range(3)}
    base = {name: name for name, num in MAX_ROLE_NB.items() if num == 1}
    base.update({"lg" + str(i): "loup garou" for i in range(2)})
    base.update({"fm" + str(i): "franc maçon" for i in range(2)})

    for l in 3, 5, 10:
        for distr in combinations(base, l):
            ret = center.copy()
            ret.update({name: base[name] for name in distr})
            yield ret


@pytest.mark.asyncio
@pytest.mark.parametrize("distribution", distributions())
async def test_victory_happy_path(distribution):
    """Test victory computing."""
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("\n===== TEST victory =====\n")

    with mock.patch("lycanthrope.game.notify_player", new=mock_notify_player):
        with mock.patch("lycanthrope.game.get_choice", new=mock_get_choice):

            # init game
            game = lycanthrope.Game()
            real_players = [
                player
                for player in distribution
                if player not in ("0", "1", "2")
            ]

            for player in real_players:
                game.add_player(player)
            game.initial_roles = distribution.copy()
            game.current_roles = distribution.copy()

            for dead, doppel in product(game.players[3:] + [None], repeat=2):
                if dead:
                    game.dead = {dead}
                else:
                    game.dead = set()

                with open(MOCK_IRC_FILE, "a") as fd:
                    fd.write(
                        "distribution:{}\n".format(str(game.current_roles))
                    )
                    fd.write("dead:{}\n".format(game.dead))
                    fd.write("victory:{}\n".format(str(await game.victory())))


@pytest.mark.asyncio
@pytest.mark.parametrize("players", iter_name_lists())
@pytest.mark.parametrize("run", range(5))
async def test_game(players, run):
    """Test whole game."""
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("\n===== TEST game =====\n")

    with mock.patch("lycanthrope.game.notify_player", new=mock_notify_player):
        with mock.patch("lycanthrope.game.get_choice", new=mock_get_choice):

            # init game
            game = lycanthrope.Game()
            for player in players:
                game.add_player(player)
            game.deal_roles()

            # perform several games
            for _ in range(run):
                await game.game(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("players", iter_name_lists())
async def test_assassin(players):
    """Tes assassin."""
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("\n===== TEST assassin and dawn =====\n")

    with mock.patch("lycanthrope.game.notify_player", new=mock_notify_player):
        with mock.patch("lycanthrope.game.get_choice", new=mock_get_choice):

            # init game
            game = lycanthrope.Game()
            for player in players:
                game.add_player(player)
            game.deal_roles()
            assa = game.players[-1]
            game.initial_roles[assa] = "assassin"
            game.current_roles = game.initial_roles.copy()
            game.dealt_roles = set(game.current_roles.values())

            await game.dawn()
            await mock_notify_player(None, str(game.tokens), game.bot)


PLAYERS = [
    {"id": "empty", "winners": {"villageois"}},
    {
        "id": "simple",
        "dead": {"a"},
        "roles": {
            "a": "villageois",
            "b": "villageois",
            "c": "loup garou",
            "d": "loup garou",
            "e": "vampire",
            "f": "vampire",
        },
        "winners": {
            "Classique": {"monstres"},
            "Première nuit": {"monstres"},
            "Anarchie": set(),
        },
    },
    {
        "id": "chasseur dead",
        "dead": {"a"},
        "roles": {
            "a": "chasseur",
            "b": "villageois",
            "c": "loup garou",
            "d": "loup garou",
            "e": "vampire",
            "f": "vampire",
        },
        "winners": {
            "Classique": {"monstres"},
            "Première nuit": {"monstres"},
            "Anarchie": set(),
        },
    },
    {
        "id": "chasseur alive",
        "dead": {"b"},
        "roles": {
            "a": "chasseur",
            "b": "villageois",
            "c": "loup garou",
            "d": "loup garou",
            "e": "vampire",
            "f": "vampire",
        },
        "winners": {
            "Classique": {"monstres"},
            "Première nuit": {"monstres"},
            "Anarchie": set(),
        },
    },
    {
        "id": "tanneur dead",
        "dead": {"b"},
        "roles": {
            "a": "chasseur",
            "b": "tanneur",
            "c": "loup garou",
            "d": "loup garou",
            "e": "vampire",
            "f": "vampire",
        },
        "winners": {"tanneur"},
    },
    {
        "id": "tanneur alive",
        "dead": {"f"},
        "roles": {
            "a": "chasseur",
            "b": "tanneur",
            "c": "loup garou",
            "d": "loup garou",
            "e": "vampire",
            "f": "vampire",
        },
        "winners": {
            "Classique": {"monstres"},
            "Première nuit": {"monstres"},
            "Anarchie": set(),
        },
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("players", PLAYERS)
@pytest.mark.parametrize(
    "scenario", ["Classique", "Première nuit", "Anarchie"]
)
async def test_victory_walker(game, players, scenario):
    """Test victory walker.

    """
    for player in players.get("roles", []):
        game.add_player(player)
    game.initial_roles = players.get("roles", {}).copy()
    game.current_roles = players.get("roles", {}).copy()
    game.dead = list(players.get("dead", []))
    game.set_scenario(scenario)
    results = await game.victory()
    if isinstance(players["winners"], dict):
        assert players["winners"][scenario] == results[0]
    else:
        assert players["winners"] == results[0]


SCENARIOS = [name for name in get_scenario(CONFIG_FILE).values()]
PLAYERS = [players for players in iter_name_lists(max_len=20)]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SCENARIO)
@pytest.mark.parametrize("players", PLAYERS)
async def test_scenario(game, scenario, players):
    """Run game for the scenario."""
    with open(MOCK_IRC_FILE, "a") as fd:
        fd.write("\n===== TEST test_scenario ({}) =====\n".format(scenario))

    with mock.patch("lycanthrope.game.notify_player", new=mock_notify_player):
        with mock.patch("lycanthrope.game.get_choice", new=mock_get_choice):

            for player in players:
                try:
                    game.add_player(player)
                except RuntimeWarning:
                    # maxnumber of players reached
                    pass
            game.set_scenario(scenario)
            await game.game()
