import asyncio
from collections import Counter, defaultdict
from functools import partial
from itertools import chain
from os.path import dirname, join, realpath
from random import shuffle

import yaml

from .irc import get_choice, notify_player


class Game:
    r"""Game


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
    _initial_tokens = {
        "assassin": "assassin",
        "chauve-souris": "chauve-souris",
        "peur": "peur",
        "peste": "peste",
        "clarté 0": "clarté",
        "clarté 1": "clarté",
        "vampire": "vampire",
        "traitre": "traitre",
        "amour 0": "amour",
        "amour 1": "amour",
    }

    def __init__(self, config_file="roles-scenario.yaml"):
        self.roles = []
        self.initial_roles = {}  # after doppelganger
        self.current_roles = {}
        self.votes = {}
        self.dead = {}
        self.tasks = []  # tasks launched
        self.victories = Counter()
        self.bot = None
        self.in_progress = False
        self.activated = set()  # set of player that made an action
        self.dealer = {}
        self.role_swaps = []  # list of tuple of exchanged roles.
        self.tokens = defaultdict(lambda: "clareté")
        self.tokens.update(self._initial_tokens)
        self.victory_tree = victory_tree()
        self.scenario_dict = {}
        self.overall_max_nb = {}
        self.add_scenarii()
        self.set_scenario()

        # initialize players with roles in the middle
        self.players = [str(num) for num in range(3)]
        self.available_roles = get_roles(config_file)
        self.available_tokens = get_token(config_file)

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
        for family, scenario in scenario_list.items():
            if scenario is None:
                continue
            for name, params in scenario.items():
                if params is None:
                    second_pass.append((family, name))
                    continue
                params["family"] = family
                self.scenario_dict[name] = params
                self.dealer[name] = get_dealer(**params)

        # update overall_max_nb
        self.overall_max_nb = total_max_role_nb(
            [scenar for _, scenar in self.scenario_dict.items()]
        )
        for family, name in second_pass:
            params = {"all_roles": self.overall_max_nb, "family": family}
            self.scenario_dict[name] = params
            self.dealer[name] = get_dealer(**params)

    def set_scenario(self, scenario="Classique"):
        """Change actif scenario."""
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

    async def set_random_scenario(self):
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

        distributed_roles = set(self.initial_roles.values())
        msgs = [
            f"Le scenario choisi aléatoirement est {scenario}",
            self.scenario_dict[scenario].get("description", ""),
            "Les roles distribués sont " + ", ".join(distributed_roles),
        ]
        for msg in msgs:
            await notify_player(None, msg, self.bot)

    def add_player(self, nick):
        """Add the player <nick> to the game.

        Only works before the game is started

        Args:
            nick (string): nick of the player
        """
        if len(self.players) >= sum(self.max_role_nb.values()):
            raise RuntimeWarning("Maximum number of players reached")
        if nick in self.tokens:
            raise ValueError("This nickname cannot be used.")
        self.players.append(nick)
        self.tokens[nick] = "clarté"

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
        scenar_constr = self.scenario_dict[self.scenario]
        if any(
            [
                roles - 3 < scenar_constr.get("min_player", 3),
                roles - 3 > scenar_constr.get("max_player", 19),
            ]
        ):
            raise ValueError("Wrong number of players for this scenario.")

        selected_roles = self.dealer[self.scenario](roles)
        shuffle(selected_roles)
        self.initial_roles = dict(zip(self.players, selected_roles))
        self.current_roles = self.initial_roles.copy()
        self.dealt_roles = set(self.current_roles.values())
        self.tokens.update({player: "clareté" for player in self.players})
        self.tokens.update(self._initial_tokens)

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
            msg = f"Tu es {roles[player]}"
            self._fire_and_forget(notify_player(player, msg, self.bot))

    async def victory_walker(self, groups, state_name="start", winners=None):
        """
        Args:
            groups (dict)
            state_name (str)
            winners (set)

        Return:
            set: winner groups
        """
        if winners is None:
            winners = set()
        state = self.victory_tree[state_name]

        for fam in (
            "bataille épique",
            "loups-garous-contre village",
            "vampires contre village",
        ):
            if fam in state:
                scenar_fam = self.scenario_dict[self.scenario].get("family")
                try:
                    next_step = state[fam][scenar_fam == fam]
                    break
                except KeyError:
                    pass
                if scenar_fam == fam:
                    state = state[fam]
        else:
            try:
                state = state["classic"]
            except KeyError:
                pass

        if "has-dead" in state:
            next_step = state["has-dead"].get(len(self.dead) != 0)
        if "dead" in state:
            check = list(state["dead"].keys())[0]
            checked_player = self._get_player_nick([check])
            if checked_player:
                if isinstance(checked_player, str):
                    checked_player = [checked_player]
                next_step = state["dead"][check][
                    any(player in self.dead for player in checked_player)
                ]
            else:
                next_step = state["dead"][check][False]
        if "exist" in state:
            group = state["exist"]
            group_name, actions = list(group.items())[0]
            next_step = actions[len(groups[group_name]) != 0]

        if "victory" in state:
            winners.add(state["victory"])
        if "action" in state:
            await self._role_callbacks[state["action"]](self, phase="day")
        if "next" in state:
            next_step = state["next"]
        if "token" in state:
            check = list(state["token"].keys())[0]
            if check in self.dead:
                next_step = state["token"][check]["dead"]
            else:
                next_step = state["token"][check]["else"]

        if "end" in state:
            return winners
        else:
            return await self.victory_walker(groups, next_step, winners)

    async def victory(self):
        """Compute victory.

        Return:
            tuple: (winning side(string), winning players (list of nick))
        """
        groups = defaultdict(set)
        for name, descr in self.available_roles.items():
            groups[descr.get("group", "villageois")].add(name)

        group_member = defaultdict(set)
        for group_name in groups:
            members = self._get_player_nick(groups[group_name])
            if isinstance(members, str):
                members = {members}
            group_member[group_name] = members if members else set()
        group_member["monstres"].update(group_member.get("loups garous", set()))

        # check vampire token
        vamp = [player for player in self.players if self.tokens[player] == "vampire"]
        if vamp:
            for group_name in groups:
                try:
                    group_member[group_name].remove(vamp[0])
                except KeyError:
                    pass
            group_member["vampire"].add(vamp[0])

        group_member["monstres"].update(group_member.get("vampire", set()))
        winners = await self.victory_walker(group_member)

        looser = await self._role_callbacks["pestiféré"](self, phase="day")

        return (
            winners,
            [
                member
                for group in winners
                for member in group_member[group]
                if member not in looser
            ],
        )

    async def vote(self):
        """Perform vote."""
        tasks = []
        for player in self.players:
            tasks.append(self._collect_vote(player))

        finished, _ = await asyncio.wait(tasks)
        for p, v in zip(player, finished):
            self.votes[p] = v.result()

    async def dawn(self):
        """Perform down steps.

        Assume roles are dealt.
        """
        await self.clean_up()

        # order roles:
        roles = [
            {name: descr}
            for name, descr in self.available_roles.items()
            if name in self.dealt_roles
        ]
        self.token_swaps = []  # tuples (order, token0, token1)

        # execute roles with synchro points
        for role in roles:
            self._fire_and_forget(
                self._role_callbacks[list(role)[0]](self, phase="dawn")
            )
        await self.clean_up()

        self.token_swaps.sort(key=lambda x: str(x[0]))
        for _, s0, s1 in self.token_swaps:
            self.tokens[s0], self.tokens[s1] = self.tokens[s1], self.tokens[s0]

    async def night(self):
        r"""Perform night steps.

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
        if not self.scenario:
            self.set_scenario()
        if len(self.players) < 6:
            msg = (
                "Le nombre de joueurs n'est pas bon. "
                "Il doit y avoir au moins 3 joueurs "
                "et non {}."
            ).format(str(len(self.players) - 3))
            await notify_player(None, msg, self.bot)
            return
        max_nb = sum(self.max_role_nb.values())
        if len(self.players) > max_nb:
            msg = (
                "Le nombre de joueurs n'est pas bon. "
                "Il doit y avoir au plus {} joueurs pour le scenario {} "
                "et non {}."
            ).format(max_nb, self.scenario, str(len(self.players) - 3))
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
            "Arrive le crépuscule. Pour certains joueurs, c'est une période "
            "d'activité."
        )
        await notify_player(None, msg, self.bot)

        await self.dawn()

        # notify token
        for nick in self.players[3:]:
            msg = f"Tu possède la marque suivante: {self.tokens[nick]}"
            self._fire_and_forget(notify_player(nick, msg, self.bot))
        await self._role_callbacks["amoureux"](self, phase="night")
        await self.clean_up()

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
        msg = (
            "Le{sg} groupe{sg} gagnant{sg} {v}: {g}, "
            "c'est à dire le{sj} joueur{sj} suivant{sj}: {j}."
        ).format(
            sg="s" if victory[0] else "",
            v="sont" if len(victory[0]) > 1 else "est",
            g=", ".join(victory[0]) if victory[0] else "personne",
            sj="s" if victory[1] else "",
            j=", ".join(victory[1]) if victory[1] else "personne",
        )
        await notify_player(None, msg, self.bot)

        traitre = [
            nick
            for nick, value in self.tokens.items()
            if value == "traitre" and nick in self.players[:3]
        ]

        if traitre:
            groups = defaultdict(set)
            for name, descr in self.available_roles.items():
                groups[descr.get("group", "villageois")].add(name)
            traitre_grp = self.available_roles[traitre[0]]["group"]
            traitre_grp_members = self._get_player_nick(groups[traitre_grp])

            msg = f"Il y avait un traire ({traitre[0]})"
            await notify_player(None, msg, self.bot)

            if any(x in self.dead for x in traitre_grp_members):
                victory[1].add(traitre[0])
                msg = "Le traitre gagne également."
            else:
                msg = "Le traitre ne gagne pas."
                try:
                    victory[1].remove(traitre[0])
                except KeyError:
                    pass
            await notify_player(None, msg, self.bot)

        if victory[1]:
            for player in victory[1]:
                self.victories[player] += 1
        msg = f"Voici le nombre de victoires: {str(dict(self.victories))}"
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
            msg = r"/!\ Certains n'ont pas eu le temps de voter. " "Tant pis pour eux."
            for tsk in pending:
                tsk.cancel()
        self.tasks = []

        raw_results = Counter(self.votes.values())

        protect_tasks, _ = await asyncio.wait(
            [
                asyncio.ensure_future(self._role_callbacks[role](self, phase="day"))
                for role in ("garde du corps", "le maître")
            ]
        )
        protected_players = [tsk.result() for tsk in protect_tasks]
        for player in protected_players:
            try:
                raw_results.pop(player)
            except KeyError:
                pass

        results = raw_results.most_common()
        if not results:
            msg = "Il n'y a pas de mort aujourd'hui."
            await notify_player(None, msg, self.bot)
        elif len(results) > 1 and results[0][1] == results[1][1]:
            # Tie. 2nd vote:
            choice = [player for player, votes in results if votes == results[0][1]]
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

            if not results or (len(results) > 2 and results[0][0] == results[1][1]):
                msg = "Il n'y a pas de mort cette nuit."
                await notify_player(None, msg, self.bot)
            return
        else:
            self.dead = {results[0][0]}
            msg = f"Le village a décidé de tuer {results[0][0]}."

    async def _vote(self, player, choice=None):
        """Collect vote for one player.

        Args:
            player (string): player voting
            choice (list or tuple): possible choices. If None, all
        other players.
        """
        if choice is None:
            choice = [adv for adv in self.players if adv not in ("0", "1", "2", player)]
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

    def _get_player_nick(self, roles, initial=True, check_afraid=False):
        """Return the nick of the player having the role.

        Args:
            roles (list, tuple): list of role to search for
            initial (bool): if true, search into initial distribution,
        otherwise, search in current  distribution.
            check_afraid (bool): if true, donnot return players with "peur"
        token.
        """
        if initial:
            cur_roles = self.initial_roles
        else:
            cur_roles = self.current_roles
        player = [
            nick
            for nick, role in cur_roles.items()
            if all(
                [
                    role in roles,
                    nick not in ("0", "1", "2"),
                    not check_afraid or self.tokens[nick] != "peur",
                ]
            )
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
    max_players = kwargs.get("max_players", 19)
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


@Game.add_role("amoureux")
async def amoureux(game, phase, synchro=""):
    amoureux = {
        nick
        for nick, value in game.tokens.items()
        if value == "amour" and nick in game.players[:3]
    }
    if phase == "night":
        if amoureux:
            msg = "L{s}amoureu{x} {v} {a}".format(
                s="es " if len(amoureux) > 1 else "'",
                x="x" if len(amoureux) > 1 else "",
                v="sont" if len(amoureux) > 1 else "est",
                a=", ".join(amoureux),
            )
            for nick in amoureux:
                game._fire_and_forget(notify_player(nick, msg, game.bot))
    if phase != "day":
        if any(x in game.dead for x in amoureux):
            game.dead.add(set(amoureux))
            msg = "Les amoureux {} meurent ensemble.".format(" et ".join(amoureux))
            game._fire_and_forget(notify_player(None, msg, game.bot))


@Game.add_role("assassin")
async def assassin(game, phase, synchro="-1"):
    if phase != "dawn":
        return
    msg = "Avec qui vas-tu échanger la marque de l'assassin ?"
    assa = game._get_player_nick(["assassin"])
    if not assa:
        return

    await notify_player(assa, msg, game.bot)
    choice = await get_choice(assa, game.players[3:], game.bot)
    game.token_swaps.append((synchro, "assassin", choice))
    game.activated.add(assa)


@Game.add_role("chasseur")
async def chasseur(game, phase="night", synchro=0):
    if phase != "day":
        return
    chas = game._get_player_nick(["chasseur"])
    if not chas:
        return
    msg = "Chasseur tu es mort."
    await notify_player(chas, msg, game.bot)
    if not game.votes.get(chas) or game.votes[chas] in game.dead:
        msg = "Tu dois immédiatement tuer un joueur non mort."
        alive = {player for player in game.players[3:] if player not in game.dead}
        dead = await get_choice(chas, alive, game.bot)
        game.dead.add(dead)
        msg = "Dans un dernier élan de vie, le chasseur tue " + dead
        game._fire_and_forget(notify_player(None, msg, game.bot))


@Game.add_role("chasseur de fantômes")
async def chasseur_fantome(game, phase="night", synchro=0):
    if phase != "night":
        return
    cha = game._get_player_nick(["chasseur de fantôme"], check_afraid=True)
    if not cha:
        return

    players = set(game.players[3:])
    players.pop(cha)
    for _ in range(2):
        msg = "Veux-tu voir une carte ?"
        await notify_player(cha, msg, game.bot)
        choice = await get_choice(cha, ("oui", "non"), game.bot)
        if choice == "non":
            return

        game.activated.add(cha)
        msg = "Quel joueur veux-tu regarder ?"
        choice = await get_choice(cha, players, game.bot)
        players.pop(choice)
        role = game.current_roles[choice]
        msg = f"{choice} est {role}."
        await notify_player(cha, msg, game.bot)

        if "loup" in role or "vampire" in role or role == "tanneur":
            msg = f"Tu deviens toi-même un {role}."
            game._fire_and_forget(notify_player(cha, msg, game.bot))
            game.current_roles[cha] = role
            return


@Game.add_role("la chose")
async def chose(game, phase="night", synchro=0):
    if not phase == "night":
        return
    player_li = game.players[3:]
    player_pos = {nick: pos for pos, nick in enumerate(player_li)}
    player_nb = len(player_li)
    chose = game._get_player_nick(["la chose"], check_afraid=True)
    if not chose:
        return

    game.activated.add(chose)
    neigh = [player_li[(player_pos[chose] + i) % player_nb] for i in (-1, 1)]
    msg = "Tes voision sont {} et {}. Lequel veux-tu touccher ?".format(
        neigh[0], neigh[1]
    )
    touched = await get_choice(chose, neigh, game.bot)

    neigh = [player_li[(player_pos[touched] + i) % player_nb] for i in (-1, 1)]
    msg = (
        "Tes voisins sont {} et {}, et l'un d'eux est la chose "
        "et la chose t'as touché."
    ).format(neigh[0], neigh[1])
    game._fire_and_forget(notify_player(touched, msg, game.bot))


@Game.add_role("le comte")
async def comte(game, phase="dawn", synchro="-6B"):
    com = game._get_player_nick(["le comte"])
    if phase != "dawn" or not com:
        return

    game.activated.add(com)
    players = set(game.players[3:])
    players.remove(com)
    msg = (
        "Quel joueur veux-tu effrayer en échangeant son jeton "
        "contre le jeton de peur ?"
    )
    await notify_player(com, msg, game.bot)
    choice = await get_choice(com, players, game.bot)
    game.token_swaps.append((synchro, "peur", choice))


@Game.add_role("comploteuse")
async def comploteuse(game, phase="dawn", synchro="-3"):
    if phase != "dawn":
        return
    comp = game._get_player_nick(["comploteuse"])
    if not comp:
        return
    game.activated.add(comp)
    msg = "Contre qui va tu changer la marque du traitre ?"
    await notify_player(comp, msg, game.bot)
    choice = await get_choice(comp, game.player[:3], game.bot)
    game.token_swaps.append((synchro, "traitre", choice))


@Game.add_role("cupidon")
async def cupidon(game, phase="dawn", synchro="-4"):
    if phase != "dawn":
        return
    cup = game._get_player_nick(["cupidon"])
    if not cup:
        return
    msg = "Tu peux rendre amoureux 2 personnes. "
    await notify_player(cup, msg, game.bot)
    msg = (
        'Choisis le 1er amoureux. choisi "aucun" si tu ne veux rendre '
        "personne amoureux."
    )
    await notify_player(cup, msg, game.bot)
    choice = await get_choice(cup, chain(["aucun"], game.players[:3]), game.bot)
    if choice == "aucun":
        return
    game.activated.add(cup)
    game.token_swaps.append((synchro, "amour 0", choice))

    msg = "Qui sera le deuxième aoureux?"
    await notify_player(cup, msg, game.bot)
    choice = await get_choice(cup, game.players[:3], game.bot)
    game.token_swaps.append((synchro, "amour 1", choice))


@Game.add_role("diseuse de bonnes aventures")
async def diseuse(game, phase="night", synchro=0):
    diseuse = game._get_player_nick(["diseuse de bonnes aventures"])
    if phase != "night" or not diseuse:
        return
    msg = (
        "Les joueurs ayant consultés ou échangés un rôle ou une marque " "sont {}."
    ).format(", ".join(game.activated))
    game._fire_and_forget(notify_player(diseuse, msg, game.bot))


@Game.add_role("divinateur")
async def divinateur(game, phase="night", synchro=0):
    if phase != "day":
        return
    div = game._get_player_nick(["divinateur"], check_afraid=True)
    if not div:
        return
    game.activated.add(div)
    msg = "Quel joueur veux-tu révéler ?"
    await notify_player(div, msg, game.bot)
    choice = await get_choice(div, game.players[3:], game.bot)
    role = game.current_roles[choice]
    msg = f"{choice} est {role}."
    if "loup" in role or "vampire" in role or role in ("assassin", "tanneur"):
        notified = div
    else:
        notified = None
    game._fire_and_forget(notified, msg, game.bot)


@Game.add_role("franc maçon")
async def franc_macon(game, phase="night", synchro=0):
    """Execute franc macon's turn.

    This turn is independant of the other turns.
    """
    if phase != "night":
        return
    frama = game._get_player_nick(["franc maçon"], check_afraid=True)
    if not frama:
        return
    elif isinstance(frama, list):
        game.activated.update(set(frama))
        msg = "Il y a 2 franc-maçons ({}).".format(" et ".join(frama))
        for player in frama:
            game._fire_and_forget(notify_player(player, msg, game.bot))
    else:
        game.activated.add(frama)
        msg = "Tu es le seul franc maçon."
        game._fire_and_forget(notify_player(frama, msg, game.bot))


@Game.add_role("garde du corps")
async def garde(game, phase="day", synchro=0):
    if phase != "day":
        return None
    garde = game._get_player_nick(["garde du corps"], initial=False)
    if not garde:
        return None
    protected = game.votes[garde]
    msg = f"Il y a un garde du corps ({garde}). Il protège {protected}."
    game._fire_and_forget(notify_player(None, msg, game.bot))
    return protected


@Game.add_role("gremlin")
async def gremlin(game, phase="night", synchro=0):
    if not phase == "day":
        return
    grem = game._get_player_nick(["gremlin"])
    if not grem:
        return
    game.activated.add(grem)
    players = set(game.players[3:])
    msg = "Veux-tu échanger des marques (jetons) ou des rôles ?"
    await notify_player(grem, msg, game.bot)
    choice = await get_choice(grem, ("jeton", "role"), game.bot)
    msg = "Choisi les 2 joueurs"
    await notify_player(grem, msg, game.bot)
    j0 = await get_choice(grem, players, game.bot)
    players.pop(j0)
    j1 = await get_choice(grem, players, game.bot)
    if choice == "jeton":
        game.tokens[j0], game.token[j1] = game.tokens[j1], game.token[j0]
    else:
        game.roles_swaps.append((j0, j1))


@Game.add_role("insomniaque")
async def insomniaque(game, phase="night", synchro=0):
    if phase == "day":
        player = next(pl for pl, ro in game.initial_role.values() if ro == "insomiaque")
        msg = f"Ton rôle est à présent {game.current_roles[player]}."
        await notify_player(player, msg, game.bot)


@Game.add_role("loup alpha")
async def loup_alpha(game, phase="night", synchro=0):
    loup = game._get_nick_player(["loup alpha"], check_afraid=True)
    if not loup:
        pass
    players = set(game.players[3:])
    meute = game._get_player_nick(
        ("loup alpha", "loup garou", "loup shaman"), check_afraid=True
    )
    players.difference_update(set(meute))
    msg = "Quel joueur veux-tu convertir en loup garou ?"
    await notify_player(loup, msg, game.bot)
    choice = await get_choice(loup, players, game.bot)
    game.current_roles[choice] = "loup garou"


@Game.add_role("loup garou")
async def loup_garou(game, phase="night", synchro=0):
    if phase != "night":
        return

    loups = game._get_player_nick(
        ("loup alpha", "loup garou", "loup shaman"), check_afraid=True
    )
    if isinstance(loups, str):
        loups = [loups]
    loup_reveur = game._get_player_nick(["loup rêveur"], check_afraid=True)

    if not loups:
        return
    game.activated.update(set(loups))
    if len(loups) > 1:
        msg = "Les {} loups garous sont {}.".format(str(len(loups)), " et ".join(loups))
        for player in loups:
            game._fire_and_forget(notify_player(player, msg, game.bot))
    elif not loup_reveur:
        msg = (
            "Tu es le seul loup garou, indique une carte du milieu "
            "que tu veux découvrir. (0, 1 ou 2)"
        )
        await notify_player(loups[0], msg, game.bot)
        choice = await get_choice(loups[0], ("0", "1", "2"), game.bot)
        msg = f"la carte {choice} est le rôle {game.initial_roles[choice]}."
        game._fire_and_forget(notify_player(loups[0], msg, game.bot))

    if loup_reveur:
        msg = f"Le loup rêveur est {loup_reveur}."
        for players in loups:
            game._fire_and_forget(notify_player(player, msg, game.bot))


@Game.add_role("loup rêveur")
async def loup_reveur(game, phase="night", synchro=0):
    pass


@Game.add_role("loup shaman")
async def loup_shaman(game, phase="night", synchro=0):
    if not phase == "night":
        return
    sham = game._get_player_nick(["loup shaman"], check_afraid=True)
    if not sham:
        return

    game.activated.add(sham)
    msg = "Quelle carte veux-tu voir? "
    await notify_player(sham, msg, game.bot)
    choice = await get_choice(sham, game.players[3:], game.bot)
    msg = f"Le role de {choice} est {game.initial_roles[choice]}."
    game._fire_and_forget(notify_player(sham, msg, game.bot))


@Game.add_role("le maître")
async def maitre(game, phase="night", synchro=0):
    if phase != "day":
        return None
    maitre = game._get_player_nick(["le maître"])
    if not maitre:
        return None
    vamp = game._get_player_nick(["vampire", "le comte"])
    if not vamp:
        return None

    if isinstance(vamp, str):
        vamp = [vamp]
    vamp.extend(
        [
            nick
            for nick, token in game.tokens.items()
            if token == "vampire" and nick in game.players
        ]
    )
    if any(game.votes[player] == maitre for player in vamp):
        msg = "Le maître est protégé par un vampire."
        game._fire_and_forget(notify_player(None, msg, game.bot))
        return maitre
    else:
        return None


@Game.add_role("noiseuse")
async def noiseuse(game, phase="night", synchro=0, noiseuse=None):
    """Execute the noiseuse's turn.

    Args:
        noiseuse (str): noiseuse's nickname

    Return:
        tuple: the 2 roles to switch.
    """
    if phase != "night":
        return
    noiseuse = game._get_player_nick(["noiseuse"], check_afraid=True)
    if not noiseuse:
        return

    game.activated.add(noiseuse)
    msg = (
        "Choisis les 2 personnes dont tu veux inverser les rôles.\n"
        "Première personne."
    )
    choice = [player for player in game.players[3:] if player != noiseuse]
    await notify_player(noiseuse, msg, game.bot)
    first = await get_choice(noiseuse, choice, game.bot)

    choice = [player for player in game.players[3:] if player not in (choice, noiseuse)]
    msg = "Deuxième personne."
    await notify_player(noiseuse, msg, game.bot)
    second = await get_choice(noiseuse, choice, game.bot)

    game.role_swaps.append((first, second))


@Game.add_role("pestiféré")
async def pestifere(game, phase="night", synchro="0"):
    """Pestifere power

    If phase is day, return players that cannot win.
    """
    if phase == "dawn":
        pestif = game._get_player_nick(["pestiféré"])
        if not pestif:
            return None
        game.activated.add(pestif)
        msg = "Qui veux-tu contaminer en echangeant sa marque ?"
        await notify_player(pestif, msg, game.bot)
        choice = get_choice(pestif, game.players[3:], game.bot)
        game.token_swaps.append((synchro, "peste", choice))

    if phase == "day":
        pest = next(k for k, v in game.tokens.items() if v == "peste")
        infected = [nick for nick, vote in game.votes.items() if vote == pest]
        msg = (
            "Les joueurs {} ont votés pour la personne contaminée par "
            "la peste et ne peuvent donc gagner."
        ).format("et ".join(infected))
        if infected:
            game.fire_and_forget(notify_player(None, msg, game.bot))
        return infected


@Game.add_role("prêtre")
async def pretre(game, phase="night", synchro=-2):
    if phase != "dawn":
        return
    pretre = game._get_player_nick(["prêtre"])
    if not pretre:
        return

    game.activated.add(pretre)
    msg = "Contre quelle marque vas-tu échanger la tienne ?"
    await notify_player(pretre, msg, game.bot)
    available_tokens = {"clareté 0", "clareté 1"}
    choice = await get_choice(pretre, available_tokens, game.bot)
    available_tokens.remove(choice)
    game.token_swaps.append((-2, choice, "prêtre"))
    msg = "Veux-tu échanger la marque d'un joueur ?"
    await notify_player(pretre, msg, game.bot)
    choice = await get_choice(pretre, ("oui", "non"), game.bot)
    if choice == "oui":
        msg = "Quel joueur ?"
        players = set(game.players[3:])
        players.remove(pretre)
        choice = await get_choice(pretre, players, game.bot)
        game.token_swaps.append((-2, choice, available_tokens.pop()))


@Game.add_role("renfield")
async def renfield(game, phase="night", synchro=0):
    ren = game._get_player_nick(["renfield"])
    if not ren or phase != "dawn":
        return
    vic = next(p for p, t in game.tokens.items() if t == "vampire")
    msg = "La vicitme des vampire est {}."
    if vic == "vampire":
        msg = "Il n'y a pas victime de vampires."
    game._fire_and_forget(notify_player(ren, msg, game.bot))
    game.token_swaps.append((synchro, "chauve-souris", ren))


@Game.add_role("sbire")
async def sbire(game, phase="night", synchro=0):
    """Execute sbire's turn.

    This turn is independant of other turns.
    """
    if phase != "night":
        return

    loups = game._get_player_nick(
        ("loup alpha", "loup garou", "loup shaman", "loup rêveur"),
        check_afraid=True,
    )
    if isinstance(loups, str):
        loups = [loups]

    sbire = game._get_player_nick(["sbire"], check_afraid=True)
    if not sbire:
        return
    game.activated.add(sbire)
    if loups:
        msg = "Il y a {} loups garous ({}).".format(len(loups), ", ".join(loups))
    else:
        msg = "Il n'y a pas de loup garou."
    game._fire_and_forget(notify_player(sbire, msg, game.bot))


@Game.add_role("sorcière")
async def sorciere(game, phase="night", synchro=0):
    if phase != "night":
        return
    sor = game._get_player_nick(["sorcière"], check_afraid=True)
    if not sor:
        return

    msg = "Veux-tu consulter une carte et si oui laquelle?"
    await notify_player(sor, msg, game.bot)
    choice = await get_choice(sor, ["Non", "0", "1", "2"])
    if choice == "Non":
        return
    game.activated.add(sor)
    msg = f"Il s'agit du rôle de {game.current_roles[choice]}."
    await notify_player(sor, msg, game.bot)
    msg = "A qui veux-tu faire endosser ce rôle ?"
    await notify_player(sor, msg, game.bot)
    player = await get_choice(sor, game.players[3:], game.bot)
    game.role_swaps.append((choice, player))


@Game.add_role("soulard")
async def soulard(game, phase="night", synchro=0):
    """Execute the soulard's turn."""
    if phase != "night":
        return
    soulard = game._get_player_nick(["soulard"], check_afraid=True)
    if not soulard:
        return

    game.activated.add(soulard)
    msg = "Choisis la carte que tu veux echanger avec toi-même."
    await notify_player(soulard, msg, game.bot)
    choice = await get_choice(soulard, ("0", "1", "2"), game.bot)
    game.role_swaps.append((soulard, choice))


@Game.add_role("tanneur")
async def tanneur(game, phase="night", synchro=0):
    pass


@Game.add_role("trappeur")
async def trappeur(game, phase="night", synchro=0):
    if phase != "night":
        return
    trap = game._get_nick_player(["trappeur"], check_afraid=True)
    if not trap:
        return
    players = set(game.players[3:])

    msg = "Veux-tu consulter le rôle d'un joueur ?"
    await notify_player(trap, msg, game.bot)
    choice = await get_choice(trap, ("oui", "non"), game.bot)
    req = "Quel joueur veux-tu consulter ?"
    if choice == "oui":
        game.activated.add(trap)
        await notify_player(trap, req, game.bot)
        choice = await get_choice(trap, players, game.bot)
        msg = f"{choice} est {game.current_roles[choice]}."
        players.remove(choice)
        await notify_player(trap, msg, game.bot)

    msg = "Veux-tu consulter la marque d'un joueur ?"
    await notify_player(trap, msg, game.bot)
    choice = await get_choice(trap, ("oui", "non"), game.bot)
    if choice == "oui":
        game.activated.add(trap)
        await notify_player(trap, req, game.bot)
        choice = await get_choice(trap, players, game.bot)
        msg = f"{choice} est marqué de {game.tokens[choice]}."
        players.remove(choice)
        await notify_player(trap, msg, game.bot)


@Game.add_role("vampire")
async def vampire(game, phase="dawn", synchro=-6):
    if phase != "dawn":
        return
    vamp = game._get_player_nick(["vampire", "le comte", "le maître"])
    if not vamp:
        return
    if isinstance(vamp, str):
        vamp = [vamp]

    game.activated.update(set(vamp))
    msg = (
        "Les vampires sont {}. \n"
        "Vous devez choisir qui mordre pour le transformer "
        "en vampire. En cas d'égalité dans les votes, le joueur converti "
        "sera pris au hasard parmis les plus mordus"
    ).format(", ".join(vamp))
    for player in vamp:
        await notify_player(player, msg, game.bot)

    players = game.players[3:]
    tasks = [asyncio.ensure_future(get_choice(i, players, game.bot)) for i in vamp]

    finished, _ = asyncio.wait(tasks)
    results = Counter(finished.result())
    msg = "Les joueurs mordus sont: {}".format(
        "\n".join("{}: {}" for k, v in results.most_common())
    )
    new_vamp = results.most_common()[0][0]
    msg = f"Le joueur tranformé en vampire est {new_vamp}"
    game.token_swaps((synchro, new_vamp, vampire))


@Game.add_role("villageois")
async def villageois(game, phase="night", synchro=0):
    pass


@Game.add_role("voleur")
async def voleur(game, phase="night", synchro=0):
    """Execute the voleur's turn."""
    if phase != "night":
        return
    voleur = game._get_player_nick(["voleur"], check_afraid=True)
    if not voleur:
        return

    game.activated.add(voleur)
    msg = "Choisis quelle carte tu veux voler."
    await notify_player(voleur, msg, game.bot)
    choice = await get_choice(
        voleur,
        [player for player in game.players[3:] if player != voleur],
        game.bot,
    )
    msg = f"Ton nouveau rôle est {game.initial_roles[choice]}"
    game._fire_and_forget(notify_player(voleur, msg, game.bot))
    return game.role_swaps.append((voleur, choice))


@Game.add_role("voyante")
async def voyante(game, phase="night", synchro=0):
    """Execute the voyante's turn.

    This turn is independant of the other turns.
    """
    if phase != "night":
        return
    voyante = game._get_player_nick(["voyante"], check_afraid=True)
    if not voyante:
        return

    game.activated.add(voyante)
    msg = (
        "Quelle carte veux-tu voir? "
        "Si tu choisis une des cartes du milieu, tu pourras en regarder "
        "une autre du milieu."
    )
    await notify_player(voyante, msg, game.bot)
    choice = await get_choice(voyante, game.players, game.bot)
    msg = f"Le role de {choice} est {game.initial_roles[choice]}."
    await notify_player(voyante, msg, game.bot)

    if choice in ("0", "1", "2"):
        msg = "Quelle carte veux tu regarder?"
        choice = await get_choice(voyante, ("0", "1", "2"), game.bot)
        msg = f"Le role de {choice} est {game.initial_roles[choice]}."
        game._fire_and_forget(notify_player(voyante, msg, game.bot))


def get_param(filename, param):
    with open(join(dirname(realpath(__file__)), filename)) as fp:
        return yaml.load(fp.read(), Loader=yaml.SafeLoader).get(param)


get_roles = partial(get_param, param="characters")
get_scenario = partial(get_param, param="scenario")
get_token = partial(get_param, param="tokens")


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
    perso_counts.extend([Counter(s["roles"]) for s in scenario if s and "roles" in s])
    return max_dict(perso_counts)


def victory_tree(filename="victory.yaml"):
    with open(join(dirname(realpath(__file__)), filename)) as fp:
        return yaml.load(fp.read(), Loader=yaml.SafeLoader)
