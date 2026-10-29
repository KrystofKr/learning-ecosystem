# Podmínka aktivního předplatného Codex

**Automatizace smí zpracovávat materiály pouze po aktuálním potvrzení placeného plánu přes přihlášení ChatGPT.** Samotné přihlášení, starý údaj v tokenu ani funkční API klíč nejsou potvrzením této podmínky.

Implementace: [subscription.py](../src/learning_app/knowledge_pipeline/subscription.py), [oznámení a stav](../src/learning_app/knowledge_pipeline/access_reporting.py). Podmínka je zapnutá vždy; lokální konfigurace neobsahuje přepínač pro její vypnutí.

## Co se ověřuje

Kontrola spustí vlastní krátkodobý Codex app-server přes stdio a provede pouze:

1. Inicializaci spojení.
2. account/read s požadavkem na obnovu přihlášení.
3. account/rateLimits/read pro aktuální plán a stav limitů.

Neposílá thread/start, turn/start ani uživatelský obsah a negeneruje modelovou odpověď. Přihlašovací údaje spravuje Codex; Python nekopíruje jeho tokeny a neukládá e-mail, identifikátor účtu ani surové odpovědi účtového rozhraní.

Podklady protokolu: [OpenAI Docs: account a limity](https://learn.chatgpt.com/docs/app-server), [OpenAI Docs: přihlášení](https://learn.chatgpt.com/docs/auth). Odpovídající struktury byly ověřeny také proti schématům vygenerovaným místní verzí CLI.

Přijímá se pouze známý placený typ plánu uvedený v živé odpovědi limitů Codexu. Free se zablokuje. Chybějící nebo neznámý typ se označí jako neověřený a běh se také zastaví. Za rozhodující se nepovažuje jen údaj z dříve uloženého přihlášení.

Jde o kontrolu aktuálně vykazovaného oprávnění služby, nikoli přímé čtení faktur nebo bankovních plateb. Pokud služba potřebný údaj neposkytne, automatizace zůstane pozastavená.

## Kde podmínka platí

| Operace | Chování |
|---|---|
| run | Kontrola před stahováním a před dalším novým videem. |
| scheduled | Kontrola před rozhodováním o čekající práci; blokace nepotvrdí dokončení týdne. |
| update | Kontrola před obnovou transakce a zpracováním místních přepisů. |
| PLAN a REVIEW | Opakovaná kontrola před spuštěním modelu. |
| Zveřejnění doplnění | Opakovaná kontrola před potvrzením změny syntézy. |
| baseline | Bez předplatného se ani nezačne načítat seznam videí. |
| init | Prázdné místní úložiště lze připravit; dokončení existující rozpracované transakce už vyžaduje kontrolu. |
| check, validate | Místní kontrola dat zůstává dostupná bez předplatného a bez sítě. |
| access | Výslovná kontrola přístupu bez stahování a modelového úkolu. |

Úloha Windows zůstává zaregistrovaná. Při dalším pokusu ověří přístup a případně oznámí pozastavení; materiály se při zamítnutí nezpracovávají. Při ztrátě přístupu během běhu se zastaví další chráněný krok. Již stažené podklady zůstanou pro pozdější navázání.

## Zastavení a oznámení

Blokace rozlišuje neaktivní předplatné, chybějící přihlášení, nevhodný způsob přihlášení, vyčerpaný limit a nemožnost ověřit přístup. Síťová chyba se neoznamuje jako potvrzené zrušení předplatného.

Důvod se vypíše do konzole a aplikace se pokusí zobrazit místní oznámení Windows. Současně uloží do místního incoming:

- ALERT.txt: čitelný důvod pozastavení.
- access_state.json: stav přístupu, čas kontroly a důvod blokace.
- status.json: výsledek pokusu blocked; není to úspěšné zpracování.

Při potvrzeném přístupu se aktualizuje access_state.json. ALERT.txt dál uchovává poslední problém; aktuální stav přístupu a běhu určují JSON záznamy.

Návratový kód 10 znamená, že bylo oznámení potvrzeno, 20 že se jeho doručení nepodařilo potvrdit. Odpovídá to již existujícímu Windows launcheru. Ani selhání oznámení nepovolí pokračovat; místní záznam a konzolový důvod zůstávají dostupné.

API klíče se do podřízených procesů nepředávají. Modelové spuštění navíc vyžaduje přihlášení ChatGPT. Neprobíhá automatický přechod na API platbu, jiný model ani obnovování předplatného.

## Obnovení práce

~~~bash
.venv/bin/learning-knowledge access
.venv/bin/learning-knowledge run
~~~

Po obnovení předplatného nebo přihlášení první příkaz ověří přístup. Druhý naváže na uloženou práci. Stejně může navázat příští plánovaný běh. Záznam blokace zaručí opakování i v týdnu, který byl předtím označený jako dokončený.

Kontrola má omezený čas a při chybě svůj proces ukončí. Nedostupná síť, neplatná odpověď, chybějící CLI či nepodporované rozhraní znamenají zastavení s příslušným důvodem.

## Ověření

Testy používají syntetické účty a vlastní místní proces napodobující účetní protokol. Ověřují Free proti starému Pro údaji, API klíč, výpadek, vyčerpaný limit, ukončení zavěšené kontroly, blokaci před stahováním i obnovou transakce a zachování syntézy při ztrátě přístupu před zápisem.

Tyto testy nevytvářejí skutečné modelové úkoly ani neposílají testovací oznámení do Windows.
