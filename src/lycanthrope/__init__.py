"""IRC bot that play 'loup garou e une nuit'."""

# TODO move DESCR in conf file.
DESCR = {'tanneur': "Le but du taneur est de mourir.",
         'chasseur': ("Lorsque le chasseur meurt, il tue immédiatement "
                      "la personne de son choix."),
         'doppelgänger': "Copie le rôle d'un autre joueur.",
         'loup garou': ("Le but des loup garou est de ne pas se faire "
                        "détecter. Si l'un d'entre eux meurt, "
                        "ils ne gagnent pas. "
                        "Lors de leur tour, ils se reconnaissent. "
                        "S'il n'y a qu'un loup garou, "
                        "il prends connaissance d'une carte au centre"),
         'sbire': ("Ce joueur fait parti de la meute de loups garous. "
                   "Son but est de faire gagner les loups garous."),
         'franc maçon': ("Villageois comme les autres."
                         "Lors de son tour, ils prennent connaissance de "
                         "l'identité des autres franc-maçon."),
         'voyante': ("Villageoise. Elle prends connaissance du rôle de "
                     "n'importe quel autre joueur ou de 2 cartes du centre."),
         'voleur': ("Échange sa carte avec n'importe quel autre joueur et "
                    "prends connaissance de son nouveau rôle."),
         'noiseuse': "Intervertie 2 rôles qui ne sont pas elle.",
         'soulard': "Échange sa carte avec une du centre sans les regarder.",
         'insomniaque': "Regarde sa carte."}
