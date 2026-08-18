"""Ce que l'agent a fait en bataille reelle — compte une fois, pas deux.

Le premier compte rendu tire du journal du 18/08 annoncait « 204 manoeuvres,
4 refus » la ou il y en avait **102 et 2** : chaque lot y figure deux fois, un
`ACK` structure puis une ligne `manoeuvre` lisible, et compter les lignes
revenait a compter deux representations du meme evenement.
"""

from __future__ import annotations

from totalwar_ai.learning.live import read

ACK = (
    '<10.0s><1000ms> [totalwar_ai] ACK {{"protocol_version":"0.1.0",'
    '"type":"action_result","sequence":{seq},"status":"accepted",'
    '"error":{err},"detail":{{"note":"{launched} ordre(s) lance(s), '
    '{refused} refuse(s)"}}}}'
)


def _ack(seq: int, launched: int = 2, refused: int = 0, err: str = "null", ms: int = 1000) -> str:
    return ACK.format(seq=seq, err=err, launched=launched, refused=refused).replace(
        "<1000ms>", f"<{ms}ms>"
    )


def _manoeuvre(moves: int, attacks: int, halts: int, launched: int, refused: int, ms: int) -> str:
    return (
        f"<0.0s><{ms}ms> [totalwar_ai] manoeuvre : {moves} deplacement(s), "
        f"{attacks} attaque(s), {halts} arret(s) — {launched} ordre(s) lance(s), "
        f"{refused} refuse(s)"
    )


def test_un_lot_journalise_deux_fois_ne_compte_qu_une_fois() -> None:
    """Le defaut exact, dans sa forme la plus nue.

    `ACK` et `manoeuvre` decrivent le meme lot de commandes. La ligne lisible
    enrichit le lot ; elle n'en cree jamais un second.
    """
    lecture = read(
        [
            _ack(1, launched=4, refused=1, ms=1000),
            _manoeuvre(3, 2, 0, launched=4, refused=1, ms=1000),
        ]
    )
    assert len(lecture.batches) == 1, "un evenement, un lot"
    assert lecture.launched == 4
    assert lecture.refused == 1
    assert lecture.requested == 5
    assert lecture.batches[0].attacks == 2, "la ventilation vient de la ligne manoeuvre"


def test_les_accuses_de_relachement_ne_sont_pas_des_lots() -> None:
    """Rendre le controle au joueur n'est pas commander.

    Le journal du 18/08 en contient 215 : les compter ferait passer une bataille
    de cent lots pour une bataille de trois cents.
    """
    lecture = read(
        [
            _ack(1, ms=1000),
            '<11.0s><1100ms> [totalwar_ai] ACK {"protocol_version":"0.1.0",'
            '"type":"action_result","sequence":2,"status":"released","error":null,'
            '"detail":{"note":"controle rendu apres delai"}}',
        ]
    )
    assert len(lecture.batches) == 1


def test_une_fenetre_sans_ordre_est_rapportee_avec_son_contexte() -> None:
    """C'est l'ecart que ce module existe pour mesurer.

    En bataille reelle, l'agent est reste 364 s sans emettre une commande pendant
    que son armee passait de 12 a 9 unites.
    """
    lecture = read(
        [
            "<0.0s><1000ms> [totalwar_ai] BATTLE phase Deployed : 12 allies, 10 ennemis",
            _ack(1, ms=1000),
            "<0.0s><200000ms> [totalwar_ai] BATTLE phase Deployed : 9 allies, 10 ennemis",
            _ack(2, ms=400000),
        ]
    )
    assert len(lecture.silences) == 1
    fenetre = lecture.silences[0]
    assert fenetre.duration == 399.0
    assert fenetre.allies_before == 12
    assert fenetre.allies_after == 9
    assert fenetre.allies_lost == 3
    assert lecture.longest_no_action_window == 399.0


def test_un_silence_sans_motif_le_dit_au_lieu_de_l_inventer() -> None:
    """Le journal du jeu porte les ordres, jamais les raisons de n'en donner aucun.

    Un instrument qui inventerait la cause d'un silence serait pire qu'un
    silence : c'est la lecon des trois instruments qui ont menti avant lui.
    """
    lecture = read([_ack(1, ms=1000), _ack(2, ms=100000)])
    assert lecture.silences[0].reason is None
    rendu = lecture.render()
    assert "reason=inconnu" in rendu
    assert "ne veut pas dire" in rendu


def test_le_delai_avant_la_premiere_attaque_est_mesure() -> None:
    """Un agent qui commande sans jamais attaquer n'a pas livre bataille."""
    lecture = read(
        [
            "<0.0s><1000ms> [totalwar_ai] BATTLE phase Deployed : 12 allies, 10 ennemis",
            _ack(1, ms=1000),
            _manoeuvre(2, 0, 0, launched=2, refused=0, ms=1000),
            "<0.0s><50000ms> [totalwar_ai] BATTLE phase Deployed : 10 allies, 10 ennemis",
            _ack(2, ms=60000),
            _manoeuvre(0, 1, 0, launched=1, refused=0, ms=60000),
        ]
    )
    assert lecture.time_to_first_attack == 60.0
    assert lecture.losses_before_first_attack == 2
    assert lecture.enemy_losses_before_first_attack == 0


def test_sans_aucune_attaque_le_delai_est_absent_et_non_nul() -> None:
    """Zero seconde et « jamais » ne sont pas la meme chose."""
    lecture = read([_ack(1, ms=1000), _manoeuvre(2, 0, 0, launched=2, refused=0, ms=1000)])
    assert lecture.time_to_first_attack is None
    assert "**jamais**" in lecture.render()


def test_un_journal_sans_commande_le_dit() -> None:
    lecture = read(["<0.0s><1000ms> [totalwar_ai] sonde active - protocole 0.1.0"])
    assert "n'a jamais pilote" in lecture.render()
