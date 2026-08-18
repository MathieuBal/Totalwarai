"""Le journal de sonde devient une table de verdicts, sans en inventer.

Quatre sessions de jeu ont ete depouillees a l'oeil sur des centaines de lignes.
Ces tests pincent ce qu'un depouillement a l'oeil rate : la difference entre
« absent » et « pas teste », et l'accesseur qui ne dit rien du tout.
"""

from __future__ import annotations

from pathlib import Path

from totalwar_ai.learning.census import (
    ABSENT,
    OK,
    UNTESTED,
    expected_accessors,
    read,
)

LUA = (
    Path(__file__).resolve().parents[2]
    / "lua_mod"
    / "script"
    / "battle"
    / "mod"
    / "totalwar_ai_probe.lua"
)

JOURNAL = """
20:11:02.1 [totalwar_ai] sonde totalwar_ai (revision 15)
20:11:02.2 [totalwar_ai] --- recensement des accesseurs (allie : wh_main_emp_inf_spearmen) ---
20:11:02.3 [totalwar_ai]   API type OK value=wh_main_emp_inf_spearmen
20:11:02.4 [totalwar_ai]   API fatigue_state OK value=3
20:11:02.5 [totalwar_ai]   API speed ABSENT error=pas une fonction
20:11:02.6 [totalwar_ai]   ATTR fire_while_moving OK value=false
20:11:02.7 [totalwar_ai]   ATTR causes_terror ABSENT error=attribut inconnu
20:11:02.8 [totalwar_ai]   API is_visible_to_alliance OK value=true sur=ennemi
20:11:02.9 [totalwar_ai]   API can_reach_position NON TESTE raison=aucune unite allie
20:11:03.0 [totalwar_ai]   API bm:get_terrain_height OK value=118.5
20:11:03.1 [totalwar_ai] ] STATE une ligne d'etat qui ne doit pas etre lue
20:11:03.2 [totalwar_ai] MISSILE t0 ordre envoye unite=1234 type=wh_main_emp_inf_archers fire_while_moving=false ammo=1
20:11:03.3 [totalwar_ai] MISSILE t1 1234 en marche a 400 ms
20:11:03.4 [totalwar_ai] MISSILE t2 1234 arret a 5200 ms
20:11:03.5 [totalwar_ai] MISSILE t3 1234 cible a 5600 ms
20:11:03.6 [totalwar_ai] MISSILE t4 1234 premiere salve a 6100 ms en_marche=false apres_arret=900
""".strip().splitlines()


def _par_nom(census, nom):  # type: ignore[no-untyped-def]
    return next(item for item in census.findings if item.name == nom)


def test_les_trois_verdicts_ne_se_confondent_pas() -> None:
    """`ABSENT` et `NON TESTE` ne disent pas la meme chose.

    La revision 14 avait declare le moral « structurellement absent » apres
    l'avoir demande sous un mauvais nom. Confondre les deux est exactement
    l'erreur que la revision 15 existe pour empecher : l'une parle du jeu,
    l'autre seulement de la session.
    """
    census = read(JOURNAL)
    assert _par_nom(census, "type").verdict == OK
    assert _par_nom(census, "speed").verdict == ABSENT
    assert _par_nom(census, "can_reach_position").verdict == UNTESTED
    assert len(census.untested) == 1, "un non-teste ne doit pas grossir le compte des absents"


def test_les_valeurs_et_le_camp_sont_conserves() -> None:
    """Un accesseur a argument dit sur quel camp il a repondu."""
    census = read(JOURNAL)
    assert _par_nom(census, "fatigue_state").value == "3"
    visible = _par_nom(census, "is_visible_to_alliance")
    assert visible.verdict == OK
    assert visible.target == "ennemi", "sans le camp, la reponse ne veut rien dire"


def test_les_attributs_et_les_methodes_de_bm_sont_distingues() -> None:
    census = read(JOURNAL)
    assert _par_nom(census, "fire_while_moving").kind == "ATTR"
    assert _par_nom(census, "bm:get_terrain_height").kind == "bm"


def test_les_lignes_d_etat_ne_sont_pas_lues() -> None:
    """Les etats periodiques noient le recensement : ils sont ecartes."""
    census = read(JOURNAL)
    assert all("etat" not in item.name for item in census.findings)


def test_un_accesseur_attendu_et_muet_est_signale() -> None:
    """Le cas le plus dangereux : celui qui ne produit aucune ligne.

    Un accesseur attendu qui n'apparait nulle part ne produit ni OK, ni absent,
    ni non-teste — donc aucune alerte, et il se confond avec un accesseur jamais
    inscrit au recensement.
    """
    census = read(JOURNAL, expected=("type", "speed", "current_target", "is_wavering"))
    assert census.silent == ("current_target", "is_wavering")


def test_la_liste_attendue_se_lit_dans_la_source_lua() -> None:
    """Une seule liste de reference, et elle vit dans la sonde.

    La recopier en Python la ferait deriver — le defaut exact que l'ADR 0019 a
    coute a debusquer sur `STILL_DISTANCE`.
    """
    attendus = expected_accessors(LUA.read_text(encoding="utf-8"))
    # Les trois familles doivent y figurer : methode simple, attribut, argument.
    assert "fatigue_state" in attendus
    assert "fire_while_moving" in attendus
    assert "is_visible_to_alliance" in attendus
    assert "can_reach_position" in attendus
    assert len(attendus) > 40, "le recensement porte sur une centaine d'accesseurs"


def test_le_chronometre_du_tir_est_reconstitue() -> None:
    """La mesure qui juge un correctif du simulateur."""
    census = read(JOURNAL)
    tir = census.missile
    assert tir.conclusive
    assert tir.unit == "1234"
    assert tir.moving_at == 400.0
    assert tir.stopped_at == 5200.0
    assert tir.target_at == 5600.0
    assert tir.volley_at == 6100.0
    assert tir.volley_while_moving is False
    assert tir.volley_after_stop == 900.0
    assert "n'est partie qu'a l'arret" in tir.explain()


def test_sans_salve_le_chronometre_refuse_de_conclure() -> None:
    """Un instrument qui tranche sur une case vide est un instrument qui ment.

    C'est le defaut que l'ADR 0018 a du corriger sur la conversion d'assaut :
    sans contact, la lecture annoncait « l'avantage tient jusqu'au contact ».
    """
    partiel = [ligne for ligne in JOURNAL if "MISSILE t4" not in ligne]
    census = read(partiel)
    assert not census.missile.conclusive
    assert "non tranche" in census.missile.explain()


def test_un_chronometrage_avorte_dit_pourquoi() -> None:
    census = read(["[totalwar_ai] MISSILE aucune unite de tir : chronometrage impossible"])
    assert census.missile.aborted is not None
    assert "Chronometrage impossible" in census.missile.explain()


def test_la_revision_du_pack_est_relevee() -> None:
    assert read(JOURNAL).revision == 15


def test_un_journal_sans_recensement_le_dit() -> None:
    census = read(["[totalwar_ai] sonde totalwar_ai (revision 15)"])
    assert "Aucune ligne de recensement" in census.render()


def test_une_valeur_a_plusieurs_mots_n_est_pas_tronquee() -> None:
    """`describe()` rend des noms et des tables, pas seulement des nombres.

    Une valeur coupee au premier espace ferait passer « Empire Spearmen of
    Altdorf » pour « Empire », et « table de 3 elements » pour « table ».
    """
    census = read(
        [
            "[totalwar_ai]   API name OK value=Empire Spearmen of Altdorf",
            "[totalwar_ai]   API owned_special_abilities OK value=table de 3 elements",
            "[totalwar_ai]   API foo ABSENT error=attempt to call a nil value (method foo)",
        ]
    )
    assert _par_nom(census, "name").value == "Empire Spearmen of Altdorf"
    assert _par_nom(census, "owned_special_abilities").value == "table de 3 elements"
    assert _par_nom(census, "foo").detail == "attempt to call a nil value (method foo)"


def test_le_camp_se_distingue_d_une_valeur_qui_finit_par_un_mot() -> None:
    """`sur=` suit la valeur : il ne doit ni la manger ni s'y noyer."""
    census = read(["[totalwar_ai]   API is_visible_to_alliance OK value=true sur=ennemi"])
    trouvaille = census.findings[0]
    assert trouvaille.value == "true"
    assert trouvaille.target == "ennemi"
