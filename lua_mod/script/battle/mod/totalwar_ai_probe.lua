--[[----------------------------------------------------------------------------
    TotalWarAI — sonde de faisabilite
    ------------------------------------------------------------------------
    Objectif unique : prouver qu'un aller-retour complet est possible entre une
    bataille solo de Total War: WARHAMMER III et un programme Python.

        1. reperer une unite alliee controlable ;
        2. ecrire son identifiant et sa position dans un fichier ;
        3. lire une commande de deplacement ecrite par Python ;
        4. deplacer l'unite ;
        5. renvoyer un accuse ;
        6. rendre le controle au joueur.

    Ce n'est PAS l'agent : aucune tactique, aucune gestion d'armee, une seule
    unite a la fois. Tout le reste vit cote Python.

    Ce fichier est une implementation originale. Les API utilisees ont ete
    identifiees en etudiant la documentation de modding et le mod AI General 3
    (voir docs/research/ai-general-3-findings.md) ; aucune portion de ce mod
    n'est reprise ici.

    SECURITE
        - refus categorique en multijoueur ;
        - toute unite prise est relachee au bout de release_after_ms ;
        - la presence du fichier `totalwar_ai_stop` libere tout et coupe la
          lecture des commandes, meme si plus rien d'autre ne fonctionne ;
        - une commande deja traitee n'est jamais rejouee.
------------------------------------------------------------------------------]]

-- Numero de revision du script, incremente a chaque modification de ce fichier.
--
-- Le pack doit etre reconstruit apres toute modification, et l'oubli est le
-- diagnostic le plus frequent de ce projet : quatre essais en bataille y sont
-- passes. Ce numero apparait dans le journal, et `probe --log` le compare a
-- celui du depot — la question « mon pack est-il a jour ? » se repond alors
-- sans avoir a la poser.
TOTALWAR_AI_PROBE_REVISION = 15

-- PREMIERE LIGNE EXECUTEE. Elle doit apparaitre dans le journal du jeu des que
-- le fichier est charge, quel que soit le contexte (frontend, campagne,
-- bataille) et quoi qu'il advienne ensuite. Son absence signifie que le jeu
-- n'a pas trouve le fichier — pas que la sonde a echoue.

out(
    "[totalwar_ai] === fichier charge (sonde v0.1.0, revision "
        .. TOTALWAR_AI_PROBE_REVISION
        .. ") ==="
)

-- Le meme fichier peut etre place a deux emplacements dans le pack, par
-- prudence. Sans cette garde, il tournerait en double.
if totalwar_ai_probe_loaded then
    out("[totalwar_ai] deja charge : second exemplaire ignore")
    return totalwar_ai_probe_loaded
end

local PROBE = {
    protocol_version = "0.1.0",
    mod_key = "totalwar_ai_probe",

    -- Chemins relatifs au repertoire de travail du jeu, comme le fait
    -- AI General 3 avec `./mod_config/`.
    dir = "./totalwar_ai/",
    state_file = "./totalwar_ai/totalwar_ai_state.jsonl",
    command_file = "./totalwar_ai/totalwar_ai_command.json",
    ack_file = "./totalwar_ai/totalwar_ai_ack.jsonl",
    stop_file = "./totalwar_ai/totalwar_ai_stop",

    poll_interval_ms = 500,
    -- Deux etats par seconde. La boucle Python decide une fois par seconde ;
    -- observer plus finement coute peu et rend l'inference des decisions
    -- nettement plus sure — c'est sur ces etats que l'agent apprendra a jouer
    -- comme l'IA du moteur. Repasser a 1000 si le jeu s'en trouve ralenti.
    state_interval_ms = 500,

    sequence = 0,             -- compteur des etats emis
    last_command_sequence = 0, -- derniere commande executee : jamais rejouee
    controlled = {},          -- ui_id -> { uc = unitcontroller, release_at_ms = number }
    aborted = false,
    can_write = nil,          -- resultat du test d'ecriture, evalue une seule fois
    phase = "unknown",        -- derniere phase de bataille annoncee par le jeu
    ai_planner = nil,         -- script_ai_planner du jeu, quand des unites lui sont confiees
    delegated = {},           -- ui_id -> true, les unites actuellement confiees
}

--- Phases annoncees par le jeu, dans l'ordre observe en bataille.
---
--- Un ordre emis avant `Deployed` est accepte par le moteur mais ne produit
--- aucun deplacement : constate en jeu, unite immobile 33 secondes durant apres
--- un ordre acquitte. Publier la phase permet a Python de le savoir au lieu de
--- conclure a une panne.
local PHASES = { "Startup", "PrebattleWeather", "PrebattleCinematic", "Deployment", "Deployed" }

--[[--------------------------------------------------------------------------
    Journalisation
    Tout passe aussi par `out()` : si l'ecriture de fichier s'avere impossible
    en contexte bataille, le log du jeu reste un canal de repli exploitable.
----------------------------------------------------------------------------]]

function PROBE:log(msg)
    out("[totalwar_ai] " .. tostring(msg))
end

--[[--------------------------------------------------------------------------
    Encodage JSON minimal
    Le sous-ensemble strictement necessaire aux deux messages sortants : pas de
    bibliotheque a embarquer, pas de dependance a la presence d'un module JSON.
----------------------------------------------------------------------------]]

local function escape_string(text)
    text = tostring(text)
    text = string.gsub(text, "\\", "\\\\")
    text = string.gsub(text, '"', '\\"')
    text = string.gsub(text, "\n", "\\n")
    text = string.gsub(text, "\r", "\\r")
    text = string.gsub(text, "\t", "\\t")
    return text
end

local function json_string(text)
    return '"' .. escape_string(text) .. '"'
end

--- Formate un nombre pour du JSON, sans jamais toucher a `math`.
---
--- L'environnement Lua du jeu est restreint : `math.huge` y vaut nil, ce qui a
--- fait echouer la sonde au troisieme essai. On evite donc toute la
--- bibliotheque `math`, y compris `math.floor`, dont la presence n'est pas
--- davantage garantie.
---
--- * NaN se reconnait a ce qu'il est different de lui-meme ;
--- * l'infini se reconnait a ce que `x * 0` ne vaut pas 0 (un fini, si).
local function json_number(value)
    if type(value) ~= "number" then
        return "0"
    end
    if value ~= value or value * 0 ~= 0 then
        return "0" -- ni NaN ni infini dans un JSON valide
    end
    if value % 1 == 0 then
        return string.format("%d", value)
    end
    return string.format("%.3f", value)
end

--[[--------------------------------------------------------------------------
    Analyse JSON minimale
    On n'analyse que ce que Python peut envoyer : un objet plat, plus un objet
    `destination` a trois nombres. Tout le reste est rejete plutot que devine.
----------------------------------------------------------------------------]]

--- Extrait la valeur texte d'une cle : "cle" : "valeur"
local function read_string_field(text, key)
    return string.match(text, '"' .. key .. '"%s*:%s*"([^"]*)"')
end

--- Extrait la valeur numerique d'une cle : "cle" : 12.5
local function read_number_field(text, key)
    local raw = string.match(text, '"' .. key .. '"%s*:%s*(-?%d+%.?%d*)')
    if raw then
        return tonumber(raw)
    end
    return nil
end

--- Extrait un vecteur { x, y, z } depuis un sous-objet.
local function read_vector_field(text, key)
    local block = string.match(text, '"' .. key .. '"%s*:%s*(%b{})')
    if not block then
        return nil
    end
    local x = read_number_field(block, "x")
    local y = read_number_field(block, "y")
    local z = read_number_field(block, "z")
    if not x or not z then
        return nil -- l'altitude peut manquer, pas le plan du terrain
    end
    return { x = x, y = y or 0, z = z }
end

--- Extrait la liste `moves` d'une commande de groupe.
---
--- Forme attendue, produite par Python :
---   "moves":[{"unit_id":"1001","destination":{"x":1,"y":2,"z":3}}, ...]
---
--- On decoupe sur les objets equilibres plutot que d'ecrire un analyseur JSON :
--- ce format est le notre, il ne varie pas, et un analyseur complet serait plus
--- de code a se tromper. Une entree incomplete est **omise**, jamais devinee.
local function read_moves_field(text)
    local block = string.match(text, '"moves"%s*:%s*(%b[])')
    if not block then
        return {}
    end
    local moves = {}
    for entry in string.gmatch(block, "%b{}") do
        local unit_id = read_string_field(entry, "unit_id")
        local destination = read_vector_field(entry, "destination")
        if unit_id and destination then
            moves[#moves + 1] = { unit_id = unit_id, destination = destination }
        end
    end
    return moves
end

--- Extrait la liste `attacks` d'une commande.
---
---   "attacks":[{"unit_id":"1008","target_id":"1016","melee":true}, ...]
---
--- Meme parti pris que `read_moves_field` : ce format est le notre, une entree
--- incomplete est omise plutot que devinee.
local function read_attacks_field(text)
    local block = string.match(text, '"attacks"%s*:%s*(%b[])')
    if not block then
        return {}
    end
    local attacks = {}
    for entry in string.gmatch(block, "%b{}") do
        local unit_id = read_string_field(entry, "unit_id")
        local target_id = read_string_field(entry, "target_id")
        if unit_id and target_id then
            attacks[#attacks + 1] = {
                unit_id = unit_id,
                target_id = target_id,
                -- `melee` absent vaut faux : forcer le corps a corps est une
                -- decision, pas un defaut. Une unite de tir a qui on l'impose
                -- perdrait son avantage.
                melee = string.match(entry, '"melee"%s*:%s*true') ~= nil,
            }
        end
    end
    return attacks
end

--- Extrait une liste d'identifiants : "cle":["1001","1002"]
local function read_id_list(text, key)
    local block = string.match(text, '"' .. key .. '"%s*:%s*(%b[])')
    if not block then
        return {}
    end
    local ids = {}
    for unit_id in string.gmatch(block, '"([^"]+)"') do
        ids[#ids + 1] = unit_id
    end
    return ids
end

--- Extrait la liste `halts` d'une commande : les unites a immobiliser.
---
---   "halts":["1007","1009"]
local function read_halts_field(text)
    local block = string.match(text, '"halts"%s*:%s*(%b[])')
    if not block then
        return {}
    end
    local halts = {}
    for unit_id in string.gmatch(block, '"([^"]+)"') do
        halts[#halts + 1] = unit_id
    end
    return halts
end

--[[--------------------------------------------------------------------------
    Acces aux fichiers
----------------------------------------------------------------------------]]

function PROBE:read_file(path)
    if not io or not io.open then
        return nil, "io indisponible dans ce contexte"
    end
    local handle, err = io.open(path, "r")
    if not handle then
        return nil, err
    end
    local content = handle:read("*a")
    handle:close()
    return content, nil
end

function PROBE:append_line(path, line)
    if not io or not io.open then
        return false, "io indisponible dans ce contexte"
    end
    local handle, err = io.open(path, "a")
    if not handle then
        return false, err
    end
    handle:write(line .. "\n")
    handle:close()
    return true, nil
end

--- Le droit d'ecrire, evalue une seule fois. Le detail du diagnostic est
--- produit par `diagnose_io`, appele au demarrage.
function PROBE:check_write_access()
    if self.can_write ~= nil then
        return self.can_write
    end
    local ok = self:append_line(self.state_file, "")
    self.can_write = ok and true or false
    return self.can_write
end

--- Etablit ce que les entrees-sorties permettent vraiment, et le dit.
---
--- Appele au demarrage, avant toute detection d'unite : c'est la question de
--- faisabilite centrale du prototype (la lecture de fichier en bataille est
--- attestee par un mod tiers, l'ecriture ne l'est pas), et sa reponse ne doit
--- dependre de rien d'autre — sinon un echec ailleurs la rendrait muette.
function PROBE:diagnose_io()
    self:log("--- diagnostic des entrees-sorties ---")

    if not io or not io.open then
        self:log("io.open INDISPONIBLE dans ce contexte : aucun echange par fichier possible")
        self.can_write = false
        return
    end
    self:log("io.open disponible")

    local ok_dir, err_dir = self:append_line(self.state_file, "")
    if ok_dir then
        self.can_write = true
        self:log("ECRITURE OK dans " .. self.state_file)
        self:log("le repertoire de travail du jeu contient donc bien " .. self.dir)
    else
        self.can_write = false
        self:log("ecriture refusee dans " .. self.state_file .. " (" .. tostring(err_dir) .. ")")

        -- Distinguer « pas de droit d'ecriture » de « dossier absent » : on
        -- tente la racine du repertoire de travail, qui existe forcement.
        local ok_root, err_root = self:append_line("./totalwar_ai_probe_io_test.txt", "test")
        if ok_root then
            self:log("mais ECRITURE OK a la racine : le dossier " .. self.dir .. " est absent")
            self:log("=> creer ce dossier dans le repertoire de travail du jeu")
        else
            self:log("ecriture refusee a la racine aussi (" .. tostring(err_root) .. ")")
            self:log("=> ECRITURE IMPOSSIBLE en bataille : repli sur ce journal")
        end
    end

    if self:read_file(self.command_file) then
        self:log("lecture OK : " .. self.command_file .. " existe deja")
    else
        self:log("lecture : " .. self.command_file .. " absent (normal avant la 1re commande)")
    end
    self:log("--- fin du diagnostic ---")
end

function PROBE:file_exists(path)
    local content = self:read_file(path)
    return content ~= nil
end

--- Marque qu'une sentinelle d'arret a deja ete honoree.
---
--- Le fichier ne peut pas etre supprime : `os.remove` n'est pas garanti dans le
--- bac a sable du jeu, alors que `io.open` l'est. On le vide donc de son sens.
local STOP_CONSUMED = "consumed"

--- Un arret est-il demande **pour cette bataille** ?
---
--- La seule presence du fichier ne suffit pas. Il survit a la bataille qui l'a
--- vu naitre, et la sonde de la bataille suivante le lisait comme un ordre
--- d'arret : elle se coupait avant meme le deploiement, sans que rien du cote
--- Python ne puisse le rattraper — l'arret du Lua est definitif. Ce piege a
--- coute plusieurs essais.
---
--- Une sentinelle laissee par une bataille precedente n'a plus d'objet : les
--- unites qu'elle protegeait n'existent plus. On la consomme au demarrage.
function PROBE:stop_requested()
    local content = self:read_file(self.stop_file)
    if content == nil then
        return false
    end
    return string.find(content, STOP_CONSUMED, 1, true) == nil
end

--- Neutralise une sentinelle heritee d'une bataille precedente.
function PROBE:consume_stale_stop()
    if not self:stop_requested() then
        return
    end
    self:log(
        "sentinelle d'arret laissee par une bataille precedente : levee. "
            .. "Elle ne protegeait que des unites qui n'existent plus."
    )
    local handle = io and io.open and io.open(self.stop_file, "w")
    if handle then
        handle:write(STOP_CONSUMED .. "\n")
        handle:close()
    else
        -- Ecriture refusee : mieux vaut refuser de demarrer que de tourner en
        -- ignorant un arret qu'on ne sait pas lever.
        self:log("ECHEC : impossible de lever la sentinelle, la sonde reste arretee")
    end
end

--[[--------------------------------------------------------------------------
    Messages sortants
----------------------------------------------------------------------------]]

--- Ecrit une ligne, et se plaint si l'ecriture echoue.
---
--- Le droit d'ecriture a ete constate une seule fois, au demarrage, en phase de
--- deploiement. Rien ne garantit qu'il subsiste toute la bataille. Un echec
--- ulterieur doit donc se voir dans le journal plutot que de passer inapercu.
function PROBE:write_or_complain(path, line, label)
    local ok, err = self:append_line(path, line)
    if not ok then
        self:log_occasionally(
            "write_failure_" .. label,
            "ECRITURE REFUSEE dans " .. path .. " : " .. tostring(err)
        )
    end
    return ok
end

function PROBE:emit_state(unit_id, unit_type, position, controllable)
    self.sequence = self.sequence + 1
    local line = "{"
        .. '"protocol_version":' .. json_string(self.protocol_version) .. ","
        .. '"type":"unit_state",'
        .. '"sequence":' .. json_number(self.sequence) .. ","
        .. '"game_time_ms":' .. json_number(bm:time_elapsed_ms()) .. ","
        .. '"phase":' .. json_string(self.phase) .. ","
        .. '"unit":{'
        .. '"id":' .. json_string(unit_id) .. ","
        .. '"type":' .. json_string(unit_type) .. ","
        .. '"position":{'
        .. '"x":' .. json_number(position.x) .. ","
        .. '"y":' .. json_number(position.y) .. ","
        .. '"z":' .. json_number(position.z)
        .. "},"
        .. '"controllable":' .. tostring(controllable and true or false)
        .. "}}"

    -- Le journal reste le canal de repli, mais seulement quand il en est un.
    --
    -- Tant que l'ecriture de fichier fonctionne, Python lit le flux et n'a nul
    -- besoin du journal : un etat par seconde y devient du bruit qui noie le
    -- diagnostic — 297 Ko en dix-sept minutes de bataille, ou `probe --log`
    -- n'affichait plus que des lignes identiques. On n'en garde donc qu'un
    -- echantillon. Si l'ecriture devient impossible, le journal redevient le
    -- seul canal : on y remet alors chaque etat, integralement.
    if self:check_write_access() then
        self:log_occasionally("state_count", "STATE " .. line)
        self:write_or_complain(self.state_file, line, "state")
    else
        self:log("STATE " .. line)
    end
    return self.sequence
end

--- Compte les objets d'un tableau JSON produit ici, sans l'analyser.
---
--- Chaque unite commence par `{"id":`, motif qui n'apparait nulle part ailleurs
--- dans ce que nous ecrivons. Suffisant pour un resume de journal.
local function count_entries(array)
    local total = 0
    for _ in string.gmatch(array, '{"id":') do
        total = total + 1
    end
    return total
end

--- Fragment JSON decrivant une unite, limite a ce que le jeu expose vraiment.
---
--- Chaque champ optionnel n'est ecrit que si le recensement a valide son
--- accesseur. Le moral et la fatigue, absents du bac a sable, n'y figurent donc
--- jamais : Python les verra manquants au lieu de les lire a zero.
function PROBE:unit_snapshot(unit)
    local position = self:unit_position(unit)
    local parts = {
        '"id":' .. json_string(self:unit_identifier(unit)),
        '"type":' .. json_string(tostring(unit:type())),
        '"position":{"x":' .. json_number(position.x)
            .. ',"y":' .. json_number(position.y)
            .. ',"z":' .. json_number(position.z) .. "}",
    }

    local function add_bool(field, name)
        local value = self:read_field(unit, name)
        if value ~= nil then
            parts[#parts + 1] = '"' .. field .. '":' .. tostring(value and true or false)
        end
    end

    local function add_number(field, name)
        local value = self:read_field(unit, name)
        if type(value) == "number" then
            parts[#parts + 1] = '"' .. field .. '":' .. json_number(value)
        end
    end

    add_bool("controllable", "is_controllable")
    add_bool("commanding", "is_commanding_unit")
    add_bool("idle", "is_idle")
    -- `is_valid_target` repond « peut-on lui tirer dessus en ce moment », et
    -- **pas** « est-elle en vie ». Sur 21 057 observations en bataille reelle,
    -- il valait faux 1 942 fois sur des unites bien vivantes -- trois unites de
    -- tir sont restees marquees mortes six minutes durant, soixante-huit hommes
    -- debout et le carquois plein, et n'ont jamais ete confiees a l'IA du jeu.
    -- On le publie donc sous son vrai nom, et la vie se lit au compte d'hommes.
    add_bool("targetable", "is_valid_target")
    add_bool("routing", "is_routing")
    add_bool("shattered", "is_shattered")
    add_bool("in_melee", "is_in_melee")
    add_bool("hidden", "is_hidden")
    add_bool("can_fly", "can_fly")
    add_number("hitpoints", "unary_hitpoints")
    add_number("men_alive", "number_of_men_alive")
    add_number("bearing", "bearing")
    add_number("ammo", "ammo_left")
    add_number("missile_range", "missile_range")

    return "{" .. table.concat(parts, ",") .. "}"
end

--- Toutes les unites d'une alliance, sous forme de tableau JSON.
function PROBE:alliance_snapshot(alliance)
    local fragments = {}
    local armies = alliance:armies()
    for army_index = 1, armies:count() do
        local units = armies:item(army_index):units()
        for index = 1, units:count() do
            local unit = units:item(index)
            if unit then
                local ok, fragment = pcall(function() return self:unit_snapshot(unit) end)
                if ok then
                    fragments[#fragments + 1] = fragment
                end
                -- Une unite illisible est omise, jamais publiee a moitie : un
                -- etat partiel ferait decider l'agent sur des champs faux.
            end
        end
    end
    return "[" .. table.concat(fragments, ",") .. "]"
end

--- Publie la bataille entiere : notre camp, et tout ce qui n'est pas a nous.
function PROBE:emit_battle_state()
    self.sequence = self.sequence + 1
    local alliances = bm:alliances()
    local locale = bm:local_alliance()

    local allies = "[]"
    local enemies = {}
    for index = 1, alliances:count() do
        local snapshot = self:alliance_snapshot(alliances:item(index))
        if index == locale then
            allies = snapshot
        elseif snapshot ~= "[]" then
            -- Plusieurs alliances adverses sont possibles : on les fusionne,
            -- l'agent ne distingue que « nous » et « eux ».
            enemies[#enemies + 1] = string.sub(snapshot, 2, #snapshot - 1)
        end
    end

    local line = "{"
        .. '"protocol_version":' .. json_string(self.protocol_version) .. ","
        .. '"type":"battle_state",'
        .. '"sequence":' .. json_number(self.sequence) .. ","
        .. '"game_time_ms":' .. json_number(bm:time_elapsed_ms()) .. ","
        .. '"phase":' .. json_string(self.phase) .. ","
        .. '"allies":' .. allies .. ","
        .. '"enemies":[' .. table.concat(enemies, ",") .. "]"
        .. "}"

    -- Le journal du jeu reste le canal de repli, mais on n'y deverse pas vingt
    -- unites par seconde : un resume suffit a savoir que le flux vit.
    self:log_occasionally(
        "battle_state_count",
        "BATTLE phase " .. self.phase
            .. " : " .. tostring(count_entries(allies)) .. " allies, "
            .. tostring(count_entries("[" .. table.concat(enemies, ",") .. "]")) .. " ennemis"
    )

    if self:check_write_access() then
        self:write_or_complain(self.state_file, line, "battle_state")
    end
    return self.sequence
end

--- `refused` : identifiants que la commande n'a pas pu appliquer.
---
--- Un accuse qui ne dit que « accepte » et un compte global laisse Python
--- croire que tout est passe. Constate en bataille : dix-huit unites
--- demandees, six confiees, un accuse `accepted` — et l'agent a supervise
--- douze unites qu'il ne tenait pas. La liste permet de les ecarter.
function PROBE:emit_ack(sequence, status, error_message, detail, refused)
    local line = "{"
        .. '"protocol_version":' .. json_string(self.protocol_version) .. ","
        .. '"type":"action_result",'
        .. '"sequence":' .. json_number(sequence) .. ","
        .. '"status":' .. json_string(status) .. ","
        .. '"error":' .. (error_message and json_string(error_message) or "null")

    local fields = {}
    if detail then
        fields[#fields + 1] = '"note":' .. json_string(detail)
    end
    if refused and #refused > 0 then
        local ids = {}
        for index = 1, #refused do
            ids[index] = json_string(tostring(refused[index]))
        end
        fields[#fields + 1] = '"refused":[' .. table.concat(ids, ",") .. "]"
    end
    if #fields > 0 then
        line = line .. ',"detail":{' .. table.concat(fields, ",") .. "}"
    end
    line = line .. "}"

    self:log("ACK " .. line)
    if self:check_write_access() then
        self:write_or_complain(self.ack_file, line, "ack")
    end
end

--[[--------------------------------------------------------------------------
    Unites
----------------------------------------------------------------------------]]

--- Premiere unite alliee vivante et controlable.
--- Renvoie aussi le nombre d'unites vues et le nombre de controlables, pour que
--- l'appelant puisse expliquer un echec au lieu de se taire.
function PROBE:find_controllable_unit()
    local alliance = bm:alliances():item(bm:local_alliance())
    local army = alliance:armies():item(bm:local_army())
    local units = army:units()
    local seen = units:count()
    local controllable = 0
    local first = nil

    for index = 1, seen do
        local unit = units:item(index)
        if unit and unit:is_controllable() then
            controllable = controllable + 1
            if not first and unit:is_valid_target() then
                first = unit
            end
        end
    end
    return first, army, seen, controllable
end

--[[--------------------------------------------------------------------------
    Recensement des capacites

    Le mod tiers etudie ne lit ni les effectifs, ni le moral, ni la fatigue :
    rien ne dit si le jeu les expose. Plutot que de supposer, on demande. Chaque
    accesseur candidat est appele une fois sous `pcall` sur une unite reelle, et
    le resultat est journalise. Une seule bataille suffit alors a trancher ce
    que l'agent pourra observer.

    Ce recensement ne tourne qu'au demarrage, une fois : ce n'est pas un cout
    par tick.
----------------------------------------------------------------------------]]

--- Accesseurs sans argument a tester sur une unite alliee, par famille.
---
--- **Une absence constatee sous un mauvais nom n'est pas une absence.** Le
--- recensement de la revision 14 avait essaye `unary_morale`, `fatigue`,
--- `unary_fatigue`, `fatigue_level`, `speed` et `width` — tous absents — puis
--- conclu que le moral, la fatigue, la vitesse et la formation etaient
--- structurellement hors de portee. La documentation du jeu nomme pourtant
--- `fatigue_state`, `is_wavering`, `slow_speed`, `fast_speed` et
--- `ordered_width`, qui n'ont jamais ete demandes.
---
--- Les anciens noms restent dans la liste : savoir qu'ils sont absents fait
--- partie du resultat, et les retirer effacerait la trace de l'erreur.
local UNIT_ACCESSORS = {
    -- Identite et nature.
    "unique_ui_id",
    "type",
    "name",
    "is_controllable",
    "is_valid_target",
    "is_commanding_unit",
    "strategic_value",
    "is_infantry",
    "is_cavalry",
    "is_artillery",
    "can_fly",
    "is_currently_flying",
    -- Mouvement et formation.
    "is_idle",
    "is_moving",
    "is_moving_fast",
    "bearing",
    "ordered_bearing",
    "ordered_position",
    "ordered_width",
    "slow_speed",
    "fast_speed",
    "speed", -- essaye en revision 14 : absent
    "width", -- essaye en revision 14 : absent
    -- Effectifs et sante.
    "number_of_men",
    "number_of_men_alive",
    "initial_number_of_men",
    "unary_of_men_alive",
    "unary_hitpoints",
    "number_of_enemies_killed",
    -- Combat en cours.
    "current_target",
    "is_in_melee",
    "is_under_missile_attack",
    -- Psychologie. Aucun de ces cinq n'a jamais ete demande.
    "fatigue_state",
    "is_wavering",
    "is_crumbling",
    "is_unstable",
    "is_rampaging",
    "is_routing",
    "is_shattered",
    "unary_morale", -- essaye en revision 14 : absent
    "fatigue", -- essaye en revision 14 : absent
    "unary_fatigue", -- essaye en revision 14 : absent
    "fatigue_level", -- essaye en revision 14 : absent
    -- Tir.
    "ammo_left",
    "starting_ammo",
    "missile_range",
    -- Visibilite.
    "is_hidden",
    "is_visible_to_alliance",
    -- Capacites.
    "num_special_abilities",
    "owned_special_abilities",
    "owned_passive_special_abilities",
    "owned_non_passive_special_abilities",
    "can_use_magic",
}

--- Attributs a interroger via `has_attribute(cle)`.
---
--- Les quatre premiers decident d'une question de fidelite que notre simulateur
--- tranche aujourd'hui **dans le sens permissif sans preuve** : une unite de tir
--- y tire en se deplacant. Si le jeu l'interdit aux unites depourvues de
--- `fire_while_moving`, notre repli tirant repose sur une permissivite qui
--- n'existe pas — et il porte `balanced_clash` a 11 victoires sur 12.
local UNIT_ATTRIBUTES = {
    "fire_while_moving",
    "mounted_fire",
    "mounted_fire_move",
    "mounted_fire_parthian",
    "causes_fear",
    "causes_terror",
    "fatigue_immune",
    "unbreakable",
    "undead",
    "stalk",
    "snipe",
    "hide_forest",
}

--- Methodes candidates de `script_ai_planner`.
---
--- **Aucune n'est acquise au-dela des cinq premieres**, seules a avoir ete vues
--- employees par un mod existant. Les suivantes viennent de descriptions d'API
--- que nous n'avons jamais verifiees, et tout un plan de « profils
--- d'agressivite » en depend : les recenser coute un essai, batir dessus sans
--- les recenser en couterait plusieurs.
local PLANNER_METHODS = {
    -- Vues a l'oeuvre dans un mod existant.
    "new",
    "add_sunits",
    "remove_sunits",
    "release",
    "ensure_units_are_released",
    -- Annoncees, jamais constatees ici.
    "attack_force",
    "rush_force",
    "rush_unit",
    "defend_position",
    "set_should_reorder",
    "set_intercept_range",
    "set_priority",
    "attack_unit",
    "start",
    "stop",
}

--- Methodes candidates au niveau de l'armee.
---
--- `army_handicap` decide de la reponse a « la difficulte de bataille change-t-elle
--- le comportement du planificateur ? ». Sans elle, la question reste ouverte.
local ARMY_METHODS = {
    "army_handicap",
    "units",
    "create_unit_controller",
    "is_commanding_unit_alive",
    "unit_count",
}

--- Decrit brievement une valeur, sans jamais lever d'erreur.
local function describe(value)
    local kind = type(value)
    if kind == "number" then
        return "number " .. json_number(value)
    end
    if kind == "string" then
        return "string " .. string.sub(value, 1, 48)
    end
    if kind == "boolean" then
        return "boolean " .. tostring(value)
    end
    if kind == "nil" then
        return "nil"
    end
    return kind
end

--- Journalise, pour une unite, les accesseurs disponibles et leur valeur.
function PROBE:census_unit_accessors(unit, label)
    self:log(
        "--- recensement des accesseurs (" .. tostring(label or "?") .. " : "
            .. tostring(unit:type()) .. ") ---"
    )
    local disponibles = {}
    for index = 1, #UNIT_ACCESSORS do
        local name = UNIT_ACCESSORS[index]
        local method = unit[name]
        -- **L'absence se journalise comme un fait, avec son motif.** Un `nil`
        -- silencieux ne dit pas si l'accesseur n'existe pas, s'il a leve une
        -- erreur, ou s'il a repondu « rien » — trois situations differentes,
        -- dont une seule est une absence.
        if type(method) ~= "function" then
            self:log("  API " .. name .. " ABSENT error=pas une fonction")
        else
            local ok, value = pcall(method, unit)
            if ok then
                self:log("  API " .. name .. " OK value=" .. describe(value))
                disponibles[#disponibles + 1] = name
            else
                self:log("  API " .. name .. " ABSENT error=" .. tostring(value))
            end
        end
    end

    -- `has_attribute` prend un argument : il ne peut pas passer par la boucle
    -- ci-dessus, et c'est pourtant lui qui porte la question du tir en
    -- mouvement.
    if type(unit.has_attribute) ~= "function" then
        self:log("  API has_attribute ABSENT error=pas une fonction")
    else
        for index = 1, #UNIT_ATTRIBUTES do
            local cle = UNIT_ATTRIBUTES[index]
            local ok, value = pcall(unit.has_attribute, unit, cle)
            if ok then
                self:log("  ATTR " .. cle .. " OK value=" .. tostring(value))
            else
                self:log("  ATTR " .. cle .. " ABSENT error=" .. tostring(value))
            end
        end
        disponibles[#disponibles + 1] = "has_attribute"
    end

    self:log("accesseurs utilisables : " .. table.concat(disponibles, ", "))
    self:log("--- fin du recensement ---")

    -- Le recensement ne sert pas qu'au diagnostic : il decide de ce qui est
    -- publie. Un champ dont l'accesseur est absent n'apparait pas dans l'etat,
    -- plutot que d'y figurer a zero — un zero se confond avec une vraie valeur.
    self.available = {}
    for index = 1, #disponibles do
        self.available[disponibles[index]] = true
    end
    return disponibles
end

--- Recense l'API du planificateur de bataille du moteur, **sans l'instancier**.
---
--- On inspecte la table de classe : `type(script_ai_planner.rush_force)` dit si
--- la methode existe, sans creer de planificateur ni confier la moindre unite.
--- Un recensement ne doit jamais changer l'etat de la bataille.
---
--- La reponse decide de ce qui est constructible : sans `rush_force` ni
--- `attack_force`, un profil « tres difficile » ne serait qu'un nom.
function PROBE:census_planner_api()
    self:log("--- recensement de script_ai_planner ---")
    if type(script_ai_planner) ~= "table" then
        self:log("  script_ai_planner : ABSENT — la delegation est impossible")
        self:log("--- fin du recensement ---")
        return {}
    end

    local disponibles = {}
    for index = 1, #PLANNER_METHODS do
        local name = PLANNER_METHODS[index]
        if type(script_ai_planner[name]) == "function" then
            self:log("  " .. name .. " : presente")
            disponibles[#disponibles + 1] = name
        else
            self:log("  " .. name .. " : ABSENT")
        end
    end
    self:log("methodes du planificateur : " .. table.concat(disponibles, ", "))
    self:log("--- fin du recensement ---")
    return disponibles
end

--- Recense ce qu'une armee expose, et lit la difficulte de bataille.
---
--- `army_handicap()` vaut 1 facile, 0 normal, -1 difficile, -2 tres difficile.
--- Le relever des deux cotes permettra de savoir si le planificateur auquel on
--- confie nos unites se comporte differemment selon le reglage — question
--- ouverte, qu'aucune documentation ne tranche.
function PROBE:census_army_api()
    self:log("--- recensement de l'armee ---")
    local alliances = bm:alliances()
    local locale = bm:local_alliance()

    for a = 1, alliances:count() do
        local armies = alliances:item(a):armies()
        for b = 1, armies:count() do
            local army = armies:item(b)
            local camp = (a == locale) and "nous" or "eux"
            for index = 1, #ARMY_METHODS do
                local name = ARMY_METHODS[index]
                local method = army[name]
                if type(method) ~= "function" then
                    if a == locale and b == 1 then
                        self:log("  " .. name .. " : ABSENT")
                    end
                elseif name == "army_handicap" or name == "unit_count" then
                    -- Seuls ces deux-la sont appeles : les autres ont des effets
                    -- de bord, et un recensement doit rester sans consequence.
                    local ok, value = pcall(method, army)
                    self:log(
                        "  " .. camp .. " alliance " .. tostring(a) .. " armee " .. tostring(b)
                            .. " " .. name .. " : " .. (ok and describe(value) or "ERREUR")
                    )
                elseif a == locale and b == 1 then
                    self:log("  " .. name .. " : presente")
                end
            end
        end
    end
    self:log("--- fin du recensement ---")
end

--- Noms candidats pour lire l'altitude du sol depuis le battle_manager.
---
--- Tous inventes : aucun n'a jamais ete constate. On se contente de tester leur
--- existence, sans les appeler — un nom inconnu peut avoir des effets de bord.
local TERRAIN_METHODS = {
    "get_terrain_height",
    "terrain_height",
    "get_ground_height",
    "ground_height",
    "get_height_at_position",
}

--- Recense ce que le jeu dit du **terrain**.
---
--- La fiche de faisabilite a longtemps porte « aucune donnee de terrain ».
--- C'etait vrai des accesseurs d'unite recenses, et faux du reste : deux voies
--- n'avaient jamais ete testees.
---
--- 1. `unit:position():get_y()` **repond** — altitude entre 21 et 33 relevee en
---    bataille. C'est l'altitude du sol sous chaque unite, et elle est deja
---    publiee dans l'etat.
--- 2. `v_to_ground(v(x, y, z))` projette un point **sur le sol**. Notre propre
---    code l'appelle a chaque ordre de deplacement. Si le vecteur rendu expose
---    son `get_y()`, alors nous tenons une sonde d'altitude en tout point de la
---    carte — et un relief complet devient calculable avant le premier coup de
---    feu.
---
--- Ce recensement pose la question et journalise la reponse. Il ne construit
--- rien : batir un module de terrain avant de savoir couterait un essai de
--- plus, et ce projet en a deja perdu trois de cette facon.
function PROBE:census_terrain()
    self:log("--- recensement du terrain ---")

    for index = 1, #TERRAIN_METHODS do
        local name = TERRAIN_METHODS[index]
        if type(bm[name]) == "function" then
            self:log("  bm:" .. name .. " : presente")
        end
    end

    if type(v) ~= "function" or type(v_to_ground) ~= "function" then
        self:log("  v / v_to_ground : ABSENT — pas de sonde d'altitude possible")
        self:log("--- fin du recensement ---")
        return
    end
    self:log("  v_to_ground : presente")

    -- Origine : une unite a nous, dont on connait deja l'altitude par une autre
    -- voie. Comparer les deux dira si la projection au sol raconte la meme
    -- chose que la position d'une unite.
    local unit = self:find_controllable_unit()
    local origine = { x = 0, z = 0 }
    if unit then
        local ok, position = pcall(function() return unit:position() end)
        if ok and position then
            origine.x = position:get_x()
            origine.z = position:get_z()
            self:log("  altitude sous l'unite : " .. describe(position:get_y()))
        end
    end

    -- Une croix autour de l'origine. Des altitudes qui **different** prouvent
    -- que la sonde lit le relief ; des valeurs toutes identiques diraient
    -- qu'elle ne renvoie qu'une constante, et ne servirait a rien.
    local ecarts = { { 0, 0 }, { 150, 0 }, { -150, 0 }, { 0, 150 }, { 0, -150 } }
    for index = 1, #ecarts do
        local dx, dz = ecarts[index][1], ecarts[index][2]
        local x, z = origine.x + dx, origine.z + dz
        local ok, altitude = pcall(function()
            return v_to_ground(v(x, 0, z)):get_y()
        end)
        self:log(
            "  sol en (" .. json_number(x) .. ", " .. json_number(z) .. ") : "
                .. (ok and describe(altitude) or "ERREUR " .. tostring(altitude))
        )
    end
    self:log("--- fin du recensement ---")
end

--- Chronometre la mise en batterie d'une unite de tir.
---
--- **C'est la mesure qui juge une correction deja livree.** Notre simulateur
--- laisse aujourd'hui toute unite de tir non engagee tirer, en mouvement ou non.
--- L'argument etait qu'un ordre de repli et un ordre de deplacement sont la meme
--- commande vers le jeu — ce qui reste vrai — mais il masquait une question
--- distincte : **le jeu autorise-t-il un tireur ordinaire a tirer en marchant ?**
---
--- Si la reponse est non, le repli tirant qui porte `balanced_clash` a onze
--- victoires sur douze repose sur une permissivite que WARHAMMER III n'a pas, et
--- le simulateur devra se taire.
---
--- Quatre instants suffisent a trancher :
---
---     t0  ordre de deplacement emis
---     t1  is_moving passe a faux
---     t2  current_target devient non nul
---     t3  ammo_left decroit pour la premiere fois
---
--- `t3 - t1` est le delai de mise en batterie ; `t3 - t0` l'attente reelle avant
--- le premier degat. Et surtout : **`t3` tombe-t-il avant `t1` ?** Une salve
--- partie avant l'arret repondrait oui au tir en mouvement.
---
--- Le suivi s'arrete de lui-meme apres `MISSILE_WATCH_TICKS` releves : ce n'est
--- pas un cout permanent.
local MISSILE_WATCH_TICKS = 120
local MISSILE_WATCH_INTERVAL_MS = 250

function PROBE:watch_missile_readiness(unit)
    if not unit then
        self:log("MISSILE aucune unite de tir : chronometrage impossible")
        return
    end
    local depart = self:read_field(unit, "ammo_left")
    if depart == nil then
        self:log("MISSILE ammo_left absent : chronometrage impossible")
        return
    end

    self:log(
        "--- chronometrage de la mise en batterie (" .. tostring(unit:type()) .. ") ---"
    )
    -- **Le temps se compte en ticks, pas avec `bm:time_stamp()`.** Cet
    -- accesseur n'a jamais ete recense, et introduire un appel non verifie dans
    -- le script dont le role est justement de ne rien supposer serait le
    -- meilleur moyen de perdre la mesure entiere sur une erreur Lua.
    local suivi = {
        restant = MISSILE_WATCH_TICKS,
        munitions = depart,
        arrete = nil,
        cible = nil,
        salve = nil,
        ecoule = 0,
    }

    bm:repeat_callback(function()
        suivi.restant = suivi.restant - 1
        if suivi.restant <= 0 then
            bm:remove_process("totalwar_ai_missile")
            self:log("--- fin du chronometrage ---")
            return
        end

        suivi.ecoule = suivi.ecoule + MISSILE_WATCH_INTERVAL_MS
        local maintenant = suivi.ecoule
        local bouge = self:read_field(unit, "is_moving")
        if suivi.arrete == nil and bouge == false then
            suivi.arrete = maintenant
            self:log("MISSILE t1 arret a " .. json_number(maintenant) .. " ms")
        end

        local cible = self:read_field(unit, "current_target")
        if suivi.cible == nil and cible ~= nil then
            suivi.cible = maintenant
            self:log("MISSILE t2 cible acquise a " .. json_number(maintenant) .. " ms")
        end

        local munitions = self:read_field(unit, "ammo_left")
        if suivi.salve == nil and munitions ~= nil and munitions < suivi.munitions then
            suivi.salve = maintenant
            -- **La ligne qui tranche.** `en_marche=true` signifie qu'une salve
            -- est partie alors que l'unite se deplacait encore : le tir en
            -- mouvement serait alors autorise, et notre simulateur aurait
            -- raison. `en_marche=false` dit l'inverse, et nous devrons le
            -- corriger.
            self:log(
                "MISSILE t3 premiere salve a " .. json_number(maintenant) .. " ms"
                    .. " en_marche=" .. tostring(bouge == true)
                    .. " apres_arret="
                    .. (suivi.arrete and json_number(maintenant - suivi.arrete) or "jamais_arrete")
            )
            bm:remove_process("totalwar_ai_missile")
            self:log("--- fin du chronometrage ---")
        end
    end, MISSILE_WATCH_INTERVAL_MS, "totalwar_ai_missile")
end

--- Lance le chronometrage sur la premiere unite de tir trouvee.
function PROBE:start_missile_watch()
    self:watch_missile_readiness(self:find_missile_unit())
end

--- Appelle un accesseur si le recensement l'a declare utilisable.
---
--- Renvoie `nil` quand il est absent : l'appelant omet alors le champ. C'est
--- deliberement different de renvoyer zero, qui se confondrait avec une mesure.
function PROBE:read_field(unit, name)
    if not self.available or not self.available[name] then
        return nil
    end
    local ok, value = pcall(unit[name], unit)
    if not ok then
        return nil
    end
    return value
end

--- Journalise ce que l'on peut savoir des alliances en presence.
function PROBE:census_alliances()
    self:log("--- recensement des alliances ---")
    local ok, err = pcall(function()
        local alliances = bm:alliances()
        local total = alliances:count()
        local locale = bm:local_alliance()
        self:log("  alliances : " .. tostring(total) .. ", locale = " .. tostring(locale))
        for index = 1, total do
            local alliance = alliances:item(index)
            local armies = alliance:armies()
            local unites = 0
            for army_index = 1, armies:count() do
                unites = unites + armies:item(army_index):units():count()
            end
            self:log(
                "  alliance "
                    .. tostring(index)
                    .. " : "
                    .. tostring(armies:count())
                    .. " armee(s), "
                    .. tostring(unites)
                    .. " unite(s)"
                    .. (index == locale and " <- la notre" or "")
            )
        end
    end)
    if not ok then
        self:log("  ERREUR pendant le recensement des alliances : " .. tostring(err))
    end
    self:log("--- fin du recensement ---")
end

--- Premiere unite alliee comptant plus d'une entite.
---
--- Sert au recensement : le seigneur, figurine unique, ne dit rien de ce que
--- valent `number_of_men_alive` et `unary_hitpoints` sur une vraie unite.
function PROBE:find_multi_entity_unit()
    local alliance = bm:alliances():item(bm:local_alliance())
    local army = alliance:armies():item(bm:local_army())
    local units = army:units()
    for index = 1, units:count() do
        local unit = units:item(index)
        if unit then
            local ok, men = pcall(function() return unit:number_of_men_alive() end)
            if ok and type(men) == "number" and men > 1 then
                return unit
            end
        end
    end
    return nil
end

--- Trouve une unite de tir alliee, pour le chronometrage de mise en batterie.
---
--- `missile_range` plutot que le nom : le recensement a etabli que les
--- identifiants de WARHAMMER III ne disent pas si une unite tire — un
--- `wh3_main_tze_inf_blue_horrors_0` n'a que le segment `_inf_` et porte
--- pourtant quatre-vingt-dix de portee.
function PROBE:find_missile_unit()
    local alliance = bm:alliances():item(bm:local_alliance())
    local army = alliance:armies():item(bm:local_army())
    local units = army:units()
    for index = 1, units:count() do
        local unit = units:item(index)
        if unit then
            local ok, portee = pcall(function() return unit:missile_range() end)
            if ok and type(portee) == "number" and portee > 0 then
                return unit
            end
        end
    end
    return nil
end

function PROBE:unit_position(unit)
    local position = unit:position()
    return {
        x = position:get_x(),
        y = position:get_y(),
        z = position:get_z(),
    }
end

function PROBE:unit_identifier(unit)
    return tostring(unit:unique_ui_id())
end

--- Retrouve une unite alliee par son identifiant d'interface.
--- Retrouve une unite dans n'importe quelle alliance.
---
--- Une cible d'attaque est adverse : la recherche limitee a notre armee, qui
--- suffit pour donner un ordre, ne suffit pas pour designer un ennemi.
function PROBE:find_any_unit_by_id(wanted_id)
    local alliances = bm:alliances()
    for a = 1, alliances:count() do
        local armies = alliances:item(a):armies()
        for b = 1, armies:count() do
            local units = armies:item(b):units()
            for index = 1, units:count() do
                local unit = units:item(index)
                if unit and tostring(unit:unique_ui_id()) == tostring(wanted_id) then
                    return unit
                end
            end
        end
    end
    return nil
end

--- Retrouve une unite de notre camp, dans **n'importe laquelle** de nos armees.
---
--- Cette recherche portait autrefois sur la seule `bm:local_army()`, alors que
--- `alliance_snapshot` publie toutes les armees de l'alliance. L'agent voyait
--- donc des unites qu'aucun ordre ne pouvait atteindre : constate en bataille,
--- 18 allies observes, 6 seulement trouves, et tout ordre aux 12 autres refuse
--- par « unite introuvable ». Observer et commander doivent parcourir le meme
--- ensemble, sans quoi l'agent raisonne sur une armee qu'il ne commande pas.
---
--- L'armee est renvoyee avec l'unite : c'est d'elle que vient le
--- `unitcontroller`, et ce n'est pas forcement la notre.
function PROBE:find_unit_by_id(wanted_id)
    local alliance = bm:alliances():item(bm:local_alliance())
    local armies = alliance:armies()

    for army_index = 1, armies:count() do
        local army = armies:item(army_index)
        local units = army:units()
        for index = 1, units:count() do
            local unit = units:item(index)
            if unit and tostring(unit:unique_ui_id()) == tostring(wanted_id) then
                return unit, army
            end
        end
    end
    -- Introuvable : on rend tout de meme notre armee, pour que l'appelant qui
    -- ne teste que l'unite ne travaille jamais sur un `nil`.
    return nil, armies:item(bm:local_army())
end

--[[--------------------------------------------------------------------------
    Prise et restitution du controle
----------------------------------------------------------------------------]]

function PROBE:release_unit(ui_id)
    local entry = self.controlled[ui_id]
    if not entry then
        return false
    end
    if entry.uc then
        entry.uc:release_control()
    end
    self.controlled[ui_id] = nil
    self:log("controle rendu au joueur pour l'unite " .. tostring(ui_id))
    return true
end

--- Rend au joueur tout ce que la sonde detient, par quelque voie que ce soit.
---
--- Point de passage unique de la sentinelle de fichier, de la commande d'arret
--- et de la fin de bataille. La delegation a l'IA du jeu s'y defait donc aussi :
--- la greffer ailleurs laisserait une voie d'arret incapable de la rompre, et
--- les unites resteraient confiees a une IA que plus rien ne pilote.
function PROBE:release_all(reason)
    local count = 0
    for ui_id, _ in pairs(self.controlled) do
        if self:release_unit(ui_id) then
            count = count + 1
        end
    end
    count = count + self:reclaim_units()
    if count > 0 then
        self:log("toutes les unites relachees (" .. tostring(reason or "sans motif") .. ")")
    end
    return count
end

--[[--------------------------------------------------------------------------
    Execution d'une commande
----------------------------------------------------------------------------]]

--- Prend une unite et lui donne sa destination. Ne journalise aucun accuse :
--- l'appelant sait s'il traite une unite seule ou tout un groupe.
---
--- Renvoie `true`, ou `false` et le motif du refus.
function PROBE:start_move(unit_id, destination, release_after_ms)
    local unit, army = self:find_unit_by_id(unit_id)
    if not unit then
        return false, "unite introuvable"
    end
    if not unit:is_controllable() then
        return false, "unite non controlable"
    end

    local uc = army:create_unit_controller()
    if not uc then
        return false, "creation du unitcontroller impossible"
    end

    local added = pcall(function() uc:add_units(unit) end)
    if not added then
        return false, "uc:add_units a echoue (groupe verrouille ?)"
    end

    local moved = pcall(function()
        uc:goto_location(v_to_ground(v(destination.x, destination.y, destination.z)), true)
    end)
    if not moved then
        uc:release_control()
        return false, "uc:goto_location a echoue"
    end

    self.controlled[unit_id] = { uc = uc, release_at_ms = bm:time_elapsed_ms() + release_after_ms }
    return true, nil
end

--- Lance une unite a l'attaque d'une autre. Ne journalise aucun accuse.
---
--- `uc:melee(true)` force le corps a corps : sans lui, une unite de tir tire
--- sur sa cible au lieu de la charger, ce qui est le plus souvent souhaitable.
--- C'est donc a l'appelant de decider, jamais un defaut.
function PROBE:start_attack(unit_id, target_id, force_melee, release_after_ms)
    local unit, army = self:find_unit_by_id(unit_id)
    if not unit then
        return false, "unite introuvable"
    end
    if not unit:is_controllable() then
        return false, "unite non controlable"
    end

    local target = self:find_any_unit_by_id(target_id)
    if not target then
        return false, "cible introuvable : " .. tostring(target_id)
    end
    local ok_valid, valid = pcall(function() return target:is_valid_target() end)
    if ok_valid and not valid then
        return false, "cible deja hors de combat"
    end

    local uc = army:create_unit_controller()
    if not uc then
        return false, "creation du unitcontroller impossible"
    end
    if not pcall(function() uc:add_units(unit) end) then
        return false, "uc:add_units a echoue (groupe verrouille ?)"
    end

    if force_melee then
        -- Facultatif : son absence ne doit pas faire echouer l'attaque.
        pcall(function() uc:melee(true) end)
    end

    local ordered = pcall(function() uc:attack_unit(target, true, true) end)
    if not ordered then
        uc:release_control()
        return false, "uc:attack_unit a echoue"
    end

    self.controlled[unit_id] = { uc = uc, release_at_ms = bm:time_elapsed_ms() + release_after_ms }
    return true, nil
end

--- Immobilise une unite. Ne journalise aucun accuse.
---
--- « Tenir la position » ne se traduisait par aucun ordre, ce qui laissait
--- l'unite poursuivre ce qu'elle faisait : l'agent croyait tenir sa ligne
--- pendant que l'armee continuait d'avancer. Un arret explicite est le seul
--- moyen de rendre cette intention.
function PROBE:start_halt(unit_id)
    local unit, army = self:find_unit_by_id(unit_id)
    if not unit then
        return false, "unite introuvable"
    end
    if not unit:is_controllable() then
        return false, "unite non controlable"
    end

    local uc = army:create_unit_controller()
    if not uc then
        return false, "creation du unitcontroller impossible"
    end
    if not pcall(function() uc:add_units(unit) end) then
        return false, "uc:add_units a echoue (groupe verrouille ?)"
    end
    if not pcall(function() uc:halt() end) then
        uc:release_control()
        return false, "uc:halt a echoue"
    end

    -- L'arret est instantane : rendre la main tout de suite, plutot que de
    -- confisquer l'unite cinq secondes pour un ordre deja execute.
    uc:release_control()
    return true, nil
end

--[[--------------------------------------------------------------------------
    Delegation a l'IA du jeu

    Le moteur de WARHAMMER III embarque sa propre IA de bataille, accessible par
    `script_ai_planner`. Elle connait le terrain, le pathfinding, les statistiques
    d'unites et les formations — tout ce que le recensement a montre inaccessible
    a un script Lua.

    Lui confier des unites est **bien plus engageant** qu'un ordre de deplacement :
    le joueur en perd le controle jusqu'a ce qu'on les reprenne, sans delai de
    restitution automatique. Toutes les voies d'arret existantes doivent donc la
    defaire : sentinelle de fichier, commande d'arret, fin de bataille.
----------------------------------------------------------------------------]]

--- `script_unit` correspondant a une unite, cree au besoin.
---
--- Le jeu en tient deja un pour la plupart des unites ; les invocations et les
--- transformations font exception. `script_unit:new` peut signaler une erreur
--- de script dans ce cas — d'ou le `pcall`, qui laisse la delegation continuer
--- sur les autres unites au lieu de tout interrompre.
function PROBE:script_unit_for(unit)
    local ok, existing = pcall(function() return bm:get_scriptunit_for_unit(unit) end)
    if ok and existing then
        return existing
    end
    local created_ok, created = pcall(function() return script_unit:new(unit) end)
    if created_ok and created then
        return created
    end
    return nil
end

--- Confie des unites a l'IA du jeu.
---
--- Renvoie le nombre d'unites effectivement confiees, le motif du premier
--- refus, et **la liste des identifiants refuses**. Une unite en echec
--- n'empeche pas les autres, mais Python doit savoir lesquelles pour cesser de
--- les compter comme siennes.
function PROBE:delegate_units(unit_ids)
    local sunits, refuses, premier_refus = {}, {}, nil
    for index = 1, #unit_ids do
        local unit_id = unit_ids[index]
        local unit = self:find_unit_by_id(unit_id)
        if not unit then
            refuses[#refuses + 1] = unit_id
            premier_refus = premier_refus or (tostring(unit_id) .. " : unite introuvable")
        elseif not unit:is_controllable() then
            refuses[#refuses + 1] = unit_id
            premier_refus = premier_refus or (tostring(unit_id) .. " : unite non controlable")
        else
            -- Une unite sous notre controle direct ne peut pas etre confiee :
            -- on la rend d'abord, sans quoi les deux ordres se disputeraient.
            self:release_unit(unit_id)
            local sunit = self:script_unit_for(unit)
            if sunit then
                sunits[#sunits + 1] = sunit
                self.delegated[tostring(unit_id)] = true
            else
                refuses[#refuses + 1] = unit_id
                premier_refus = premier_refus or (tostring(unit_id) .. " : script_unit indisponible")
            end
        end
    end

    if #sunits == 0 then
        return 0, premier_refus or "aucune unite confiable", refuses
    end

    if self.ai_planner then
        local ok, err = pcall(function() self.ai_planner:add_sunits(sunits) end)
        if not ok then
            return 0, "add_sunits a echoue : " .. tostring(err), unit_ids
        end
    else
        local ok, planner = pcall(function()
            return script_ai_planner:new("totalwar_ai", sunits, false)
        end)
        if not ok or not planner then
            return 0, "creation du script_ai_planner impossible : " .. tostring(planner), unit_ids
        end
        self.ai_planner = planner
    end
    return #sunits, premier_refus, refuses
end

--- Reprend des unites confiees a l'IA du jeu.
---
--- Sans `unit_ids`, tout est repris et le planificateur dissous : c'est la voie
--- des arrets d'urgence, qui ne doit rien laisser derriere elle.
---
--- Avec `unit_ids`, seules ces unites reviennent — le reste continue d'etre
--- joue par l'IA du jeu. C'est ce qui permet de **superviser** : lui laisser la
--- bataille, et ne reprendre que l'unite dont elle fait mauvais usage.
---
--- Sans effet s'il n'y a rien a reprendre : la reprise doit pouvoir etre
--- demandee a tout moment, y compris par precaution.
function PROBE:reclaim_units(unit_ids)
    if not self.ai_planner then
        return 0, unit_ids
    end

    if unit_ids and #unit_ids > 0 then
        local sunits, rendues, refuses = {}, 0, {}
        for index = 1, #unit_ids do
            local unit_id = tostring(unit_ids[index])
            if self.delegated[unit_id] then
                local unit = self:find_unit_by_id(unit_id)
                local sunit = unit and self:script_unit_for(unit)
                if sunit then
                    sunits[#sunits + 1] = sunit
                    self.delegated[unit_id] = nil
                    rendues = rendues + 1
                else
                    refuses[#refuses + 1] = unit_id
                end
            else
                -- Jamais confiee, ou deja rendue : la reprendre n'a pas de sens,
                -- et le taire ferait recommencer l'appelant indefiniment.
                refuses[#refuses + 1] = unit_id
            end
        end
        if rendues == 0 then
            return 0, refuses
        end
        pcall(function() self.ai_planner:remove_sunits(sunits) end)
        self:log("reprise partielle : " .. tostring(rendues) .. " unite(s) retirees a l'IA du jeu")

        -- Un planificateur vide n'a plus lieu d'etre : le garder ferait croire
        -- qu'une delegation est en cours, et le prochain arret la chercherait.
        if next(self.delegated) == nil then
            pcall(function() self.ai_planner:release() end)
            self.ai_planner = nil
        end
        return rendues, refuses
    end

    local rendues = 0
    for _ in pairs(self.delegated) do
        rendues = rendues + 1
    end
    pcall(function() self.ai_planner:release() end)
    pcall(function() self.ai_planner:ensure_units_are_released() end)
    self.ai_planner = nil
    self.delegated = {}
    self:log("controle rendu au joueur : " .. tostring(rendues) .. " unite(s) reprises a l'IA du jeu")
    return rendues
end

--- Programme la restitution du controle, quoi qu'il arrive ensuite.
function PROBE:schedule_release(sequence, unit_id, release_after_ms)
    bm:callback(function()
        if self.controlled[unit_id] then
            self:release_unit(unit_id)
            self:emit_ack(sequence, "released", nil, "controle rendu apres delai")
        end
    end, release_after_ms, "totalwar_ai_release_" .. tostring(sequence) .. "_" .. tostring(unit_id))
end

function PROBE:execute_move(sequence, unit_id, destination, release_after_ms)
    local ok, motif = self:start_move(unit_id, destination, release_after_ms)
    if not ok then
        self:emit_ack(sequence, "rejected", motif .. " : " .. tostring(unit_id))
        return
    end

    self:emit_ack(sequence, "accepted", nil, "deplacement lance")
    self:log("unite " .. tostring(unit_id) .. " envoyee vers "
        .. json_number(destination.x) .. ", " .. json_number(destination.z))
    self:schedule_release(sequence, unit_id, release_after_ms)
end

--[[--------------------------------------------------------------------------
    Boucle de lecture des commandes
----------------------------------------------------------------------------]]

--- Execute une commande portant deplacements et attaques.
---
--- Un ordre par unite obligerait a autant d'allers-retours par fichier, chacun
--- avec son numero de sequence et son accuse : commander vingt unites prendrait
--- vingt secondes. Une armee manoeuvre d'un bloc ou pas du tout.
---
--- Chaque unite recoit son propre `unitcontroller` : c'est ce qui permet des
--- destinations et des cibles differentes, donc une manoeuvre et non un troupeau.
---
--- Une unite en echec n'annule pas les autres. L'accuse recapitule combien
--- d'ordres ont ete lances et combien refuses, avec le motif du premier refus —
--- un accuse « accepte » qui tairait dix-neuf echecs serait un mensonge.
function PROBE:execute_orders(sequence, content, release_after_ms)
    local moves = read_moves_field(content)
    local attacks = read_attacks_field(content)
    local halts = read_halts_field(content)
    if #moves == 0 and #attacks == 0 and #halts == 0 then
        self:emit_ack(sequence, "rejected", "aucun ordre dans la commande")
        return
    end

    local lances, refuses, premier_refus = 0, 0, nil

    -- `ok, motif` sont recueillis dans des variables avant l'appel : un appel
    -- multi-valeurs place ailleurs qu'en dernier argument serait tronque a sa
    -- premiere valeur, et le motif du refus deviendrait l'identifiant.
    local function compter(unit_id, ok, motif)
        if ok then
            lances = lances + 1
            self:schedule_release(sequence, unit_id, release_after_ms)
        else
            refuses = refuses + 1
            premier_refus = premier_refus or (tostring(unit_id) .. " : " .. tostring(motif))
        end
    end

    for index = 1, #moves do
        local move = moves[index]
        local ok, motif = self:start_move(move.unit_id, move.destination, release_after_ms)
        compter(move.unit_id, ok, motif)
    end
    for index = 1, #attacks do
        local attack = attacks[index]
        local ok, motif =
            self:start_attack(attack.unit_id, attack.target_id, attack.melee, release_after_ms)
        compter(attack.unit_id, ok, motif)
    end

    for index = 1, #halts do
        local unit_id = halts[index]
        local ok, motif = self:start_halt(unit_id)
        if ok then
            lances = lances + 1 -- pas de restitution a programmer : deja rendue
        else
            refuses = refuses + 1
            premier_refus = premier_refus or (tostring(unit_id) .. " : " .. tostring(motif))
        end
    end

    local resume = tostring(lances) .. " ordre(s) lance(s), " .. tostring(refuses) .. " refuse(s)"
    if lances == 0 then
        self:emit_ack(sequence, "rejected", premier_refus, resume)
        return
    end
    self:emit_ack(sequence, "accepted", premier_refus, resume)
    self:log(
        "manoeuvre : " .. tostring(#moves) .. " deplacement(s), "
            .. tostring(#attacks) .. " attaque(s), "
            .. tostring(#halts) .. " arret(s) — " .. resume
    )
end

--- Neutralise une commande laissee par une partie precedente.
---
--- La memoire anti-rejeu vit en memoire : elle repart vide a chaque bataille.
--- Un fichier de commande oublie sur le disque etait donc execute au demarrage
--- de la bataille suivante — constate en jeu, un ordre d'une partie passee
--- deplacant une unite d'une nouvelle partie. Aucun ordre ne doit survivre a la
--- bataille pour laquelle il a ete emis.
---
--- On ne supprime pas le fichier : Python en est proprietaire, et le detruire
--- masquerait la trace. On note simplement sa sequence comme deja traitee.
function PROBE:discard_stale_command()
    local content = self:read_file(self.command_file)
    if not content or content == "" then
        return
    end
    local sequence = read_number_field(content, "sequence")
    if not sequence then
        return
    end
    self.last_command_sequence = sequence
    self:log(
        "commande anterieure a cette bataille ignoree (sequence "
            .. json_number(sequence)
            .. ") : un ordre ne survit pas a la bataille qui l'a recu"
    )
end

function PROBE:process_command_file()
    if self.aborted then
        return
    end

    -- L'arret d'urgence par fichier prime sur tout le reste : il fonctionne
    -- meme si l'analyse des commandes echoue.
    if self:stop_requested() then
        self:abort("fichier d'arret present")
        return
    end

    local content = self:read_file(self.command_file)
    if not content or content == "" then
        return
    end

    local version = read_string_field(content, "protocol_version")
    if version ~= self.protocol_version then
        return -- version absente ou incompatible : on ignore, sans bruit
    end

    local sequence = read_number_field(content, "sequence")
    if not sequence then
        return -- commande incomplete : Python ecrivait peut-etre encore
    end

    if sequence <= self.last_command_sequence then
        return -- deja traitee : on ne rejoue jamais une commande
    end

    local command_type = read_string_field(content, "type")
    if not command_type then
        self.last_command_sequence = sequence
        self:emit_ack(sequence, "rejected", "champ 'type' absent")
        return
    end

    -- La sequence est consommee avant execution : une commande qui echoue ne
    -- doit pas etre retentee en boucle a chaque tick.
    self.last_command_sequence = sequence

    if command_type == "abort" then
        self:abort("commande abort recue")
        self:emit_ack(sequence, "released", nil, "arret demande")
        return
    end

    -- `orders` porte deplacements et attaques dans un seul message. Deux
    -- commandes successives se perdraient : le fichier est un objet unique,
    -- remplace a chaque ecriture, et le Lua ne le relit que toutes les 500 ms.
    -- `move_units` reste accepte : un pack plus recent que Python doit marcher.
    if command_type == "delegate" then
        local ids = read_id_list(content, "unit_ids")
        if #ids == 0 then
            self:emit_ack(sequence, "rejected", "aucune unite a confier")
            return
        end
        -- Trois valeurs de retour : les capturer d'abord, jamais dans l'appel
        -- de `emit_ack`, ou Lua les tronquerait a la premiere.
        local ok, confiees, motif, refuses = pcall(function()
            return self:delegate_units(ids)
        end)
        if not ok then
            self:emit_ack(
                sequence, "rejected", "erreur d'execution : " .. tostring(confiees), nil, ids
            )
            return
        end
        if confiees == 0 then
            self:emit_ack(sequence, "rejected", motif, nil, refuses or ids)
            return
        end
        self:emit_ack(
            sequence, "accepted", motif, tostring(confiees) .. " unite(s) confiee(s)", refuses
        )
        self:log(tostring(confiees) .. " unite(s) confiee(s) a l'IA du jeu")
        return
    end

    if command_type == "reclaim" then
        local ids = read_id_list(content, "unit_ids")
        local ok, rendues, refuses = pcall(function() return self:reclaim_units(ids) end)
        if not ok then
            self:emit_ack(
                sequence, "rejected", "erreur d'execution : " .. tostring(rendues), nil, ids
            )
            return
        end
        self:emit_ack(
            sequence, "released", nil, tostring(rendues) .. " unite(s) reprises", refuses
        )
        return
    end

    if command_type == "orders" or command_type == "move_units" then
        local ok_group, err_group = pcall(function()
            self:execute_orders(
                sequence,
                content,
                read_number_field(content, "release_after_ms") or 5000
            )
        end)
        if not ok_group then
            self:emit_ack(sequence, "rejected", "erreur d'execution : " .. tostring(err_group))
        end
        return
    end

    if command_type ~= "move_unit" then
        self:emit_ack(sequence, "rejected", "type de commande inconnu : " .. command_type)
        return
    end

    local unit_id = read_string_field(content, "unit_id")
    local destination = read_vector_field(content, "destination")
    if not unit_id or not destination then
        self:emit_ack(sequence, "rejected", "commande incomplete (unit_id ou destination)")
        return
    end

    local release_after_ms = read_number_field(content, "release_after_ms") or 5000

    local ok, err = pcall(function()
        self:execute_move(sequence, unit_id, destination, release_after_ms)
    end)
    if not ok then
        self:emit_ack(sequence, "rejected", "erreur d'execution : " .. tostring(err))
    end
end

--- Journalise une explication, mais pas a chaque tick : les premieres fois,
--- puis de loin en loin. Un journal noye est un journal inutile.
function PROBE:log_occasionally(counter_name, msg)
    local count = (self[counter_name] or 0) + 1
    self[counter_name] = count
    if count <= 3 or count % 20 == 0 then
        self:log(msg .. " (occurrence " .. tostring(count) .. ")")
    end
end

function PROBE:publish_state()
    if self.aborted then
        return
    end

    local unit, army, seen, controllable = self:find_controllable_unit()
    if not unit then
        -- Ne jamais echouer en silence : dire ce qu'on a vu.
        self:log_occasionally(
            "no_unit_count",
            "aucune unite controlable : " .. tostring(seen) .. " unites vues, "
                .. tostring(controllable) .. " controlables"
        )
        return
    end

    self:emit_state(
        self:unit_identifier(unit),
        tostring(unit:type()),
        self:unit_position(unit),
        true
    )

    -- La bataille entiere, dans le meme flux. L'etat mono-unite ci-dessus reste
    -- publie : il est petit, et c'est lui que la commande `probe` consomme.
    local ok, err = pcall(function() self:emit_battle_state() end)
    if not ok then
        self:log_occasionally("battle_state_error", "ERREUR dans emit_battle_state : " .. tostring(err))
    end
end

--[[--------------------------------------------------------------------------
    Arret et demarrage
----------------------------------------------------------------------------]]

function PROBE:abort(reason)
    if self.aborted then
        return
    end
    self.aborted = true
    self:release_all(reason)
    bm:remove_process("totalwar_ai_poll")
    bm:remove_process("totalwar_ai_state")
    self:log("SONDE ARRETEE : " .. tostring(reason))
end

--- Vrai si la bataille est multijoueur, ou si on n'a pas pu le determiner.
--- Le doute profite a la prudence : ce prototype ne doit jamais tourner dans
--- une partie a plusieurs.
function PROBE:is_multiplayer_or_unknown()
    local ok, result = pcall(function() return bm:is_multiplayer() end)
    if not ok then
        self:log("impossible de determiner le type de partie : sonde desactivee")
        return true
    end
    return result and true or false
end

--- Execute une methode en rattrapant toute erreur.
--- Une erreur dans un callback periodique le tue silencieusement : sans cette
--- garde, la sonde s'arreterait sans que rien ne l'indique.
function PROBE:guarded(method_name)
    local ok, err = pcall(function() self[method_name](self) end)
    if not ok then
        self:log_occasionally(
            "error_" .. method_name,
            "ERREUR dans " .. method_name .. " : " .. tostring(err)
        )
    end
end

function PROBE:start()
    if self:is_multiplayer_or_unknown() then
        self:log("multijoueur ou type de partie inconnu : la sonde reste desactivee")
        return
    end

    self:log("sonde active - protocole " .. self.protocol_version)
    self:log("repertoire d'echange attendu : " .. self.dir)

    -- Le diagnostic des entrees-sorties passe en premier : c'est la question
    -- de faisabilite centrale, et sa reponse ne doit dependre de rien d'autre.
    self:diagnose_io()

    local unit, _, seen, controllable = self:find_controllable_unit()
    self:log(
        "armee du joueur : " .. tostring(seen) .. " unites, "
            .. tostring(controllable) .. " controlables, "
            .. (unit and ("premiere = " .. self:unit_identifier(unit)) or "aucune utilisable")
    )

    -- Avant tout rappel periodique : neutraliser ce qu'une partie precedente a
    -- laisse sur le disque, faute de quoi cette bataille en heriterait.
    --
    -- L'ordre compte : la sentinelle d'abord. Tant qu'elle etait lue comme un
    -- arret, la sonde se coupait quelques secondes apres le chargement, avant
    -- meme le deploiement, et plus rien du cote Python ne pouvait la relancer.
    self:consume_stale_stop()
    self:discard_stale_command()

    -- Recensement unique : il dit ce que le jeu expose vraiment, et remplace
    -- les lignes « inconnue » de la fiche de faisabilite par des faits.
    self:census_alliances()
    self:guarded("census_planner_api")
    self:guarded("census_army_api")
    self:guarded("census_terrain")
    if unit then
        local ok, err = pcall(function() self:census_unit_accessors(unit, "premiere unite") end)
        if not ok then
            self:log("ERREUR pendant le recensement des accesseurs : " .. tostring(err))
        end

        -- La premiere unite d'une armee est le seigneur : une figurine seule.
        -- Recenser sur elle seule a donne `number_of_men_alive = 1` et
        -- `unary_hitpoints = 1`, d'ou l'on ne peut rien conclure sur ce que ces
        -- nombres signifient pour une unite de quatre-vingts hommes. On recense
        -- donc aussi une unite de troupe, la seule representative.
        local troop = self:find_multi_entity_unit()
        if troop then
            local ok_troop, err_troop = pcall(function()
                self:census_unit_accessors(troop, "unite de troupe")
            end)
            if not ok_troop then
                self:log("ERREUR pendant le recensement de la troupe : " .. tostring(err_troop))
            end
        else
            self:log("aucune unite de plus d'une entite trouvee : recensement partiel")
        end

        -- Le chronometrage du tir n'est pas un recensement d'accesseur : il
        -- demande d'observer une unite dans la duree. Il s'arrete de lui-meme,
        -- soit a la premiere salve, soit au bout de son quota de releves.
        self:guarded("start_missile_watch")
    end

    bm:repeat_callback(function() self:guarded("publish_state") end,
        self.state_interval_ms, "totalwar_ai_state")
    bm:repeat_callback(function() self:guarded("process_command_file") end,
        self.poll_interval_ms, "totalwar_ai_poll")

    -- Suivi des phases : Python doit pouvoir distinguer « l'ordre n'a rien
    -- produit » de « la bataille n'a pas encore commence ».
    for index = 1, #PHASES do
        local name = PHASES[index]
        bm:register_phase_change_callback(name, function()
            self.phase = name
            self:log("phase : " .. name)
        end)
    end

    -- Filet de securite : quoi qu'il arrive, rien ne reste pris a la fin.
    bm:register_phase_change_callback("Complete", function()
        self.phase = "Complete"
        -- Publier un dernier etat **avant** de tout arreter. Sans lui, Python ne
        -- voit jamais la fin de la bataille : le publieur d'etats disparait avec
        -- l'arret, l'issue reste `unknown`, et deux batailles enregistrees ne
        -- peuvent plus etre comparees — ce qui est pourtant tout l'interet de
        -- les enregistrer.
        self:guarded("emit_battle_state")
        self:abort("fin de bataille")
    end)
end

totalwar_ai_probe_loaded = PROBE

-- `bm` est le battle_manager, cree par le jeu en contexte de bataille
-- uniquement. Ce fichier peut aussi etre charge depuis le menu principal ou la
-- carte de campagne : il n'y a alors rien a faire, et c'est normal.
if bm then
    out("[totalwar_ai] contexte de bataille detecte : demarrage dans 1 seconde")
    bm:callback(function() PROBE:start() end, 1000, "totalwar_ai_start")
else
    out("[totalwar_ai] pas de battle_manager : hors bataille, sonde en veille")
end

return PROBE
