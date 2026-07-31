# Worked example: rainfall over the flooded areas around Coimbra

A real question, the tool calls it triggers, and the real answer.

Every number on this page was produced by running the calls against
`https://api.cheias.pt` on **2026-07-31**. Nothing here is illustrative or
rounded from imagination. You can reproduce all of it with the `curl` commands
in the appendix.

---

## The question

> How much rain fell over the areas that actually flooded around Coimbra?

This is harder than it looks. The rainfall lives in a raster (ERA5 reanalysis,
one value per grid cell). The flooding lives in a vector layer (Copernicus EMS
delineations, thousands of irregular polygons). Answering means clipping one to
the other, and the naive version of that gets the wrong answer silently.

---

## Step 1 — find the flood polygons

Coimbra sits at roughly 40.21° N, 8.42° W. Ask for EMS delineations in a box
around it, largest first.

```
get_vector_features(
    collection_id = "public.flood_extent_emsr864",
    bbox          = [-8.55, 40.10, -8.30, 40.30],
    sortby        = "-area_ha",
    simplify      = 0.0001,
    limit         = 3,
)
```

Real response, trimmed to the parts that matter:

```json
{
  "numberMatched": 87,
  "numberReturned": 3,
  "features": [
    {
      "properties": {
        "ogc_fid": 3579,
        "area_ha": 3918.251701109906,
        "activation": "EMSR864",
        "aoi": "AOI05",
        "datetime": "2026-02-08T00:00:00+00:00",
        "event_type": "5-Flood",
        "storm": "Leonardo/Marta"
      }
    }
  ]
}
```

**87 flood polygons** in that box, totalling **5,062.12 ha**. The largest is
`ogc_fid=3579` at **3,918.25 ha** — the Mondego floodplain west of the city.
All of them are dated 2026-02-08 and attributed to storm Leonardo/Marta.

### Why `simplify=0.0001` is here

`ogc_fid=3579` has **31,437 vertices** in its native form. Requests above roughly
1 MB are rejected by the backend's edge — as a `413` or a `403` depending on the
network path, and as an HTML page rather than JSON either way. Measured on my
path, the boundary is 1 MiB exactly:

| Body size | Result |
|---|---|
| 921,609 bytes | `200` |
| 1,024,009 bytes | `200` |
| 1,048,585 bytes | `413 Request Entity Too Large` |

Simplifying server-side in tipg cuts this geometry to 4,706 vertices at
`0.0001`. Stripping alone does not always save you: the largest polygon in this
collection, `ogc_fid=111` at 10,437 ha, is still 1,817,111 bytes with `id` and
`links` removed. Big areas need `simplify`.

### Use `0.0001`, and do not round it up

**Past about `0.0005`, tipg stops coarsening geometries and starts returning
them as `null`.** HTTP 200, properties intact, geometry gone. Measured across 500
features of this collection:

| `simplify` | Features with null geometry |
|---|---|
| none | 0 of 500 |
| `0.0001` | 0 of 500 |
| `0.0005` | 143 of 500 (29%) |
| `0.001` | 305 of 500 (61%) |
| `0.01` | 481 of 500 (96%) |

`0.001` is the obvious round number to reach for and it destroys **61%** of the
collection without saying so. `get_vector_features` counts these and refuses to
let it pass quietly:

```
null_geometry_count: 51
null_geometry_note: "51 of 87 features came back with null geometry —
  simplify=0.001 is too coarse for this collection. Try 0.0001, which loses
  none. Those features cannot be used as an area of interest…"
```

**Check `null_geometry_count` whenever you pass `simplify`.**

**The precision cost of `0.0001` is negligible.** `precipitation-storm-total` is
a 175×265 grid over `[-9.6, 36.9, -6.1, 42.2]` — cells of exactly **0.02°**,
about 2.22 km north–south. A tolerance of `0.0001` is roughly 11 m, or **1/200th
of a single pixel**. You are moving the boundary by far less than the resolution
of the thing you are measuring, so there is no reason to reach for a coarser
value in the first place.

It still shifts the answer very slightly, because the boundary crosses cells
differently — see
[the tolerance caveat](#simplification-changes-the-answer-slightly) below. Note
that `area_ha` is a *stored column*, not computed from the returned geometry, so
it survives even a null geometry and cannot be used to measure the error — or to
notice that geometries went missing.

---

## Step 2 — clip the rainfall raster to each polygon

```
get_zonal_statistics(
    collection_id = "precipitation-daily",
    item_id       = "precipitation-storm-total",
    geojson       = <a Feature from step 1>,
)
```

`precipitation-storm-total` is *total accumulated precipitation for the entire
storm period*, in millimetres, from ERA5. A bare Polygon or MultiPolygon is
wrapped into a Feature for you; a FeatureCollection is passed through whole and
comes back with per-feature statistics.

### The trap the tools remove for you

Feeding a Feature straight from a vector query into a statistics call is the
obvious thing to do, and doing it against the **raw tipg API** fails every time on
a polygon of any size:

```
HTTP 413 Request Entity Too Large
```

tipg sets each GeoJSON Feature's `id` to the **hex-encoded WKB of its geometry**,
then repeats that hex string inside a `links` URL. Measured on the
already-simplified `ogc_fid=3579` as tipg returns it:

| Field | Bytes | Share |
|---|---|---|
| `geometry` | 93,578 | 4.4% |
| **`id`** | **1,010,476** | **47.8%** |
| **`links`** | **1,010,781** | **47.8%** |
| `properties` | 181 | — |
| **whole raw Feature** | **2,115,084** | → `413` |

So 95.6% of the payload is a redundant copy of the geometry in a format TiTiler
has no use for — and `simplify` does not rescue you, because it shrinks the 4.4%
and leaves the rest untouched.

`get_vector_features` strips `id` and `links`, so the Feature it hands you is
**93,808 bytes** and the chain above just works. If you are driving the raw API
with `curl`, reshape to `{type, geometry, properties}` yourself — the appendix
does exactly that.

The same tipg quirk is why `get_vector_feature` routes through a CQL2 filter
rather than `items/{id}` — see
[`tipg-api-notes.md`](../servers/eo-mcp/docs/tipg-api-notes.md) §3.

Real statistics for the largest polygon (`ogc_fid=3579`):

```json
{
  "min": 302.087890625,
  "max": 370.19873046875,
  "mean": 339.6684506734212,
  "median": 340.9502258300781,
  "std": 19.97446240489846,
  "count": 24.0,
  "sum": 8152.042816162109,
  "valid_pixels": 24.0,
  "masked_pixels": 20.0,
  "valid_percent": 54.55,
  "percentile_2": 302.087890625,
  "percentile_98": 370.19873046875
}
```

> **`sum` is not a rainfall total.** It is the arithmetic sum of pixel values —
> here, 24 depth readings in millimetres added together. It has no physical
> meaning: `sum / count` is just `mean` again (8152.04 / 24 = 339.67). Reporting
> "total rainfall was 8,152 mm" would be confident nonsense.
>
> There is no `total` key in this response, and none of the tools advertise one.
> A genuine catchment total needs pixel area × depth, and this server computes no
> geometry by design, so it cannot produce one. **Report the mean, with the
> polygon area beside it.**

Across the three largest polygons:

| `ogc_fid` | Area (ha) | Mean (mm) | Min | Max | Valid pixels | Valid % |
|---|---|---|---|---|---|---|
| 3579 | 3,918.25 | **339.67** | 302.09 | 370.20 | 24 | 54.55 |
| 3691 | 547.81 | **356.61** | 340.95 | 369.75 | 8 | 80.00 |
| 3773 | 212.42 | **356.92** | 348.07 | 364.72 | 5 | 83.33 |

---

## The answer

**The flooded areas around Coimbra received roughly 340–357 mm of rain over the
storm period — about 20–28% more than the 279.47 mm national mean for the same
raster.**

The largest polygon, the 3,918 ha Mondego floodplain, averaged 339.67 mm with a
range of 302–370 mm. The two smaller polygons upstream both averaged around
357 mm, which is consistent with heavier accumulation in the higher ground
feeding the river.

All flooding is dated 2026-02-08, activation EMSR864, storm Leonardo/Marta.

---

## The mistake this example exists to prevent

There is a STAC collection called `flood-extent-emsr864`, and it has an item
that looks like exactly what you want:

```
GET /collections/flood-extent-emsr864/items/flood-extent-emsr864-2026-02-08
```

**Do not use its geometry as your area of interest.** It is a placeholder:

```json
{
  "assets": {},
  "properties": { "datetime": "2026-02-08T00:00:00Z" },
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[-9.5,37],[-6,37],[-6,42.2],[-9.5,42.2],[-9.5,37]]]
  }
}
```

That rectangle is all of mainland Portugal — roughly **171,700 km²**. The area
that actually flooded is **892 km²** (89,199.48 ha across 5,496 polygons). Using
it as an AOI computes over an area **~193x too large**.

Run it and you get:

```
mean = 279.47 mm   over 23,128 pixels
```

That number is real, precise, returned with HTTP 200, and **completely wrong**
as an answer to the question. It is just the national mean — the flood polygons
never entered the computation. Nothing errors. Nothing warns you.

**The flood geometry always comes from tipg** (`public.flood_extent_emsr864` via
`get_vector_features`), never from the STAC item. The two flood-extent STAC
collections exist to record that the event happened and to point at the vector
service; they carry no raster assets and no usable footprint.

---

## Caveats

### Simplification changes the answer slightly

For `ogc_fid=3579`, which is large enough to survive both tolerances:

| Tolerance | Mean (mm) | Valid pixels |
|---|---|---|
| `0.0001` | 339.6684506734212 | 24 |
| `0.001` | 338.2572912597656 | 25 |

A 1.4 mm spread (~0.4%). But note this comparison is only possible *because* this
particular polygon is big enough to survive `0.001` — 61% of the collection is
not, and comes back with null geometry instead. **The precision difference is the
smaller problem; the missing geometries are the bigger one.** Use `0.0001` and
check `null_geometry_count`.

If you need an exact clip, don't simplify at all — split the AOI into pieces
small enough to stay under the body cap and combine them. Combining means an
**area-weighted mean**, not a sum of means: weight each piece's mean by its valid
pixel count. Adding means together, like adding `sum` values, produces a number
with no meaning.

### The raster is coarse

`precipitation-storm-total` is 175 × 265 pixels covering
`[-9.6, 36.9, -6.1, 42.2]` — cells of roughly 2 km. A 3,918 ha polygon contains
only **24 valid pixels**, and `valid_percent` is 54.55, meaning nearly half the
cells touching the polygon are masked. Treat the mean as an estimate over a
handful of cells, not a precise catchment total. The smaller polygons have
5 and 8 pixels respectively — quote those with real caution.

### `numberMatched` is a count, not a promise of delivery

Step 1 reports `numberMatched: 87` but `numberReturned: 3`, because `limit=3`.
The response also sets `truncated: true` and carries a `note` spelling it out:

> Showing 1 of 87 matching features (limit=1, offset=0). This is a page, not the
> whole result — do not describe it as the complete set. Raise limit, page with
> offset, or narrow with bbox/filter.

Raise `limit` or page with `offset` to see the rest.

---

## Appendix — reproduce it

```bash
V="https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/items"
R="https://api.cheias.pt/raster/collections/precipitation-daily/items/precipitation-storm-total/statistics?assets=data"

# Step 1 — flood polygons near Coimbra, largest first
curl -s "$V?f=geojson&bbox=-8.55,40.10,-8.30,40.30&limit=3&sortby=-area_ha&simplify=0.0001" \
  > step1.json
jq '{numberMatched, numberReturned}' step1.json
jq '.features[0].properties' step1.json

# Total flooded area in the box.
# NOTE: simplify=0.0001, not 0.001 — at 0.001 tipg returns null geometry for 51
# of these 87 features. area_ha is a stored column so the sum is unaffected,
# which is exactly what makes the coarser tolerance dangerous: the total looks
# right while the shapes are gone.
curl -s "$V?f=geojson&bbox=-8.55,40.10,-8.30,40.30&limit=200&simplify=0.0001" \
  | jq '[.features[].properties.area_ha] | add'

# Step 2 — zonal statistics over the largest polygon
jq '.features[0] | {type:"Feature", geometry, properties:{}}' step1.json > aoi.json
curl -s -X POST "$R" -H "Content-Type: application/json" -d @aoi.json \
  | jq '.properties.statistics.data_b1'

# The national baseline, for comparison
curl -s "$R" | jq '.data_b1 | {min, max, mean}'
```

The `curl` route and the MCP tools hit the same endpoints — the tools add
parameter validation, the `public.` prefix normalisation, and the CQL2 filter
that works around tipg's broken `items/{id}` route. See
[`servers/eo-mcp/docs/tipg-api-notes.md`](../servers/eo-mcp/docs/tipg-api-notes.md)
for the full list of divergences between the live API and its documentation.
