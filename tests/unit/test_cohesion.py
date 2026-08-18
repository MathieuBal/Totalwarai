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
    UnitTimeline,
    from_battle_log,
    study,
)


def _t(unit: str, ordre: float | None = None, contact: float | None = None) -> UnitTimeline:
    return UnitTimeline(unit_id=unit, first_order_at=ordre, first_contact_at=contact)


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


# --- ce que la mesure ne doit pas compter ------------------------------------


def test_les_ordres_anterieurs_a_deployed_ne_comptent_pas(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Le defaut que la baseline reelle a revele dans cette mesure meme.

    Sur la bataille du 18/08, les douze unites ont toutes recu leur premier ordre
    a 3,1 s — avant que `Deployed` ne commence a 7,6 s. La mesure annoncait donc
    « armee ordonnee a 100 % » alors que le moteur avait acquitte ces ordres sans
    en executer un seul, et que trois unites seulement se mettaient en mouvement.
    """
    lignes = [
        json.dumps(
            {
                "game_time_ms": 3100,
                "phase": "unknown",
                "decision": True,
                "orders": {
                    "moves": [{"unit_id": u} for u in ("a", "b", "c")],
                    "attacks": [],
                    "halts": [],
                },
            }
        ),
        json.dumps(
            {
                "game_time_ms": 10000,
                "phase": "Deployed",
                "decision": True,
                "orders": {"moves": [{"unit_id": "a"}], "attacks": [], "halts": []},
            }
        ),
    ]
    cohesion = from_battle_log(lignes)
    ordonnees = {item.unit_id: item.first_order_at for item in cohesion.units}
    assert ordonnees == {"a": 10.0}, "seuls les ordres qui prennent effet comptent"


def test_un_contact_se_lit_dans_l_inventaire_des_unites() -> None:
    lignes = [
        json.dumps(
            {
                "game_time_ms": 10000,
                "phase": "Deployed",
                "decision": True,
                "orders": {"moves": [{"unit_id": "a"}], "attacks": [], "halts": []},
            }
        ),
        json.dumps(
            {"game_time_ms": 50000, "phase": "Deployed", "units": [{"id": "a", "in_melee": True}]}
        ),
    ]
    cohesion = from_battle_log(lignes)
    assert cohesion.first_contact_time == 50.0


def test_une_armee_qui_n_engage_jamais_le_dit() -> None:
    """Zero contact et « contact immediat » ne doivent pas se lire pareil."""
    cohesion = study([_t("a", 0.0), _t("b", 0.0)])
    assert cohesion.first_contact_time is None
    assert "aucun contact" in cohesion.render()
