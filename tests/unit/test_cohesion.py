"""L'armee arrive-t-elle ensemble ? La mesure, avant tout correctif.

Baseline reelle du 18/08 22h20 : trois unites s'engagent entre 192,6 s et
241,6 s, puis **rien pendant 235 secondes**. Ces tests pincent les deux facons
dont cette mesure pourrait mentir — compter des ordres sans effet, et noyer le
vide dans une mediane.
"""

from __future__ import annotations

import json

from totalwar_ai.learning.cohesion import (
    ISOLATION_THRESHOLD,
    Survey,
    UnitTimeline,
    from_battle_log,
    study,
)


def _t(unit: str, ordre: float | None = None, contact: float | None = None) -> UnitTimeline:
    return UnitTimeline(
        unit_id=unit,
        first_order_at=ordre,
        first_active_order_at=ordre,
        first_contact_at=contact,
    )


def _roster(*allies: str, tour: int = 1, ennemis: tuple[str, ...] = ()) -> str:
    fiches = {identifiant: {"side": "ally", "type": "inf"} for identifiant in allies}
    fiches |= {identifiant: {"side": "enemy", "type": "inf"} for identifiant in ennemis}
    return json.dumps({"turn": tour, "roster": fiches})


def _etat(instant: float, *unites: str, melee: tuple[str, ...] = ()) -> str:
    """Un etat publie : qui est present, et qui est au contact."""
    return json.dumps(
        {
            "game_time_ms": int(instant * 1000),
            "phase": "Deployed",
            "units": [{"id": u, "in_melee": u in melee} for u in unites],
        }
    )


def _ordres(
    instant: float,
    *,
    phase: str = "Deployed",
    moves: tuple[str, ...] = (),
    halts: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "game_time_ms": int(instant * 1000),
            "phase": phase,
            "decision": True,
            "orders": {
                "moves": [{"unit_id": u} for u in moves],
                "attacks": [],
                "halts": list(halts),
            },
        }
    )


def test_la_cohorte_compte_ce_qui_rejoint_le_premier_choc() -> None:
    """Trois unites sur douze dans la minute, c'est la definition du compte-gouttes."""
    unites = [_t("a", 0.0, 100.0), _t("b", 0.0, 130.0), _t("c", 0.0, 400.0)]
    unites += [_t(f"z{i}", 0.0) for i in range(9)]
    cohesion = study(unites)
    assert cohesion.first_contact_time == 100.0
    assert cohesion.first_contact_unit == "a"
    assert cohesion.contact_cohort(60.0) == 2
    assert cohesion.contact_cohort(400.0) == 3


def test_le_plus_grand_vide_nomme_le_defaut_que_la_mediane_noie() -> None:
    """Une mediane de delais ne montre pas le trou ; elle le dilue.

    Sur la bataille reelle, la mediane des delais de soutien vaut 727 s — un
    chiffre vrai et inutilisable, car il melange le renfort attendu et des
    arrivees appartenant deja a une autre phase. Le vide, lui, vaut 235,5 s et
    designe l'endroit exact.
    """
    cohesion = study(
        [
            _t("a", 0.0, 192.6),
            _t("b", 0.0, 206.6),
            _t("c", 0.0, 241.6),
            _t("d", 0.0, 477.1),
        ]
    )
    vide = cohesion.largest_contact_gap
    assert vide is not None
    ecart, avant, apres = vide
    assert round(ecart, 1) == 235.5
    assert (avant, apres) == ("c", "d")


def test_un_engagement_accompagne_n_est_pas_isole() -> None:
    """Le garde-fou doit pouvoir dire non, sinon il ne dit rien."""
    ensemble = study([_t("a", 0.0, 100.0), _t("b", 0.0, 110.0), _t("c", 0.0, 120.0)])
    assert ensemble.isolated_contacts == []

    seule = study([_t("a", 0.0, 100.0), _t("b", 0.0, 500.0), _t("c", 0.0, 900.0)])
    assert len(seule.isolated_contacts) == 3


def test_le_seuil_d_isolement_porte_sur_les_voisines_pas_sur_l_armee() -> None:
    """« Toute l'armee doit avancer » serait un mauvais critere.

    Une diversion a deux unites, un flanc a trois, une reserve deliberee sont des
    manoeuvres legitimes. Ce qui compte est qu'une unite ne parte pas seule, pas
    que l'armee entiere parte.
    """
    diversion = study(
        [_t("a", 0.0, 100.0), _t("b", 0.0, 105.0)] + [_t(f"z{i}", 0.0) for i in range(10)]
    )
    assert ISOLATION_THRESHOLD == 1
    assert diversion.isolated_contacts == [], "deux unites ensemble ne sont pas isolees"


# --- le denominateur : d'ou vient l'armee etudiee -----------------------------


def test_la_population_vient_du_roster_pas_des_ordres() -> None:
    """Le defaut le plus grave que cette mesure pouvait porter.

    Le lecteur precedent construisait l'armee a partir des unites ayant recu un
    ordre — tout en affirmant en commentaire l'inverse. Une doctrine qui
    oublierait completement deux regiments les aurait donc fait disparaitre du
    denominateur, et **ses ratios se seraient ameliores**.

    `c` ne recoit jamais rien : elle doit rester dans l'armee.
    """
    lignes = [
        _roster("a", "b", "c"),
        _ordres(10.0, moves=("a",)),
        _etat(20.0, "a", "b", "c", melee=("a", "b")),
    ]
    cohesion = from_battle_log(lignes)
    fiches = {item.unit_id: item for item in cohesion.units}

    assert len(cohesion.units) == 3, "une unite oubliee reste dans l'armee"
    assert fiches["b"].first_order_at is None, "b n'a jamais recu d'ordre"
    assert fiches["b"].first_contact_at is not None, "b est pourtant entree au contact"
    assert fiches["c"].first_order_at is None
    assert fiches["c"].first_contact_at is None


def test_les_ennemis_ne_sont_pas_dans_la_chronologie_alliee() -> None:
    """L'inventaire publie les deux camps ; la mesure n'en decrit qu'un.

    Le lecteur precedent enregistrait `in_melee` pour tous les identifiants de
    l'inventaire : sur un corpus reel, des unites adverses entraient dans une
    chronologie censee decrire notre armee.
    """
    lignes = [
        _roster("a", ennemis=("x",)),
        _etat(20.0, "x", melee=("x",)),
        _etat(50.0, "a", "x", melee=("a", "x")),
    ]
    cohesion = from_battle_log(lignes)

    assert [item.unit_id for item in cohesion.units] == ["a"]
    assert cohesion.first_contact_time == 50.0, "le contact ennemi de 20 s n'est pas le notre"


def test_un_renfort_tardif_ne_dilue_pas_la_premiere_vague() -> None:
    """Le roster est canonique, mais il est aussi temporel.

    Constate sur un corpus reel : un treizieme allie apparait a 648 s alors que le
    premier contact a lieu a 86 s. Le compter dans le denominateur de la premiere
    vague la ferait paraitre plus faible qu'elle ne l'a ete, pour une unite qui ne
    pouvait pas y participer.
    """
    lignes = [
        _roster("a", "b", "c"),
        _etat(10.0, "a", "b"),
        _etat(100.0, "a", "b", melee=("a", "b")),
        _roster("d", tour=200),
        _etat(600.0, "a", "b", "c", "d", melee=("a", "b")),
    ]
    cohesion = from_battle_log(lignes)

    assert len(cohesion.units) == 4, "le renfort appartient bien a l'armee"
    assert len(cohesion.available_at(100.0)) == 2, "seules a et b etaient la au premier choc"
    assert cohesion.first_contact_wave_size == 2
    assert cohesion.first_contact_wave_share == 1.0, "deux presentes sur deux, pas deux sur quatre"


# --- ce que la mesure ne doit pas compter ------------------------------------


def test_les_ordres_anterieurs_a_deployed_ne_comptent_pas() -> None:
    """Le defaut que la baseline reelle a revele dans cette mesure meme.

    Sur la bataille du 18/08, les douze unites ont toutes recu leur premier ordre
    a 3,1 s — avant que `Deployed` ne commence a 7,6 s. La mesure annoncait donc
    « armee ordonnee a 100 % » alors que le moteur avait acquitte ces ordres sans
    en executer un seul, et que trois unites seulement se mettaient en mouvement.
    """
    lignes = [
        _roster("a", "b", "c"),
        _ordres(3.1, phase="unknown", moves=("a", "b", "c")),
        _ordres(10.0, moves=("a",)),
    ]
    cohesion = from_battle_log(lignes)
    ordonnees = {
        item.unit_id: item.first_order_at
        for item in cohesion.units
        if item.first_order_at is not None
    }
    assert ordonnees == {"a": 10.0}, "seuls les ordres qui prennent effet comptent"


def test_un_arret_n_est_pas_une_mise_en_mouvement() -> None:
    """Douze `HOLD` ne sont pas une armee mobilisee.

    Sans cette separation, une doctrine qui se contenterait d'arreter tout le
    monde afficherait « armee ordonnee a 100 % » sans que personne ne bouge —
    exactement le faux positif que ces mesures existent pour eviter.
    """
    lignes = [_roster("a", "b"), _ordres(10.0, halts=("a", "b"))]
    cohesion = from_battle_log(lignes)

    assert cohesion.commanded_army_share(30.0) == 1.0
    assert cohesion.active_order_share(30.0) == 0.0
    assert all(item.first_active_order_at is None for item in cohesion.units)


def test_un_contact_se_lit_dans_l_inventaire_des_unites() -> None:
    lignes = [_roster("a"), _ordres(10.0, moves=("a",)), _etat(50.0, "a", melee=("a",))]
    cohesion = from_battle_log(lignes)
    assert cohesion.first_contact_time == 50.0


def test_une_armee_qui_n_engage_jamais_le_dit() -> None:
    """Zero contact et « contact immediat » ne doivent pas se lire pareil."""
    cohesion = study([_t("a", 0.0), _t("b", 0.0)])
    assert cohesion.first_contact_time is None
    assert "aucun contact" in cohesion.render()


# --- l'agregation : trois etats, pas deux ------------------------------------


def test_une_bataille_a_contact_unique_ne_disparait_pas_de_l_agregation() -> None:
    """Le pire cas sortait de la mesure par la meme porte que l'absence de defaut.

    `largest_contact_gap` exige deux entrees en melee. Une bataille ou une seule
    unite sur douze engage pendant que onze regardent n'a donc pas de vide
    mesurable — comme une bataille sans aucun contact. Les confondre reviendrait a
    ne plus voir le desastre.
    """
    seule = study([_t("a", 0.0, 100.0)] + [_t(f"z{i}", 0.0) for i in range(11)])
    aucune = study([_t(f"z{i}", 0.0) for i in range(12)])
    groupee = study([_t("a", 0.0, 100.0), _t("b", 0.0, 110.0)])

    etude = Survey()
    etude.add("solitaire", seule)
    etude.add("sans_contact", aucune)
    etude.add("groupee", groupee)

    assert [nom for nom, _ in etude.single_contact] == ["solitaire"]
    assert [nom for nom, _ in etude.no_contact] == ["sans_contact"]
    assert [nom for nom, _ in etude.multi_contact] == ["groupee"]

    assert seule.largest_contact_gap is None
    assert etude.gaps == [10.0], "un vide absent ne devient jamais zero"
    assert round(seule.first_contact_wave_share, 4) == round(1 / 12, 4)
    assert "Contact unique" in etude.render()


# --- la symetrie des deux lecteurs -------------------------------------------


def test_le_banc_et_le_live_repondent_la_meme_chose(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Deux denominateurs differents porteraient le meme nom sans mesurer pareil.

    Si le banc prenait « toutes les unites du scenario » quand le live prend « les
    alliees deja presentes », la comparaison banc/live qui a conclu que le banc ne
    reproduit pas la fragmentation comparerait deux definitions au lieu de deux
    batailles.

    Meme scenario que `test_un_renfort_tardif_ne_dilue_pas_la_premiere_vague`,
    joue cette fois par `from_events`.
    """
    from totalwar_ai.domain.actions import ActionType
    from totalwar_ai.domain.unit_state import Side, UnitRole
    from totalwar_ai.learning.cohesion import from_events
    from totalwar_ai.telemetry.events import Event, EventType

    def _armee(*identifiants: str, engagees: tuple[str, ...] = ()) -> list:  # type: ignore[type-arg]
        return [
            make_unit(
                identifiant,
                Side.ALLY,
                UnitRole.MELEE_INFANTRY,
                is_engaged=identifiant in engagees,
            )
            for identifiant in identifiants
        ]

    etats = [
        make_battle(_armee("a", "b"), game_time=10.0),
        make_battle(_armee("a", "b", engagees=("a", "b")), game_time=100.0),
        make_battle(_armee("a", "b", "c", "d", engagees=("a", "b")), game_time=600.0),
    ]
    evenements = [
        Event(
            type=EventType.ACTION_SENT,
            battle_id="test-battle",
            game_time=10.0,
            payload={"type": ActionType.MOVE_GROUP.value, "actors": ["a", "b"]},
        ),
        Event(
            type=EventType.PLAN_SELECTED,
            battle_id="test-battle",
            game_time=10.0,
            payload={"type": ActionType.MOVE_GROUP.value, "actors": ["c", "d"]},
        ),
    ]

    cohesion = from_events(evenements, etats)
    fiches = {item.unit_id: item for item in cohesion.units}

    assert len(cohesion.units) == 4
    assert len(cohesion.available_at(100.0)) == 2, "c et d ne sont apparues qu'a 600 s"
    assert cohesion.first_contact_wave_share == 1.0, "deux sur deux, pas deux sur quatre"
    assert fiches["c"].first_order_at is None, "un plan choisi n'est pas un ordre envoye"
    assert fiches["d"].first_seen_at == 600.0


def test_un_hold_du_banc_ne_compte_pas_comme_mise_en_mouvement(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Meme frontiere que le lecteur live entre `halts` et `moves`.

    `OrderTranslator` ne produit un arret que pour `HOLD_POSITION` ; tout le reste
    devient un deplacement ou une attaque. Les deux lecteurs doivent donc tracer
    la ligne au meme endroit.
    """
    from totalwar_ai.domain.actions import ActionType
    from totalwar_ai.domain.unit_state import Side, UnitRole
    from totalwar_ai.learning.cohesion import from_events
    from totalwar_ai.telemetry.events import Event, EventType

    etats = [
        make_battle(
            [make_unit(u, Side.ALLY, UnitRole.MELEE_INFANTRY) for u in ("a", "b")],
            game_time=10.0,
        )
    ]
    evenements = [
        Event(
            type=EventType.ACTION_SENT,
            battle_id="test-battle",
            game_time=10.0,
            payload={"type": ActionType.HOLD_POSITION.value, "actors": ["a", "b"]},
        )
    ]

    cohesion = from_events(evenements, etats)
    assert cohesion.commanded_army_share(30.0) == 1.0
    assert cohesion.active_order_share(30.0) == 0.0
