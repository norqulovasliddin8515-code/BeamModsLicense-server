-- Vehicle Lua controller (auxiliary). Fail-closed: the vehicle stays locked
-- until a correctly Ed25519-signed ticket for THIS token/mod/HWID/nonce arrives.
local MOD = {}
MOD.type = "auxiliary"

__BMS_CRYPTO__

local PUBKEY  = "__BMS_PUBKEY__"
local TOKEN   = "__BMS_TOKEN__"
local MODKEY  = "__BMS_MODKEY__"
local CB_NAME = "__BMS_CB__"
local GE_CODE = "__BMS_GE_CODE__"

local unlocked  = false
local pending   = true
local nonce     = ""
local reasonMsg = "Checking license..."
local warnTimer = 0

local REASONS = {
  ["hwid_mismatch"] = "License is activated on another PC. Contact @BeamModsStudio_bot",
  ["revoked"]       = "License revoked. Contact @BeamModsStudio_bot",
  ["wrong_mod"]     = "License does not belong to this mod",
  ["invalid"]       = "Invalid license",
  ["offline"]       = "License server unreachable. Connect to the internet once to activate.",
}

local function lockVehicle()
  if electrics.setIgnitionLevel then electrics.setIgnitionLevel(0) end
  if electrics.values then
    electrics.values.throttle = 0
    electrics.values.brake = 1
    electrics.values.parkingbrake = 1
    electrics.values.engineRunning = 0
    electrics.values.ignitionLevel = 0
  end
  if powertrain then
    local engine = powertrain.getDevice("mainEngine")
    if engine then
      if engine.setIgnition then engine:setIgnition(0) end
      engine.ignitionCoef = 0
    end
  end
end

local function unlockVehicle()
  unlocked = true
  if electrics.setIgnitionLevel then electrics.setIgnitionLevel(3) end
  if powertrain then
    local engine = powertrain.getDevice("mainEngine")
    if engine then
      if engine.setIgnition then engine:setIgnition(1) end
      engine.ignitionCoef = 1
    end
  end
end

local function parseTicket(body, hwid)
  if type(body) ~= "string" then return nil end
  local nl = string.find(body, "\n", 1, true)
  if not nl then return nil end
  local payload = string.sub(body, 1, nl - 1)
  local sig = (string.gsub(string.sub(body, nl + 1), "%s+$", ""))
  local fields = {}
  for part in string.gmatch(payload .. "|", "([^|]*)|") do fields[#fields + 1] = part end
  if #fields ~= 8 or fields[1] ~= "BMS1" then return nil end
  if fields[3] ~= MODKEY or fields[4] ~= TOKEN or fields[5] ~= hwid then return nil end
  if not ed25519_verify(sig, payload, PUBKEY) then return nil end
  return fields
end

local function deny(code)
  pending = false
  unlocked = false
  reasonMsg = REASONS[code] or REASONS["invalid"]
  guihooks.message(reasonMsg, 10, "DRM")
end

local function onResult(net, cached, hwid, now)
  if not pending then return end
  now = tonumber(now) or 0

  local live = parseTicket(net, hwid)
  if live and live[6] == nonce then
    pending = false
    if live[2] == "ok" then unlockVehicle() else deny(live[2]) end
    return
  end

  local offline = parseTicket(cached, hwid)
  if offline and offline[2] == "ok" then
    local issued, expires = tonumber(offline[7]) or 0, tonumber(offline[8]) or 0
    if expires > now and issued <= now + 600 then
      pending = false
      unlockVehicle()
      return
    end
  end
  deny("offline")
end

local function init(jbeamData)
  unlocked, pending, warnTimer = false, true, 0
  reasonMsg = "Checking license..."
  local entropy = tostring({}) .. tostring(objectId) .. tostring(math.random())
  if os and os.clock then entropy = entropy .. tostring(os.clock()) end
  if os and os.time then entropy = entropy .. tostring(os.time()) end
  nonce = string.sub(sha512_hex(entropy), 1, 32)
  obj:queueGameEngineLua("local f=(function() " .. GE_CODE .. " end)() f(" ..
    tostring(objectId) .. "," .. string.format("%q", nonce) .. ")")
end

local function updateGFX(dt)
  if not unlocked then
    lockVehicle()
    warnTimer = warnTimer + dt
    if warnTimer > 3 then
      guihooks.message(reasonMsg, 3, "DRM")
      warnTimer = 0
    end
  end
end

MOD.init = init
MOD.updateGFX = updateGFX
MOD[CB_NAME] = onResult

return MOD
