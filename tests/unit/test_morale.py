"""Voir venir la deroute, puisque le jeu ne donne pas le moral.

Une regle fondee sur la sante ne verra jamais venir une contagion : douze unites
sur douze ont rompu en bataille reelle, dix au-dessus de 40 % de sante. Reste a
savoir si autre chose l'annonce -- et ces tests verifient que la mesure sait le
dire, dans un sens comme dans l'autre.
"""

from __future__ import annotations

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState
from totalwar_ai.learning.morale import study


def _unite(
    unit_id: str,
    *,
    x: float = 0.0,
    side: Side = Side.ALLY,
    routing: bool = False,
    melee: bool = False,
    hp: float = 1.0,
) -> UnitState:
    return UnitState(
        id=unit_id,
        side=side,
        role=UnitRole.MELEE_INFANTRY,
        position=Vector3(x, 0.0, 0.0),
        is_routing=routing,
        is_engaged=melee,
        health_ratio=hp,
        entity_ratio=hp,
    )


def _bataille(tours: list[tuple[UnitState, ...]]) -> list[BattleState]:
    return [
        BattleState(battle_id="m", game_time=index * 1.0, units=unites)
        for index, unites in enumerate(tours)
    ]


def test_un_signal_qui_annonce_vraiment_ressort() -> None:
    """La contagion est spatiale : un allie qui rompt a cote annonce la suite."""
    tours = []
    for instant in range(80):
        # `voisine` rompt a t=40 ; `suiveuse` la suit a t=50.
        voisine = _unite("voisine", x=10.0, routing=instant >= 40)
        suiveuse = _unite("suiveuse", x=20.0, routing=instant >= 50)
        # `loin` ne rompt jamais, et se tient a l'ecart.
        loin = _unite("loin", x=900.0)
        tours.append((voisine, suiveuse, loin, _unite("e", side=Side.ENEMY, x=5.0)))

    resultat = study([_bataille(tours)])
    voisinage = next(
        item for item in resultat.signals if item.name == "un allie en deroute a moins de 100 m"
    )
    assert voisinage.rate_present > voisinage.rate_absent, (
        f"{voisinage.rate_present:.1%} contre {voisinage.rate_absent:.1%}"
    )
    assert resultat.routs > 0


def test_un_signal_sans_rapport_ne_ressort_pas() -> None:
    """Un signal present partout n'annonce rien, et la mesure doit le dire."""
    tours = []
    for instant in range(80):
        # Toutes au contact en permanence, une seule rompt : « au contact »
        # ne separe rien.
        tours.append(
            (
                _unite("a", x=0.0, melee=True, routing=instant >= 60),
                _unite("b", x=300.0, melee=True),
                _unite("c", x=600.0, melee=True),
                _unite("e", side=Side.ENEMY, x=5.0),
            )
        )

    contact = next(item for item in study([_bataille(tours)]).signals if item.name == "au contact")
    assert not contact.usable or contact.lift <= 1.2


def test_une_unite_deja_rompue_n_alimente_plus_la_mesure() -> None:
    """On cherche ce qui precede la rupture, pas ce qui la suit."""
    tours = [
        (_unite("a", routing=instant >= 5), _unite("e", side=Side.ENEMY, x=5.0))
        for instant in range(40)
    ]

    resultat = study([_bataille(tours)])
    # Cinq etats avant la rupture, et rien apres.
    assert resultat.samples <= 5


def test_l_horizon_ne_traverse_pas_la_fin_d_une_bataille() -> None:
    """Melanger ferait annoncer la deroute d'une partie par l'etat d'une autre."""
    calme = [(_unite("a"), _unite("e", side=Side.ENEMY, x=5.0)) for _ in range(40)]
    rompue = [
        (_unite("a", routing=instant >= 2), _unite("e", side=Side.ENEMY, x=5.0))
        for instant in range(40)
    ]

    ensemble = study([_bataille(calme), _bataille(rompue)])
    separe = study([_bataille(calme)])
    assert separe.routs == 0, "une bataille sans deroute ne doit rien etiqueter"
    assert ensemble.routs > 0


def test_un_corpus_vide_le_dit() -> None:
    assert "Aucun etat exploitable" in study([]).render()
