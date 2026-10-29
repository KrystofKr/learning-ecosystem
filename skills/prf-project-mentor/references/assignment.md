# Zadání a hranice potvrzení

Původní zadání:
https://sites.google.com/view/7tilo-2026/semestr%C3%A1ln%C3%AD-pr%C3%A1ce

Následuje pracovní mapa požadavků podle kontroly v konverzaci 24. 10. 2026.
Při zahájení ověř původní stránku nebo dostupnou kopii se známým původem;
při nedostupnosti jasně označ aktuálnost jako neověřenou. Nestahuj opakovaně
stejný obsah v každém tahu, pokud nic nenaznačuje změnu. Z této mapy ani
z dřívějšího řešení asistenta neodvozuj souhlas vyučujícího.

## Závazné technické části

- Doména přirozených čísel včetně nuly; pro Python běžně `int` s validací.
- Datový model funkcí, nejméně ZERO, SUCCESSOR, PROJECTION, COMPOSITION,
  RECURSION a MINIMIZATION. Interpret vyhodnocuje AST, ne matematický zápis.
- Nulová funkce má stanovenou aritu; projekce index od 1 do n.
- Kompozice předá výsledky k vnitřních funkcí vnější funkci arity k;
  vnitřní funkce mají stejnou vstupní aritu.
- Primitivní rekurze: f(x,0)=g(x), f(x,j+1)=h(x,j,f(x,j)). Pro g arity k
  má h aritu k+2 a výsledek k+1. Ohlídej pořadí argumentů kroku.
- Minimalizace hledá poslední argument od 0; pokud výpočet kandidáta
  neskončí, nesmí jej přeskočit. Striktní evaluace musí zachovat parciálnost.
- Pojmenované funkce a opakované použití dříve definovaných AST.
  ADD, MULTIPLY, POWER a konstanty nesmějí obcházet konstrukce vlastními
  aritmetickými uzly. Konstruktor generující standardní AST je jiná věc
  než zvláštní algoritmus při evaluaci.
- Parser strukturálního formátu a detekce nevalidních projekcí a arit.
- Kontext s počtem kroků, maximální hloubkou a limitem. Přesně definovaná
  cena konstrukcí a jeden společný rozpočet pro všechny podvýpočty.
- Výsledek rozlišuje RESULT, STEP_LIMIT_EXCEEDED, INVALID_FUNCTION,
  INVALID_ARGUMENTS. Limit nedokazuje nedefinovanost; timeout jej nenahrazuje.
- Povinné odvozené funkce: Σ(x,y,z), quotient, equivalence, modulo, Π(x,y,z)
  a jejich pomocné funkce, všechny reprezentovatelné pomocí AST.
- Individuální F podle posledních tří číslic skutečného studentského čísla:
  a určuje spojení R/M/H, b rekurzivní R a pomocnou H, c tělo G;
  M=μz[G(x,y,z)=0]. R musí skutečně vzniknout rekurzí, M minimalizací.
  Konkrétní řádky vždy přečti z tabulek zadání; nehádej je z paměti.

## Nejasnosti, které nesmějí zmizet při výuce

1. **EQ:** místní skripta používají 1 při rovnosti; tabulky G hledají nulu
   ve výrazech `eq(...)`. Dřívější asistentská verze zavedla EQ0=abs(x-y),
   aby minimalizace hledala rovnost. To je zvolený výklad, nikoliv potvrzený
   význam zadání. Nejprve ověř definici ve skutečných podkladech; nepovažuj
   studentovu jinou konvenci automaticky za chybu. Nesmíš potichu zaměnit
   charakteristickou rovnost za vzdálenost. Při prozatímním výkladu zaznamenej
   volbu, důvod a dopad na výsledky i definiční obor.
2. **Σ a Π:** stránka je uvádí bez přesného rozepsání. Dřívější řešení je
   vyložilo jako x+y+z a x*y*z; neprohlašuj to za potvrzenou definici ani
   je automaticky zaměňuj za omezenou sumaci nebo součin.
3. **b=8/9:** uzavřené vzorce a uvedené rekurence si odporují. Rekurence
   se základem 0 a krokem R(x,j+1)=R(x,j)+j dává j*(j-1)/2, nikoliv
   uvedené lineární vzorce. Řeš pouze tehdy, když se týkají studentovy varianty;
   zaznamenej, zda byla upřesněna formule, nebo rekurence.
4. **COMBINATION:** samostatný operátor je na jednom místě nepovinný, ale
   objevuje se v povinných testech. COMBINE má rozporný popis řetězení
   a příklad (ADD,MULTIPLY,POWER)(2,3)=(5,6,8). Veď studenta k výslovnému
   návrhu sémantiky a testu; při n-tici řeš odlišení od skalárních PRF.
5. **Dělení nulou a 0^0:** konvenci ověř a zdokumentuj. V dosavadních
   podkladech/řešení je quotient(x,0)=0, modulo(x,0)=x a power(0,0)=1;
   označ, co skutečně doložil zdroj a co je návrhová volba.

Pokud zůstane rozpor otevřený, uč dál nezávislé konstrukce a připrav studentovi
konkrétní otázku k vyučujícímu. Netvrď úplnou shodu jen proto, že prochází testy.
Předběžná konvence dovoluje procvičování, nikoliv automatickou certifikaci.

## Odevzdání a dokumentace

Jeden `PRF_<studentske_cislo>.zip` s kořenovým projektem a README, src/, tests/,
functions/required.txt, functions/individual.txt, examples/input.txt a output.txt.
Samotný ukázkový individual.example.txt nenahrazuje individuální funkci studenta.
Projekt musí fungovat bez IDE; pro Python zvol reprodukovatelné spouštění
a běžný testovací příkaz, například `python -m unittest discover`.
Build konfigurace musí odpovídat zvoleným nástrojům. Nevkládej cache a soubory IDE.

README má obsahovat jméno a číslo, popis, spuštění programu a testů, vstupní
formát, AST, matematická pravidla/evaluaci konstrukcí, příklady a testy,
definici kroků, odvození a matematický zápis vlastní F, analýzu definovanosti
a terminace, experimentální výsledky a známá omezení. Výstup příkladu musí
odpovídat odevzdávané variantě. Zkontroluj i aktuální pokyny pro termín a e-mail;
příprava těchto souborů sama nepovoluje nic odeslat.

V tomto repozitáři může být podpůrným zdrojem
`ecosystem/data/studium/subjects/tilo/sources/custom-skripta-v3-xtilo/TILO_custom_skripta_7TILO_1-14_AUDIT_v3_XTILO.pdf`
od kořene ekosystému,
zejména strany 20–27. Nezaměňuj pomocná skripta za původní zadání a ověř obsah
konkrétní stránky, než jí podepřeš tvrzení.
