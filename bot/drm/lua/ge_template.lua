-- GE (game-engine) side: HWID, network, cache. It does NOT decide anything:
-- it hands raw signed data to the vehicle controller, which verifies it.
__BMS_CRYPTO__

local TOKEN       = "__BMS_TOKEN__"
local MODKEY      = "__BMS_MODKEY__"
local URL_TLS     = "__BMS_URL_HTTPS__"
local URL_PLAIN   = "__BMS_URL_HTTP__"
local CACHE_FILE  = "__BMS_CACHE_FILE__"
local DEVICE_FILE = "__BMS_DEVICE_FILE__"
local CTRL_NAME   = "__BMS_CTRL__"
local CB_NAME     = "__BMS_CB__"
local MEMO_KEY    = "__BMS_MEMO__"

local function steamIdentity()
  local probes = {
    function() return Steam.getAccountID() end,
    function() return Steam.getUserId64() end,
    function() return Engine.Steam.getAccountID() end,
    function() return Engine.Steam.getUserId64() end,
  }
  for _, probe in ipairs(probes) do
    local ok, val = pcall(probe)
    if ok and val ~= nil then
      local sv = tostring(val)
      if sv ~= "" and sv ~= "0" and sv ~= "nil" then return "steam:" .. sv end
    end
  end
  return nil
end

local function deviceIdentity()
  local data = jsonReadFile(DEVICE_FILE)
  if type(data) == "table" and type(data["id"]) == "string" and #data["id"] >= 16 then
    return data["id"]
  end
  local seed = tostring({}) .. tostring(os.time()) .. tostring(os.clock()) .. tostring(math.random())
  local fresh = "dev:" .. string.sub(sha512_hex(seed), 1, 40)
  jsonWriteFile(DEVICE_FILE, { ["id"] = fresh }, true)
  return fresh
end

local function computeHwid()
  -- Steam account first: copying a settings file to another PC can't fake it.
  local raw = steamIdentity() or deviceIdentity()
  return string.sub(sha512_hex("BMS-HWID|" .. raw), 1, 64)
end

local function fetch(url)
  local ok, body, code = pcall(function()
    local client
    if string.sub(url, 1, 6) == "https:" then
      local okTls, https = pcall(require, "ssl.https")
      if not okTls or type(https) ~= "table" then return nil, nil end
      client = https
    else
      client = require("socket.http")
    end
    client.TIMEOUT = 4
    return client.request(url)
  end)
  if ok and code == 200 and type(body) == "string" then return body end
  return nil
end

return function(vehicleId, nonce)
  local hwid = computeHwid()
  local net = ""
  local lastFail = rawget(_G, MEMO_KEY)
  -- After a failed attempt, skip the (blocking) network for 10 min; the cache covers it.
  if not (type(lastFail) == "number" and os.time() - lastFail < 600) then
    local query = "?t=" .. TOKEN .. "&m=" .. MODKEY .. "&h=" .. hwid .. "&n=" .. nonce .. "&v=2"
    local body = fetch(URL_TLS .. query) or fetch(URL_PLAIN .. query)
    if body and string.sub(body, 1, 5) == "BMS1|" then
      net = body
      rawset(_G, MEMO_KEY, nil)
      jsonWriteFile(CACHE_FILE, { ["t"] = body }, true)
    else
      rawset(_G, MEMO_KEY, os.time())
    end
  end

  local cached = ""
  local cacheData = jsonReadFile(CACHE_FILE)
  if type(cacheData) == "table" and type(cacheData["t"]) == "string" then cached = cacheData["t"] end

  local veh = be:getObjectByID(vehicleId)
  if veh then
    veh:queueLuaCommand(string.format(
      "local c=controller.getController(%q) if c and c[%q] then c[%q](%q,%q,%q,%d) end",
      CTRL_NAME, CB_NAME, CB_NAME, net, cached, hwid, os.time()))
  end
end
