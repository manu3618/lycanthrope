from lycanthrope import DESCR
from lycanthrope.game import Game
from lycanthrope.irc import LycanthropeBot


if __name__ == '__main__':
    game = Game()
    bot = LycanthropeBot(roles=DESCR, game=game)
    game.bot = bot
    bot.start()
