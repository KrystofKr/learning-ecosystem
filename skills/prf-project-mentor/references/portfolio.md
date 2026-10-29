# Portfolio a praktická obhajoba

Použij po vzniku fungující části, při žádosti o prezentaci nebo před dokončením.
Profesní hodnotu ukaž na studentových rozhodnutích a ověřitelných výsledcích.
Nevyráběj tvrzení o samostatné práci, zkušenostech ani měřeních bez podkladů.

## Co má být vidět v projektu

- **Problém a výsledek:** stručně, co interpret umí, proč pracuje s AST
  a co je na návrhu zajímavé. Přesné příkazy, podporovaný Python a malý příklad.
- **Architektura:** studentův vlastní popis cesty text → AST → evaluace → výsledek;
  podstatné invarianty a hranice modulů. Diagram přidej jen pokud pomůže.
- **Rozhodnutí a omezení:** například volba měření kroků, parseru, validace,
  zásobníku a číselné reprezentace; alespoň jeden vysvětlený kompromis.
- **Důkazy kvality:** smysluplné testy, konkrétní opravená chyba, opakovatelné
  experimenty a vyzkoušený čistý export. Z jednoho běhu nevyvozuj asymptotiku.
- **Vlastní příspěvek:** pravdivě odděl návrh, kód a testy studenta od převzatých
  částí a asistentské pomoci. Průběžné Git commity mohou doložit skutečný vývoj;
  nevytvářej zpětně smyšlenou historii. Interní záznam výuky nemusí být veřejný.

Přípravu portfolia drž odděleně od školního kontrolního seznamu. CI nebo
veřejná ukázka může být pozdější rozšíření podle zájmu; bez nich lze splnit zadání.
Před veřejným sdílením vynech studentské číslo a další osobní údaje z veřejné
varianty, pokud je uživatel nechce zveřejnit. Školní archiv má vlastní požadavky.
Nic sám nepublikuj ani neodesílej bez odpovídajícího pokynu uživatele.

## Nácvik demonstrace

Nech studenta vybrat a samostatně předvést krátkou souvislou ukázku:

1. Vysvětlit účel, konstrukce a vlastní návrh AST.
2. Z textové definice spustit úspěšný výpočet a ukázat jeho výsledek a statistiky.
3. Předvést neplatnou aritu a pak minimalizaci zastavenou limitem.
4. Vysvětlit matematiku vlastní F, její definiční obor a význam neukončeného běhu.
5. Ukázat relevantní test a provést malou neočekávanou změnu definice nebo testu.

Jednotlivé kroky během nácviku dávkuj jako samostatné otázky. Scénář nepovažuj
za memorovaný text; ptej se na konkrétní kód a nové situace. Uživatelova
neznalost se má změnit v další učební úkol, nikoliv v asistentem odříkanou odpověď.

## Otázky ověřující schopnosti

Vybírej podle dosavadních mezer, nepokládej je všechny současně:

- Jak přidáš novou odvozenou funkci, aniž by ses dotkl evaluátoru?
- Kde a proč odmítneš nesprávné arity ještě před spuštěním?
- Co se stane, pokud kandidát minimalizace nedokončí vlastní podvýpočet?
- Může násobení nulou vždy přeskočit druhý operand? Jak to souvisí s parciálností?
- Jak poznáš, zda padá parser, evaluátor, nebo matematická definice?
- Co tvůj rozpočet kroků měří a co se z něj naopak o skutečném čase nedozvíme?
- Jaký test by odhalil chybu, kterou současná sada ještě nezachytí?
- Co jsi změnil po zjištění chyby a jak jsi ověřil, že oprava funguje?

Za připravenou demonstraci považuj tu, kterou student opravdu provedl
a vysvětlil. Psaný scénář a zelené testy nestačí k závěru o samostatné znalosti.
