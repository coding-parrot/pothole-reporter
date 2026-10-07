// A location's town, road class and street do not change between requests, but every
// lookup asked the state GIS (which stalls for 20 s at a time) and a public geocoder
// (which allows one request a second) again, and the only memory was five minutes inside
// one function instance. Live answers are kept here for a week, keyed by the same 11 m
// cell, in the control table that already expires rows by expires_at.
//
// Only an answer both upstreams gave live is stored. A caller's own address hint is that
// caller's claim; the local snapshot cannot tell a state highway from a street; and an
// outage must be asked again, not remembered.

// The version is part of every key. Raise it whenever the stored answer gains a field
// the service reads, or cells stored under the old shape keep answering without it for
// up to a week. v2: the ward (ward_name, ward_no, ward_code, lookup.ward) and
// address_parts.localities, which ward tender matching reads.
const VERSION = "v2";
const WEEK_MS = 7 * 86_400_000;
const MEMORY_CELLS = 5_000;

function storable(value) {
  if (!value || !value.road_ownership || value.road_ownership === "unknown") return false;
  if (value.address_source !== "operator_geocoder" || !value.address) return false;
  if (value.source === "kgis_snapshot") return false;
  return ["available", "out_of_scope"].includes(value.lookup?.kgis);
}

export function createCachedGeolocator({ geolocator, repository, ttlMs = WEEK_MS, now = Date.now } = {}) {
  if (!geolocator || !repository) throw new Error("A geolocator and a repository are required.");
  const cellOf = (lat, lng) => `GEO#${VERSION}#${lat.toFixed(4)},${lng.toFixed(4)}`;
  // The store is a table read of about 8 ms. A function instance that has already
  // answered for a cell keeps that answer in memory and reads nothing.
  const memory = new Map();
  const remember = (cell, value, expiresAt) => {
    memory.set(cell, { value, expiresAt });
    while (memory.size > MEMORY_CELLS) memory.delete(memory.keys().next().value);
  };
  return {
    kgisTimeoutMs: geolocator.kgisTimeoutMs,
    // The ward roster is read from the packaged polygons, not from an upstream: nothing
    // to cache, but core.mjs asks its geolocator for it and must find it here.
    wardRoster: (wardCode) => (typeof geolocator.wardRoster === "function"
      ? geolocator.wardRoster(wardCode) : Promise.resolve([])),
    async resolve(input) {
      const { lat, lng } = input;
      const cell = Number.isFinite(lat) && Number.isFinite(lng) ? cellOf(lat, lng) : null;
      if (cell) {
        const held = memory.get(cell);
        // The store is an optimisation. If it is throttled or down, ask upstream as before.
        const stored = held && held.expiresAt > now() ? held
          : await repository.getGeoCell(cell).catch(() => null);
        if (stored && stored.expiresAt > now() && storable(stored.value)) {
          if (stored !== held) remember(cell, stored.value, stored.expiresAt);
          return { ...stored.value, lat, lng, lookup: { ...stored.value.lookup, cache: "hit" } };
        }
      }
      const value = await geolocator.resolve(input);
      if (cell && storable(value)) {
        remember(cell, value, now() + ttlMs);
        await repository.putGeoCell(cell, value, now() + ttlMs).catch(() => {});
      }
      return value && typeof value === "object"
        ? { ...value, lookup: { ...(value.lookup || {}), cache: "miss" } } : value;
    },
  };
}
