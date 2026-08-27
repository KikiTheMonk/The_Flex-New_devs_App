# Revenue Dashboard — Bug Report

Four bugs found and fixed in the revenue dashboard. Details per bug below.

## At a glance

| Issue | Why it broke | Fix | Why the fix works | Files affected |
|---|---|---|---|---|
| 1. Revenue numbers were fake | DB connection never succeeded, so a silent fallback served hardcoded mock data instead | Repaired `database_pool.py` (real settings, async-compatible pool, sync `get_session`) | Connection now succeeds, so the fallback path never triggers and the real query runs | `backend/app/core/database_pool.py` |
| 2. Client B saw Client A's revenue | Redis cache key used `property_id` only, and two tenants share a `prop-001` | Added `tenant_id` to the cache key in `cache.py` | Each tenant now gets their own cache entry, so one tenant's result can never be read back as another's | `backend/app/services/cache.py` |
| 3. Totals off by a cent | Frontend rounded a sub-cent float with `Math.round(x*100)/100`, which misrounds due to floating-point drift | Round to cents server-side with `Decimal.quantize(..., ROUND_HALF_UP)` before sending the value | Exact decimal math replaces imprecise float math, done once upstream, so there's nothing left for the frontend to get wrong | `backend/app/api/v1/dashboard.py` |
| 4. Property dropdown showed the wrong tenant's properties | Dashboard used one hardcoded, tenant-agnostic list of all 5 properties instead of asking the backend | Added a tenant-scoped `/dashboard/properties` endpoint and pointed the dropdown at it | Each tenant now only ever gets their own properties back, so the other tenant's names can't appear | `backend/app/services/reservations.py`, `backend/app/api/v1/dashboard.py`, `frontend/src/lib/secureApi.ts`, `frontend/src/components/Dashboard.tsx` |

## Correct data (from the database seed)

| Client (tenant) | Property ID | Property name | Revenue | Bookings |
|---|---|---|---|---|
| Sunset Properties | `prop-001` | Beach House Alpha | $2,250.00 | 4 |
| Sunset Properties | `prop-002` | City Apartment Downtown | $4,975.50 | 4 |
| Sunset Properties | `prop-003` | Country Villa Estate | $6,100.50 | 2 |
| Ocean Rentals | `prop-001` | Mountain Lodge Beta | $0.00 | 0 |
| Ocean Rentals | `prop-004` | Lakeside Cottage | $1,776.50 | 4 |
| Ocean Rentals | `prop-005` | Urban Loft Modern | $3,256.00 | 3 |

Note both tenants have a `prop-001` — that ID collision is what caused
Bug 2 below.

---

## 1. Revenue numbers were fake

**Found by:** Client A's dashboard total ($1,000 / 3 bookings) didn't match
their real reservations ($2,250 / 4 bookings). Tracing the request path
showed `reservations.py` silently falls back to hardcoded mock data
whenever the DB call fails — and the mock value matched exactly what was
on screen. So the DB connection was failing every time.

**Root cause:** Three separate bugs in `database_pool.py`, each enough on
its own to break the connection:
- Built the connection string from settings that don't exist
  (`supabase_db_user`, etc.) — reading a setting that was never defined
  throws immediately, so the connection string was never even built.
- Used a sync-only pool class (`QueuePool`) on an async engine —
  `QueuePool` is built for SQLAlchemy's old synchronous engines;
  the async engine here needs its own async-compatible pooling, so
  passing `QueuePool` in is rejected as incompatible.
- `get_session()` was marked `async def` but didn't need to be — it just
  returns a ready-to-use session object, not something to `await`.
  Marking it `async` meant callers got a coroutine instead of a session,
  which breaks the moment the code tries to use it as one.

**Fix:** `backend/app/core/database_pool.py` — use the real
`settings.database_url`, drop the incompatible pool class, make
`get_session` a plain `def`.

**Proof it works:**
| | Before | After |
|---|---|---|
| API response | `$1,000.00`, 3 bookings (fake) | `$2,250.00`, 4 bookings (real) |

---

## 2. Client B saw Client A's revenue

**Found by:** Ocean Rentals (tenant B) occasionally saw revenue numbers
that belonged to Sunset Properties (tenant A). Both tenants happen to have
a property with the same ID, `prop-001`.

**Root cause:** The DB query itself was correctly scoped by
`tenant_id` — but the Redis cache key wasn't:
```python
cache_key = f"revenue:{property_id}"   # tenant_id ignored
```
- Property IDs aren't globally unique across tenants — each tenant has
  their own `prop-001`, `prop-002`, etc. Building the cache key from
  `property_id` alone treats those as the same entry.
- `tenant_id` was already being passed into the function, it just never
  made it into the key — so Redis had no way to tell "tenant A's
  prop-001" and "tenant B's prop-001" apart.
- Whichever tenant's request hit the (empty) cache first got their
  result stored under that shared key, and the cache doesn't know or
  care who asked — the next request for the same key, from anyone,
  gets served the same cached value for up to 5 minutes.

**Fix:** `backend/app/services/cache.py` — include `tenant_id` in the key:
```python
cache_key = f"revenue:{tenant_id}:{property_id}"
```

**Proof it works:**
| | Before | After |
|---|---|---|
| Client A requests `prop-001` | $2,250.00 | $2,250.00 |
| Client B requests `prop-001` right after | $2,250.00 ← leaked | $0.00 ← their own correct number |

---

## 3. Totals off by a cent

**Found by:** Finance flagged some totals as "slightly off." The DB stores
revenue to 3 decimal places on purpose (e.g. `2250.055`), but the
frontend rounded it to cents using JS floats:
```js
Math.round(data.total_revenue * 100) / 100
```

**Root cause:**
- The DB deliberately stores 3 decimal places (`NUMERIC(10,3)`), so a
  real total can be something like `2250.055`, not a clean 2-decimal
  dollar amount.
- `total_revenue` arrives in JavaScript as a regular floating-point
  number. Floats can't represent every decimal value exactly, so
  `2250.055 * 100` doesn't always come out to exactly `225005.5` — it
  lands very slightly above or below depending on the value.
- `Math.round` just rounds whatever number it's given — it has no idea
  the input already drifted off by a tiny fraction, so it sometimes
  rounds down when the true value should round up.
- This only misfires for specific sub-cent values (about 1 in 20), which
  is exactly why it looked random to finance instead of consistently
  broken.

**Fix:** `backend/app/api/v1/dashboard.py` — round once, server-side,
with exact decimal math before the number ever reaches the frontend:
```python
Decimal(total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
```

**Proof it works:**
| | Before | After |
|---|---|---|
| Test booking of $0.055 | API: `2250.055` → displayed `$2250.05` (wrong) | API: `2250.06` (correct) |
| Existing properties (prop-001–003) | unchanged | unchanged — no regression |

---

## 4. Property dropdown showed the wrong tenant's properties

**Found by:** Logged in as Ocean Rentals, "Mountain Lodge Beta" never
appeared in the property selector — instead the dropdown showed "Beach
House Alpha," a Sunset Properties property.

**Root cause:**
- `Dashboard.tsx` never asked the backend for the tenant's properties —
  it rendered from a hardcoded array of all 5 properties across both
  tenants, shown identically to every logged-in user regardless of
  tenant:
  ```js
  const PROPERTIES = [
    { id: 'prop-001', name: 'Beach House Alpha' }, // ...
  ];
  ```
- Because both tenants have a `prop-001`, the hardcoded list could only
  hold one name for that ID — it picked Sunset's ("Beach House Alpha"),
  so Ocean's "Mountain Lodge Beta" had no way to ever show up.
- Revenue itself was never at risk here — the `/dashboard/summary` query
  is already scoped by `tenant_id` (see Bug 2), so picking a property
  that isn't really yours just returns $0.00 / 0 bookings, not the other
  tenant's real numbers. This was a property-name leak, not a revenue
  leak.

**Fix:**
- `backend/app/services/reservations.py` — added
  `list_properties_for_tenant(tenant_id)`, querying
  `properties WHERE tenant_id = :tenant_id`.
- `backend/app/api/v1/dashboard.py` — added `GET /dashboard/properties`,
  scoped to the authenticated user's `tenant_id`.
- `frontend/src/lib/secureApi.ts` — added `getDashboardProperties()`.
- `frontend/src/components/Dashboard.tsx` — dropdown now fetches from
  that endpoint on mount instead of the hardcoded array.