"""Le journal de sonde devient une table de verdicts, sans en inventer.

Quatre sessions de jeu ont ete depouillees a l'oeil sur des centaines de lignes.
Ces tests pincent ce qu'un depouillement a l'oeil rate : la difference entre
« absent » et « pas teste », et l'accesseur qui ne dit rien du tout.
"""

from __future__ import annotations

from pathlib import Path

from totalwar_ai.learning.census import (
    ABSENT,
    ERROR,
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


# --- ERREUR n'est pas une absence --------------------------------------------


def test_un_accesseur_qui_leve_n_est_pas_un_accesseur_absent() -> None:
    """Le Lua ecrit `ABSENT error=` dans deux cas qui n'ont rien de commun.

    L'un dit que le jeu n'expose pas l'accesseur, l'autre qu'il l'expose et que
    **notre appel** est en cause. C'est la confusion qui a fait declarer le moral
    « structurellement absent » en revision 14, apres l'avoir demande sous un
    mauvais nom — une session de jeu perdue pour une distinction manquante.
    """
    census = read(
        [
            "[totalwar_ai]   API speed ABSENT error=pas une fonction",
            "[totalwar_ai]   API fatigue_state ABSENT error=attempt to index a nil value",
            "[totalwar_ai]   ATTR causes_terror ABSENT error=attribut inconnu",
        ]
    )
    assert _par_nom(census, "speed").verdict == ABSENT
    assert _par_nom(census, "fatigue_state").verdict == ERROR
    assert _par_nom(census, "causes_terror").verdict == ERROR
    assert len(census.absent) == 1
    assert len(census.failed) == 2
    assert "l'accesseur existe et l'appel a leve" in census.render()


# --- familles que le lecteur ignorait ----------------------------------------


def test_les_methodes_recensees_par_presence_sont_lues() -> None:
    """`script_ai_planner` et les methodes d'armee ne sont pas appelees.

    Les appeler aurait des effets de bord, et un recensement doit rester sans
    consequence sur la bataille : le verdict porte donc sur l'existence seule.
    """
    census = read(
        [
            "[totalwar_ai]   rush_force : presente",
            "[totalwar_ai]   attack_force : ABSENT",
        ]
    )
    assert _par_nom(census, "rush_force").verdict == OK
    assert _par_nom(census, "attack_force").verdict == ABSENT
    assert _par_nom(census, "rush_force").kind == "meth"


def test_le_handicap_d_armee_porte_sa_valeur_et_son_camp() -> None:
    census = read(
        [
            "[totalwar_ai]   nous alliance 1 armee 1 army_handicap : 0",
            "[totalwar_ai]   eux alliance 2 armee 1 unit_count : 8",
        ]
    )
    handicap = _par_nom(census, "army_handicap")
    assert handicap.verdict == OK
    assert handicap.value == "0"
    assert handicap.target == "nous"
    assert _par_nom(census, "unit_count").target == "eux"


def test_une_ligne_d_alliance_n_est_pas_prise_pour_une_methode() -> None:
    """`alliances : 2, locale = 1` a la meme forme qu'un verdict de presence.

    Sans discrimination sur le verdict lui-meme, elle serait comptee comme un
    accesseur nomme « alliances » — un accesseur invente de toutes pieces.
    """
    census = read(["[totalwar_ai]   alliances : 2, locale = 1"])
    assert census.findings == []


# --- le relief ----------------------------------------------------------------


def test_les_trois_sources_d_altitude_sont_comparees() -> None:
    """Savoir laquelle croire decide de tout usage du relief par l'agent."""
    census = read(
        ["[totalwar_ai]   CONCORDANCE unit_y=118.62 v_to_ground=118.50 get_terrain_height=118.55"]
    )
    assert len(census.terrain.available) == 3
    assert census.terrain.consistent is True
    assert census.terrain.spread is not None and census.terrain.spread < 0.2
    assert "s'accordent" in census.terrain.explain()


def test_un_desaccord_d_altitude_est_annonce_comme_tel() -> None:
    """Deux sources qui divergent ne mesurent pas la meme chose."""
    census = read(
        ["[totalwar_ai]   CONCORDANCE unit_y=130.00 v_to_ground=118.50 get_terrain_height=118.55"]
    )
    assert census.terrain.consistent is False
    assert "divergent" in census.terrain.explain()


def test_une_source_seule_ne_permet_aucune_concordance() -> None:
    """Inconnu n'est pas la meme chose que desaccord.

    Le Lua ecrit `indisponible` quand une source n'a pas repondu. Conclure a
    l'accord sur une seule valeur serait trancher sur une case vide.
    """
    census = read(
        [
            "[totalwar_ai]   CONCORDANCE unit_y=118.62 v_to_ground=indisponible "
            "get_terrain_height=indisponible"
        ]
    )
    assert census.terrain.consistent is None
    assert "reste inconnue" in census.terrain.explain()


def test_le_relief_compte_comme_teste_pour_les_accesseurs_muets() -> None:
    """La concordance prouve que les deux sources ont ete demandees.

    Sans cela, `v_to_ground` et `get_terrain_height` seraient signales « attendus
    et muets » alors qu'ils viennent precisement de repondre.
    """
    census = read(
        ["[totalwar_ai]   CONCORDANCE unit_y=118.62 v_to_ground=118.50 get_terrain_height=118.55"],
        expected=("v_to_ground", "get_terrain_height", "fatigue_state"),
    )
    assert census.silent == ("fatigue_state",)


def test_les_sondes_de_sol_sont_conservees() -> None:
    census = read(["[totalwar_ai]   sol en (100, 200) : 121.4"])
    assert census.soil == ("(100, 200) -> 121.4",)
