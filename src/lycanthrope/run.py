import argparse

from lycanthrope.game import Game
from lycanthrope.irc import LycanthropeBot


def argparser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s", "--server", dest="server", default="chat.freenode.net"
    )
    parser.add_argument("-p", "--port", dest="port", default=6697, type=int)
    parser.add_argument(
        "-n",
        "--nick",
        dest="nickname",
        default="lycanthrope_bot",
        help="bot's nickname",
    )
    parser.add_argument(
        "-c",
        "--chan",
        dest="chan",
        default="#test_lycanthrope",
        help="chan used to play",
    )
    return parser


if __name__ == "__main__":
    game = Game()
    connection_attr = vars(argparser().parse_args())
    bot = LycanthropeBot(
        roles=game.available_roles,
        tokens=game.available_tokens,
        game=game,
        connect_conf=connection_attr,
    )
    game.bot = bot
    bot.start()
