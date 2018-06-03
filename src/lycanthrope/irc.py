"""IRC bot that interact with the game."""
import asyncio
import logging
import socket
import ssl
import yaml
from collections import defaultdict
from contextlib import suppress
from pprint import pformat

# TODO: put in conf.

CONNECTION_ARGS = {
    'server': 'chat.freenode.net',
    'port': 6697,
    'nickname': 'lycanthrope_bot',
    'chan': "#test_lycanthrope"
}

# messages sent by user to bot
# Last message from a player
PRIVMSGS = defaultdict(str)
# {playerr: asyncio.Condition()}
PRIVMSG_COND = defaultdict(lambda: asyncio.Condition())


async def notify_player(player, msg, bot=None):
    """Deliver a message to a player.

    Args:
        player (string): nick of player to warn. If None, the message is
    delivered on default channel.
        msg (string): message to deliver.
        bot (LycanthropeBot): IRC bot to use to speak
    """
    if bot:
        if player is None:
            await bot.send_to_chan(msg)
        else:
            await asyncio.sleep(1)
            await bot.send_priv_msg(player, msg)


async def get_choice(player, choices, bot=None):
    """Ask the player to make a choice.

    Args:
        player (string): nick of the player.
        choices (list or tuple): available choices.
    """
    # TODO:
    # * tell possible choices
    # * check return
    msg = "Tu dois choisir parmi les choix suivant: {}".format(
        ', '.join(choices)
    )
    await notify_player(player, msg, bot)
    # TODO await choice
    choice = await read_chan(player, bot)
    while choice not in choices:
        msg = ("Je n'ai pas compris. Tu dois choisir un et un seul des mots "
               "suvants: {}").format(', '.join(choices))
        await notify_player(player, msg, bot)
        choice = await read_chan(player, bot)
    return choice


async def read_chan(player, bot):
    """Return the last message sent by a player."""
    if bot:
        bot.logger.debug("privmsgs buff:" + pformat(PRIVMSGS))
        with await PRIVMSG_COND[player]:
            await PRIVMSG_COND[player].wait()
            return PRIVMSGS[player]


async def _buffer_privmsg(player, msg):
    """Buffer message for further processing.

    Put the private message into the correct dict and notify other processes.
    """
    with await PRIVMSG_COND[player]:
        PRIVMSGS[player] = msg
        PRIVMSG_COND[player].notify()


class LycanthropeBot():
    """IRC interface to lycanthrope game: command dispatcher.

    Attr:
        _callbacks (dict): functions that must be used to reat to commands
        host (string): IRC server's hostname
        nick (string): bot's nickname
        chan (string): IRN channel into wich operate
        bot (asyncirc.connect):
    """

    _callbacks = {}

    def __init__(self, game, roles=None, config='./config.yaml',
                 loglevel=logging.DEBUG, logfile='/tmp/lycanthrope.log'):
        """Init

        Args:
            game (lycanthrope.Game): the playable board.
            roles (dict): role descriptions.
            config (str): path to config file containig IRC conf.
            loglevel (int): logging level.
            logfile (str): path to log file.
        """
        self.game = game
        if roles is not None:
            self.role = roles
        else:
            self.role = {}

        # logging
        logging.basicConfig(filename=logfile, level=loglevel)
        self.logger = logging.getLogger()

        # IRCconnection
        with open(config) as conf:
            self.connect_param = yaml.load(conf.read())

    def _connect(self):
        '''Connect to the IRC server.'''
        plain_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock = ssl.wrap_socket(plain_sock)
        self.info('connecting with parameters '
                  + pformat(self.connect_param))
        self._sock.connect((self.connect_param['server'],
                            self.connect_param['port']))
        # self._sock.setblocking(False)
        if self.connect_param.get('password'):
            self._sock.send("PASS %s\r\n" % (self.connect_param['password']))
        msg = ("NICK {nick}\r\n"
               "USER {nick} 8 * :{nick}\r\n").format(
                    nick=self.connect_param['nickname'])
        self._sock.send(msg.encode())
        if self.connect_param.get('chan'):
            msg = ("JOIN {chan}\r\n"
                   "NOTICE {chan} :{msg}\r\n").format(
                        chan=self.connect_param['chan'],
                        msg="I'm in da place!"
                    )
            self._sock.send(msg.encode())

    def start(self):
        """Start the bot."""
        if not self.game:
            raise RuntimeError("no game instanciated")
        self._connect()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self._rcv_forever())

    def info(self, msg):
        '''Log a message.

        Args:
            msg: message to log.
        '''
        self.logger.info(msg)

    async def _rcv_forever(self):
        """Receive messages forever (mail loop).

        Yield:
            dict: user, message
        """
        loop = asyncio.get_event_loop()
        tsk = []
        while True:
            buf = ""
            pending = tsk = [
                asyncio.ensure_future(loop.sock_recv(self._sock, 1024))
            ]
            while pending:
                done, pending = await asyncio.wait(tsk, timeout=1)
                await asyncio.sleep(0)

            line = done.pop().result()
            buf += line.decode()
            for msg in buf.split('\n'):
                if not msg:
                    continue
                self.logger.debug("recv {}".format(msg))
                if msg.startswith('PING'):
                    self._sock.send('PONG {}'.format(msg).encode())
                parsed = _safe_parse(msg)
                if parsed:
                    self.logger.debug('parsed msg: {}'.format(str(parsed)))
                    if parsed['user'] in self.game.players:
                        await _buffer_privmsg(parsed['user'], parsed['msg'])
                    asyncio.ensure_future(
                        self.react(parsed['user'], parsed['msg'])
                    )

                    self.logger.debug("privmsgs buff:" + pformat(PRIVMSGS))
            await asyncio.sleep(0)

    async def react(self, user, message):
        """Process a message

        When !command [<args>] is found, find the appropriate callback
        """
        if not message.startswith('!'):
            return

        cmd, *args = message.split()
        cmd = cmd.lstrip('!')
        with suppress(KeyError):
            res = await self._callbacks[cmd](self, user, *args)
            await self.send_priv_msg(user, res)

    async def send_to_chan(self, msg):
        """Send a message to the main chan.

        Args:
            msg (str): message to send
        """
        line = "NOTICE {} :{}\r\n".format(self.connect_param['chan'], msg)
        self.logger.debug("sending " + line)
        self._sock.send(line.encode())

    async def send_priv_msg(self, user, msg):
        """Send a private message to a user.

        Args:
            user (str): the user
            msg (str): the message
        """
        if not msg:
            return
        for chunk in msg.split('\n'):
            line = "PRIVMSG {} :{}\r\n".format(user, chunk)
            self.logger.debug("sending " + line)
            self._sock.send(line.encode())

    @classmethod
    def register_cmd(cls, callback):
        '''
        decorator to register new callback

        callback should have the same name as the command
        It takes an instance of the calling LycanthropeBot object as its first
        argument and may accept an arbitrary number of positional arguments.
        '''
        cls._callbacks[callback.__name__] = callback
        return callback


@LycanthropeBot.register_cmd
async def ping(bot, user='', message='', *args):
    '''
    usage !ping <message>
    answer: "pong <nick> <message>"
    '''
    return "pong {} {}".format(user, message)


@LycanthropeBot.register_cmd
async def ls(bot, *args):
    '''
    list all available commands
    '''
    ret = "Available commands: {}".format(
        ', '.join(sorted(bot._callbacks.keys()))
    )
    return ret


@LycanthropeBot.register_cmd
async def help(bot, user, cmd=None, *args):
    '''
    usage: !help [cmd]
    '''
    if not cmd:
        return "try !help <cmd>\n" + ls(bot)
    cmd = bot._callbacks.get(cmd, None)
    if cmd in None:
        return
    doc = cmd.__doc__
    if not doc:
        return
    return '\n'.join(line.strip() for line in doc.strip.split('\n'))


@LycanthropeBot.register_cmd
async def play(bot, user, *args):
    '''
    Add the player to the game.
    '''
    bot.game.add_player(user)
    await bot.send_to_chan("{} fait maintenant parti des joueurs".format(user))


@LycanthropeBot.register_cmd
async def kill(bot, user, message=None, *args):
    '''
    Vote to kill a user.
    '''
    if not message:
        return "usage: !kill <player>"
    vote = message.strip()
    if vote in bot.game.players and not vote == user:
        bot.game.votes[user] = vote
        return "Ton vote contre {} est bien pris en compte.".format(vote)
    return "{} n'est pas un joueur valide.".format(vote)


@LycanthropeBot.register_cmd
async def role(bot, user, role=None, *args):
    '''
    usage: !role [role]

    Display role explanation. If none is provided, display role list.
    '''
    if args:
        role = role + ' ' + args[0]
    try:
        return pformat(bot.role[role])
    except KeyError:
        msg = "usage: !role [role]\nroles: {}".format(
            ', '.join(bot.role.keys())
        )
        return msg


@LycanthropeBot.register_cmd
async def start(bot, user, *args):
    '''
    start the game.
    '''
    await notify_player(None, "Le jeu démarre dans quelques secondes.", bot)
    await asyncio.sleep(10)
    asyncio.ensure_future(bot.game.game())
    await notify_player(None, "Le jeu démarre.", bot)


@LycanthropeBot.register_cmd
async def stop(bot, user, *args):
    '''
    stop participation.
    '''
    bot.game.remove_player(user)
    await bot.send_to_chan("Les joueurs sont: "
                           + ', '.format(bot.game.players))


@LycanthropeBot.register_cmd
async def remove(bot, user, nick, *args):
    '''
    usage: !remove <player>

    remove the player from the game.
    '''
    bot.game.remove_player(user)
    await bot.send_to_chan("Les joueurs sont: "
                           + ', '.format(bot.game.players))


def _safe_parse(msg):
    """Parse an irc message.

    Args:
        msg (str): raw message

    Return:
        dict: {'user': user, 'msg': message}
    """
    if 'PRIVMSG' in msg:
        user = msg.split(':')[1].split('!')[0]
        message = msg.split(':')[2].strip()
        return {'user': user, 'msg': message}
