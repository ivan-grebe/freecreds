# ASSIST Institution Codes

Reference mapping from ASSIST's internal institution code (the identifier stored
in `institutions.code` in [data/assist.db](data/assist.db)) to the school's
display name and term system.

Generated from [tests/fixtures/institutions.json](tests/fixtures/institutions.json) —
re-run the snippet at the bottom of this file to refresh.

> **Known quirks in upstream data**
> - `COMPTON` is used for both Compton College and (historically) El Camino College.
> - `SU` is used for both Simpson University and Stanton University.
> - Some codes are whitespace-padded in the raw feed; `institutions.code` stores
>   them padded, but the API trims with `TRIM(code)` on output.

## California Community Colleges — CCC (116)

| ASSIST Code | Institution | Term |
| --- | --- | --- |
| `AHC` | Allan Hancock College | Semester |
| `ALAMEDA` | College of Alameda | Semester |
| `ARC` | American River College | Semester |
| `AVC` | Antelope Valley College | Semester |
| `BAKERFLD` | Bakersfield College | Semester |
| `BARSTOW` | Barstow Community College | Semester |
| `BUTTE` | Butte College | Semester |
| `CABRILLO` | Cabrillo College | Semester |
| `CAMINO` | El Camino College | Semester |
| `CANADA` | Canada College | Semester |
| `CANYONS` | College of the Canyons | Semester |
| `CERRITOS` | Cerritos College | Semester |
| `CERRO` | Cerro Coso Community College | Semester |
| `CHABOT` | Chabot College | Semester |
| `CHAFFEY` | Chaffey College | Semester |
| `CITRUS` | Citrus College | Semester |
| `CLOVIS` | Clovis Community College | Semester |
| `COASTLIN` | Coastline Community College | Semester |
| `COLUMBIA` | Columbia College | Semester |
| `COMPTON` | Compton College / El Camino College | Semester |
| `CONTRA` | Contra Costa College | Semester |
| `COPPER` | Copper Mountain College | Semester |
| `CRAFTON` | Crafton Hills College | Semester |
| `CRC` | Cosumnes River College | Semester |
| `CUESTA` | Cuesta College | Semester |
| `CUYAMACA` | Cuyamaca College | Semester |
| `CYPRESS` | Cypress College | Semester |
| `DAC` | De Anza College | Quarter |
| `DESERT` | College of the Desert | Semester |
| `DIABLO` | Diablo Valley College | Semester |
| `EVERGRN` | Evergreen Valley College | Semester |
| `FEATHER` | Feather River College | Semester |
| `FOLSOM` | Folsom Lake College | Semester |
| `FOOTHILL` | Foothill College | Quarter |
| `FRESNO` | Fresno City College | Semester |
| `FULLRTON` | Fullerton College | Semester |
| `GAVILAN` | Gavilan College | Semester |
| `GLENDALE` | Glendale Community College | Semester |
| `GMCC` | Grossmont College | Semester |
| `GWC` | Golden West College | Semester |
| `HARTNELL` | Hartnell College | Semester |
| `IMPERIAL` | Imperial Valley College | Semester |
| `IRVINE` | Irvine Valley College | Semester |
| `KRC` | Reedley College | Semester |
| `LACC` | Los Angeles City College | Semester |
| `LAEC` | East Los Angeles College | Semester |
| `LAHC` | Los Angeles Harbor College | Semester |
| `LAMC` | Los Angeles Mission College | Semester |
| `LANEY` | Laney College | Semester |
| `LAPC` | Los Angeles Pierce College | Semester |
| `LASC` | Los Angeles Southwest College | Semester |
| `LASSEN` | Lassen Community College | Semester |
| `LATT` | Los Angeles Trade Technical College | Semester |
| `LAVC` | Los Angeles Valley College | Semester |
| `LAWC` | West Los Angeles College | Semester |
| `LBCC` | Long Beach City College | Semester |
| `MARIN` | College of Marin | Semester |
| `MATEO` | College of San Mateo | Semester |
| `MCC` | Madera Community College | Semester |
| `MEDANOS` | Los Medanos College | Semester |
| `MENDOCIN` | Mendocino College | Semester |
| `MERCED` | Merced College | Semester |
| `MERRITT` | Merritt College | Semester |
| `MESA` | San Diego Mesa College | Semester |
| `MIRACSTA` | MiraCosta College | Semester |
| `MIRAMAR` | San Diego Miramar College | Semester |
| `MISSION` | Mission College | Semester |
| `MODESTO` | Modesto Junior College | Semester |
| `MONTEREY` | Monterey Peninsula College | Semester |
| `MOORPARK` | Moorpark College | Semester |
| `MTSAC` | Mount San Antonio College | Semester |
| `MTSJC` | Mt. San Jacinto College | Semester |
| `MVC` | Moreno Valley College | Semester |
| `NAPA` | Napa Valley College | Semester |
| `NORCO` | Norco College | Semester |
| `OCC` | Orange Coast College | Semester |
| `OHLONE` | Ohlone College | Semester |
| `OXNARD` | Oxnard College | Semester |
| `PALOMAR` | Palomar College | Semester |
| `PALOVRDE` | Palo Verde College | Semester |
| `PASADENA` | Pasadena City College | Semester |
| `PORTER` | Porterville College | Semester |
| `POSITAS` | Las Positas College | Semester |
| `RCC` | Riverside City College | Semester |
| `REDWOODS` | College of the Redwoods | Semester |
| `RIOHONDO` | Rio Hondo College | Semester |
| `RSC` | Santa Ana College | Semester |
| `SADDLBK` | Saddleback College | Semester |
| `SBCC` | Santa Barbara City College | Semester |
| `SBVC` | San Bernardino Valley College | Semester |
| `SCC` | Sacramento City College | Semester |
| `SDCC` | San Diego City College | Semester |
| `SEQUOIAS` | College of the Sequoias | Semester |
| `SFCITY` | City College of San Francisco | Semester |
| `SHASTA` | Shasta College | Semester |
| `SIERRA` | Sierra College | Semester |
| `SISKIYOU` | College of the Siskiyous | Semester |
| `SJCC` | San Jose City College | Semester |
| `SJDELTA` | San Joaquin Delta College | Semester |
| `SKYLINE` | Skyline College | Semester |
| `SMCC` | Santa Monica College | Semester |
| `SOLANO` | Solano Community College | Semester |
| `SANTIAGO` | Santiago Canyon College | Semester |
| `SRC` | Santa Rosa Junior College | Semester |
| `SWSTRN` | Southwestern College | Semester |
| `TAFT` | Taft College | Semester |
| `TAHOE` | Lake Tahoe Community College | Quarter |
| `VENTURA` | Ventura College | Semester |
| `VISTA` | Berkeley City College | Semester |
| `VVCC` | Victor Valley College | Semester |
| `WCC` | Woodland Community College | Semester |
| `WHC` | Coalinga College | Semester |
| `WHCL` | Lemoore College | Semester |
| `WVC` | West Valley College | Semester |
| `YUBA` | Yuba College | Semester |

## California State University — CSU (23)

| ASSIST Code | Institution | Term |
| --- | --- | --- |
| `HSU` | California Polytechnic University, Humboldt | Semester |
| `CPP` | California Polytechnic University, Pomona | Quarter |
| `CPSLO` | California Polytechnic University, San Luis Obispo | Quarter |
| `CSUB` | California State University, Bakersfield | Semester |
| `CSUCI` | California State University, Channel Islands | Semester |
| `CSUC` | California State University, Chico | Semester |
| `CSUDH` | California State University, Dominguez Hills | Semester |
| `CSUHAY` | California State University, East Bay | Quarter |
| `CSUFRES` | California State University, Fresno | Semester |
| `CSUFULL` | California State University, Fullerton | Semester |
| `CSULB` | California State University, Long Beach | Semester |
| `CSULA` | California State University, Los Angeles | Semester |
| `CSUMA` | California State University, Maritime Academy | Semester |
| `CSUMB` | California State University, Monterey Bay | Semester |
| `CSUN` | California State University, Northridge | Semester |
| `CSUS` | California State University, Sacramento | Semester |
| `CSUSB` | California State University, San Bernardino | Quarter |
| `CSUSM` | California State University, San Marcos | Semester |
| `CSUSTAN` | California State University, Stanislaus | Semester |
| `SDSU` | San Diego State University | Semester |
| `SFSU` | San Francisco State University | Semester |
| `SJSU` | San Jose State University | Semester |
| `SSU` | Sonoma State University | Semester |

## University of California — UC (9)

| ASSIST Code | Institution | Term |
| --- | --- | --- |
| `UCB` | University of California, Berkeley | Semester |
| `UCD` | University of California, Davis | Quarter |
| `UCI` | University of California, Irvine | Quarter |
| `UCLA` | University of California, Los Angeles | Quarter |
| `UCM` | University of California, Merced | Semester |
| `UCR` | University of California, Riverside | Quarter |
| `UCSD` | University of California, San Diego | Quarter |
| `UCSB` | University of California, Santa Barbara | Quarter |
| `UCSC` | University of California, Santa Cruz | Quarter |

## AICCU / Independent (31)

| ASSIST Code | Institution | Term |
| --- | --- | --- |
| `APU` | Azusa Pacific University | Semester |
| `AU` | Antioch University | Quarter |
| `CBU` | California Baptist University | Semester |
| `CDU` | Charles R. Drew University of Medicine and Science | Semester |
| `CLU` | California Lutheran University | Semester |
| `CUI` | Concordia University Irvine | Semester |
| `DUC` | Dominican University of California | Semester |
| `FPU` | Fresno Pacific University | Semester |
| `LAPU` | Los Angeles Pacific University | Semester |
| `LASIERRA` | La Sierra University | Quarter |
| `LCAD` | Laguna College of Art + Design | Semester |
| `LMU` | Loyola Marymount University | Semester |
| `MENLO` | Menlo College | Semester |
| `MSMU` | Mount Saint Mary's University Los Angeles | Semester |
| `NDNU` | Notre Dame de Namur University | Semester |
| `NU` | National University | Quarter |
| `PAU` | Palo Alto University | Quarter |
| `PEPRDN` | Pepperdine University | Semester |
| `SCU` | Santa Clara University | Quarter |
| `SCUHS` | Southern California University of Health Sciences | Trimester |
| `SMC` | Saint Mary's College of California | Semester |
| `SU` | Simpson University / Stanton University | mixed |
| `TUW` | Touro University Worldwide | Semester |
| `ULV` | University of La Verne | Semester |
| `UOFR` | University of Redlands | Semester |
| `UOP` | University of the Pacific | Semester |
| `USD` | University of San Diego | Semester |
| `USFCA` | University of San Francisco | Semester |
| `VUSC` | Vanguard University of Southern California | Semester |
| `WC` | Whittier College | Semester |

## Regenerating this file

```python
import json
from collections import defaultdict

data = json.load(open("tests/fixtures/institutions.json"))
TERM = {0: "Semester", 1: "Quarter", 2: "Trimester"}
CAT  = {2: "CCC", 0: "CSU", 1: "UC", 5: "AICCU"}

groups = defaultdict(list)
for i in data:
    if isinstance(i.get("category"), int):
        groups[i["category"]].append((
            (i.get("code") or "").strip(),
            (i.get("names") or [{}])[-1].get("name", ""),
            TERM.get(i.get("termType"), ""),
        ))

for cat, rows in groups.items():
    print(f"## {CAT[cat]}")
    for code, name, term in sorted(set(rows), key=lambda x: x[1]):
        print(f"| `{code}` | {name} | {term} |")
```
