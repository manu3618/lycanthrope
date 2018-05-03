"""IRC bot that interact with the game."""


async def notify_player(player, msg):
    """Deliver a message to a player.

    Args:
        player (string): nick of player to warn. If None, the message is
    delivered on default channel.
        msg (string): message to deliver.
    """
    raise NotImplemented


async def get_choice(player, choices):
    """Ask the player to make a choice.

    Args:
        player (string): nick of the player.
        choices (list or tuple): available choices.
    """
    # TODO:
    # * tell possible choices
    # * check return

    raise NotImplemented
