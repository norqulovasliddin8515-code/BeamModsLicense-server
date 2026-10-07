-- ==== BMS crypto fragment: SHA-512 + Ed25519 signature VERIFY (pure LuaJIT) ====
-- Port of TweetNaCl (public domain). Field elements: 16 limbs x 16 bit in doubles.
local bit = require("bit")
local band, bor, bxor, rshift, lshift, arshift = bit.band, bit.bor, bit.bxor, bit.rshift, bit.lshift, bit.arshift
local floor = math.floor
local TWO32 = 4294967296

-- ---------- SHA-512 (64-bit words as hi/lo 32-bit unsigned doubles) ----------
local K = {0x428a2f98,0xd728ae22, 0x71374491,0x23ef65cd, 0xb5c0fbcf,0xec4d3b2f, 0xe9b5dba5,0x8189dbbc, 0x3956c25b,0xf348b538, 0x59f111f1,0xb605d019, 0x923f82a4,0xaf194f9b, 0xab1c5ed5,0xda6d8118, 0xd807aa98,0xa3030242, 0x12835b01,0x45706fbe, 0x243185be,0x4ee4b28c, 0x550c7dc3,0xd5ffb4e2, 0x72be5d74,0xf27b896f, 0x80deb1fe,0x3b1696b1, 0x9bdc06a7,0x25c71235, 0xc19bf174,0xcf692694, 0xe49b69c1,0x9ef14ad2, 0xefbe4786,0x384f25e3, 0xfc19dc6,0x8b8cd5b5, 0x240ca1cc,0x77ac9c65, 0x2de92c6f,0x592b0275, 0x4a7484aa,0x6ea6e483, 0x5cb0a9dc,0xbd41fbd4, 0x76f988da,0x831153b5, 0x983e5152,0xee66dfab, 0xa831c66d,0x2db43210, 0xb00327c8,0x98fb213f, 0xbf597fc7,0xbeef0ee4, 0xc6e00bf3,0x3da88fc2, 0xd5a79147,0x930aa725, 0x6ca6351,0xe003826f, 0x14292967,0xa0e6e70, 0x27b70a85,0x46d22ffc, 0x2e1b2138,0x5c26c926, 0x4d2c6dfc,0x5ac42aed, 0x53380d13,0x9d95b3df, 0x650a7354,0x8baf63de, 0x766a0abb,0x3c77b2a8, 0x81c2c92e,0x47edaee6, 0x92722c85,0x1482353b, 0xa2bfe8a1,0x4cf10364, 0xa81a664b,0xbc423001, 0xc24b8b70,0xd0f89791, 0xc76c51a3,0x654be30, 0xd192e819,0xd6ef5218, 0xd6990624,0x5565a910, 0xf40e3585,0x5771202a, 0x106aa070,0x32bbd1b8, 0x19a4c116,0xb8d2d0c8, 0x1e376c08,0x5141ab53, 0x2748774c,0xdf8eeb99, 0x34b0bcb5,0xe19b48a8, 0x391c0cb3,0xc5c95a63, 0x4ed8aa4a,0xe3418acb, 0x5b9cca4f,0x7763e373, 0x682e6ff3,0xd6b2b8a3, 0x748f82ee,0x5defb2fc, 0x78a5636f,0x43172f60, 0x84c87814,0xa1f0ab72, 0x8cc70208,0x1a6439ec, 0x90befffa,0x23631e28, 0xa4506ceb,0xde82bde9, 0xbef9a3f7,0xb2c67915, 0xc67178f2,0xe372532b, 0xca273ece,0xea26619c, 0xd186b8c7,0x21c0c207, 0xeada7dd6,0xcde0eb1e, 0xf57d4f7f,0xee6ed178, 0x6f067aa,0x72176fba, 0xa637dc5,0xa2c898a6, 0x113f9804,0xbef90dae, 0x1b710b35,0x131c471b, 0x28db77f5,0x23047d84, 0x32caab7b,0x40c72493, 0x3c9ebe0a,0x15c9bebc, 0x431d67c4,0x9c100d4c, 0x4cc5d4be,0xcb3e42b6, 0x597f299c,0xfc657e2a, 0x5fcb6fab,0x3ad6faec, 0x6c44198c,0x4a475817}
local H0 = {0x6a09e667,0xf3bcc908, 0xbb67ae85,0x84caa73b, 0x3c6ef372,0xfe94f82b, 0xa54ff53a,0x5f1d36f1, 0x510e527f,0xade682d1, 0x9b05688c,0x2b3e6c1f, 0x1f83d9ab,0xfb41bd6b, 0x5be0cd19,0x137e2179}

local function u32(x) return x % TWO32 end

local function rotr(h, l, n)
  if n < 32 then
    return u32(bor(rshift(h, n), lshift(l, 32 - n))), u32(bor(rshift(l, n), lshift(h, 32 - n)))
  elseif n == 32 then
    return l, h
  else
    n = n - 32
    return u32(bor(rshift(l, n), lshift(h, 32 - n))), u32(bor(rshift(h, n), lshift(l, 32 - n)))
  end
end

local function shr(h, l, n) -- n < 32
  return rshift(h, n) % TWO32, u32(bor(rshift(l, n), lshift(h, 32 - n)))
end

local function sha512(msg)
  local len = #msg
  local bytes = { msg:byte(1, -1) }
  bytes[#bytes + 1] = 0x80
  while (#bytes % 128) ~= 112 do bytes[#bytes + 1] = 0 end
  local bitlen = len * 8
  for _ = 1, 8 do bytes[#bytes + 1] = 0 end -- high 64 bits of 128-bit length
  for i = 7, 0, -1 do
    bytes[#bytes + 1] = floor(bitlen / 2 ^ (8 * i)) % 256
  end

  local Hh, Hl = {}, {}
  for i = 1, 8 do Hh[i] = H0[2 * i - 1]; Hl[i] = H0[2 * i] end
  local Wh, Wl = {}, {}

  for chunk = 0, #bytes - 1, 128 do
    for t = 1, 16 do
      local o = chunk + (t - 1) * 8
      Wh[t] = ((bytes[o + 1] * 256 + bytes[o + 2]) * 256 + bytes[o + 3]) * 256 + bytes[o + 4]
      Wl[t] = ((bytes[o + 5] * 256 + bytes[o + 6]) * 256 + bytes[o + 7]) * 256 + bytes[o + 8]
    end
    for t = 17, 80 do
      local ah, al = rotr(Wh[t - 15], Wl[t - 15], 1)
      local bh, bl = rotr(Wh[t - 15], Wl[t - 15], 8)
      local ch, cl = shr(Wh[t - 15], Wl[t - 15], 7)
      local s0h, s0l = u32(bxor(ah, bh, ch)), u32(bxor(al, bl, cl))
      ah, al = rotr(Wh[t - 2], Wl[t - 2], 19)
      bh, bl = rotr(Wh[t - 2], Wl[t - 2], 61)
      ch, cl = shr(Wh[t - 2], Wl[t - 2], 6)
      local s1h, s1l = u32(bxor(ah, bh, ch)), u32(bxor(al, bl, cl))
      local lo = Wl[t - 16] + s0l + Wl[t - 7] + s1l
      local carry = floor(lo / TWO32)
      Wl[t] = lo % TWO32
      Wh[t] = (Wh[t - 16] + s0h + Wh[t - 7] + s1h + carry) % TWO32
    end

    local ah_, al_ = Hh[1], Hl[1]
    local bh_, bl_ = Hh[2], Hl[2]
    local ch_, cl_ = Hh[3], Hl[3]
    local dh_, dl_ = Hh[4], Hl[4]
    local eh_, el_ = Hh[5], Hl[5]
    local fh_, fl_ = Hh[6], Hl[6]
    local gh_, gl_ = Hh[7], Hl[7]
    local hh_, hl_ = Hh[8], Hl[8]

    for t = 1, 80 do
      local xh, xl = rotr(eh_, el_, 14)
      local yh, yl = rotr(eh_, el_, 18)
      local zh, zl = rotr(eh_, el_, 41)
      local S1h, S1l = u32(bxor(xh, yh, zh)), u32(bxor(xl, yl, zl))
      local chh = u32(bxor(band(eh_, fh_), band(bit.bnot(eh_), gh_)))
      local chl = u32(bxor(band(el_, fl_), band(bit.bnot(el_), gl_)))
      local lo = hl_ + S1l + chl + K[2 * t] + Wl[t]
      local c = floor(lo / TWO32)
      local t1l = lo % TWO32
      local t1h = (hh_ + S1h + chh + K[2 * t - 1] + Wh[t] + c) % TWO32

      xh, xl = rotr(ah_, al_, 28)
      yh, yl = rotr(ah_, al_, 34)
      zh, zl = rotr(ah_, al_, 39)
      local S0h, S0l = u32(bxor(xh, yh, zh)), u32(bxor(xl, yl, zl))
      local mjh = u32(bxor(band(ah_, bh_), band(ah_, ch_), band(bh_, ch_)))
      local mjl = u32(bxor(band(al_, bl_), band(al_, cl_), band(bl_, cl_)))
      lo = S0l + mjl
      c = floor(lo / TWO32)
      local t2l = lo % TWO32
      local t2h = (S0h + mjh + c) % TWO32

      hh_, hl_ = gh_, gl_
      gh_, gl_ = fh_, fl_
      fh_, fl_ = eh_, el_
      lo = dl_ + t1l
      c = floor(lo / TWO32)
      el_ = lo % TWO32
      eh_ = (dh_ + t1h + c) % TWO32
      dh_, dl_ = ch_, cl_
      ch_, cl_ = bh_, bl_
      bh_, bl_ = ah_, al_
      lo = t1l + t2l
      c = floor(lo / TWO32)
      al_ = lo % TWO32
      ah_ = (t1h + t2h + c) % TWO32
    end

    local add = { ah_, al_, bh_, bl_, ch_, cl_, dh_, dl_, eh_, el_, fh_, fl_, gh_, gl_, hh_, hl_ }
    for i = 1, 8 do
      local lo = Hl[i] + add[2 * i]
      local c = floor(lo / TWO32)
      Hl[i] = lo % TWO32
      Hh[i] = (Hh[i] + add[2 * i - 1] + c) % TWO32
    end
  end

  local out = {}
  for i = 1, 8 do
    for _, w in ipairs({ Hh[i], Hl[i] }) do
      out[#out + 1] = floor(w / 16777216) % 256
      out[#out + 1] = floor(w / 65536) % 256
      out[#out + 1] = floor(w / 256) % 256
      out[#out + 1] = w % 256
    end
  end
  return out -- 64 bytes, 1-indexed
end

local function bytes_to_hex(t, n)
  local s = {}
  for i = 1, (n or #t) do s[i] = string.format("%02x", t[i]) end
  return table.concat(s)
end

local function hex_to_bytes0(h) -- 0-indexed byte table, nil on bad hex
  if type(h) ~= "string" or #h % 2 ~= 0 or h:find("[^0-9a-fA-F]") then return nil end
  local t = {}
  for i = 0, #h / 2 - 1 do t[i] = tonumber(h:sub(2 * i + 1, 2 * i + 2), 16) end
  return t
end

-- ---------- GF(2^255-19) ----------
local function gf(init)
  local r = {}
  for i = 0, 15 do r[i] = 0 end
  if init then for i = 1, #init do r[i - 1] = init[i] end end
  return r
end

local D = gf({0x78a3, 0x1359, 0x4dca, 0x75eb, 0xd8ab, 0x4141, 0xa4d, 0x70, 0xe898, 0x7779, 0x4079, 0x8cc7, 0xfe73, 0x2b6f, 0x6cee, 0x5203})
local D2 = gf({0xf159, 0x26b2, 0x9b94, 0xebd6, 0xb156, 0x8283, 0x149a, 0xe0, 0xd130, 0xeef3, 0x80f2, 0x198e, 0xfce7, 0x56df, 0xd9dc, 0x2406})
local X = gf({0xd51a, 0x8f25, 0x2d60, 0xc956, 0xa7b2, 0x9525, 0xc760, 0x692c, 0xdc5c, 0xfdd6, 0xe231, 0xc0a4, 0x53fe, 0xcd6e, 0x36d3, 0x2169})
local Y = gf({0x6658, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666})
local I = gf({0xa0b0, 0x4a0e, 0x1b27, 0xc4ee, 0xe478, 0xad2f, 0x1806, 0x2f43, 0xd7a7, 0x3dfb, 0x99, 0x2b4d, 0xdf0b, 0x4fc1, 0x2480, 0x2b83})
local Lc = {[0]=0xed, 0xd3, 0xf5, 0x5c, 0x1a, 0x63, 0x12, 0x58, 0xd6, 0x9c, 0xf7, 0xa2, 0xde, 0xf9, 0xde, 0x14, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x10}
local gf0, gf1 = gf(), gf({ 1 })

local function set25519(r, a) for i = 0, 15 do r[i] = a[i] end end

local function car25519(o)
  local c = 1
  for i = 0, 15 do
    local cv = o[i] + c + 65535
    c = floor(cv / 65536)
    o[i] = cv - c * 65536
  end
  o[0] = o[0] + c - 1 + 37 * (c - 1)
end

local function sel25519(p, q, b)
  if b == 1 then
    for i = 0, 15 do p[i], q[i] = q[i], p[i] end
  end
end

local function pack25519(o, n)
  local m, t = gf(), gf()
  for i = 0, 15 do t[i] = n[i] end
  car25519(t); car25519(t); car25519(t)
  for _ = 1, 2 do
    m[0] = t[0] - 0xffed
    for i = 1, 14 do
      m[i] = t[i] - 0xffff - band(arshift(m[i - 1], 16), 1)
      m[i - 1] = band(m[i - 1], 0xffff)
    end
    m[15] = t[15] - 0x7fff - band(arshift(m[14], 16), 1)
    local b = band(arshift(m[15], 16), 1)
    m[14] = band(m[14], 0xffff)
    sel25519(t, m, 1 - b)
  end
  for i = 0, 15 do
    o[2 * i] = band(t[i], 0xff)
    o[2 * i + 1] = rshift(t[i], 8) % 256
  end
end

local function neq25519(a, b)
  local c, d = {}, {}
  pack25519(c, a); pack25519(d, b)
  local diff = 0
  for i = 0, 31 do diff = bor(diff, bxor(c[i], d[i])) end
  return diff ~= 0
end

local function par25519(a)
  local d = {}
  pack25519(d, a)
  return band(d[0], 1)
end

local function unpack25519(o, n)
  for i = 0, 15 do o[i] = n[2 * i] + n[2 * i + 1] * 256 end
  o[15] = band(o[15], 0x7fff)
end

local function A(o, a, b) for i = 0, 15 do o[i] = a[i] + b[i] end end
local function Z(o, a, b) for i = 0, 15 do o[i] = a[i] - b[i] end end

local function M(o, a, b)
  local t = {}
  for i = 0, 30 do t[i] = 0 end
  for i = 0, 15 do
    local ai = a[i]
    for j = 0, 15 do t[i + j] = t[i + j] + ai * b[j] end
  end
  for i = 0, 14 do t[i] = t[i] + 38 * t[i + 16] end
  for i = 0, 15 do o[i] = t[i] end
  car25519(o); car25519(o)
end

local function S(o, a) M(o, a, a) end

local function pow2523(o, i)
  local c = gf()
  set25519(c, i)
  for a = 250, 0, -1 do
    S(c, c)
    if a ~= 1 then M(c, c, i) end
  end
  set25519(o, c)
end

local function inv25519(o, i)
  local c = gf()
  set25519(c, i)
  for a = 253, 0, -1 do
    S(c, c)
    if a ~= 2 and a ~= 4 then M(c, c, i) end
  end
  set25519(o, c)
end

local function add(p, q)
  local a, b, c, d, e, f, g, h, t = gf(), gf(), gf(), gf(), gf(), gf(), gf(), gf(), gf()
  Z(a, p[2], p[1]); Z(t, q[2], q[1]); M(a, a, t)
  A(b, p[1], p[2]); A(t, q[1], q[2]); M(b, b, t)
  M(c, p[4], q[4]); M(c, c, D2)
  M(d, p[3], q[3]); A(d, d, d)
  Z(e, b, a); Z(f, d, c); A(g, d, c); A(h, b, a)
  M(p[1], e, f); M(p[2], h, g); M(p[3], g, f); M(p[4], e, h)
end

local function cswap(p, q, b) for i = 1, 4 do sel25519(p[i], q[i], b) end end

local function pack(r, p)
  local tx, ty, zi = gf(), gf(), gf()
  inv25519(zi, p[3])
  M(tx, p[1], zi); M(ty, p[2], zi)
  pack25519(r, ty)
  r[31] = bxor(r[31], par25519(tx) * 128)
end

local function scalarmult(p, q, s)
  set25519(p[1], gf0); set25519(p[2], gf1); set25519(p[3], gf1); set25519(p[4], gf0)
  for i = 255, 0, -1 do
    local b = band(rshift(s[floor(i / 8)], i % 8), 1)
    cswap(p, q, b)
    add(q, p)
    add(p, p)
    cswap(p, q, b)
  end
end

local function scalarbase(p, s)
  local q = { gf(), gf(), gf(), gf() }
  set25519(q[1], X); set25519(q[2], Y); set25519(q[3], gf1)
  M(q[4], X, Y)
  scalarmult(p, q, s)
end

local function unpackneg(r, p)
  local t, chk, num, den, den2, den4, den6 = gf(), gf(), gf(), gf(), gf(), gf(), gf()
  set25519(r[3], gf1)
  unpack25519(r[2], p)
  S(num, r[2]); M(den, num, D); Z(num, num, r[3]); A(den, r[3], den)
  S(den2, den); S(den4, den2); M(den6, den4, den2)
  M(t, den6, num); M(t, t, den)
  pow2523(t, t)
  M(t, t, num); M(t, t, den); M(t, t, den); M(r[1], t, den)
  S(chk, r[1]); M(chk, chk, den)
  if neq25519(chk, num) then M(r[1], r[1], I) end
  S(chk, r[1]); M(chk, chk, den)
  if neq25519(chk, num) then return false end
  if par25519(r[1]) == rshift(p[31], 7) then Z(r[1], gf0, r[1]) end
  M(r[4], r[1], r[2])
  return true
end

local function modL(r, x)
  for i = 63, 32, -1 do
    local carry = 0
    local j = i - 32
    while j < i - 12 do
      x[j] = x[j] + carry - 16 * x[i] * Lc[j - (i - 32)]
      carry = floor((x[j] + 128) / 256)
      x[j] = x[j] - carry * 256
      j = j + 1
    end
    x[j] = x[j] + carry
    x[i] = 0
  end
  local carry = 0
  for j = 0, 31 do
    x[j] = x[j] + carry - floor(x[31] / 16) * Lc[j]
    carry = floor(x[j] / 256)
    x[j] = x[j] % 256
  end
  for j = 0, 31 do x[j] = x[j] - carry * Lc[j] end
  for i = 0, 31 do
    x[i + 1] = x[i + 1] + floor(x[i] / 256)
    r[i] = x[i] % 256
  end
end

local function reduce64(h1) -- h1: 1-indexed 64 bytes -> 0-indexed 32-byte scalar
  local x, r = {}, {}
  for i = 0, 63 do x[i] = h1[i + 1] end
  x[64] = 0
  modL(r, x)
  return r
end

-- Is S < L ? (rejects malleable signatures)
local function scalar_is_canonical(s0)
  for i = 31, 0, -1 do
    if s0[i] < Lc[i] then return true end
    if s0[i] > Lc[i] then return false end
  end
  return false
end

-- ed25519_verify(sig_hex(128), message(string), pubkey_hex(64)) -> boolean
local function ed25519_verify(sig_hex, msg, pk_hex)
  local sig, pk = hex_to_bytes0(sig_hex), hex_to_bytes0(pk_hex)
  if not sig or not pk or #sig ~= 63 or #pk ~= 31 then return false end
  local s = {}
  for i = 0, 31 do s[i] = sig[32 + i] end
  if not scalar_is_canonical(s) then return false end

  local q = { gf(), gf(), gf(), gf() }
  if not unpackneg(q, pk) then return false end

  local buf = {}
  for i = 0, 31 do buf[#buf + 1] = string.char(sig[i]) end
  for i = 0, 31 do buf[#buf + 1] = string.char(pk[i]) end
  local h = reduce64(sha512(table.concat(buf) .. msg))

  local p = { gf(), gf(), gf(), gf() }
  scalarmult(p, q, h)
  local q2 = { gf(), gf(), gf(), gf() }
  scalarbase(q2, s)
  add(p, q2)
  local t = {}
  pack(t, p)
  local diff = 0
  for i = 0, 31 do diff = bor(diff, bxor(sig[i], t[i])) end
  return diff == 0
end

local function sha512_hex(s) return bytes_to_hex(sha512(s)) end
-- ==== end crypto fragment ====
