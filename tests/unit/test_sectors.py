"""La concentration locale : la manoeuvre doit exister avant de gagner.

Ces tests n'exigent **aucune victoire**. Ils exigent que la primitive fasse ce
qu'elle annonce : decouper, refuser un appat, n'envoyer que le necessaire, et
relacher apres la rupture. Un taux de victoire ne dirait rien de tout cela.
"""

from __future__ import annotations

import pytest

from totalwar_ai.agent.sectors import (
    ASSAULT_RATIO,
    Assignment,
    Manoeuvre,
    ManoeuvrePhase,
    ManoeuvreRole,
    commit,
    split_sectors,
)
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole

#: Front pointant vers les z croissants : la laterale est donc l'axe des x.
FRONT = Vector3(0.0, 0.0, 1.0)


def _ligne(make_unit, side, prefix, xs, z, role=UnitRole.MELEE_INFANTRY):  # type: ignore[no-untyped-def]
    return [make_unit(f"{prefix}{i}", side, role, x=x, z=z) for i, x in enumerate(xs)]


def test_le_front_se_decoupe_en_tranches_laterales(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Trois groupes bien separes doivent donner trois secteurs."""
    ennemis = (
        _ligne(make_unit, Side.ENEMY, "e_g", [-200.0, -190.0], 200.0)
        + _ligne(make_unit, Side.ENEMY, "e_c", [-5.0, 5.0], 200.0)
        + _ligne(make_unit, Side.ENEMY, "e_d", [190.0, 200.0], 200.0)
    )
    allies = _ligne(make_unit, Side.ALLY, "a_", [-20.0, 0.0, 20.0], 0.0)
    state = make_battle(allies + ennemis)

    carte = split_sectors(state, FRONT, allies)
    assert len(carte.sectors) == 3
    assert [len(item.enemy_ids) for item in carte.sectors] == [2, 2, 2]
    # Les secteurs vont de gauche a droite le long de la laterale.
    assert carte.sectors[0].centre.x < carte.sectors[1].centre.x < carte.sectors[2].centre.x


def test_aucune_unite_adverse_n_est_oubliee(make_unit, make_battle) -> None:
    """La borne haute doit appartenir a quelqu'un.

    Une tranche ouverte des deux cotes laisserait l'unite la plus a droite hors
    de tout secteur : elle disparaitrait du denominateur, et le rapport local
    serait flatteur precisement la ou l'on va frapper.
    """
    ennemis = _ligne(make_unit, Side.ENEMY, "e_", [-90.0, -30.0, 30.0, 90.0], 200.0)
    allies = _ligne(make_unit, Side.ALLY, "a_", [0.0], 0.0)
    state = make_battle(allies + ennemis)

    carte = split_sectors(state, FRONT, allies)
    couvert = {unit_id for item in carte.sectors for unit_id in item.enemy_ids}
    assert couvert == {unite.id for unite in ennemis}


def test_un_secteur_faible_mais_soutenu_est_un_appat(make_unit, make_battle) -> None:
    """Le detachement isole colle a leur masse ne doit pas etre choisi.

    Sans le terme de soutien, la primitive irait systematiquement au plus faible
    — c'est-a-dire, souvent, la ou l'on se ferait envelopper.
    """
    # Bornes du decoupage : x va de -200 a 200, donc trois tranches de 133 m
    # coupees a -67 et +67. L'appat est pose juste **avant** la coupe, son
    # escorte juste **apres** — hors de son secteur, mais a portee de soutien.
    appat = _ligne(make_unit, Side.ENEMY, "e_appat", [60.0], 200.0)
    escorte = _ligne(make_unit, Side.ENEMY, "e_esc", [75.0, 90.0], 200.0)
    lointain = _ligne(make_unit, Side.ENEMY, "e_loin", [200.0], 200.0)
    # A gauche : deux unites, reellement seules.
    isole = _ligne(make_unit, Side.ENEMY, "e_seul", [-200.0, -190.0], 200.0)
    # Placees a egale portee des deux secteurs, pour que le seul ecart entre eux
    # soit le cout — et non le nombre d'unites qu'on peut y amener.
    allies = _ligne(make_unit, Side.ALLY, "a_", [-100.0, -90.0, -80.0, -70.0, -60.0, -50.0], 180.0)
    state = make_battle(allies + appat + escorte + lointain + isole)

    carte = split_sectors(state, FRONT, allies)
    milieu = next(item for item in carte.sectors if "e_appat0" in item.enemy_ids)
    assert milieu.enemy_ids == ("e_appat0",), "l'appat doit etre seul dans son secteur"
    assert milieu.support > 0.0, "l'escorte voisine doit compter dans le cout"
    assert milieu.cost > milieu.enemy_strength

    gauche = next(item for item in carte.sectors if "e_seul0" in item.enemy_ids)
    assert gauche.support == 0.0, "le secteur isole n'a personne a portee"
    # Une unite soutenue par deux voisines coute plus cher que deux unites seules.
    assert milieu.cost > gauche.cost

    choisi = carte.best()
    assert choisi is not None
    assert "e_appat0" not in choisi.enemy_ids, "l'appat ne doit pas etre choisi"


def test_on_envoie_le_necessaire_et_pas_toute_l_armee(make_unit, make_battle) -> None:
    """Concentrer n'est pas s'engager.

    Ce qui reste hors de l'assaut est ce qui fixe l'adversaire ailleurs : sans
    cela, l'assaut n'est qu'une charge generale avec une cible mieux choisie.
    """
    ennemis = _ligne(make_unit, Side.ENEMY, "e_", [-10.0, 10.0], 150.0)
    allies = _ligne(make_unit, Side.ALLY, "a_", [-50.0, -30.0, -10.0, 10.0, 30.0, 50.0], 0.0)
    state = make_battle(allies + ennemis)

    carte = split_sectors(state, FRONT, allies)
    secteur = carte.best()
    assert secteur is not None

    assaut = commit(secteur, state, allies, game_time=30.0)
    assert assaut is not None
    assert assaut.ratio >= ASSAULT_RATIO
    assert len(assaut.attackers) < len(allies), "il doit rester des unites pour fixer ailleurs"


def test_sans_superiorite_atteignable_on_ne_fait_rien(make_unit, make_battle) -> None:
    """L'abstention est ce qui protege les scenarios deja gagnes.

    Quand aucun secteur n'atteint le rapport, la primitive ne rend rien et
    l'agent garde exactement son comportement precedent.
    """
    ennemis = _ligne(make_unit, Side.ENEMY, "e_", [-20.0, 0.0, 20.0, 40.0], 150.0)
    allies = _ligne(make_unit, Side.ALLY, "a_", [0.0], 0.0)
    state = make_battle(allies + ennemis)

    carte = split_sectors(state, FRONT, allies)
    assert carte.best() is None


def test_une_unite_hors_de_portee_ne_concentre_rien(make_unit, make_battle) -> None:
    """Compter toute l'armee ferait croire a une superiorite que la geometrie interdit."""
    ennemis = _ligne(make_unit, Side.ENEMY, "e_", [0.0, 20.0], 150.0)
    proches = _ligne(make_unit, Side.ALLY, "a_pres", [0.0], 100.0)
    lointaines = _ligne(make_unit, Side.ALLY, "a_loin", [0.0, 20.0, 40.0], -400.0)
    state = make_battle(proches + lointaines + ennemis)

    carte = split_sectors(state, FRONT, proches + lointaines)
    secteur = carte.sectors[0]
    assert set(secteur.reachable) == {"a_pres0"}, "les lointaines ne comptent pas"


def test_le_decoupage_ne_depend_pas_de_l_ordre_des_unites(make_unit, make_battle) -> None:
    """Meme situation, meme decoupage.

    Un decoupage qui dependrait de l'ordre d'arrivee — ou d'une tranche prise
    dans un ensemble — rendrait le banc dependant de `PYTHONHASHSEED`. C'est le
    defaut que l'ADR 0011 a coute a trouver ; il ne doit pas revenir par ici.
    """
    ennemis = _ligne(make_unit, Side.ENEMY, "e_", [-90.0, -30.0, 30.0, 90.0], 200.0)
    allies = _ligne(make_unit, Side.ALLY, "a_", [-20.0, 0.0, 20.0], 0.0)

    droit = split_sectors(make_battle(allies + ennemis), FRONT, allies)
    inverse = split_sectors(
        make_battle(list(reversed(ennemis)) + list(reversed(allies))), FRONT, allies
    )
    assert [item.enemy_ids for item in droit.sectors] == [
        item.enemy_ids for item in inverse.sectors
    ]


def test_la_rupture_se_constate_et_relache_l_assaut(make_unit, make_battle) -> None:
    """Rompre n'est pas poursuivre.

    Une fois le secteur enfonce, l'assaut se dissout et le choix recommence sur
    l'etat du moment — au lieu de suivre les fuyards a travers la carte.
    """
    ennemis = _ligne(make_unit, Side.ENEMY, "e_", [-10.0, 10.0], 150.0)
    allies = _ligne(make_unit, Side.ALLY, "a_", [-30.0, -10.0, 10.0, 30.0], 0.0)
    state = make_battle(allies + ennemis)

    carte = split_sectors(state, FRONT, allies)
    secteur = carte.best()
    assert secteur is not None
    assaut = commit(secteur, state, allies, game_time=30.0)
    assert assaut is not None
    assert not assaut.broken(state)

    # Le secteur a cede : une unite morte, l'autre en deroute.
    apres = make_battle(
        [
            *allies,
            make_unit(
                "e_0", Side.ENEMY, UnitRole.MELEE_INFANTRY, x=-10.0, z=150.0, entity_ratio=0.0
            ),
            make_unit("e_1", Side.ENEMY, UnitRole.MELEE_INFANTRY, x=10.0, z=150.0, is_routing=True),
        ]
    )
    assert assaut.broken(apres)


def test_le_rapport_annonce_est_celui_qui_sera_livre(make_unit, make_battle) -> None:
    """Le rapport publie doit se calculer sur le cout complet, soutien compris.

    Un rapport annonce sur la seule force du secteur serait un chiffre flatteur
    — exactement le genre de mesure que cette session a passe son temps a
    corriger.
    """
    ennemis = _ligne(make_unit, Side.ENEMY, "e_", [-10.0, 10.0], 150.0)
    allies = _ligne(make_unit, Side.ALLY, "a_", [-30.0, -10.0, 10.0, 30.0, 50.0], 0.0)
    state = make_battle(allies + ennemis)

    carte = split_sectors(state, FRONT, allies)
    secteur = carte.best()
    assert secteur is not None
    assaut = commit(secteur, state, allies, game_time=0.0)
    assert assaut is not None

    engagee = sum(unite.effective_strength for unite in allies if unite.id in set(assaut.attackers))
    assert assaut.ratio == pytest.approx(engagee / secteur.cost)


# --- la manoeuvre : des roles, une phase, une readiness ----------------------


def test_les_unites_rapides_flanquent_et_les_autres_portent_le_choc(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Le planificateur le faisait deja ; la manoeuvre le nomme.

    `_command_cavalry` emet un `FLANK` la ou `_command_front_line` emet un
    `ATTACK_TARGET`, mais nulle part ce partage n'etait ecrit. Sans nom, il ne
    pouvait pas etre retenu jusqu'au contact.
    """
    ennemis = _ligne(make_unit, Side.ENEMY, "e", (0.0, 15.0), 200.0)
    fantassins = _ligne(make_unit, Side.ALLY, "a", (0.0, 15.0, 30.0), 120.0)
    cavalier = make_unit("a_cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, x=45.0, z=120.0)
    allies = [*fantassins, cavalier]
    etat = make_battle([*allies, *ennemis])

    carte = split_sectors(etat, FRONT, allies)
    manoeuvre = commit(carte.best(), etat, allies, game_time=0.0)

    assert manoeuvre is not None
    assert manoeuvre.role_of("a_cav") is ManoeuvreRole.FLANK
    autres = {manoeuvre.role_of(unit.id) for unit in fantassins if unit.id in manoeuvre.attackers}
    assert autres <= {ManoeuvreRole.ASSAULT}, "un fantassin ne flanque pas"


def test_une_manoeuvre_nait_au_contact_tant_que_personne_ne_la_retient(
    make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    """Le modele arrive avant le comportement : cette etape ne change rien.

    Naitre en `ASSEMBLE` retiendrait les participants alors qu'aucun code ne sait
    encore les liberer — l'armee serait paralysee entre deux commits.
    """
    ennemis = _ligne(make_unit, Side.ENEMY, "e", (0.0,), 200.0)
    allies = _ligne(make_unit, Side.ALLY, "a", (0.0, 15.0, 30.0), 120.0)
    etat = make_battle([*allies, *ennemis])

    manoeuvre = commit(split_sectors(etat, FRONT, allies).best(), etat, allies, game_time=0.0)

    assert manoeuvre is not None
    assert manoeuvre.phase is ManoeuvrePhase.CONTACT
    assert not manoeuvre.holding


def test_un_tireur_a_portee_est_pret_sans_jamais_toucher_l_ennemi(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """L'amendement C de LIVE-002, inscrit dans le modele.

    Une manoeuvre coordonnee peut n'avoir que deux regiments en melee, les autres
    tirant a cent vingt metres. Exiger le contact de chacun declarerait absent un
    appui-feu parfaitement en place, et ferait attendre l'assaut apres lui.
    """
    cible = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, x=0.0, z=200.0)
    poste = Vector3(0.0, 0.0, 120.0)
    tireur = make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, x=0.0, z=120.0)
    etat = make_battle([tireur, cible])

    appui = Assignment("a_arc", ManoeuvreRole.FIRE_SUPPORT, staging=poste)
    assert appui.ready(etat, centre=cible.position, targets=("e1",))

    # Meme position, cible hors de portee : en place ne suffit pas.
    lointaine = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, x=0.0, z=400.0)
    assert not appui.ready(
        make_battle([tireur, lointaine]), centre=lointaine.position, targets=("e1",)
    )


def test_un_flanqueur_deja_au_contact_n_est_pas_pret_mais_en_retard(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """C'est exactement le defaut du 18/08 : parti avant tout le monde."""
    poste = Vector3(0.0, 0.0, 120.0)
    libre = make_unit("a_cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, x=0.0, z=120.0)
    engage = make_unit("a_cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, x=0.0, z=120.0, is_engaged=True)
    flanc = Assignment("a_cav", ManoeuvreRole.FLANK, staging=poste)

    assert flanc.ready(make_battle([libre]), centre=poste)
    assert not flanc.ready(make_battle([engage]), centre=poste)


def test_un_fixateur_a_trois_cents_metres_ne_fixe_rien(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Un `HOLD` loin de la zone n'est pas une fixation, c'est une absence."""
    poste = Vector3(0.0, 0.0, 180.0)
    loin = make_unit("a_inf", Side.ALLY, UnitRole.MELEE_INFANTRY, x=0.0, z=-120.0)
    pres = make_unit("a_inf", Side.ALLY, UnitRole.MELEE_INFANTRY, x=0.0, z=170.0)
    fixation = Assignment("a_inf", ManoeuvreRole.FIX, staging=poste)

    assert not fixation.ready(make_battle([loin]), centre=poste)
    assert fixation.ready(make_battle([pres]), centre=poste)


def test_le_contact_attend_les_requis_de_la_manoeuvre_pas_l_armee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Un seuil du type « huit sur douze » mesurerait la masse, pas la coordination.

    `numerical_superiority` gagne ses trois batailles avec une seule unite au
    contact : la condition doit porter sur les participants **requis de cette
    manoeuvre**, et sur eux seuls.
    """
    poste = Vector3(0.0, 0.0, 120.0)
    present = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, x=0.0, z=120.0)
    retard = make_unit("a2", Side.ALLY, UnitRole.MELEE_INFANTRY, x=0.0, z=-300.0)
    spectateur = make_unit("a3", Side.ALLY, UnitRole.MELEE_INFANTRY, x=0.0, z=-300.0)
    etat = make_battle([present, retard, spectateur])

    manoeuvre = Manoeuvre(
        sector=0,
        centre=poste,
        attackers=("a1", "a2"),
        targets=(),
        ratio=2.0,
        assignments=(
            Assignment("a1", ManoeuvreRole.FIX, staging=poste),
            Assignment("a2", ManoeuvreRole.FIX, staging=poste),
        ),
    )
    assert manoeuvre.missing(etat) == ("a2",)
    assert not manoeuvre.can_engage(etat), "a3 n'est pas participante : elle ne compte pas"

    optionnelle = Manoeuvre(
        sector=0,
        centre=poste,
        attackers=("a1", "a2"),
        targets=(),
        ratio=2.0,
        assignments=(
            Assignment("a1", ManoeuvreRole.FIX, staging=poste),
            Assignment("a2", ManoeuvreRole.FIX, staging=poste, required=False),
        ),
    )
    assert optionnelle.can_engage(etat), "un participant optionnel ne retient pas le contact"
