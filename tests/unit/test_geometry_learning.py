"""Ou l'IA du moteur place ses unites — et comment on le mesure sans se mentir.

L'etalonnage ne peut pas se faire ici contre la doublure : elle n'a **aucune**
logique de formation, elle marche droit sur sa cible. Il se fait donc contre des
etats construits a la main, dont la geometrie est connue au metre pres. Le
principe est le meme qu'ailleurs — verifier l'instrument sur une reponse connue
avant de le braquer sur une inconnue.
"""

from __future__ import annotations

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState
from totalwar_ai.learning.geometry import CONTACT_DISTANCE, learn_formation


def _unite(
    unit_id: str,
    role: UnitRole,
    side: Side,
    x: float,
    z: float,
    *,
    engaged: bool = False,
) -> UnitState:
    return UnitState(
        id=unit_id,
        side=side,
        role=role,
        position=Vector3(x, 0.0, z),
        is_engaged=engaged,
    )


def _etat(*unites: UnitState) -> BattleState:
    return BattleState(battle_id="g", game_time=0.0, units=unites)


def _face_a_face(*allies: UnitState, distance: float = 300.0) -> BattleState:
    """Nos unites, et une ligne ennemie droit devant, a `distance` en z."""
    ennemis = [
        _unite("e1", UnitRole.MELEE_INFANTRY, Side.ENEMY, -30.0, distance),
        _unite("e2", UnitRole.MELEE_INFANTRY, Side.ENEMY, 30.0, distance),
    ]
    return _etat(*allies, *ennemis)


# --- ce que la mesure retrouve --------------------------------------------------


def test_la_profondeur_retrouve_un_retrait_connu() -> None:
    """Des tireurs poses quarante metres en arriere doivent ressortir a quarante."""
    etat = _face_a_face(
        _unite("i1", UnitRole.MELEE_INFANTRY, Side.ALLY, -40.0, 20.0),
        _unite("i2", UnitRole.MELEE_INFANTRY, Side.ALLY, 40.0, 20.0),
        _unite("a1", UnitRole.RANGED_INFANTRY, Side.ALLY, -40.0, -20.0),
        _unite("a2", UnitRole.RANGED_INFANTRY, Side.ALLY, 40.0, -20.0),
    )

    modele = learn_formation([etat])
    infanterie = modele.placement(UnitRole.MELEE_INFANTRY)
    tireurs = modele.placement(UnitRole.RANGED_INFANTRY)
    assert infanterie is not None and tireurs is not None
    assert infanterie.depth == 20.0
    assert tireurs.depth == -20.0
    # L'ordre de bataille se lit tel quel : le plus avance d'abord.
    assert [item.role for item in modele.ordered()] == [
        UnitRole.MELEE_INFANTRY,
        UnitRole.RANGED_INFANTRY,
    ]


def test_l_ecart_au_centre_retrouve_les_ailes() -> None:
    """Une cavalerie posee a cent metres sur le flanc doit ressortir a cent."""
    etat = _face_a_face(
        _unite("i1", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, 0.0),
        _unite("c1", UnitRole.SHOCK_CAVALRY, Side.ALLY, -150.0, 0.0),
        _unite("c2", UnitRole.SHOCK_CAVALRY, Side.ALLY, 150.0, 0.0),
    )

    modele = learn_formation([etat])
    cavalerie = modele.placement(UnitRole.SHOCK_CAVALRY)
    infanterie = modele.placement(UnitRole.MELEE_INFANTRY)
    assert cavalerie is not None and infanterie is not None
    assert cavalerie.flank == 150.0
    assert infanterie.flank == 0.0


def test_la_mesure_ne_depend_pas_de_l_orientation_de_la_carte() -> None:
    """Une meme formation tournee de quatre-vingt-dix degres donne les memes chiffres.

    C'est ce qui rend la mesure transportable d'une carte a l'autre : elle est
    relative a l'axe qui va vers l'ennemi, jamais aux axes du terrain.
    """
    nord = _face_a_face(
        _unite("i", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, 30.0),
        _unite("a1", UnitRole.RANGED_INFANTRY, Side.ALLY, -50.0, -15.0),
        _unite("a2", UnitRole.RANGED_INFANTRY, Side.ALLY, 50.0, -15.0),
    )
    # Rotation de 90 degres : (x, z) -> (z, -x), ennemis compris.
    est = _etat(
        _unite("i", UnitRole.MELEE_INFANTRY, Side.ALLY, 30.0, 0.0),
        _unite("a1", UnitRole.RANGED_INFANTRY, Side.ALLY, -15.0, 50.0),
        _unite("a2", UnitRole.RANGED_INFANTRY, Side.ALLY, -15.0, -50.0),
        _unite("e1", UnitRole.MELEE_INFANTRY, Side.ENEMY, 300.0, 30.0),
        _unite("e2", UnitRole.MELEE_INFANTRY, Side.ENEMY, 300.0, -30.0),
    )

    droite = learn_formation([nord]).placement(UnitRole.RANGED_INFANTRY)
    tournee = learn_formation([est]).placement(UnitRole.RANGED_INFANTRY)
    assert droite is not None and tournee is not None
    assert round(droite.depth, 6) == round(tournee.depth, 6)
    assert round(droite.flank, 6) == round(tournee.flank, 6)


def test_l_espacement_est_la_distance_a_l_allie_le_plus_proche() -> None:
    etat = _face_a_face(
        _unite("i1", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, 0.0),
        _unite("i2", UnitRole.MELEE_INFANTRY, Side.ALLY, 25.0, 0.0),
    )

    infanterie = learn_formation([etat]).placement(UnitRole.MELEE_INFANTRY)
    assert infanterie is not None
    assert infanterie.spacing == 25.0


def test_une_dispersion_large_se_publie() -> None:
    """Un role qui va partout ne doit pas passer pour un role qui tient un poste."""
    devant = _face_a_face(
        _unite("i", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, 0.0),
        _unite("c", UnitRole.SHOCK_CAVALRY, Side.ALLY, 0.0, 100.0),
    )
    derriere = _face_a_face(
        _unite("i", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, 0.0),
        _unite("c", UnitRole.SHOCK_CAVALRY, Side.ALLY, 0.0, -100.0),
    )

    cavalerie = learn_formation([devant, derriere]).placement(UnitRole.SHOCK_CAVALRY)
    assert cavalerie is not None
    assert cavalerie.depth_spread > 50.0, "une position tres variable est passee pour stable"


# --- ce que la mesure refuse ----------------------------------------------------


def test_la_melee_n_est_pas_une_formation() -> None:
    """Des que les lignes se melangent, on mesurerait du desordre."""
    etat = _face_a_face(
        _unite("i1", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, 0.0, engaged=True),
        _unite("i2", UnitRole.MELEE_INFANTRY, Side.ALLY, 30.0, 0.0),
    )

    modele = learn_formation([etat])
    assert modele.samples == 0
    assert modele.skipped == 1
    assert not modele.placements


def test_deux_armees_au_contact_ne_comptent_pas() -> None:
    """Le contact defait la formation avant meme que la melee ne s'engage."""
    proche = _face_a_face(
        _unite("i1", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, 0.0),
        _unite("i2", UnitRole.MELEE_INFANTRY, Side.ALLY, 30.0, 0.0),
        distance=CONTACT_DISTANCE - 10.0,
    )

    assert learn_formation([proche]).samples == 0


def test_une_armee_seule_ne_donne_aucun_axe() -> None:
    """Sans adversaire, « devant » n'a pas de sens."""
    etat = _etat(
        _unite("i1", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, 0.0),
        _unite("i2", UnitRole.MELEE_INFANTRY, Side.ALLY, 30.0, 0.0),
    )

    assert learn_formation([etat]).samples == 0


def test_un_corpus_vide_le_dit() -> None:
    modele = learn_formation([])
    assert modele.samples == 0
    assert "Aucune formation mesurable" in modele.render()
