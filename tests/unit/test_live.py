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
    assert lecture.longest_no_command_window == 399.0


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


DEPLOYED = "<44.0s><8400ms> \tBattle is now entering phase: Deployed"
SONDE_DEPLOYED = "<44.0s><8400ms> [totalwar_ai] phase : Deployed"
COUNTDOWN = "<99.0s><900000ms> \tBattle is now entering phase: VictoryCountdown"


def test_deux_accuses_de_meme_sequence_ne_font_qu_un_lot() -> None:
    """Le dictionnaire seul ne suffisait pas.

    `lots` etait bien indexe par `sequence`, mais `ordre.append()` etait
    inconditionnel : deux `ACK accepted` de meme sequence donnaient **une** entree
    et **deux** `Batch`. Le contrat annonce n'etait donc pas tenu.
    """
    lecture = read([DEPLOYED, _ack(200, ms=10000), _ack(200, ms=10000)])
    assert len(lecture.batches) == 1
    assert lecture.launched == 2, "le lot ne doit pas etre compte deux fois"


def test_un_accuse_sans_compteur_d_ordres_n_est_pas_un_lot() -> None:
    """Un accuse de protocole n'est pas une commande.

    Sans ce contrat, un futur `DELEGATE accepted` creerait un faux lot, couperait
    une fenetre de silence en deux, et raccourcirait
    `longest_no_command_window` sans que rien ne le signale.
    """
    protocole = (
        '<50.0s><100000ms> [totalwar_ai] ACK {"protocol_version":"0.1.0",'
        '"type":"action_result","sequence":9,"status":"accepted","error":null,'
        '"detail":{"note":"delegation acceptee"}}'
    )
    lecture = read([DEPLOYED, _ack(1, ms=10000), protocole, _ack(2, ms=200000)])
    assert len(lecture.batches) == 2, "l'accuse de protocole ne cree pas de lot"
    assert lecture.longest_no_command_window == 190.0, "et ne coupe pas le silence"


def test_la_borne_vient_de_l_evenement_de_phase_pas_du_heartbeat() -> None:
    """Le defaut qui a fait compter trois lots pre-`Deployed` au lieu de deux.

    `log_occasionally` ne journalise que les occurrences 1, 2, 3 puis une sur
    vingt : le heartbeat `BATTLE phase Deployed` est deliberement epars. Sur le
    journal du 18/08 il arrive a 11,1 s alors que la phase a change a **8,4 s**.

    *Le premier evenement qui produit une valeur n'est pas l'instant ou l'etat est
    devenu vrai.*
    """
    heartbeat = (
        "<57.6s><11100ms> [totalwar_ai] BATTLE phase Deployed : 12 allies, 10 ennemis "
        "(occurrence 20)"
    )
    lecture = read([SONDE_DEPLOYED, _ack(1, ms=3100), _ack(2, ms=10600), heartbeat])
    assert lecture.active_from == 8.4, "la borne est celle du changement de phase"
    assert len(lecture.pre_deployed) == 1, "seul le lot de 3,1 s precede Deployed"
    assert len(lecture.batches) == 1, "celui de 10,6 s est deja effectif"


def test_les_ordres_avant_deployed_sont_comptes_a_part() -> None:
    """Le moteur les acquitte et ne les execute pas.

    Notre propre Lua le documente : « un ordre emis avant `Deployed` est accepte
    par le moteur mais ne produit aucun deplacement ». Les mêler aux commandes
    reelles fait croire a une activite qui n'a rien produit.
    """
    lecture = read([DEPLOYED, _ack(1, launched=12, ms=3100), _ack(2, launched=3, ms=10600)])
    assert [item.sequence for item in lecture.pre_deployed] == [1]
    assert lecture.launched == 3, "seuls les ordres effectifs comptent"
    assert "PRE_DEPLOYED_COMMAND" in lecture.render()


def test_une_paralysie_finale_est_vue_malgre_l_absence_de_commande_suivante() -> None:
    """`pairwise` seul ne voit que les creux **entre** deux commandes.

    Un agent qui joue puis se tait jusqu'a la defaite n'a jamais de « commande
    suivante » : sa paralysie n'etait comptee nulle part, et la bataille la plus
    muette affichait `longest_no_command_window = 0`.
    """
    lecture = read([DEPLOYED, _ack(1, ms=10000), COUNTDOWN])
    assert lecture.closing_at == 900.0
    assert lecture.longest_no_command_window == 890.0
    assert lecture.silences[0].end == 900.0


def test_une_paralysie_initiale_est_vue_elle_aussi() -> None:
    """Entre `Deployed` et la premiere commande, l'agent est deja attendu."""
    lecture = read([DEPLOYED, _ack(1, ms=300000)])
    assert lecture.silences[0].start == 8.4
    assert lecture.longest_no_command_window == 291.6


def test_le_decompte_de_victoire_borne_la_fenetre_avant_la_fin_reelle() -> None:
    """Apres `VictoryCountdown`, le combat est decide.

    Un silence a ce moment-la n'est plus une abstention, et le compter ferait
    passer toutes les batailles gagnees pour des paralysies.
    """
    complete = "<99.9s><1000000ms> \tBattle is now entering phase: Complete"
    lecture = read([DEPLOYED, _ack(1, ms=10000), COUNTDOWN, complete])
    assert lecture.closing_at == 900.0, "le decompte prime sur la fin reelle"


def test_un_lot_entierement_refuse_reste_une_commande() -> None:
    """Une commande partie et morte au jeu n'est pas une absence de commande.

    Le journal du 18/08 contient quatre accuses `rejected` — « 0 ordre(s)
    lance(s), 1 refuse(s) » — que le lecteur ecartait. Les ignorer allongeait
    artificiellement les fenetres de silence et rendait un refus integral
    invisible, alors que c'est exactement le genre d'evenement que LIVE-001
    cherche.
    """
    refuse = (
        '<50.0s><50000ms> [totalwar_ai] ACK {"protocol_version":"0.1.0",'
        '"type":"action_result","sequence":9,"status":"rejected",'
        '"error":"1002 : unite non controlable",'
        '"detail":{"note":"0 ordre(s) lance(s), 1 refuse(s)"}}'
    )
    lecture = read([DEPLOYED, _ack(1, ms=10000), refuse])
    assert len(lecture.batches) == 2, "le lot refuse compte comme une commande"
    assert lecture.refused == 1
    assert lecture.batches[-1].launched == 0


def test_un_relachement_de_controle_n_est_toujours_pas_une_commande() -> None:
    """Le garde-fou doit rester ferme sur `released`.

    Le journal du 18/08 en contient 679 : les compter ferait passer une bataille
    de cent lots pour une bataille de huit cents.
    """
    relache = (
        '<51.0s><51000ms> [totalwar_ai] ACK {"protocol_version":"0.1.0",'
        '"type":"action_result","sequence":10,"status":"released","error":null,'
        '"detail":{"note":"controle rendu apres delai"}}'
    )
    lecture = read([DEPLOYED, _ack(1, ms=10000), relache])
    assert len(lecture.batches) == 1
