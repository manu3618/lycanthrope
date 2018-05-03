import asyncio
import tempfile
from collections import Counter
from random import choice, randint

import faker
import pytest

import lycanthrope
import mock
from lycanthrope import MAX_ROLE_NB

# Persistent file for IRC-base interactions
MOCK_IRC_FILE = tempfile.NamedTemporaryFile(prefix='lycanthrope-').name


def test_init():
    lycanthrope.Game()


def iter_name_lists(min_len=3, max_len=10):
    """Yield set of names.

    Args:
        max_len (int): maximal list length
    """
    names_gen = faker.Faker()
    for length in range(min_len, max_len):
        yield {names_gen.first_name() for _ in range(length)}


async def mock_get_choice(player, choices):
    """Simulate the get_choice()

    Args:
        player (string): unsused
        choices (list or tuple): availablechoices
    Return:
        A random choice
    """
    await asyncio.sleep(randint(0, 10)/100)
    cho = choice(choices)
    with open(MOCK_IRC_FILE, 'a') as fd:
        fd.write("- {} \tchoose \t{} \t({})\n".format(player, cho, choices))
    return cho


async def mock_notify_player(player, msg):
    """Simulate notify_player.

    Args:
        player (string): unused
        msg (string): message to deliver
    """
    await asyncio.sleep(randint(0, 10)/100)
    with open(MOCK_IRC_FILE, 'a') as fd:
        fd.write(" --> [{}]\t{}\n".format(player, msg))


@pytest.mark.parametrize('players', iter_name_lists())
def test_deal_role(players):
    """Deal roles among players and perform checks.

    Args:
        players (iterable): list/tuple/set of player's nick.
    """
    game = lycanthrope.Game()

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


@pytest.mark.asyncio
@pytest.mark.parametrize('players', iter_name_lists(9, 10))
async def test_notify_roles(players):
    """Test initial notification of players.

    Deal roles among players and then notitfy them.

    Args:
        players (string): players
    """
    with open(MOCK_IRC_FILE, 'a') as fd:
        fd.write("\n===== TEST test_notify_roles =====\n")

    with mock.patch('lycanthrope.notify_player', new=mock_notify_player):
        game = lycanthrope.Game()
        for player in players:
            game.add_player(player)
        game.deal_roles()
        await game.notify_player_roles()
        await game.notify_player_roles(False)
        if game.tasks:
            await asyncio.wait(game.tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize('players', iter_name_lists())
@pytest.mark.parametrize('run', range(5))
async def test_turns(players, run):
    """Test player turn."""
    with open(MOCK_IRC_FILE, 'a') as fd:
        fd.write("\n===== TEST test_turns =====\n")

    with mock.patch('lycanthrope.notify_player', new=mock_notify_player):
        with mock.patch('lycanthrope.get_choice', new=mock_get_choice):

            # init game
            game = lycanthrope.Game()
            for player in players:
                game.add_player(player)
            game.deal_roles()
            with open(MOCK_IRC_FILE, 'a') as fd:
                fd.write("Initial distribution: {}\n".format(
                    str(game.initial_roles)
                ))

            # execute turns
            turn_names = [name for name in game.__dir__()
                          if name.endswith('_turn')]
            for turn_name in turn_names:
                with open(MOCK_IRC_FILE, 'a') as fd:
                    fd.write("----- TEST {} -----\n".format(turn_name))
                ret = await getattr(game, turn_name)()
                if ret:
                    with open(MOCK_IRC_FILE, 'a') as fd:
                        fd.write("- switches: {}\n".format(str(ret)))
                if game.tasks:
                    await asyncio.wait(game.tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize('players', iter_name_lists())
@pytest.mark.parametrize('run', range(5))
async def test_night(players, run):
    """Test night."""
    with open(MOCK_IRC_FILE, 'a') as fd:
        fd.write("\n===== TEST night =====\n")

    with mock.patch('lycanthrope.notify_player', new=mock_notify_player):
        with mock.patch('lycanthrope.get_choice', new=mock_get_choice):

            # init game
            game = lycanthrope.Game()
            for player in players:
                game.add_player(player)
            game.deal_roles()
            with open(MOCK_IRC_FILE, 'a') as fd:
                fd.write("Initial distribution: {}\n".format(
                    str(game.initial_roles)
                ))

            await game.night()
            with open(MOCK_IRC_FILE, 'a') as fd:
                fd.write("Finale distribution: {}\n".format(
                    str(game.current_roles)
                ))

            # clean up
            if game.tasks:
                await asyncio.wait(game.tasks)
