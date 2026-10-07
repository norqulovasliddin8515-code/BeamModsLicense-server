-- luajit tests/drm/run_vectors.lua   (after: python tests/drm/gen_vectors.py)
local here = arg[0]:match("(.*/)") or "./"
local src = io.open(here .. "../../bot/drm/lua/crypto_fragment.lua"):read("*a")
local C = assert(loadstring(src .. "\nreturn {verify=ed25519_verify, sha512_hex=sha512_hex}"))()
local V = dofile(here .. "vectors.lua")
local function unhex(h) return (h:gsub("..", function(x) return string.char(tonumber(x, 16)) end)) end
local pass, fail, t0 = 0, 0, os.clock()
for i, v in ipairs(V) do
  local m = unhex(v.msg)
  if C.verify(v.sig, m, v.pk) == v.ok and C.sha512_hex(m) == v.sha then pass = pass + 1
  else fail = fail + 1; print("FAIL", i) end
end
print(string.format("pass=%d fail=%d  avg %.1f ms/verify", pass, fail, (os.clock() - t0) * 1000 / #V))
os.exit(fail == 0 and 0 or 1)
