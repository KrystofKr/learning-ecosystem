# Milníky vlastní implementace

Milníky jsou mapa závislostí, nikoliv povinný rozvrh na pevný počet dní.
Nezadávej celou tabulku jako jednu domácí úlohu. Aktuální úkol zmenši podle
studentovy úrovně a navazuj na vlastní kód. Tyto poznámky neobsahují hotové
implementace ani úplné AST odvozených funkcí.

| Milník | Co student vytvoří | Jak ověřit porozumění |
| --- | --- | --- |
| 1. Specifikace a základy | Vlastní seznam požadavků, konvencí, vstupů a stavů výsledku; malé ruční příklady | Rozliší funkci od její reprezentace, aritu od hodnoty, limit od nedefinovanosti |
| 2. Minimální svislý průchod | ZERO, SUCCESSOR, PROJECTION, jednoduché evaluate a první smysluplné testy | Přidá nový příklad, odmítne neplatný vstup a vysvětlí reprezentaci |
| 3. Kompozice a sdílení | Obecná kompozice, kontroly arit, pojmenování; vlastní rozhodnutí o kombinaci | Ručně odvodí pořadí podvýpočtů, sestaví dosud neviděnou funkci |
| 4. Kontext a výsledky | Počítání kroků, hloubka, limit a jednotné chyby v dosud existujících uzlech | Spočítá malý běh, vysvětlí přesnou hranici limitu a sdílení rozpočtu |
| 5. Primitivní rekurze | Obecné schéma, vlastní ADD, PRED a MULTIPLY z konstrukcí | Vysvětlí parametry h a odvodí jinou funkci bez přidání aritmetického uzlu |
| 6. Minimalizace | Postupné kandidáty, úspěšný a nekončící případ | Obhájí, proč nesmí přeskočit nedefinovaný kandidát ani limit nazvat důkazem neterminace |
| 7. Parser | Malou gramatiku, parser do téhož AST, reference a diagnostiku | Odliší lexikální, syntaktickou, validační a vstupní chybu; nepoužije eval |
| 8. Povinná knihovna | Funkce required.txt a potřebné pomocné konstrukce | Každou odvodí matematicky, vysvětlí arity a zvolí hraniční testy |
| 9. Individuální varianta | Vlastní R, G, M, H, F, individual.txt a analýzu oboru | Dosadí skutečné číslice, ručně vyřeší příklad a vysvětlí výskyt skutečné μ |
| 10. Odevzdání a demonstrace | Spustitelný čistý archiv, README, experimenty a ukázku | Spustí projekt v čisté složce a zvládne obhajobu i malou změnu |

## Jak z milníku udělat lekci

Vyber nejmenší krok s viditelným výsledkem. Příklady vhodných zadání:

- „Navrhni datová pole projekce a uveď dva neplatné konstruktory. Zatím nepiš evaluátor.“
- „Na tomto malém AST ručně napiš, co dostane vnější funkce. Potom doplň jen kompozici.“
- „Pro tebou zvolenou definici kroku najdi nejmenší limit, při němž tento běh uspěje.“
- „Tento test padá. Nejdřív rozhodni, jestli je vadná očekávaná hodnota, nebo program.“
- „Bez nového typu uzlu sestav jednoduchou funkci jinou než tu z předchozího příkladu.“

Správnou odpověď k zadání nepřidávej před studentovým pokusem. Malý úspěch
uzavři vysvětlením nebo úlohou s jiným vstupem/strukturou, nikoliv automatickým
přidáním celého dalšího modulu.

## Rozhodnutí, která patří studentovi

- Datové objekty vs. jiná reprezentace; neměnnost, sdílení a validační invarianty.
- Oddělení syntaxe, evaluace, knihovny a vstupů/výstupů. Průběžně ukaž důsledek
  propojení, ale nepředepisuj názvy všech tříd a souborů podle starého řešení.
- Co přesně stojí krok a jakou hloubku měří; jak zachovat limit uvnitř každého
  podvýpočtu. U velmi hlubokých AST prober limit zásobníku Pythonu a alternativy.
  Explicitní zásobník může být pozdější zlepšení, nikoliv podmínka první lekce.
- Jakou gramatiku a vazby názvů podporuje. Parser zaváděj nad fungujícím API,
  aby chyba parseru nemaskovala chybu matematického modelu.
- Chování chybných argumentů, nevalidního AST a neexistujících referencí.
- Jak testovat známé hodnoty, nulu, arity, přesné hranice rozpočtu a parciálnost;
  proč jeden souhlasný příklad ani počet testů neprokazuje správnost.

Postup může být jednodušší než předchozí asistentovo řešení. Nevyžaduj
generátor všech studentských variant, webové UI, cloud, databázi, plugin ani
přepis Turingových simulátorů; zadání je nepotřebuje. Řeš studentovu konkrétní F.
Pro portfolio přidávej jen vlastnosti s jasně vysvětlitelným přínosem.
