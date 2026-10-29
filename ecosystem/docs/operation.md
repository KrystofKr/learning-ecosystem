# Provoz, synchronizace a obnova

`ecosystem.py` určuje kořen podle svého skutečného umístění. Runtime používá
relativně odvozené zdrojové cesty a stejný Python jako obsahová pipeline.
Osobní data jsou v `ecosystem/data/` a nejsou součástí veřejné distribuce.
Soukromá varianta verzovaná na GitHubu přenáší studijní knihovnu, pokrok,
trénink a metodické verze. Provozní záznamy a zámky zůstávají místní.
[Rozdělení repozitářů a přechod mezi zařízeními](repositories.md).

## Publikace metodiky

1. Pipeline dokončí transakci textu, stavové evidence a archivu. Uvolní zámek
   syntézy; rozhraní služby zavolá synchronizátor. Totéž platí pro obnovu.
2. Synchronizátor drží vlastní výlučný zámek a sdílený zámek syntézy. Při
   `pending.json` nebo neshodě hashů odmítne publikaci. Registr kontroluje
   identity, zdroje a acyklické předpoklady.
3. Verze je hash textu a definice kurikula. Části zdroje mají vlastní otisky;
   verze techniky zahrnuje její definici a skutečně používaný text. Posun
   řádků bez změny používaného textu osvojení nezneplatňuje.
4. V `methods/versions/<hash>/` vznikne zdrojový snímek, katalog a změnový
   souhrn. Pro další verze vzniká také `source.diff`. Vše se zpřístupní jedním
   atomickým přepnutím `current.json`; čtenář ověří otisky katalogu a snímku.
   Soubory se zapisují atomicky a katalog uzavírá připravený snímek. Neuzavřený
   snímek lze po pádu obnovit. Při návratu ke stejnému obsahu se již existující
   soubory verze nemění; změny proti právě předchozí publikaci jsou v ukazateli.
5. Příští učební průchod používá novou metodiku. Osobní výsledky se nemění;
   staré verze, lekce, odpovědi a hodnocení zůstávají dohledatelné. Pokusy
   starší dotčené techniky se zobrazují jako doklady k dřívější verzi a pro
   aktuální radu je potřeba cílené nové ověření.

Změna textu není sama důkazem změny pedagogického účinku. Označení k ověření
je konzervativní kontrola. Novou nepokrytou kapitolu katalog přizná; nevymýšlí
pro ni potichu věcná kritéria. Statické instrukce a registr se neupravují
libovolným modelem na základě textu videa. Aktualizují se jejich čtené podklady,
citace, verze metod a výběr následující lekce.

## Selhání a opakování

- Selže-li publikace, starý ukazatel zůstane platný. Opakovaný `sync` dokončí
  odvození z již zkontrolované syntézy, bez nového zpracování přepisu.
- `KnowledgePipeline.check()` a plánovač rozpoznají rozdíl zdrojového i
  konfiguračního hashe. Nedokončená synchronizace se proto neztratí po úspěšném
  zapsání přepisu ani po restartu.
- Samostatná instalace `knowledge_pipeline/` bez okolního ekosystému funguje
  nadále nezávisle. Integrace se aktivuje pouze u nadřazeného `ecosystem.py`
  s registrem technik. Produkční kontrola předplatného zůstává zachovaná.
- Při poškození publikovaného katalogu nepřepínej naslepo na živý zdroj.
  Zachovej poškozenou verzi k rozboru a obnov odpovídající soubory ze zálohy.

## Osobní evidence

`studium/subjects/ID/state.json` drží výsledky předmětu. Vedlejší `coaching.json`
drží plán a experimenty. `training/ID/progress.json` drží vydané metodické lekce
a neměnné pokusy s opravami. Každá evidence má vlastní formát a atomické zápisy.
Při obnově záloh je nutné zachovat jejich odkazy a odpovídající metodické verze.

Vystavení jedné lekce používá jednu načtenou verzi metodiky. Při čtení uložených
pokusů se znovu kontrolují podmínky hodnocení a vazba na vystavenou lekci;
neplatný doklad se nesmí proměnit v potvrzenou fázi. Pokud se vlastní odpověď
uloží, ale aktuální metodika není dostupná, rozhraní potvrdí záznam s
`method_status: unavailable` a neurčeným `stale_lesson`. Nehlásí neúspěch zápisu
po jeho skutečném dokončení.

Import kopíruje konzistentní snímky předmětů pod jejich zámky, ověřuje původní
i převzaté soubory a zaznamenává manifest. Výsledná knihovna je místní.
Dokončení instalace nevytváří v externím repozitáři odkazy ani další zálohy.
Po opravě provozních cest odstraní původní odkaz `learning_app`, pokud míří
na tento ekosystém. Přesun ani jeho opakování tento odkaz nevytváří.
Historické záznamy si ponechávají původní cesty jako doklad migrace.

## Oddělení školních projektů

Školní projekty a jejich Git patří do samostatného `osu_repository`.
Ekosystém má vlastní skilly, knihovnu i Python prostředí. Místní LibreOffice
je v `knowledge_pipeline/.venv/libreoffice/`; spouštěč neodkazuje do školního
prostředí. Původní studijní složky a staré návrhy se ve školním repozitáři
neobnovují.

Jednorázový příkaz `ecosystem/scripts/repair_layout.py` vrátí dříve chybně
přesunutý adresář školních projektů do jeho původního skutečného umístění.
Nevytváří kompatibilní odkaz zpět. Při selhání přejmenování obnoví původní
alias a zachová soubory. Přerušení opravy spouštěčů lze dokončit opakováním
příkazu. Stav zaznamená do `data/layout.json`; studijní výsledky nemění.
