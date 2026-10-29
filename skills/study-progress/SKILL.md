---
name: study-progress
description: Ukládej a obnovuj skutečné studijní pokusy odděleně podle předmětů; rozliš pomoc, pokrytí a samostatný výkon s odstupem. Použij při navázání, zápisu výsledku či dotazu na pokrok. Metodu a experimenty koordinuje study-coach, výuku study-tutor.
---

# Studijní paměť

Udržuj doložitelný stav učení, aby další lekce mohla navázat i v novém chatu.

Před první operací přečti [společný protokol](references/protocol.md).
Pro příkazy a formát vstupů použij [rozhraní úložiště](references/storage.md).
Úložiště obsluhuje `scripts/study_store.py`; cesta ke skriptu je relativní k tomuto skillu.

V projektu Learning Ecosystem je výchozí knihovna `ecosystem/data/studium/`.
Pro stav osvojení technik použij `ecosystem.py status --subject ID` podle
[tréninkového rozhraní](../study-coach/references/training.md); jde o odlišnou
evidenci od znalostí předmětu. Při navázání zohledni obě, pokud probíhá trénink.

## Postup

1. Urči absolutní cestu ke studijnímu úložišti a konkrétní `subject_id` podle protokolu. Při nejasném předmětu polož jednu krátkou otázku. Nevytvářej předmět pouze podle názvu složky projektu.
2. Pro navázání načti `resume`. Pro přehled použij `status`. Neodvozuj znalosti z paměti chatu, když existují uložené výsledky.
   Je-li přítomný `study-coach`, načti také jeho `coaching_store.py view` pro tento předmět: aktuální cíl, plán, experiment a kontext pokusů. Chybějící `coaching.json` znamená žádné koučovací záznamy, nikoli ztracenou historii výsledků.
3. Nový předmět založ pomocí `init` s názvem a alespoň jedním cílem. Chybějící informace zjisti od uživatele; nezačínej se smyšlenými předměty či výsledky.
4. Z výuky převezmi výsledek konkrétní aktivity: zadání, skutečnou odpověď, vyhodnocení, míru nápovědy, chyby, použité zdroje a další krok. Zapiš ho přes `checkpoint` ještě před další výukovou aktivitou. Při samotném výkladu může být odpověď prázdná a výsledek musí být `introduced`.
5. Při opakování stejného zápisu zachovej `checkpoint_id`; shodný zápis je idempotentní, odlišný obsah pod stejným ID je chyba. Při opravě vytvoř nový záznam s `correction_of` odkazujícím na účinný checkpoint téže aktivity a v `evaluation` uveď důvod i ověřený podklad. Zachovej původní `lesson_id`, `topic_id`, `activity` a skutečnou `learner_answer`; oprava není nový pokus. Projdi související hodnocení, neoprávněně připsané obtíže i další kroky a oprav každý dotčený záznam zvlášť. Původní historii nemaž; pro interpretaci používej `is_effective` z `history` a aktuální stav z `resume`.
6. Ukládej po vyhodnocených cvičeních i dokončených výkladech. Konec lekce není jediný okamžik zápisu. V případě chyby zápisu ji uveď a netvrď, že je postup uložený.

## Interpretace výsledků

- `introduced`: látka byla představena, zvládnutí nebylo ověřeno.
- `unassessed`: hodnocení není uzavřeno, například kvůli neověřené konvenci nebo zrušenému kritériu. Nedokládá chybu, zvládnutí ani výklad a nemění stav znalostí či termín opakování. Důvod a ověření zaznamenej do `evaluation` a `next_step`; nevymýšlej odpověď a nepřipisuj `misconceptions`.
- `needs-practice`: pokus ukázal potřebu dalšího procvičení.
- `passed`: odpověď splnila uvedené kritérium. S nápovědou se téma vede jako `practicing`; bez nápovědy jako `demonstrated`.
- `demonstrated` znamená doložený úspěch v zaznamenané aktivitě. Zohledni obtížnost, šíři cíle a pozdější opakování; jeden lehký příklad nepotvrzuje zvládnutí celého předmětu.
- Vysvětlení asistenta, přečtený text ani uživatelovo „rozumím“ samy o sobě nejsou úspěšným testem.
- Nejistota učitele ani použití jiné možné konvence nejsou samy o sobě `needs-practice`. Před přijetím negativního hodnocení musí být doložené kritérium pro danou aktivitu. Při chybě učitele lze původní výsledek opravit na `passed`, `needs-practice`, `introduced` nebo `unassessed` podle skutečných podkladů; pouhé nové vysvětlení chybný záznam neopraví.

## Doklady skutečného učení

Podle metodiky Justina Sunga rozliš retenci, použití a přenos i celkovou cenu
učení (kapitoly 1 a 9). Přesnou metodickou radu načti přes
[metodické rozhraní](../study-coach/references/tools.md); čísla vycházejí ze záznamů.

- `introduced` dokládá skutečně vysvětlený obsah; přečtení materiálů asistentem,
  plán, reflexe ani prohlédnutí metody nejsou výkladem uživateli.
- `demonstrated` zachovej jako historický stav StudyStore, ne jako procentní
  zvládnutí nebo potvrzení dlouhodobé retence. `hints_used: 0` samo nepotvrzuje,
  že uživatel předem neviděl řešení. Nové podmínky pokusu ukládej přes
  `coaching_store.py` jako `attempt-context`; neměň striktní formát checkpointu.
- Rozliš rozpoznání, vybavení, vysvětlení, aplikaci a přenos. Opakování téže
  odpovědi po vysvětlení nedokládá přenos do nového problému.
- Pro výsledek s odstupem potřebuješ skutečný čas pokusu, relevantní předchozí
  expozici a podmínky pomoci. `created_at` je čas zápisu, nikoli jistý čas učení.
  Neznámé údaje neodvozuj ze stáří chatu či souboru.
- Při přehledu počítej jen účinné aktivity po vyřešení oprav. Úspěšnost může
  používat pouze `passed` a `needs-practice`, s uvedením počtů, typu úlohy,
  pomoci a omezení. `introduced` a `unassessed` nejsou neúspěšné testy.
- Délka práce, počet materiálů a počet zpráv AI nezvyšují znalostní stav.
  Chybějící čas nebo pomoc nejsou nula. Malý vzorek ukaž jako „2 ze 3“
  s omezením; nevytvářej univerzální skóre člověka.
- Termíny opakování přizpůsob cíli a výsledku; den–týden–měsíc je autorův
  příklad, nikoli povinný algoritmus. Reflexe metody a uživatelská jistota
  před zpětnou vazbou se ukládají odděleně, bez smyšleného hodnocení.

Při předání učiteli nebo kouči vrať pouze vybraný předmět: cíle, relevantní stav témat, poslední checkpoint, otevřené obtíže, naplánované opakování a známé podmínky pokusů. Načtení stavu neopravňuje ke změně materiálů ani výsledků.
