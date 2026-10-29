# Vedený trénink technik

Toto rozhraní doplňuje koučování o konkrétní lekce a trvale evidovaný postup
v jednotlivých technikách. Příkazy spouštěj z kořene ekosystému přes
`.venv/bin/python -B ecosystem.py`. Původní StudyStore zůstává autoritou pro
výsledky předmětu; trénink technik má vlastní evidenci v `ecosystem/data/training/`.

## Jedna návštěva

1. Použij již zvolený předmět. Chybí-li, vypiš `subjects` a zeptej se pouze na
   předmět a nejbližší cíl, které nelze zjistit z dosavadního zadání. Stávající
   historii načti z `ecosystem/data/studium`; nevracej se ke staré externí kopii.
2. Načti `next --subject ID`. Výchozí pořadí sleduje základy a návaznosti;
   konkrétní doložená překážka nebo uživatelovo zadání může vybrat jinou techniku
   přes `--technique ID`. Udržuj jeden právě trénovaný metodický cíl, který
   prováděj při skutečném studiu předmětu, ne dlouhý druhý kurz vedle něj.
3. Přes `methods --technique ID` přečti aktuální účel, podklady, úlohu a kritéria.
   Metodické rady ověř v původním textu. Cvičení a postupové podmínky v registru
   jsou návrh tohoto ekosystému, nikoli doslovné nebo vědecky validované Sungovy
   testy. Nezkracuj metodu jen na zkratku rámce.
4. Vyber konkrétní materiál a vystav lekci: `lesson --subject ID --technique ID
   --context 'jedinečná skutečná úloha' --domain 'předmět/typ použití'`.
   Kontext odlišuje konkrétní příklady; doména odlišuje druh použití pro přenos.
   Identifikátory neobměňuj pouze kvůli splnění počtu. Přípravné předpoklady
   stručně doplň, když chybí; nevyžaduj předem celý jiný kurz.
5. Předlož úlohu a veď ji podle aktuální fáze. V orientaci můžeš vysvětlit metodu;
   v samostatném pokusu či retenci před vlastním výstupem neukazuj řešení ani
   hotovou mapu vztahů. Při žádosti o pomoc ji poskytni a skutečně zaznamenej.
   Pro mapy použij skutečný obrázek či uživatelův zápis a nech vysvětlit vztahy.
6. Vyhodnoť skutečný výstup po kritériích z vystavené lekce. Předmětovou správnost
   ověř přes `study-library`. Nejasné nebo neověřené hodnocení je `unassessed`;
   samotné sebevědomí uživatele či modelu není důkaz. Vysvětli jednu konkrétní
   opravu a podle potřeby nech uživatele provést další vlastní pokus.
7. Ulož záznam přes `record --subject ID --input /tmp/skutecny-pokus.json`.
   Pokud současně proběhla předmětová aktivita, ulož ji přes `study-progress`
   a její skutečné ID připoj do `checkpoint_ids`. Zápis techniky sám nemění
   znalostní stav tématu. Výklad předmětu ani úspěch metody si navzájem nezaměňuj.
8. Krátce pojmenuj doložený posun, jednu úpravu a další krok. Při další návštěvě
   znovu načti stav; nespoléhej na paměť chatu. Opakování s odstupem je plán
   návratu, nikoli spuštěná notifikace na pozadí.

## Fáze a jejich podmínky

| Fáze | Důkaz pro postup |
|---|---|
| `orientation` | Vlastní vysvětlení účelu, činnosti a omezení podle kritérií. |
| `guided` | Vlastní provedení konkrétní úlohy s přiměřeným vedením. |
| `independent` | Alespoň dvě úspěšná provedení v různých skutečných úlohách, bez pomoci a předem viděného řešení. |
| `transfer` | Úspěšná samostatná adaptace na jinou doménu nebo druh použití. |
| `retained` | Samostatný výkon s doloženou poslední relevantní expozicí a odstupem alespoň jednoho dne. |

Po projití fází následuje udržování. Tyto podmínky jsou průhledná pravidla
produktu pro shromažďování dokladů, nikoli záruka dlouhodobého zvládnutí.
Jednodenní hranice není tvrzení o optimálním intervalu opakování; další návrat
vol podle výkonu, cíle a termínu. Rozumný počáteční plán může obsahovat jeden
metodický cíl na několik bloků; nedávej pevný časový slib zvládnutí techniky.

## Záznam skutečného pokusu

```json
{
  "attempt_id": "pokus-jedinecne-id",
  "lesson_id": "lesson-id-vracene-nastrojem",
  "answer": "Skutečný vlastní výstup uživatele, nikoli vzorová odpověď.",
  "assessment": "unassessed",
  "criteria": {"purpose": null, "mechanism": null, "boundary": null},
  "assistance": "unknown",
  "solution_seen": null,
  "occurred_at": "2026-10-27T12:00:00+01:00",
  "review_notes": "Konkrétní důvod neuzavřeného hodnocení a další ověření.",
  "reviewer": "assistant",
  "checkpoint_ids": []
}
```

Příklad není skutečný pokus. Vždy dosaď existující `lesson_id`, skutečný čas a
přesné klíče `rubric` dané lekce. Čas zápisu nevydávej zpětně za čas starší práce.
`assessment`: `passed`, `failed`, `unassessed`; `criteria`: true, false, null.
Úspěch vyžaduje všechna kritéria true, neúspěch alespoň jedno doložené false.
`assistance`: `none`, `hint`, `solution`, `unknown`. Předchozí vidění řešení
je true, false nebo null; neznámé podmínky nepotvrzují samostatnost.
`reviewer` je `assistant`, `user` nebo `external` a uvádí původ hodnocení.
U retence přidej `exposure_at` se skutečným časem poslední relevantní expozice.

Shodné ID a obsah lze bezpečně zopakovat. Nový vlastní pokus vyžaduje novou
lekci i nové ID. Oprava hodnocení má nové `attempt_id` a `correction_of` na
účinný předchozí záznam; zachovává lekci, skutečnou odpověď a čas. Nezvyšuje
počet pokusů. Při změně hodnocení předmětu oprav také příslušný checkpoint
původním rozhraním; jde o dva různé druhy evidence.

## Nová syntéza

Po dokončeném zveřejnění nové syntézy automatizace volá `sync`. Vznikne neměnný
snímek, obnoví se řádkové odkazy, porovnají se textové části a verze technik.
Při chybě se ponechá poslední platná verze a plánovaný běh aktualizaci zopakuje.
Při navázání `needs_revalidation` nebo `source_update_review` upozorní na
změněnou metodiku. Přečti aktuální podklady i `source.diff` příslušné verze,
vysvětli relevantní změnu a ověř její použití cíleným pokusem. Starý výkon
zůstává doložený pro starou verzi; neoznačuj ho za nově chybný.

Nová syntéza sama nezvyšuje osvojení techniky ani vědomosti. Změna jen v
nesouvisející kapitole nezneplatňuje doklady ostatních technik. Nová nepokrytá
kapitola se ukáže v `unmapped_chapters`; nevyvozuj z ní neověřená kritéria.
Rozšíření registru je změna kódu/konfigurace, kterou musí projít kontrola a testy.
