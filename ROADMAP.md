# Roadmap

Each stage ends with a release on https://amirdaraee.github.io/isometric-tehran/. Dates are not promised; the
order is.

## 1. First draft (now)

- [x] The historic core (Grand Bazaar, Golestan Palace, Toopkhaneh, the old government quarter, Lalehzar) as an
      untextured 3D render from OSM, Overture and the Copernicus DEM.
- [x] Viewer from Isometric Luxembourg: deep zoom, pins, search, share, time of day, clouds, water glints.
- [x] English and Persian: bilingual labels, interface switch with right-to-left layout, search in both scripts.
- [x] 32 landmarks with cards from Wikidata and Wikipedia.
- [ ] Better building heights: map `height` and `building:levels` in OSM for the core (most buildings use the
      default today), and check the tallest defaults by eye.
- [ ] Courtyards and domes: historic houses, mosques and the bazaar's vaulted roofs, from OSM tags.

## 2. Paint pilot

- [ ] Guides for the painter from the render: line pass (edges and windows) and the kinds pass that exists.
- [ ] Paint one block (Golestan Palace and the Arg) in the inked style of Isometric Luxembourg, reusing its style
      model first, then decide whether Tehran needs its own style training (budget approved before any GPU time).
- [ ] Water mask and seams checked on the painted art.

## 3. The full core, painted

- [ ] The whole bbox painted by day, then golden hour and night versions.
- [ ] Landmark cards with credited photos (Wikimedia Commons) and short histories, in both languages.
- [ ] Tiles move out of git to release assets or object storage.

## 4. More districts

- [ ] North along Vali Asr: Sangelaj, Ferdowsi, Hasan Abad, Enghelab.
- [ ] The southern bazaar quarters and Oudlajan.
- [ ] Later: towards Tajrish and the Alborz.

## Later: living city and the calendar

- [ ] Events on the Persian (Solar Hijri) calendar: Nowruz (haft-sin and spring blossom), Chaharshanbe Suri,
      Sizdah Bedar, Yalda night, Muharram processions round the bazaar.
- [ ] Persian calendar dates in the interface when it is in Persian.
- [ ] Traffic, buses on the BRT lines and the metro, as live layers (the viewer engine has them in Isometric
      Luxembourg, switched off here).
- [ ] Seasons, snow on the Alborz, weather.
