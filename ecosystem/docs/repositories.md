# Veřejný kód a soukromé studium

`learning-ecosystem-private` je pracovní repozitář: kód, skilly, vlastní materiály,
odpovědi, výsledky a metodické verze. `learning-ecosystem` je samostatná veřejná
prezentace téhož kódu bez dat. Veřejný repozitář nesmí být forkem ani běžným
klonem soukromého s následným smazáním souborů: ty by zůstaly v historii.

## Co se přenáší

| Cesta | Soukromá verze | Veřejná verze |
|---|---|---|
| Kód, skilly, testy, dokumentace a registr technik | Ano | Ano |
| `ecosystem/data/studium/` | Ano | Ne |
| `ecosystem/data/training/` | Ano | Ne |
| `ecosystem/data/methods/` | Ano | Ne |
| `knowledge_pipeline/data/justin_sung/{incoming,archive,synthesis}/` | Ano, pokud existují | Ne |
| `knowledge_pipeline/config/baseline.json` | Ano, pokud existuje | Ne |
| Prostředí, přihlášení, místní konfigurace, zámky a instalační záznamy | Ne | Ne |

Starší metodické verze uchovávej: uložené lekce a pokusy na ně odkazují.
Pravidla `.gitattributes` zachovávají bajty studijních dat včetně konců řádků,
aby se při přenosu mezi Linuxem a Windows nezměnily ověřované otisky materiálů.
Získané materiály a přepisy zůstávají v soukromé verzi. Vyexportované obrázky
stránek v `ecosystem/data/renders/` lze znovu vytvořit a nepřenášejí se.

## Práce na druhém zařízení

Soukromý repozitář klonuj přes svůj přihlášený účet GitHub, například:

```bash
git clone git@github.com:KrystofKr/learning-ecosystem-private.git
cd learning-ecosystem-private
python3 -m venv knowledge_pipeline/.venv
knowledge_pipeline/.venv/bin/python -m pip install -e ./knowledge_pipeline -e ./ecosystem
knowledge_pipeline/.venv/bin/python -B ecosystem.py install
knowledge_pipeline/.venv/bin/python -B ecosystem.py doctor
```

Instalace vyžaduje Python 3.11+ v Linuxu/WSL. Místní přihlášení a případnou
Windows úlohu nastav na každém zařízení samostatně. Přítomnost dat nepotvrzuje
funkčnost instalace: řiď se výsledkem `doctor`. Jestli nebyl přenesen zdroj
metodiky, `doctor` tuto část neoznačí za připravenou; uložené výsledky tím neměň.

Před učením na dalším zařízení nejdřív odešli změny z předchozího zařízení,
potom stáhni novou verzi:

```bash
git pull --ff-only
```

Po učení, když neběží zapisující automatizace, ulož konzistentní stav:

```bash
git add .
python3 -B knowledge_pipeline/scripts/check_repository.py --root . --profile private
git diff --cached --stat
git commit -m "Update study progress and materials"
git push origin main
```

Odesílání dat je ruční. Stažený ZIP je vhodný k prohlížení, ale pro obousměrné
přenášení pokroku používej Git checkout. Stejný předmět neupravuj souběžně
na dvou zařízeních. Pokud `pull --ff-only` odmítne rozvětvenou historii, porovnej
a sluč oba výsledky; nenahrazuj automaticky jednu verzi druhou.

Kontrola odmítá soubory `pending.json`, protože ukazují na nedokončenou
transakci. Neodstraňuj je jen kvůli commitu; nejprve dokonči běh nebo jeho obnovu.
Soubory ignorované Gitem zůstávají na příslušném zařízení.

## Aktualizace veřejné prezentace

Kód vyvíjej a otestuj v soukromém repozitáři. Potom commitni změny a exportuj
veřejné soubory do samostatné složky mimo soukromý checkout:

```bash
python3 -B ecosystem/scripts/export_public.py --destination /absolutni/cesta/learning-ecosystem-public
git -C /absolutni/cesta/learning-ecosystem-public diff --cached --stat
git -C /absolutni/cesta/learning-ecosystem-public commit -m "Update public source"
git -C /absolutni/cesta/learning-ecosystem-public push origin main
```

Při prvním exportu vznikne nový Git repozitář bez převzetí soukromé historie.
Před prvním pushem nastav jeho `origin` na veřejný repozitář. Pro další exporty
používej stejný veřejný checkout, případně jeho čistý klon z GitHubu.

Export čte pouze commitnuté soubory, vybírá cesty povolené veřejným profilem,
nastaví veřejný profil a ignorování dat, ověří obsah a připraví změny v indexu.
Sám necommitne ani neodesílá. Odmítne neuložené změny a cílový checkout, jehož
dosažitelná historie obsahuje neveřejné cesty nebo rozpoznatelný přístupový klíč.
Soukromé větve a tagy se do veřejného repozitáře neposílají.

## Kontroly obou variant

Soubor `.github/repository-profile.json` obsahuje `schema_version: 1` a profil
`public` nebo `private`. Místní kontrola používá profil z Git indexu, nikoli
z necommitnuté úpravy pracovního souboru. Bez souboru používá veřejná pravidla.

GitHub Actions předá skutečnou viditelnost repozitáře parametrem `--profile`.
Nesoulad s uloženým profilem je chyba. Soukromá verze povoluje jen deklarované
studijní cesty; kontrola přístupových klíčů a místních provozních souborů platí
pro obě varianty. Testy používají syntetické záznamy i v soukromém repozitáři.
