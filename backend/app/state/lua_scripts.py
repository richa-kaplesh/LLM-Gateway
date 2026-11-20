PRELUDE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local SLOT = 600
local SLOTS = 144
local cur = math.floor(now / SLOT)
local h = {}
local raw = redis.call('HGETALL', KEYS[1])
for i = 1, #raw, 2 do h[raw[i]] = tonumber(raw[i + 1]) end
local dirty = {}
local dels = {}
local function put(k, v) h[k] = v; dirty[k] = true end
local function drop(k) h[k] = nil; dirty[k] = nil; dels[#dels + 1] = k end
local function refill(name, limit)
  local rate = limit / 60
  local tk = h['b:' .. name .. ':tokens'] or limit
  local ts = h['b:' .. name .. ':ts'] or now
  local el = now - ts
  if el < 0 then el = 0 end
  tk = math.min(limit, tk + el * rate)
  h['b:' .. name .. ':tokens'] = tk
  h['b:' .. name .. ':ts'] = now
  return tk, rate
end
local function persist()
  local args = {}
  for k, _ in pairs(dirty) do
    args[#args + 1] = k
    args[#args + 1] = tostring(h[k])
  end
  if #args > 0 then redis.call('HSET', KEYS[1], unpack(args)) end
  if #dels > 0 then redis.call('HDEL', KEYS[1], unpack(dels)) end
  redis.call('EXPIRE', KEYS[1], 172800)
end
"""

EVALUATE = PRELUDE + """
local reserve = ARGV[1] == 'reserve'
local frac = tonumber(ARGV[2])
local n = tonumber(ARGV[3])
local out = {}
local ok = true
local plans = {}
for i = 0, n - 1 do
  local name = ARGV[4 + i * 4]
  local kind = ARGV[5 + i * 4]
  local limit = tonumber(ARGV[6 + i * 4])
  local amount = tonumber(ARGV[7 + i * 4])
  local used, wait
  if kind == 'bucket' then
    local tk, rate = refill(name, limit)
    used = limit - tk
    if amount > limit then wait = -1 else wait = math.max(0, (amount - tk) / rate) end
  else
    local prefix = 'd:' .. name .. ':'
    local live = {}
    local expired = {}
    used = 0
    for k, v in pairs(h) do
      if string.sub(k, 1, #prefix) == prefix then
        local s = tonumber(string.sub(k, #prefix + 1))
        if s <= cur - SLOTS then
          expired[#expired + 1] = k
        else
          live[#live + 1] = {s, v}
          used = used + v
        end
      end
    end
    for _, k in ipairs(expired) do drop(k) end
    table.sort(live, function(a, b) return a[1] < b[1] end)
    local eff = limit * frac
    if amount > eff then
      wait = -1
    else
      local excess = used + amount - eff
      if excess <= 0 then
        wait = 0
      else
        wait = -1
        local freed = 0
        for _, e in ipairs(live) do
          freed = freed + e[2]
          if freed >= excess then
            wait = math.max(0, (e[1] + SLOTS) * SLOT - now)
            break
          end
        end
      end
    end
  end
  local remaining = limit - used
  local rk = 'o:' .. name .. ':rem'
  local sk = 'o:' .. name .. ':reset'
  if h[rk] and h[sk] then
    if now >= h[sk] then
      drop(rk)
      drop(sk)
    else
      if h[rk] < remaining then remaining = h[rk] end
      if h[rk] < amount and wait >= 0 then wait = math.max(wait, h[sk] - now) end
    end
  end
  if remaining < 0 then remaining = 0 end
  if wait ~= 0 then ok = false end
  plans[#plans + 1] = {name, kind, amount}
  out[#out + 1] = name
  out[#out + 1] = tostring(limit)
  out[#out + 1] = tostring(used)
  out[#out + 1] = tostring(remaining)
  out[#out + 1] = tostring(wait)
end
if reserve and ok then
  for _, p in ipairs(plans) do
    if p[2] == 'bucket' then
      put('b:' .. p[1] .. ':tokens', h['b:' .. p[1] .. ':tokens'] - p[3])
      put('b:' .. p[1] .. ':ts', now)
    else
      local f = 'd:' .. p[1] .. ':' .. cur
      put(f, (h[f] or 0) + p[3])
    end
    local rk = 'o:' .. p[1] .. ':rem'
    if h[rk] then put(rk, h[rk] - p[3]) end
  end
end
persist()
return {ok and 1 or 0, cur, unpack(out)}
"""

RELEASE = PRELUDE + """
local slot = ARGV[1]
local n = tonumber(ARGV[2])
for i = 0, n - 1 do
  local name = ARGV[3 + i * 4]
  local kind = ARGV[4 + i * 4]
  local limit = tonumber(ARGV[5 + i * 4])
  local amount = tonumber(ARGV[6 + i * 4])
  if kind == 'bucket' then
    local tk = refill(name, limit)
    put('b:' .. name .. ':tokens', math.min(limit, tk + amount))
    put('b:' .. name .. ':ts', now)
  else
    local f = 'd:' .. name .. ':' .. slot
    if h[f] then
      local v = h[f] - amount
      if v <= 0 then drop(f) else put(f, v) end
    end
  end
  local rk = 'o:' .. name .. ':rem'
  local sk = 'o:' .. name .. ':reset'
  if h[rk] and h[sk] and now < h[sk] then put(rk, h[rk] + amount) end
end
persist()
return 1
"""

OVERRIDE = PRELUDE + """
put('o:' .. ARGV[1] .. ':rem', tonumber(ARGV[2]))
put('o:' .. ARGV[1] .. ':reset', now + tonumber(ARGV[3]))
persist()
return 1
"""

DRAIN = PRELUDE + """
for i = 1, #ARGV do
  put('b:' .. ARGV[i] .. ':tokens', 0)
  put('b:' .. ARGV[i] .. ':ts', now)
end
persist()
return 1
"""
