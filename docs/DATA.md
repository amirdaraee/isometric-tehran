# Data sources

Everything is downloaded for the bounding box in [`city.json`](../city.json) only (west 51.405, south 35.668,
east 51.440, north 35.700), by [`pipeline/fetch_data.py`](../pipeline/fetch_data.py), into `data/raw/` (not in
git). Each run writes what it fetched, from where, when and under which licence to `data/raw/SOURCES.json`.

    cd pipeline && .venv/bin/python fetch_data.py all

## First-draft download (2026-11-08)

| Data | Source | As of | Size | Licence |
|---|---|---|---|---|
| OpenStreetMap: buildings, roads, rail and metro, water, land use, parks, POIs with `name`, `name:en`, `name:fa` (6,594 elements) | Overpass API, `overpass-api.de` | OSM data timestamp 2026-11-08T16:25:57Z | 3.7 MB | ODbL 1.0, © OpenStreetMap contributors |
| Overture buildings (11,952 footprints: 10,947 Microsoft ML Buildings, 1,005 OpenStreetMap; 37 with a height) | `overturemaps` CLI, public S3 GeoParquet | release 2026-10-21.0 | 1.8 MB | ODbL 1.0 |
| Copernicus DEM GLO-30, a 198 × 187 window (bbox plus 0.01°) of tile `N35_00_E051_00` | `copernicus-dem-30m` public AWS bucket, read with HTTP range requests | TanDEM-X acquisitions 2011-2015 | 0.1 MB | Copernicus DEM licence: free, worldwide, any lawful use, with the attribution "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved" |
| Wikidata items with coordinates in the bbox (534) | Wikidata Query Service (SPARQL, `wikibase:box`) | 2026-11-08 | 0.2 MB | CC0 1.0 |
| Wikipedia summaries of the 32 landmarks (56 pages, English and Persian) | Wikipedia REST API `page/summary` | 2026-11-08 | 0.04 MB | CC BY-SA 4.0 |

## What the map uses, and how

- **Buildings:** the Overture footprints. For the ones Overture took from OpenStreetMap, the OSM tags are joined
  in by way id. Heights: OSM `height`, else `building:levels`, else Overture `height` / `num_floors`, else a
  default by building type and footprint area (`pipeline/heights.py`). In this draft only 64 of 11,954 buildings
  have a height from the data; the rest use the default. Better OSM heights in central Tehran improve the map.
- **Terrain:** GLO-30 is a surface model (it includes roofs and trees), so it is smoothed into bare ground
  (`build_scene.bare_ground`). The core drops about 70 m from north to south.
- **Metro:** lines in the core run in tunnels, so they are not drawn; stations and entrances are in OSM.
- **Landmarks:** the list is [`pipeline/landmarks.json`](../pipeline/landmarks.json) (Wikidata ids). Each card
  shows the Wikidata labels and description and the start of the Wikipedia article, with links and licences.
  No facts are added by hand.

## Derived files in git

`web/public/data/` (places, landmark cards, street lines, water and cloud masks) and `web/public/tiles/` (the
rendered map) are made from the data above and carry its attribution in the README and the viewer's About panel.
Pins, street lines and footprints derived from OSM and Overture remain under ODbL; the rendered tiles are a
produced work, credited the same way.
