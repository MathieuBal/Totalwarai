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
TOTALWAR_AI_PROBE_REVISION = 5

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
    state_interval_ms = 1000,

    sequence = 0,             -- compteur des etats emis
    last_command_sequence = 0, -- derniere commande executee : jamais rejouee
    controlled = {},          -- ui_id -> { uc = unitcontroller, release_at_ms = number }
    aborted = false,
    can_write = nil,          -- resultat du test d'ecriture, evalue une seule fois
    phase = "unknown",        -- derniere phase de bataille annoncee par le jeu
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
    add_bool("alive", "is_valid_target")
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

function PROBE:emit_ack(sequence, status, error_message, detail)
    local line = "{"
        .. '"protocol_version":' .. json_string(self.protocol_version) .. ","
        .. '"type":"action_result",'
        .. '"sequence":' .. json_number(sequence) .. ","
        .. '"status":' .. json_string(status) .. ","
        .. '"error":' .. (error_message and json_string(error_message) or "null")

    if detail then
        line = line .. ',"detail":{"note":' .. json_string(detail) .. "}"
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

--- Accesseurs sans argument a tester sur une unite alliee.
local UNIT_ACCESSORS = {
    "unique_ui_id",
    "type",
    "name",
    "is_controllable",
    "is_valid_target",
    "is_commanding_unit",
    "is_idle",
    "number_of_men",
    "number_of_men_alive",
    "unary_hitpoints",
    "unary_morale",
    "is_routing",
    "is_shattered",
    "is_hidden",
    "is_in_melee",
    "can_fly",
    "bearing",
    "ammo_left",
    "missile_range",
    "fatigue",
    "unary_fatigue",
    "fatigue_level",
    "speed",
    "width",
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
        if type(method) ~= "function" then
            self:log("  " .. name .. " : ABSENT")
        else
            local ok, value = pcall(method, unit)
            if ok then
                self:log("  " .. name .. " : " .. describe(value))
                disponibles[#disponibles + 1] = name
            else
                self:log("  " .. name .. " : ERREUR " .. tostring(value))
            end
        end
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

function PROBE:find_unit_by_id(wanted_id)
    local alliance = bm:alliances():item(bm:local_alliance())
    local army = alliance:armies():item(bm:local_army())
    local units = army:units()

    for index = 1, units:count() do
        local unit = units:item(index)
        if unit and tostring(unit:unique_ui_id()) == tostring(wanted_id) then
            return unit, army
        end
    end
    return nil, army
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

function PROBE:release_all(reason)
    local count = 0
    for ui_id, _ in pairs(self.controlled) do
        if self:release_unit(ui_id) then
            count = count + 1
        end
    end
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
    if #moves == 0 and #attacks == 0 then
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

    local resume = tostring(lances) .. " ordre(s) lance(s), " .. tostring(refuses) .. " refuse(s)"
    if lances == 0 then
        self:emit_ack(sequence, "rejected", premier_refus, resume)
        return
    end
    self:emit_ack(sequence, "accepted", premier_refus, resume)
    self:log(
        "manoeuvre : " .. tostring(#moves) .. " deplacement(s), "
            .. tostring(#attacks) .. " attaque(s) — " .. resume
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
    if self:file_exists(self.stop_file) then
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

    -- Avant tout rappel periodique : neutraliser un ordre laisse par une
    -- partie precedente, faute de quoi il s'executerait ici.
    self:discard_stale_command()

    -- Recensement unique : il dit ce que le jeu expose vraiment, et remplace
    -- les lignes « inconnue » de la fiche de faisabilite par des faits.
    self:census_alliances()
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
