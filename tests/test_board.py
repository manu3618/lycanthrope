from collections import Counter

import faker
import pytest

import lycanthrope
from lycanthrope import MAX_ROLE_NB


def test_init():
    lycanthrope.Game()


def iter_name_lists(max_len=9):
    """Yield set of names.

    Args:
        max_len (int): maximal list length
    """
    names_gen = faker.Faker()
    for length in range(3, max_len):
        yield {names_gen.first_name() for _ in range(length)}


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
