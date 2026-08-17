"""Ce que devient la superiorite locale entre le choix et le contact.

L'instrument doit surtout **ne pas conclure sur une case vide** : c'est le
defaut qu'il a lui-meme eu, et qui lui faisait annoncer « l'avantage tient
jusqu'au contact » alors qu'aucun assaut n'y etait jamais arrive.
"""

from __future__ import annotations

from totalwar_ai.learning.assault import Conversion, Episode, study
from totalwar_ai.telemetry.events import Event, EventType


def _plan(instant: float, *, ratio: float, contact: int, targets: tuple[str, ...]) -> Event:
    return Event(
        battle_id="test",
        type=EventType.PLAN_SELECTED,
        game_time=instant,
        payload={
            "assault": {
                "sector": 1,
                # Quatre assaillants : la moitie en vaut deux, donc « premier
                # contact » et « contact principal » sont deux instants
                # distincts. Avec deux assaillants ils se confondraient, et le
                # test ne verifierait plus rien de la distinction.
                "attackers": ["a", "b", "c", "d"],
                "targets": list(targets),
                "ratio": 1.50,
                "started_at": 0.0,
            },
            "assault_ratio_now": ratio,
            "assault_contact": contact,
        },
    )


def test_les_phases_se_lisent_dans_la_serie_des_plans() -> None:
    """Aucun evenement dedie : le plan journalise a chaque tour suffit."""
    serie = [
        _plan(0.0, ratio=1.50, contact=0, targets=("e1",)),
        _plan(10.0, ratio=1.20, contact=0, targets=("e1",)),
        _plan(20.0, ratio=1.05, contact=1, targets=("e1",)),
        _plan(30.0, ratio=0.96, contact=2, targets=("e1",)),
    ]
    lecture = study(serie)
    assert len(lecture.episodes) == 1
    episode = lecture.episodes[0]
    assert episode.selected == 1.50
    assert episode.first_contact == 1.05
    assert episode.first_contact_delay == 20.0
    assert episode.main_contact == 0.96


def test_un_assaut_jamais_arrive_ne_vaut_aucun_verdict() -> None:
    """Le defaut que l'instrument avait, et qui l'a fait mentir.

    Sans contact, `kept_ratio` vaut `None` et `collapses` vaut faux : la lecture
    tombait dans « l'avantage tient jusqu'au contact » alors qu'il n'y avait
    jamais eu de contact. Mesure reelle qui l'a revele : sur `outnumbered`, douze
    assauts sur douze graines, tous relaches avant d'aboutir.
    """
    serie = [_plan(float(index), ratio=1.4, contact=0, targets=("e1",)) for index in range(5)]
    lecture = study(serie)
    assert lecture.never_reached == 1
    assert lecture.kept_ratio is None
    assert not lecture.collapses
    rendu = lecture.render()
    assert "Aucun assaut n'a atteint son secteur" in rendu
    assert "tient jusqu'au contact" not in rendu


def test_un_avantage_qui_s_evapore_est_designe_comme_tel() -> None:
    """1,50 au choix, 0,96 au contact : 64 % conserve — mesure sur `outnumbered`."""
    lecture = Conversion(
        episodes=[
            Episode(
                sector=1,
                attackers=3,
                selected=1.50,
                started_at=0.0,
                first_contact=0.96,
                first_contact_delay=31.0,
                main_contact=0.96,
            )
            for _ in range(6)
        ]
    )
    assert lecture.kept_ratio is not None
    assert lecture.kept_ratio < 0.7
    assert lecture.collapses
    assert "se volatilise pendant l'approche" in lecture.render()


def test_un_avantage_qui_tient_disculpe_la_mobilite() -> None:
    lecture = Conversion(
        episodes=[
            Episode(
                sector=0,
                attackers=3,
                selected=1.50,
                started_at=0.0,
                first_contact=1.45,
                first_contact_delay=6.0,
                main_contact=1.40,
            )
            for _ in range(6)
        ]
    )
    assert not lecture.collapses
    assert "**pas** la vitesse" in lecture.render()


def test_un_changement_de_secteur_ouvre_un_nouvel_episode() -> None:
    serie = [
        _plan(0.0, ratio=1.5, contact=0, targets=("e1",)),
        _plan(10.0, ratio=1.4, contact=1, targets=("e1",)),
        _plan(20.0, ratio=1.6, contact=0, targets=("e2",)),
        _plan(30.0, ratio=1.5, contact=1, targets=("e2",)),
    ]
    assert len(study(serie).episodes) == 2
