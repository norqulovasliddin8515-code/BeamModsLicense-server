-- Minimal BeamNG mock: separate GE and vehicle Lua environments, JSON files in memory,
-- real LuaSocket for HTTP. Usage: luajit beamng_mock.lua <controller.lua> <ctrlname> <steamid> <scenario>
local ctrlFile, ctrlName, steamId, store = arg[1], arg[2], arg[3], arg[4]

-- persistent "userfolder" (serialised to a file between process runs)
local function loadStore()
  local f = io.open(store, "r"); if not f then return {} end
  local s = f:read("*a"); f:close()
  local ok, t = pcall(loadstring("return " .. s)); return ok and t or {}
end
local function saveStore(t)
  local parts = {}
  for k, v in pairs(t) do parts[#parts+1] = string.format("[%q]={t=%q,id=%q}", k, v.t or "", v.id or "") end
  local f = io.open(store, "w"); f:write("{" .. table.concat(parts, ",") .. "}"); f:close()
end
local FS = loadStore()

local function jsonReadFile(p) local v = FS[p]; if not v then return nil end
  local r = {}; if v.t ~= "" then r.t = v.t end; if v.id ~= "" then r.id = v.id end; return r end
local function jsonWriteFile(p, data) FS[p] = { t = data.t, id = data.id }; saveStore(FS) end

local state = { ignition = 0, messages = {} }
local vehEnv, geEnv, module
local veh = {}
function veh:queueLuaCommand(cmd)
  local f = assert(loadstring(cmd)); setfenv(f, vehEnv); f()
end

geEnv = setmetatable({
  Steam = { getAccountID = function() return steamId end },
  be = { getObjectByID = function(_, id) return veh end },
  jsonReadFile = jsonReadFile, jsonWriteFile = jsonWriteFile,
}, { __index = _G })

vehEnv = setmetatable({
  objectId = 42,
  obj = { queueGameEngineLua = function(_, code)
    local f, err = loadstring(code); assert(f, err); setfenv(f, geEnv)
    if arg[5] == "forge" then DEFERRED = f else f() end
  end },
  electrics = { values = {}, setIgnitionLevel = function(l) state.ignition = l end },
  powertrain = { getDevice = function() return { ignitionCoef = 1 } end },
  guihooks = { message = function(m) state.messages[#state.messages+1] = m end },
  controller = { getController = function(n) if n == ctrlName then return module end end },
}, { __index = _G })

local chunk = assert(loadfile(ctrlFile)); setfenv(chunk, vehEnv)
module = chunk()
local t0 = os.clock()
module.init({})
module.updateGFX(0.016)

-- Console-bypass attempts by a user:
for k, fn in pairs(module) do
  if type(fn) == "function" and k ~= "init" and k ~= "updateGFX" and arg[5] == "forge" then
    fn("BMS1|ok|x|y|z|w|1|9999999999\n" .. string.rep("00", 64), "", "z", os.time())
  end
end
if DEFERRED then DEFERRED() end
module.updateGFX(0.016)
print(string.format("RESULT ignition=%d brake=%s msg=%s  (%.0f ms)", state.ignition,
  tostring(vehEnv.electrics.values.brake), state.messages[#state.messages] or "-", (os.clock()-t0)*1000))
