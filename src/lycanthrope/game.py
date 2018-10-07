import asyncio
from collections import Counter
from random import shuffle

from .irc import get_choice, notify_player

# max number of player with the role
MAX_ROLE_NB = {
    "tanneur": 1,
    "chasseur": 1,
    "doppelgänger": 1,
    "loup garou": 2,
    "sbire": 1,
    "franc maçon": 2,
    "voyante": 1,
    "voleur": 1,
    "noiseuse": 1,
    "soulard": 1,
    "insomniaque": 1,
    "villageois": 3,
}

MANDATORY_ROLES = {"voyante", "loup garou"}


class Game:
    """Game


    /!\ players' nick must not change during the game.

    Attributes:
        role (list): list of roles played for this game
        players (list): list of  player's nick. The firs  players, identified
    by numbers, represent the role in the middle (not real players)
        initial_roles (dict): initial distribution of roles. Looks like:
    {player_nick: role}
        current_roles (dict): current distribution of roles.
    """

    _role_callbacks = {}

    def __init__(self):
        self.roles = []
        self.ante_initial_roles = {}  # before doppelganger
        self.initial_roles = {}  # after doppelganger
        self.current_roles = {}
        self.doppelganger_choice = ""
        self.doppelganger = ""
        self.votes = {}
        self.dead = []
        self.tasks = []  # tasks launched
        self.victories = Counter()
        self.bot = None
        self.in_progress = False
        self.dealer = {}
        self.role_swaps = []  # list of tuple of exchanged roles.
        self.dealer["Anarchie"] = deal_anarc
        self.dealer["Classique"] = deal_anarc

        # initialize players with roles in the middle
        self.players = [str(num) for num in range(3)]

    @classmethod
    def add_role(cls, role):
        """Decorator. Add role callback

        Args:
            role: name of the role.
        """
        def decorator(func):
            cls._role_callbacks[role] = func

            def wrapper(*args, **kwargs):
                """Perform action the role must perform

                Args:
                    phase (str): (dawn|night|day)
                    synchro (int): nb of synchronisation point for the phase.
                """
                return func(*args, **kwargs)
            return wrapper
        return decorator

    def add_player(self, nick):
        """Add the player <nick> to the game.

        Only works befor the game is started

        Args:
            nick (string): nick of the player
        """
        if len(self.players) >= sum(MAX_ROLE_NB.values()):
            raise RuntimeWarning("Maximum number of players reached")
        elif nick not in self.players:
            self.players.append(nick)
        else:
            raise ValueError("Player's nickname already used.")

    def remove_player(self, nick):
        """Remove a  player from the game.

        Only works when the game has not started.

        Args:
            nick (string): nick of the player
        """
        if self.in_progress:
            raise RuntimeError("Game in progress")
        elif nick in self.players:
            self.players.remove(nick)

    def deal_roles(self, scenario="Classique"):
        """Maps each player to a role."""
        nb_player = len(self.players)
        nb_role = nb_player + 3
        selected_roles = self.dealer[scenario](nb_role)
        shuffle(selected_roles)

        self.ante_initial_roles = dict(zip(self.players, selected_roles))
        self.initial_roles = self.ante_initial_roles.copy()
        self.current_roles = self.initial_roles.copy()
        self.dealt_roles = set(self.current_roles.values())

    async def notify_player_roles(self, initial=True):
        """Inform each player of its initial role.

        Args:
            initial (bool): if true, notify the initial roles, otherwise the
        current one.
        """
        if initial:
            roles = self.initial_roles
        else:
            roles = self.current_roles
        for player in self.players[3:]:
            msg = "Tu es {}".format(roles[player])
            self._fire_and_forget(notify_player(player, msg, self.bot))

    async def doppelganger_turn(self):
        """Execute the doppelgänger's turn."""
        player = self._get_player_nick("doppelgänger")
        if not player:
            return ("0", "0")

        choices = [adv for adv in self.players[3:] if adv != player]
        msg = "Choisis qui tu vas imiter cette nuit."
        await notify_player(player, msg, self.bot)
        choice = await get_choice(player, choices, self.bot)
        new_role = self.doppelganger_choice = self.initial_roles[choice]
        msg = "Tu es maintenant {}".format(new_role)
        self._fire_and_forget(notify_player(player, msg, self.bot))
        if new_role == "sbire":
            asyncio.ensure_future(self.sbire_turn(player))
            return ("0", "0")
        elif new_role in ("loup garou", "franc macon"):
            self.initial_roles[player] = self.initial_roles[choice]
            return ("0", "0")
        elif new_role == "voyante":
            self._fire_and_forget(self.voyante_turn(player))
            return ("0", "0")
        elif new_role == "voleur":
            return await self.voleur_turn(player)
        elif new_role == "noiseuse":
            return await self.noiseuse_turn(player)
        elif new_role == "soulard":
            return await self.soulard_turn(player)

    async def loup_garou_turn(self, active_players=None):
        """Execute loup garou's turn.

        This turn is independant of other turns.

        Args:
            active_players: players making the loup_garou turn. If None,
        the players are the one in initial distribution.
        """
        if active_players is None:
            active_players = self._get_player_nick(["loup garou"])
        if not active_players:
            return
        elif isinstance(active_players, list):
            # notify players in fire and forget mode
            msg = "Les loups garous sont {}.".format(
                " et ".join(active_players)
            )
            for player in active_players:
                self._fire_and_forget(notify_player(player, msg, self.bot))
        else:
            msg = (
                "Tu es le seul loup garou, indique une carte du milieu "
                "que tu veux découvrir. (0, 1 ou 2)"
            )
            await notify_player(active_players, msg, self.bot)
            choice = await get_choice(
                active_players, ("0", "1", "2"), self.bot
            )
            msg = "la carte {} est le rôle {}.".format(
                choice, self.initial_roles[choice]
            )
            self._fire_and_forget(notify_player(active_players, msg, self.bot))

    async def sbire_turn(self, sbire=None):
        """Execute sbire's turn.

        This turn is independant of other turns.
        """
        lgs = self._get_player_nick(["loup garou"])
        if sbire is None:
            sbire = self._get_player_nick(["sbire"])
        if sbire:
            if lgs:
                if isinstance(lgs, list):
                    msg = "Les loups garous sont {}.".format(" et ".join(lgs))
                else:
                    msg = "Le loup garou est {}.".format(lgs)
            else:
                msg = "il n'y a pas de loup garou."
            self._fire_and_forget(notify_player(sbire, msg, self.bot))

    async def franc_macon_turn(self):
        """Execute franc macon's turn.

        This turn is independant of the other turns.
        """
        frama = self._get_player_nick(["franc maçon"])
        if not frama:
            return
        elif isinstance(frama, list):
            msg = "Il y a 2 franc-maçons ({}).".format(" et ".join(frama))
            for player in frama:
                self._fire_and_forget(notify_player(player, msg, self.bot))
        else:
            msg = "Tu es le seul franc maçon."
            self._fire_and_forget(notify_player(frama, msg, self.bot))

    async def voyante_turn(self, voyante=None):
        """Execute the voyante's turn.

        This turn is independant of the other turns.

        Args:
            voyante: the player performing the action.

        """
        if voyante is None:
            voyante = self._get_player_nick(["voyante"])
        if not voyante:
            return

        msg = (
            "Quelle carte veux-tu voir? "
            "Si tu choisis une des cartes du milieu, tu pourras en regarder "
            "une autre du milieu."
        )
        await notify_player(voyante, msg, self.bot)
        choice = await get_choice(voyante, self.players, self.bot)
        msg = "Le role de {} est {}.".format(
            choice, self.ante_initial_roles[choice]
        )
        await notify_player(voyante, msg, self.bot)

        if choice in ("0", "1", "2"):
            msg = "Quelle carte veux tu regarder?"
            choice = await get_choice(voyante, ("0", "1", "2"), self.bot)
            msg = "Le role de {} est {}.".format(
                choice, self.ante_initial_roles[choice]
            )
            self._fire_and_forget(notify_player(voyante, msg, self.bot))

    async def voleur_turn(self, voleur=None):
        """Execute the voleur's turn.

        Args:
            player (string): the player performing the turn
        Return:
            tuple: the 2 roles to switch.
        """
        if voleur is None:
            voleur = self._get_player_nick(["voleur"])
        if not voleur:
            return ("0", "0")
        msg = "Choisis quelle carte tu veux voler."
        await notify_player(voleur, msg, self.bot)
        choice = await get_choice(
            voleur,
            [player for player in self.players[3:] if player != voleur],
            self.bot,
        )
        msg = "Ton nouveau rôle est {}".format(self.initial_roles[choice])
        self._fire_and_forget(notify_player(voleur, msg, self.bot))
        return (voleur, choice)

    async def noiseuse_turn(self, noiseuse=None):
        """Execute the noiseuse's turn.

        Return:
            tuple: the 2 roles to switch.
        """
        if noiseuse is None:
            noiseuse = self._get_player_nick(["noiseuse"])
        msg = (
            "Choisis les 2 personnes dont tu veux inverser les rôles.\n"
            "Première personne."
        )
        choice = [player for player in self.players[3:] if player != noiseuse]
        if not noiseuse:
            return ("0", "0")
        await notify_player(noiseuse, msg, self.bot)
        first = await get_choice(noiseuse, choice, self.bot)

        choice = [
            player
            for player in self.players[3:]
            if player not in (choice, noiseuse)
        ]
        msg = "Deuxième personne."
        await notify_player(noiseuse, msg, self.bot)
        second = await get_choice(noiseuse, choice, self.bot)
        return (first, second)

    async def soulard_turn(self, soulard=None):
        """Execute the soulard's turn.

        Return:
            tuple: the 2 roles to switch.
        """
        if soulard is None:
            soulard = self._get_player_nick(["soulard"])
        msg = "Choisis la carte que tu veux echanger avec toi-même."
        if not soulard:
            return ("0", "0")
        await notify_player(soulard, msg, self.bot)
        choice = await get_choice(soulard, ("0", "1", "2"), self.bot)
        return (soulard, choice)

    async def insomniaque_turn(self, doppel=False):
        """Execute insomniaque's turn.

        Must be executed after all switches are done.

        Args:
            doppel (bool): if true, indicate this is the turn of the
        doppelgänger.
        """
        if doppel:
            player = self._get_player_nick(["doppelgänger"])
        else:
            player = self._get_player_nick(["insomniaque"])
        if not player:
            return
        msg = "Ton rôle est à présent {}.".format(self.current_roles[player])
        await notify_player(player, msg, self.bot)

    async def victory(self):
        """Compute victory.

        Return:
            tuple: (winning side(string), winning players (list of nick))
        """
        meute = self._get_player_nick(["loup garou", "sbire"])
        villageois = self._get_player_nick(
            [
                "chasseur",
                "doppelgänger",
                "franc macon",
                "voyante",
                "voleur",
                "noiseuse",
                "soulars",
                "insomniaque",
                "villageois",
            ]
        )
        if villageois is None:
            villageois = []
        elif isinstance(villageois, str):
            villageois = [villageois]
        if isinstance(meute, str):
            meute = [meute]

        if not self.dead:
            if self._get_player_nick(["loup garou"]):
                return ("la meute", meute)
            else:
                return ("le village", villageois)

        if (
            self.current_roles[self.dead[0]] == "doppelgänger"
            and self.doppelganger_choice == "chasseur"
        ) or self.current_roles[self.dead[0]] == "chasseur":
            if not self.votes.get(self.dead[0]):
                msg = (
                    "Chasseur, tu es mort et tu n'as voté contre"
                    "personne pendant la nuit. "
                    "Qui veux-tu emporter avec toi?"
                )
                await notify_player(self.dead[0], msg, self.bot)
                await self._vote(self.dead[0])
            self.dead.append(self.votes[self.dead[0]])

        if self._get_player_nick(["tanneur"]) in self.dead:
            if self._get_player_nick("loup garou") and any(
                player in self.dead
                for player in self._get_player_nick(["loup garou"])
            ):
                return ("le tanneur", self._get_player_nick(["tanneur"]))
            else:
                return (
                    "le tanneur et le village",
                    villageois + [self._get_player_nick("tanneur")],
                )

        if self._get_player_nick(["loup garou"]) and any(
            player in self.dead
            for player in self._get_player_nick(["loup garou"])
        ):
            return ("le village", villageois)

        if "sbire" in self.dead:
            if self._get_player_nick("loup garou"):
                return ("la meute", meute)
            else:
                return ("le village", villageois)

        if self._get_player_nick("loup garou", "sbire"):
            return ("la meute", meute)
        else:
            return ("personne", None)

    async def vote(self):
        """Perform vote."""
        tasks = []
        for player in self.players:
            tasks.append(self._collect_vote(player))

        finished, _ = await asyncio.wait(tasks)
        for p, v in zip(player, finished):
            self.vote[p] = v.result()

    async def night(self):
        """Perform nigth steps.

        /!\ Assume roles are dealt.
        """
        if self.tasks:
            await asyncio.wait(self.tasks)
            self.tasks = []
        for role in self.dealt_roles:
            self._fire_and_forget(
                self._role_callbacks[role](self, phase='night', synchro=0)
            )

        if self.tasks:
            await asyncio.wait(self.tasks)
            self.tasks = []
        self.swap_roles()

        for sw in self.switches:
            (self.current_roles[sw[0]], self.current_roles[sw[1]]) = (
                self.current_roles[sw[1]],
                self.current_roles[sw[0]],
            )

        await self.doppelganger_turn()
        first_turns = (
            "loup_garou_turn",
            "sbire_turn",
            "franc_macon_turn",
            "voyante_turn",
            "voleur_turn",
            "noiseuse_turn",
            "soulard_turn",
        )
        for turn in first_turns:
            self._fire_and_forget(getattr(self, turn)())

        finished, _ = await asyncio.wait(self.tasks)
        switches = [turn.result() for turn in finished if turn.result()]

        for sw in switches:
            (self.current_roles[sw[0]], self.current_roles[sw[1]]) = (
                self.current_roles[sw[1]],
                self.current_roles[sw[0]],
            )
        self._fire_and_forget(self.insomniaque_turn())
        if self.doppelganger_choice == "insomniaque":
            self._fire_and_forget(self.insomniaque_turn(True))
        await asyncio.wait(self.tasks)

    def swap_roles(self, switches):
        """Perfomr swaps."""
        for sw in switches:
            (self.current_roles[sw[0]], self.current_roles[sw[1]]) = (
                self.current_roles[sw[1]],
                self.current_roles[sw[0]],
            )

    async def game(self, timeout=300):
        """Begin the game. Assume all players are registered.

        Args:
            delay (int): time to wait before starting the game
            timeout (int): maximal time (in seconds) before closing the votes.
        """
        if len(self.players) < 6 or len(self.players) > 13:
            msg = (
                "Le nombre de joueurs n'est pas bon. "
                "Il doit y avoir entre 3 et 10 joueurs, "
                "et non {}."
            ).format(str(len(self.players) - 3))
            await notify_player(None, msg, self.bot)
            return

        if self.in_progress:
            # the game has alrealdy begun.
            return

        # before night
        self.in_progress = True
        self.deal_roles()
        await self.notify_player_roles()

        msg = (
            "La nuit tombe sur le village. "
            "Cependant, certains joueurs accomplissent une action "
            "de manière furtive."
        )
        await notify_player(None, msg, self.bot)

        await self.night()

        # post-night action
        msg = (
            "Le jour se lève sur le village. Le vote est ouvert. "
            "Vous devez voter dans les {} prochaines secondes "
            "pour la personne que vous voulez tuer."
        ).format(str(timeout))
        await notify_player(None, msg, self.bot)
        await self.collect_votes(timeout)
        victory = await self.victory()
        self.in_progress = False

        if self.dead:
            msg = "Le village a tué {} personne{}: {}".format(
                len(self.dead),
                "s" if len(self.dead) > 1 else "",
                ", ".join(self.dead),
            )
        else:
            msg = "Le village n'a tué personne."
        await notify_player(None, msg, self.bot)

        # repr of winners
        if victory[1] and isinstance(victory[1], (list, tuple)):
            winners = ", ".join(victory[1])
        elif victory[1]:
            winners = victory[1]
        else:
            winners = ""

        msg = (
            "C'est {} qui gagne{}, " "c'est à dire les joueurs suivant: {}."
        ).format(
            victory[0] if victory[0] else "personne",
            "ent" if "et" in victory[0] else "",
            winners,
        )
        await notify_player(None, msg, self.bot)
        if winners:
            for player in victory[1]:
                self.victories[player] += 1
        msg = "Voici le nombre de victoires: {}".format(
            str(dict(self.victories))
        )
        await notify_player(None, msg, self.bot)
        await self.clean_up()

    async def clean_up(self):
        """run all remaining tasks."""
        if self.tasks:
            await asyncio.wait(self.tasks)

    async def collect_votes(self, timeout=10):
        """Perform votes and update dead.

        Args:
            timeout (int): maximal time to vote.
        """
        if self.tasks:
            await asyncio.wait(self.tasks)
            self.tasks = []
        for player in self.players[3:]:
            self._fire_and_forget(self._vote(player))
        delay = timeout / 5
        for run in range(1, 5):
            if not self.tasks:
                break
            done, pending = await asyncio.wait(self.tasks, timeout=delay)
            if pending:
                msg = "Il manque {} votes et il reste {} secondes.".format(
                    len(pending), str(timeout - delay * run)
                )
                await notify_player(None, msg, self.bot)
            self.tasks = list(pending)

        if self.tasks:
            done, pending = await asyncio.wait(self.tasks, timeout=delay)

        if pending:
            msg = (
                r"/!\ Certains n'ont pas eu le temps de voter. "
                "Tant pis pour eux."
            )
            for tsk in pending:
                tsk.cancel()
        self.tasks = []

        results = Counter(self.votes.values()).most_common()
        if not results:
            msg = "Il n'y a pas de mort aujourd'hui."
            await notify_player(None, msg, self.bot)
        elif len(results) > 1 and results[0][1] == results[1][1]:

            # Tie. 2nd vote:
            choice = [
                player for player, votes in results if votes == results[0][1]
            ]
            msg = (
                "Il y a égalité entre {}.\n"
                "En cas de nouvelle égalité, il n'y aura pas de mort.\n"
                "Vous avez {} secondes."
            ).format(" et ".join(choice), str(delay))

            await notify_player(None, msg, self.bot)
            self.votes = {}
            for player in self.players[3:]:
                self._fire_and_forget(self._vote(player, choice))
            done, pending = await asyncio.wait(self.tasks, timeout=delay)
            for tsk in pending:
                tsk.cancel()
            self.tasks = []
            results = Counter(self.votes.values()).most_common()

            if not results or (
                len(results) > 2 and results[0][0] == results[1][1]
            ):
                msg = "Il n'y a pas de mort cette nuit."
                await notify_player(None, msg, self.bot)
            return
        else:
            self.dead = [results[0][0]]
            msg = "Le village a décidé de tuer {}.".format(results[0][0])

    async def _vote(self, player, choice=None):
        """Collect vote for one player.

        Args:
            player (string): player voting
            choice (list or tuple): possible choices. If None, all
        other players.
        """
        if choice is None:
            choice = [
                adv
                for adv in self.players
                if adv not in ("0", "1", "2", player)
            ]
        msg = "Quelle personne veux-tu éliminer?"
        await notify_player(player, msg, self.bot)
        result = await get_choice(player, choice, self.bot)
        self.votes[player] = result

    async def _collect_vote(self, player):
        """Perform vote.

        Args:
            player (string): player that vote
        Return:
            the player's choice.
        """
        choices = [choice for choice in self.players[3:] if choice != player]
        msg = "Choisis qui tu veux tuer."
        await notify_player(player, msg, self.bot)
        return await get_choice(player, choices, self.bot)

    def _get_player_nick(self, roles, initial=True):
        """Return the nick of the playerhaving the role.

        Args:
            roles (list, tuple): list of role to search for
            initial (bool): if true, search into initial distribution,
        otherwise, search in current  distribution.
        """
        if initial:
            cur_roles = self.ante_initial_roles
        else:
            cur_roles = self.current_roles
        player = [
            nick
            for nick, role in cur_roles.items()
            if role in roles and nick not in ("0", "1", "2")
        ]
        if len(player) == 2:
            return player
        if len(player) == 1:
            return player[0]
        else:
            return None

    def _fire_and_forget(self, fun):
        """Launch a task in fire&forget mode.

        Args:
            fun: coroutine to launch.
        """
        tsk = asyncio.ensure_future(fun)
        self.tasks.append(tsk)


def deal_anarc(nb_role, max_role_nb=MAX_ROLE_NB, mandatory=MANDATORY_ROLES):
    """Deal role for the "Anarchie" scenario.

    Args:
        nb_role (int): number or role to deal.
        max_role_nb (dict): maximum number of characters per roles.
        mandatory (iterable): role that must be dealt.
    """
    roles = [role for role, nb in max_role_nb.items() for _ in range(nb)]
    shuffle(roles)
    selected_roles = roles[:nb_role]

    # deal manadatory roles if needed
    for i, role in enumerate(mandatory):
        if role not in selected_roles:
            selected_roles[i] = role

    return selected_roles


@Game.add_role('chasseur')
async def chasseur(game, phase="night", synchro=0):
    pass


@Game.add_role("doppelgänger")
async def doppelganger(game, phase="night", synchro=0):
    pass


@Game.add_role('franc maçon')
async def franc_macon(game, phase="night", synchro=0):
    pass


@Game.add_role('insomniaque')
async def insomniaque(game, phase="night", synchro=0):
    if phase == 'day':
        player = next(
            pl for pl, ro in game.initial_role.values() if ro == "insomiaque"
        )
        msg = "Ton rôle est à présent {}.".format(game.current_roles[player])
        await notify_player(player, msg)


@Game.add_role('loup garou')
async def loup_garou(game, phase="night", synchro=0):
    pass


@Game.add_role('noiseuse')
async def noiseuse(game, phase="night", synchro=0):
    pass


@Game.add_role('sbire')
async def sbire(game, phase="night", synchro=0):
    pass


@Game.add_role('soulard')
async def soulard(game, phase="night", synchro=0):
    pass


@Game.add_role('tanneur')
async def tanneur(game, phase="night", synchro=0):
    pass


@Game.add_role('villageois')
async def villageois(game, phase="night", synchro=0):
    pass


@Game.add_role('voleur')
async def voleur(game, phase="night", synchro=0):
    pass


@Game.add_role('voyante')
async def voyante(game, phase="night", synchro=0):
    pass
