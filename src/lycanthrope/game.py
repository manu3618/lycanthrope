import asyncio
from collections import Counter
from functools import partial
from itertools import chain
from os.path import dirname, join, realpath
from random import shuffle

import yaml

from .irc import get_choice, notify_player


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
        self.initial_roles = {}  # after doppelganger
        self.current_roles = {}
        self.votes = {}
        self.dead = []
        self.tasks = []  # tasks launched
        self.victories = Counter()
        self.bot = None
        self.in_progress = False
        self.dealer = {}
        self.role_swaps = []  # list of tuple of exchanged roles.
        self.scenario_dict = {}
        self.overall_max_nb = {}
        self.add_scenarii()
        self.set_scenario()

        # initialize players with roles in the middle
        self.players = [str(num) for num in range(3)]
        self.available_roles = get_roles("roles-scenario.yaml")

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

    def add_scenarii(self, filename="roles-scenario.yaml"):
        """Fill self.dealer with available scenario

        Args:
            filename (str) yaml containing scenario
        """
        scenario_list = get_scenario(filename)
        second_pass = []
        for scenario in scenario_list.values():
            if scenario is None:
                continue
            for name, params in scenario.items():
                if params is None:
                    second_pass.append(name)
                    continue
                self.scenario_dict[name] = params
                self.dealer[name] = get_dealer(**params)

        # update overall_max_nb
        self.overall_max_nb = total_max_role_nb(
            [scenar for _, scenar in self.scenario_dict.items()]
        )
        for name in second_pass:
            params = {"all_roles": self.overall_max_nb}
            self.scenario_dict[name] = params
            self.dealer[name] = get_dealer(**params)

    def set_scenario(self, scenario="Classique"):
        """Change actif scenario.
        """
        if scenario not in self.dealer:
            raise ValueError(
                "'{}' not in available scenario ({})".format(
                    scenario, ", ".join(self.dealer.keys())
                )
            )
        self.scenario = scenario
        if "roles" in self.scenario_dict[scenario]:
            self.max_role_nb = Counter(self.scenario_dict[scenario]["roles"])
        elif "max_nb" in self.scenario_dict[scenario]:
            self.max_role_nb = self.scenario_dict[scenario].get("max_nb", {})
        else:
            self.max_role_nb = self.overall_max_nb

    def set_random_scenario(self):
        """Randomly choose the scenario."""
        available_scenario = list(self.dealer.keys())
        shuffle(available_scenario)
        for scenario in available_scenario:
            self.scenario = scenario
            try:
                self.deal_roles()
                break
            except ValueError:
                pass

        self.notify_player(
            None, "Le scenario choisi aléatoirement est {}".format(scenario)
        )
        self.notify_player(
            None, "{}".format(self.scenario_list[scenario].get("description"))
        )

    def add_player(self, nick):
        """Add the player <nick> to the game.

        Only works befor the game is started

        Args:
            nick (string): nick of the player
        """
        if len(self.players) >= sum(self.max_role_nb.values()):
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

    def deal_roles(self):
        """Maps each player to a role."""
        roles = len(self.players)
        selected_roles = self.dealer[self.scenario](roles)
        shuffle(selected_roles)

        self.initial_roles = dict(zip(self.players, selected_roles))
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

        if self.current_roles[self.dead[0]] == "chasseur":
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

        # order roles:
        roles = [
            {name: descr}
            for name, descr in self.available_roles.items()
            if name in self.dealt_roles
        ]
        roles.sort(key=lambda x: str(x.get("order", "100")))

        # execute roles with synchro points
        for role in roles:
            if role.get("synchro") is not None:
                await self.clean_up()
                self.swap_roles(self.role_swaps)
                self.role_swaps = []

            self._fire_and_forget(
                self._role_callbacks[list(role)[0]](
                    self, phase="night", synchro=role.get("synchro")
                )
            )

        await self.clean_up()
        self.swap_roles(self.role_swaps)

    def swap_roles(self, switches):
        """Perform swaps."""
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
            cur_roles = self.initial_roles
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


def get_dealer(*args, **kwargs):
    """Return dealer function.

    Args:
        artefact (bool): use artefact
        token (bool): use token
        madatory (list): list of mandatory roles
        max_nb (dict): {available_role: number of players with this role}
        roles (list): roles to deal, truncable
        min_player (int): minimum number of players
        max_player (int): maximum number of players

    Return:
        function: dealer function
    """
    mandatory = kwargs.get("mandatory", ["loup garou", "voyante"])
    max_nb = kwargs.get("max_nb", {})
    roles = kwargs.get("roles", [])
    max_players = kwargs.get("max_players", 10)
    min_players = kwargs.get("min_players", 3)
    all_roles = kwargs.get("all_roles", {})

    def dealer_func(
        nb_roles, min_players, max_players, roles, max_nb, mandatory, all_roles
    ):
        """Select roles

        Args:
            nb_roles (int): number of roles to deal

        Returns:
            (list): selected roles in random  order.
        """
        print()
        from pprint import pprint; pprint(locals())
        if nb_roles < min_players + 3 or nb_roles > max_players + 3:
            raise ValueError(
                "Player number must be between {} and {}".format(
                    min_players, max_players
                )
            )
        if roles:
            # roles explicitly given
            shuffle(roles)
            return roles[: 3 + nb_roles]

        if not max_nb:
            # anarchie: all roles are available
            max_nb = all_roles

        # possible roles
        role = []
        for name, nb in max_nb.items():
            role.extend([name] * nb)
        role = role[: 3 + nb_roles]
        shuffle(role)

        # mandatory roles
        for i, name in enumerate(mandatory):
            if name not in role:
                role[i] = name
        shuffle(role)
        return role

    return partial(
        dealer_func,
        min_players=min_players,
        max_players=max_players,
        roles=roles,
        max_nb=max_nb,
        mandatory=mandatory,
        all_roles=all_roles,
    )


@Game.add_role("chasseur")
async def chasseur(game, phase="night", synchro=0):
    pass


@Game.add_role("franc maçon")
async def franc_macon(game, phase="night", synchro=0):
    """Execute franc macon's turn.

    This turn is independant of the other turns.
    """
    frama = game._get_player_nick(["franc maçon"])
    if not frama:
        return
    elif isinstance(frama, list):
        msg = "Il y a 2 franc-maçons ({}).".format(" et ".join(frama))
        for player in frama:
            game._fire_and_forget(notify_player(player, msg, game.bot))
    else:
        msg = "Tu es le seul franc maçon."
        game._fire_and_forget(notify_player(frama, msg, game.bot))


@Game.add_role("insomniaque")
async def insomniaque(game, phase="night", synchro=0):
    if phase == "day":
        player = next(
            pl for pl, ro in game.initial_role.values() if ro == "insomiaque"
        )
        msg = "Ton rôle est à présent {}.".format(game.current_roles[player])
        await notify_player(player, msg)


@Game.add_role("loup garou")
async def loup_garou(game, phase="night", synchro=0):
    loups = list(
        chain(
            game._get_player_nick(role)
            for role in ["loup alpha", "loup garou", "loup shaman"]
        )
    )
    loup_reveur = game._get_player_nick("loup rêveur")

    if not loups:
        return
    if isinstance(loups, list):
        msg = "Les {} loups garous sont {}.".format(
            str(len(loups)), " et ".join(loups)
        )
        for player in loups:
            game._fire_and_forget(notify_player(player, msg, game.bot))
    elif not loup_reveur:
        msg = (
            "Tu es le seul loup garou, indique une carte du milieu "
            "que tu veux découvrir. (0, 1 ou 2)"
        )
        await notify_player(loups, msg, game.bot)
        choice = await get_choice(loups, ("0", "1", "2"), game.bot)
        await game.clean_up()
        msg = "la carte {} est le rôle {}.".format(
            choice, game.initial_roles[choice]
        )
        game._fire_and_forget(notify_player(loups, msg, game.bot))

    if loup_reveur:
        msg = "Le loup rêveur est ." + loup_reveur
        for players in loups:
            game._fire_and_forget(notify_player(player, msg, game.bot))


@Game.add_role("noiseuse")
async def noiseuse(game, phase="night", synchro=0, noiseuse=None):
    """Execute the noiseuse's turn.

    Args:
        noiseuse (str): noiseuse's nickname

    Return:
        tuple: the 2 roles to switch.
    """
    if noiseuse is None:
        noiseuse = game._get_player_nick(["noiseuse"])
    if not noiseuse:
        return

    msg = (
        "Choisis les 2 personnes dont tu veux inverser les rôles.\n"
        "Première personne."
    )
    choice = [player for player in game.players[3:] if player != noiseuse]
    await notify_player(noiseuse, msg, game.bot)
    first = await get_choice(noiseuse, choice, game.bot)

    choice = [
        player
        for player in game.players[3:]
        if player not in (choice, noiseuse)
    ]
    msg = "Deuxième personne."
    await notify_player(noiseuse, msg, game.bot)
    second = await get_choice(noiseuse, choice, game.bot)

    game.role_swaps.append((first, second))


@Game.add_role("sbire")
async def sbire(game, phase="night", synchro=0):
    """Execute sbire's turn.

    This turn is independant of other turns.
    """
    loups = list(
        chain(
            game._get_player_nick(role)
            for role in [
                "loup alpha",
                "loup garou",
                "loup shaman",
                "loup rêveur",
            ]
        )
    )
    sbire = game._get_player_nick("sbire")
    if sbire and loups:
        msg = "Il y a {} loup garou ({}).".format(
            len(loups), ", ".format(loups)
        )
        game._fire_and_forget(notify_player(sbire, msg, game.bot))


@Game.add_role("soulard")
async def soulard(game, phase="night", synchro=0):
    """Execute the soulard's turn."""
    soulard = game._get_player_nick(["soulard"])
    if not soulard:
        return

    msg = "Choisis la carte que tu veux echanger avec toi-même."
    await notify_player(soulard, msg, game.bot)
    choice = await get_choice(soulard, ("0", "1", "2"), game.bot)
    game.role_swaps.append((soulard, choice))


@Game.add_role("tanneur")
async def tanneur(game, phase="night", synchro=0):
    pass


@Game.add_role("villageois")
async def villageois(game, phase="night", synchro=0):
    pass


@Game.add_role("voleur")
async def voleur(game, phase="night", synchro=0):
    """Execute the voleur's turn."""
    voleur = game._get_player_nick(["voleur"])
    if not voleur:
        return
    msg = "Choisis quelle carte tu veux voler."
    await notify_player(voleur, msg, game.bot)
    choice = await get_choice(
        voleur,
        [player for player in game.players[3:] if player != voleur],
        game.bot,
    )
    msg = "Ton nouveau rôle est {}".format(game.initial_roles[choice])
    game._fire_and_forget(notify_player(voleur, msg, game.bot))
    return game.role_swaps.append((voleur, choice))


@Game.add_role("voyante")
async def voyante(game, phase="night", synchro=0):
    """Execute the voyante's turn.

    This turn is independant of the other turns.
    """
    voyante = game._get_player_nick(["voyante"])
    if not voyante:
        return

    msg = (
        "Quelle carte veux-tu voir? "
        "Si tu choisis une des cartes du milieu, tu pourras en regarder "
        "une autre du milieu."
    )
    await notify_player(voyante, msg, game.bot)
    choice = await get_choice(voyante, game.players, game.bot)
    msg = "Le role de {} est {}.".format(choice, game.initial_roles[choice])
    await notify_player(voyante, msg, game.bot)

    if choice in ("0", "1", "2"):
        msg = "Quelle carte veux tu regarder?"
        choice = await get_choice(voyante, ("0", "1", "2"), game.bot)
        msg = "Le role de {} est {}.".format(
            choice, game.initial_roles[choice]
        )
        game._fire_and_forget(notify_player(voyante, msg, game.bot))


def get_param(filename, param):
    with open(join(dirname(realpath(__file__)), filename)) as fp:
        return yaml.load(fp.read()).get(param)


get_roles = partial(get_param, param="characters")
get_scenario = partial(get_param, param="scenario")


def max_dict(dicts):
    """Merge dicts whose values are int.

    if key is present in multiple dict, choose the max.

    Args:
        dicts (list): list of dicts to merge

    Retrun:
        (dict) merged dict
    """
    ret = {}
    for cur_dict in dicts:
        for key, value in cur_dict.items():
            ret[key] = max(d.get(key, 0) for d in dicts)
    return ret


def total_max_role_nb(scenario):
    """Return all roles and its max  number ofoccurence for any scenario.

    Arg:
        scenario (list): list of scenario
    """
    perso_counts = [s["max_nb"] for s in scenario if s and "max_nb" in s]
    perso_counts.extend(
        [Counter(s["roles"]) for s in scenario if s and "roles" in s]
    )
    return max_dict(perso_counts)
