# Zdroj metodiky a trvalé koučovací záznamy

Oba nástroje používají standardní Python 3.10+. `coaching_store.py` navíc stejně
jako StudyStore používá `fcntl` (Linux/macOS nebo WSL). Cesty ke skriptům jsou
relativní ke `study-coach`; v příkazech dosaď jejich skutečné absolutní cesty.

## Čtení aktuální metodiky

```bash
python3 /cesta/ke/skillum/study-coach/scripts/method_library.py catalog
python3 /cesta/ke/skillum/study-coach/scripts/method_library.py read --section 4.2
python3 /cesta/ke/skillum/study-coach/scripts/method_library.py read --chapter 9
python3 /cesta/ke/skillum/study-coach/scripts/method_library.py search --query 'GRINDE'
```

Pořadí výběru originálu: `--synthesis /absolutni/cesta/main.txt`, proměnná
`JUSTIN_SUNG_SYNTHESIS`, aktuálně publikovaný snímek metodiky pod
`ecosystem/data/methods/versions/`; před první synchronizací se použije
`knowledge_pipeline/data/justin_sung/synthesis/main.txt`. Cesty se odvozují
od skutečného kořene projektu i po přesunu. Nástroj pouze čte soubor. Nevolá Codex, nestahuje videa, nemění
syntézu, archiv ani stav automatizace. Přečtení kapitoly není výuka uživatele.

`catalog` vrací kapitoly se skutečnými rozsahy řádků. `read` vrací konkrétní
kapitolu nebo podkapitolu. `search` je doslovné hledání bez rozlišení velikosti
písmen; stránkuje přes `--start` a `--limit`. Žádná shoda nedokládá absenci pojmu.
Všechny výstupy obsahují cestu a `document_sha256`. Čti širší podmínky a výjimky,
nejen shodu výrazu. Reference na metodiku zůstávají oddělené od předmětových
`source_refs` v checkpointu.

## Koučovací paměť

```bash
python3 /cesta/ke/skillum/study-coach/scripts/coaching_store.py --root /projekt/studium --subject tilo view
python3 /cesta/ke/skillum/study-coach/scripts/coaching_store.py --root /projekt/studium --subject tilo view --kind experiment
python3 /cesta/ke/skillum/study-coach/scripts/coaching_store.py --root /projekt/studium --subject tilo put --input /tmp/skutecny-zaznam.json
```

Předmět musí již existovat ve StudyStore. Nástroj zapisuje jen
`subjects/<subject_id>/coaching.json`; původní `state.json` nepřepisuje. Změny
sdílejí zámek předmětu, jsou atomické a zachovávají historii. `view --history`
zahrne staré verze; výchozí `view` vrací jen aktuální záznamy.

Každý vstup obsahuje přesně společná pole:

```json
{
  "subject_id": "tilo",
  "record_id": "experiment-2026-10-27-01",
  "kind": "experiment",
  "checkpoint_ids": [],
  "method_refs": [],
  "observations": ["Sem patří skutečný uživatelův popis; tento příklad není skutečný zápis."],
  "payload": {
    "change": "Po krátkém čtení vlastními slovy vysvětlit jednu vztahovou vazbu.",
    "expected_effect": "Lepší vysvětlení vztahu v novém příkladu.",
    "measure": "Vlastní odpověď na jinou srovnatelnou úlohu bez řešení.",
    "comparison": "Dosavadní vlastní pokus a další pokus při známé pomoci.",
    "status": "active"
  }
}
```

Příklad před reálným použitím nahraď skutečnými údaji. Při použití metodiky vlož
do `method_refs` ověřené položky se čtyřmi poli `document_sha256`, `section`
(např. `6.1`), `line_start`, `line_end` z právě načteného zdroje. Prázdný seznam
je pro zápis bez konkrétní metodické citace; není potvrzením Sungova původu.
Nástroj kontroluje formát odkazu, ne pravdivost interpretace ani archivaci staré
verze syntézy. Pokud je stará verze již nedostupná, uveď toto omezení.

`observations` je volitelný seznam skutečných pozorování; nepřeváděj vlastní
domněnku na uživatelův výrok. `checkpoint_ids` odkazují jen na existující záznamy
daného předmětu. Před novým závěrem vyřeš jejich případné opravy přes `history`.

| `kind` | Povinný `payload` | Volitelné údaje |
|---|---|---|
| `goal` | `outcome`, `criterion`, neprázdný seznam `scope` | `deadline`: YYYY-MM-DD nebo null |
| `plan` | `period_start`: datum, `capacity_minutes`, `reserve_minutes`, seznam `blocks` | žádné |
| `hypothesis` | `claim`, `alternative`, `next_test` | žádné |
| `experiment` | `change`, `expected_effect`, `measure`, `comparison`, `status` | `result`; při `completed` povinný |
| `reflection` | `what`, `how`, `why`, `next_step` | žádné |
| `attempt-context` | `checkpoint_id`, `task_mode`, `assistance`, `solution_seen` | viz níže |

Blok plánu obsahuje `topic_id`, `output`, `min_minutes`, `max_minutes` a
`estimate_basis`. Téma musí existovat. Časy jsou nezáporná celá čísla a horní
odhady s rezervou nesmějí přesáhnout kapacitu. Při chybě ukaž konflikt a nabídni
priority; nezvyšuj kapacitu automaticky. Jeden předmět může mít nejvýše jeden
aktuální experiment se stavem `active`; další stavy jsou `completed` a `abandoned`.

### Kontext skutečného pokusu

Zapiš až po potvrzeném checkpointu. `checkpoint_id` odkazuje na původní aktivitu,
nikoli na její opravu. `task_mode` je `recognition`, `recall`, `explain`, `apply`
nebo `transfer`. `assistance` je `none`, `hint`, `solution` nebo `unknown`;
`solution_seen` je true, false nebo null pro neznámé podmínky.

Volitelně lze uložit `occurred_at` a `exposure_at` (ISO 8601 s pásmem),
`review_of` (jinou původní aktivitu), `duration_minutes` a `confidence` (0–1).
Jistota je použitelná jen s `confidence_timing: before-feedback`, pokud ji
uživatel opravdu uvedl před zpětnou vazbou. Neznámé údaje vynech nebo použij
null tam, kde je podporováno. Datum uložení není automaticky datum učení.

`hints_used: 0` ve staré evidenci samo neprokazuje neviděné řešení. `assistance:
none` spolu s `solution_seen: null` stále není potvrzeně samostatný výkon.
Neznámé nebo sporné hodnocení se nepočítá jako neúspěch. Kontext nemění výsledek
checkpointu; opravu hodnocení proveď původním nástrojem přes `correction_of`.

### Opakování a revize

Shodný vstup se stejným `record_id` je idempotentní. Jiný obsah pod stejným ID
je konflikt. Pro změnu vytvoř nové ID a volitelné společné pole `replaces`
odkazující na aktuální záznam stejného druhu. Nová verze kontextu musí zachovat
původní aktivitu. U plánu uveď důvod změny v `observations`; starý plán zůstává
dohledatelný. Plánování, reflexe a čtení metodiky nevytvářejí checkpointy.
