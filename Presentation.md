# Presentation mgis-layers

Presentationen finns i [`presentation/`](presentation/README.md): bildspelet i
[`slides.md`](presentation/slides.md) med talarmanus, och bilderna görs av
[`make_figures.py`](presentation/make_figures.py).

## Ram

- **Längd:** 15 minuter, 13 bilder plus frågor. Ungefär en minut per bild, två på
  terrängprofilen.
- **Publik:** utvecklare utan GIS-vana. Begrepp som AOI, raster och projektion förklaras med
  bilder, inte formler.
- **Exempelområde:** Skå-Edeby ([`examples/aoi_ska_edeby.geojson`](examples/aoi_ska_edeby.geojson)),
  cirka 2,8 × 2,8 km. Alla kartor utom pistkartan visar samma område, så att publiken känner igen
  platsen från bild till bild.

### Tre saker publiken ska ta med sig

1. Ett anrop (`gis-layers fetch`) ger alla lager för ett svenskt område i en katalog, och sedan
   går allt att läsa offline.
2. Alla mått räknas i SWEREF99 TM i meter. Felen som projektionen ger är små och kända. Det som
   biblioteket inte hanterar är också känt.
3. Lagren går att fråga punktvis, längs en linje (terrängprofil) och som karta (hillshade).

## Disposition

| # | Min | Bild | Innehåll | Visuellt |
| --- | --- | --- | --- | --- |
| 1 | 0,5 | Titel | mgis-layers: kartlager och terrängprofiler för ett svenskt område | Hillshade över Skå-Edeby med vatten och vägar |
| 2 | 1 | Vad finns på marken här? | Frågorna man vill kunna svara på, och de fyra källorna med olika format, inloggning och koordinatsystem | En ruta per källa |
| 3 | 1 | Begrepp | AOI: en ruta i lat/lon ur en GeoJSON-fil. Raster jämfört med vektor | AOI-rutan på hillshaden; samma 160 × 160 m som trädhöjdsraster och som OSM-vektorer med hörnpunkter |
| 4 | 1,5 | Jorden är inte platt | SWEREF99 TM: cylindern längs 15° Ö, skalan 0,9996, felet växer utåt | Cylinder-skiss och Sverigekarta färgad efter längdfel, med fyra orter |
| 5 | 0,75 | Vad det betyder i ett AOI | Rutan blir vriden; hillshaden täcker alla fyra hörnen; rak linje mot kortaste vägen | AOI-rutan vid Skå-Edeby och Haparanda med sant norr och rutnätsnorr |
| 6 | 0,75 | Mät aldrig på webbkartan | Web Mercator förstorar 1,96 gånger vid Skå-Edeby | Samma 60 km-cirklar i Web Mercator och SWEREF99 TM |
| 7 | 1 | Hanterar och hanterar inte | Se [Hanterar och hanterar inte](#hanterar-och-hanterar-inte) | Två spalter och jordkrökningen som kurva |
| 8 | 1,5 | Översikt | Källorna, `fetch`, AOI-katalogen och lagrens frågor; profil, hillshade och klassning ovanpå | Flödesschema och `fetch`-kommandot |
| 9 | 1,5 | Höjdmodellen | Öppen 30 m jämfört med Lantmäteriet 1 m | Två hillshades sida vid sida |
| 10 | 1,5 | De andra lagren | Ett kartfönster per lager med frågan bredvid | Rutnät med 2 × 3 kartor |
| 11 | 1 | Allt tillsammans | Lagren ovanpå hillshaden med samma klassning som frågorna | Sammanslagen karta med profillinjen A–B |
| 12 | 2 | Terrängprofil | Kodexemplet och dess utskrift | Profilen A–B: 1 m och 30 m, trädhöjd, vägar och markanvändning |
| 13 | 1 | Gränser | Bara Sverige, AOI:n är en rektangel, OSM är så bra som taggningen, trädhöjdens ålder. Licenser | Punktlistor |
| | | Frågor | Installation och repot | |

Summa: 15 minuter.

## Projektionsfakta

Beräknade med pyproj för EPSG:3006 och GRS80. Geoidhöjden är Lantmäteriets SWEN17_RH2000 via
PROJ.

| Ort | Skalfaktor | Längdfel | Meridiankonvergens | Geoidhöjd |
| --- | --- | --- | --- | --- |
| Skå-Edeby | 0,99990 | −0,10 m/km | 2,4° | 23,6 m |
| Strömstad | 1,00020 | +0,20 m/km | −3,3° | 36,9 m |
| Kiruna | 1,00019 | +0,19 m/km | 4,9° | 29,8 m |
| Haparanda | 1,00173 | +1,73 m/km | 8,4° | 19,7 m |

- **Rak linje jämfört med geodet,** 10 km mot norr: högst 5 cm isär vid Skå-Edeby, 13 cm vid
  Haparanda. Mot öster under 2 cm.
- **Web Mercator vid 59,3° N:** skalan är 1/cos(lat) ≈ 1,96. Avstånd ser dubbelt så långa ut,
  ytor nästan fyra gånger så stora.
- **Jordkrökningen** (d²/2R): 8 cm på 1 km, 2 m på 5 km, 8 m på 10 km, 31 m på 20 km.
- **Höjdsystem:** Lantmäteriet RH 2000, EU-DEM EVRS2000 med angiven noggrannhet ±7 m. Längs
  profilen ligger EU-DEM 3–4 m över Lantmäteriet på de flacka åkrarna.

## Hanterar och hanterar inte

Hanterar:

- Alla avstånd och provpunkter räknas i SWEREF99 TM, så stegen i en profil är lika långa på
  marken oavsett latitud.
- In och ut i WGS84. Projektionen syns inte för den som anropar.
- Hillshaden görs på ett rutnät i SWEREF99 TM som täcker AOI-rutans alla fyra hörn, och hörnen
  ges tillbaka i WGS84 i den ordning som MapLibre vill ha.

Hanterar inte, med storleken på felet:

- **Skalfaktorn:** längder är rutnätsmeter, −0,1 m/km vid Skå-Edeby och +1,7 m/km vid Haparanda.
- **Geodeten:** profilen är en rak linje i planet. Det ger några centimeter över 10 km.
- **Jordkrökningen:** profilens höjder följer havsytan. Vill man räkna fri sikt längs profilen får
  man själv lägga till krökningen, och vid 5 km är den redan 2 m.
- **Höjdsystem:** höjderna är över havet. En GNSS-mottagare ger höjd över ellipsoiden, 23,6 m
  högre vid Skå-Edeby och 20–37 m i Sverige. Biblioteket räknar inte om mellan dem.
- **Utanför Sverige:** SWEREF99 TM gäller bara Sverige.

## Bilderna

- **Data:** Skå-Edeby med alla lager utom pistar, med den öppna höjdmodellen och med
  Lantmäteriets 1 m. Hur datan hämtas står i [presentation/README.md](presentation/README.md).
- **Pistar:** Hammarbybacken ([`presentation/aoi_hammarbybacken.geojson`](presentation/aoi_hammarbybacken.geojson)),
  eftersom Skå-Edeby saknar pistar. Kartan visar också liftarna och Tvärbanan ur hinderlagret.
- **Hinder:** Skå-Edeby har bara kraftledningar och stolpar, alla utan `height`-tagg och därför
  med nominella höjder. De stora ledningarna går strax utanför AOI:n, så hinderkartan har ett
  större utsnitt.
- **Terrängprofilen** går från åkrarna i väster, över flygfältet och upp över skogskullen i öster,
  från 59,3457° N 17,7187° Ö till 59,3470° N 17,7645° Ö (2,6 km). Flygfältet saknar
  markanvändning eftersom `aeroway` inte ingår i frågan mot Overpass.
- **Källhänvisning** står på varje karta efter vilka lager den visar.

## Demo eller skärmbilder

Skärmbilder är förstahandsvalet. Overpass och Lantmäteriet kan vara långsamma eller nere. Vill man
visa något live är `gis-layers profile` mot en färdig katalog säkert, eftersom det går helt
offline.
