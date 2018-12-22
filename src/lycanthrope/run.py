from lycanthrope.game import Game
from lycanthrope.irc import LycanthropeBot

if __name__ == "__main__":
    game = Game()
    bot = LycanthropeBot(
        roles=game.available_roles, tokens=game.available_tokens, game=game
    )
    game.bot = bot
    bot.start()
