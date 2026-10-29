# Rozhraní, architektura a obnova

Instalaci, příkazy a umístění dat popisuje [README](../README.md). Podmínky přístupu a řešení blokace jsou v [podmínce předplatného](codex-subscription-guard.md).

## Konfigurace

Kořen komponenty vybírá argument `--root` / argument API, potom `LEARNING_KNOWLEDGE_ROOT`, jinak kořen zdrojového checkoutu. Instalace z wheelu používá aktuální adresář. Při zadání nadřazené složky `learning_ecosystem/` se rozpozná vnořená komponenta `knowledge_pipeline/`.

Konfigurační soubor vybírá `--config` / argument API, potom `LEARNING_KNOWLEDGE_CONFIG`, jinak `config/knowledge_pipeline.local.json`. Relativní `data_dir` se vyhodnocuje vůči kořenu komponenty. Veřejný vzor je [knowledge_pipeline.example.json](../config/knowledge_pipeline.example.json).

Staré Python adaptéry `learning_app.*`, příkaz `learning-app`, proměnné `LEARNING_APP_*` a automatický výběr `config/local.json` byly odstraněny. Používej `learning-knowledge` nebo `python -m learning_app.knowledge_pipeline`.

## Python API

```python
from pathlib import Path
from learning_app.knowledge_pipeline import KnowledgePipeline
from learning_app.knowledge_pipeline.settings import load_settings

settings = load_settings(root=Path("/path/to/learning_ecosystem/knowledge_pipeline"))
pipeline = KnowledgePipeline(settings)
pipeline.initialize()
status = pipeline.check()
```

Veřejné operace jsou `initialize`, `capture_baseline`, `access`, `check`, `update`, `validate`, `run` a `scheduled`. Každá instance drží vlastní konfiguraci a editor; instance s různými knihovnami nesdílejí měnitelné nastavení. Nad stejnou knihovnou platí souborové zámky.

Editor lze vložit jako `KnowledgePipeline(settings, editor=...)`. Volatelný objekt přijímá `(task, payload, run_dir)` a vrací objekt podle [schémat PLAN/REVIEW](../src/learning_app/knowledge_pipeline/schemas.py). `run_dir` je existující absolutní adresář pro auditní výstupy.

Samostatný `CodexEditor` z [editor.py](../src/learning_app/knowledge_pipeline/editor.py) přijímá slovník s `codex_binary`, `model`, `reasoning_effort` a `timeout_seconds`. Import a vytvoření klienta nevyžadují YouTube knihovny ani nespouštějí model.

## Průchod dat

1. `downloader.py` přečte veřejnou kartu Videa a uloží přesný panel Show transcript. Známá videa určuje baseline, příchozí přepisy a ověřený archiv. Nestahuje audio/video ani nepoužívá přihlášení k YouTube nebo uživatelské doplňky yt-dlp.
2. `synthesis.py` předá nový přepis a dosavadní syntézu editoru. PLAN navrhne vložení do existujících kapitol a důvody výběru; REVIEW návrh zkontroluje. Python ověří doslovné citace, časové značky a strukturu. Dosavadní výklad se znovu neshrnuje.
3. `archive.py` uloží zdroj, obnoví CSV index, manifest a souhrnný přepis. Příchozí kopii odstraní až po zveřejnění textu i stavu.
4. `scheduler.py` hlídá pondělní hranici v `Europe/Prague` a opakuje neúspěšnou nebo čekající práci. `notifications.py` uloží chybu a předá místní oznámení Windows.

Trvalé atomické zápisy, synchronizaci souborů a kontrolní součty sdílí moduly přes `storage.py`. Editor běží neinteraktivně ve čtecím sandboxu s vypnutým shellem, webem, aplikacemi a dalšími agenty. Zápis výsledků řídí Python. Jedno video používá dvě modelová volání; oba průchody používají stejný model a nenahrazují nezávislou odbornou revizi.

## Konzistence a obnova

Před zpracováním se kontrolují index, manifest, souhrnný přepis, kontrolní součty, katalog syntézy a evidence zdrojů. `validate --panels` navíc porovná všechny uložené panely s přepisy a segmenty.

`synthesis/state/pending.json` označuje rozpracovanou transakci. Další `update` ji dokončí z již zkontrolovaného výsledku. Obnova vyžaduje ověřený přístup stejně jako nové zpracování. Ručně změněná evidence, kolize archivních souborů nebo zamítnutý návrh zpracování zastaví a zachovají zdroj pro dokončení.

Dokončená transakce v `synthesis/state/history/` obsahuje `before.txt`, `after.txt`, `before_state.json`, `after_state.json` a `completed.json`. Při obnově zálohy patří text a stav ze stejné dokončené transakce k sobě. Samotný připravený soubor nemusí být zveřejněnou verzí. Stav před přesunem komponenty z 26. 10. 2026 obsahuje staré absolutní cesty; pro současné umístění používej odpovídající dokončený stav po přesunu.

Provozní soubory jsou v `incoming/`: `status.json` určuje aktuální výsledek, `schedule_state.json` dokončené období, `access_state.json` poslední kontrolu přístupu a `run.log` průběh běhů. `ALERT.txt` uchovává poslední chybu i po pozdějším úspěchu. Historické místní audity jsou v `reports/audits/`.

## Windows a WSL

Instalátor z README vytváří úlohu pro aktuální účet bez zvýšených oprávnění. Parametry jsou `ProjectRoot`, `Distribution`, volitelný `LinuxUser` a `TaskName`. Virtuální prostředí očekává v `.venv`; plánovaný běh nic neinstaluje ani neaktivuje.

Úloha běží v pondělí v 09:00 pražského času a dohání práci minutu po přihlášení. Windows musí mít časovou zónu `Central Europe Standard Time`. Souběžné instance jsou zakázané a běh má limit 30 minut. Větší fronta může vyžadovat více běhů; hotové zdroje se neopakují. Je potřeba zapnutý počítač a dostupná relace Windows.

Při změně umístění nebo úpravě `launcher.ps1` přeregistruj úlohu pomocí
`install_task.ps1 -Replace`. Instalátor do akce úlohy ukládá kopii spouštěče;
samotná úprava souboru existující úlohu nezmění. Provozní cesta vede přímo
na kořen `knowledge_pipeline/` a jeho `scripts/run_scheduled.py`.

Kód 127 bez nového záznamu v `run.log` může způsobit chybné uvozování příkazu
WSL. Jeho parser porovnává přepínače před zpracováním uvozovek: například
`"--distribution"` nerozpozná jako přepínač a pokusí se jej spustit v Linuxu.
Spouštěč proto ponechává jednoduché argumenty bez uvozovek; cesty s mezerami
nadále uvozuje. Chování vychází z
[parseru přepínačů WSL](https://github.com/microsoft/WSL/blob/master/src/windows/common/WslClient.cpp)
a jeho [čtečky argumentů](https://github.com/microsoft/WSL/blob/master/src/windows/common/helpers.cpp).
Testy `test_windows_launcher.py` spouštějí skutečnou funkci PowerShellu bez
spuštění úlohy nebo WSL; vyžadují dostupný `pwsh` (ve Windows také `powershell.exe`).

## Napojení na Learning Ecosystem

Je-li nad komponentou přítomný `ecosystem.py` a `ecosystem/config/techniques.json`,
operace `update`, `run` a plánovač po uvolnění zámku dokončené syntézy automaticky
obnovují metodické podklady ekosystému. Nedokončenou synchronizaci zahrnuje
`check` do příznaku `pending` a plánovač ji opakuje. Synchronizace nevolá další
model, nepřepisuje studijní výsledky a samostatnou instalaci komponenty nemění.
Podrobnosti jsou v [provozu ekosystému](../../ecosystem/docs/operation.md).

## Ověření

Testy používají syntetické přepisy a řízené odpovědi sítě, modelu a Windows oznámení. Pokrývají souběh, obnovu po přerušení, integritu archivu, oddělení instancí a blokaci bez ověřeného předplatného. Úspěšný místní test nepotvrzuje ostrý průchod YouTube → Codex → archiv; ten potřebuje funkční síť, přihlášení a Windows/WSL prostředí.

