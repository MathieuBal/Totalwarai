--[[----------------------------------------------------------------------------
    Faux jeu, pour executer la sonde hors de WARHAMMER III.

    Reproduit le strict minimum de l'API de bataille dont
    `totalwar_ai_probe.lua` se sert : le battle_manager, les alliances, armees
    et unites, le unitcontroller, et les constructeurs de vecteurs.

    Ce n'est pas le jeu. Un test qui passe ici prouve que la **logique** de la
    sonde tient debout ; il ne prouve rien sur le comportement reel du moteur.
    Voir docs/feasibility.md pour ce qui ne peut etre etabli qu'en bataille.
------------------------------------------------------------------------------]]

FAKE = {
    log = {},
    callbacks = {},        -- rappels differes : { fn, at_ms, name }
    repeats = {},          -- rappels periodiques : { fn, every_ms, next_ms, name }
    phase_callbacks = {},
    now_ms = 0,
    multiplayer = false,
    controllers = {},      -- unitcontrollers crees, pour inspection
    orders = {},           -- ordres recus : { kind, unit_id, x, z }
}

function out(msg)
    FAKE.log[#FAKE.log + 1] = tostring(msg)
end

--- Toutes les lignes du journal contenant un fragment donne.
function FAKE:grep(fragment)
    local found = {}
    for index = 1, #self.log do
        if string.find(self.log[index], fragment, 1, true) then
            found[#found + 1] = self.log[index]
        end
    end
    return found
end

-- --- vecteurs ---------------------------------------------------------------

local function make_vector(x, y, z)
    return {
        x = x, y = y, z = z,
        get_x = function(self) return self.x end,
        get_y = function(self) return self.y end,
        get_z = function(self) return self.z end,
    }
end

function v(x, y, z)
    return make_vector(x, y or 0, z)
end

function v_to_ground(vec)
    return make_vector(vec.x, 0, vec.z)
end

-- --- unites -----------------------------------------------------------------

local function make_unit(id, unit_type, x, z, controllable)
    return {
        id = id,
        unit_type = unit_type,
        pos = make_vector(x, 12.5, z),
        controllable = controllable,
        unique_ui_id = function(self) return self.id end,
        type = function(self) return self.unit_type end,
        position = function(self) return self.pos end,
        is_controllable = function(self) return self.controllable end,
        is_valid_target = function(self) return true end,
    }
end

local function make_collection(items)
    return {
        items = items,
        count = function(self) return #self.items end,
        item = function(self, index) return self.items[index] end,
    }
end

-- --- unitcontroller ---------------------------------------------------------

local function make_unit_controller()
    local uc = {
        units = {},
        released = false,
        add_units = function(self, unit)
            self.units[#self.units + 1] = unit
            return true
        end,
        goto_location = function(self, destination, should_run)
            for index = 1, #self.units do
                FAKE.orders[#FAKE.orders + 1] = {
                    kind = "goto",
                    unit_id = self.units[index].id,
                    x = destination.x,
                    z = destination.z,
                }
                -- Le faux jeu deplace l'unite immediatement.
                self.units[index].pos = make_vector(destination.x, 12.5, destination.z)
            end
            return true
        end,
        release_control = function(self)
            self.released = true
            FAKE.orders[#FAKE.orders + 1] = { kind = "release" }
            return true
        end,
    }
    FAKE.controllers[#FAKE.controllers + 1] = uc
    return uc
end

-- --- battle_manager ---------------------------------------------------------

function FAKE:setup(units)
    local army = {
        units_collection = make_collection(units),
        units = function(self) return self.units_collection end,
        create_unit_controller = function(self) return make_unit_controller() end,
    }
    local alliance = {
        armies_collection = make_collection({ army }),
        armies = function(self) return self.armies_collection end,
    }

    bm = {
        alliances_collection = make_collection({ alliance }),
        alliances = function(self) return self.alliances_collection end,
        local_alliance = function(self) return 1 end,
        local_army = function(self) return 1 end,
        is_multiplayer = function(self) return FAKE.multiplayer end,
        time_elapsed_ms = function(self) return FAKE.now_ms end,
        callback = function(self, fn, ms, name)
            FAKE.callbacks[#FAKE.callbacks + 1] =
                { fn = fn, at_ms = FAKE.now_ms + ms, name = name }
        end,
        repeat_callback = function(self, fn, ms, name)
            FAKE.repeats[#FAKE.repeats + 1] =
                { fn = fn, every_ms = ms, next_ms = FAKE.now_ms + ms, name = name }
        end,
        remove_process = function(self, name)
            for index = #FAKE.repeats, 1, -1 do
                if FAKE.repeats[index].name == name then
                    table.remove(FAKE.repeats, index)
                end
            end
        end,
        register_phase_change_callback = function(self, phase, fn)
            FAKE.phase_callbacks[phase] = fn
        end,
    }
    return army
end

--- Avance l'horloge et declenche les rappels arrives a echeance.
function FAKE:advance(ms)
    local target = self.now_ms + ms
    while self.now_ms < target do
        self.now_ms = math.min(self.now_ms + 100, target)

        for index = #self.callbacks, 1, -1 do
            local entry = self.callbacks[index]
            if entry.at_ms <= self.now_ms then
                table.remove(self.callbacks, index)
                entry.fn()
            end
        end

        for index = 1, #self.repeats do
            local entry = self.repeats[index]
            if entry and entry.next_ms <= self.now_ms then
                entry.next_ms = self.now_ms + entry.every_ms
                entry.fn()
            end
        end
    end
end

function FAKE:make_unit(id, unit_type, x, z, controllable)
    return make_unit(id, unit_type, x, z, controllable)
end

return FAKE
