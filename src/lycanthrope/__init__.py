from random import shuffle


# max number of playerr with the role
MAX_ROLE_NB = {'tanneur': 1,
               'chasseur': 1,
               'doppelgänger': 1,
               'loup garou': 2,
               'sbire': 1,
               'franc maçon': 2,
               'voyante': 1,
               'voleur': 1,
               'noiseuse': 1,
               'soulard': 1,
               'insomniaque': 1}


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

    def __init__(self):
        self.roles = []
        self.initial_roles = {}
        self.current_roles = {}

        # initialize players with roles in the middle
        self.players = [str(num) for num in range(3)]

    def add_player(self, nick):
        """Add the player <nick> to the game.

        Only works befor the game is started

        Args:
            nick (string): nick of the player
        """
        if len(self.players) >= sum(MAX_ROLE_NB.values()):
            raise RuntimeWarning("Maximum number of players reached")
        if nick not in self.players:
            self.players.append(nick)

    def deal_roles(self):
        """Maps each player to a role."""
        nb_player = len(self.players)
        nb_role = nb_player + 3

        # select roles
        roles = [role for role, nb in MAX_ROLE_NB.items()
                 for _ in range(nb)]
        shuffle(roles)

        # at least one 'voyante' and one 'loup garou' must be dealt
        selected_roles = roles[:nb_role]
        if 'voyante' not in selected_roles:
            selected_roles[0] = 'voyante'
        if 'loup garou' not in selected_roles:
            selected_roles[1] = 'loup garou'
        shuffle(selected_roles)

        self.initial_roles = dict(zip(self.players, selected_roles))
        self.current_roles = self.initial_roles.copy()
