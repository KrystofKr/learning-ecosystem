# Knowledge Pipeline

Lokální nástroj pro sběr přepisů videí Justina Sunga a průběžné obohacování tematické studijní syntézy. Nový přepis se porovná s existujícím výkladem, užitečné detaily se doplní do příslušných kapitol a zpracovaný zdroj se přesune do archivu.
Veškeré soubory automatizace jsou soustředěné pod knowledge_pipeline/.

Veřejný obsah komponenty tvoří **kód, testy, dokumentace a ukázka konfigurace**. Přepisy, syntézy, historie, logy a místní nastavení patří do ignorovaných složek.

## Struktura

```text
src/learning_app/
  knowledge_pipeline/   Samostatný sběr, redakce, archiv a plánování
tests/                   Testy se syntetickými daty
scripts/                 Spouštěč, kontrola repozitáře a skripty Windows
config/knowledge_pipeline.example.json
docs/                    Architektura a provozní informace

data/                    Lokální data — mimo Git
  justin_sung/
    incoming/            Přepisy čekající na dokončení
    archive/             TXT, panely, úseky, metadata a společné seznamy
    synthesis/
      main.txt           Hlavní syntéza
      state/             Stav, rozpracovaná transakce a historie
config/knowledge_pipeline.local.json  Nastavení modulu — mimo Git
config/baseline.json     Výchozí seznam již známých videí — mimo Git
reports/audits/          Historické místní audity — mimo Git
```

## Instalace a první spuštění

Následující příkazy spusť ze složky knowledge_pipeline/. Vyžaduje Python 3.11+ v Linuxu nebo WSL. Pro skutečné obohacování je potřeba nainstalovaný Codex CLI a jeho funkční přihlášení. Místní oznámení a přiložený plánovač používají Windows/WSL.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/learning-knowledge init
.venv/bin/learning-knowledge access
```

`init` vytvoří prázdnou osnovu a místní úložiště. Existující data a stav nepřepisuje. Konfiguraci lze upravit v `config/knowledge_pipeline.local.json`; jediný vzor je [config/knowledge_pipeline.example.json](config/knowledge_pipeline.example.json). Relativní `data_dir` se vyhodnocuje vůči kořenu komponenty. Jiné umístění nastavíš přes `--root`, `--config`, `LEARNING_KNOWLEDGE_ROOT` nebo `LEARNING_KNOWLEDGE_CONFIG`. Podrobnosti popisuje [technická reference](docs/reference.md).

Před prvním stahováním zachyť současný seznam videí. Zůstanou označená jako již známá a automatizace bude stahovat teprve další přírůstky:

```bash
.venv/bin/learning-knowledge baseline
```

Přepisy a syntéza nejsou součástí checkoutu. Nové klonování začne prázdným úložištěm. Pro navázání na vlastní existující materiály potřebuješ odpovídající archiv, hlavní text a stavovou evidenci.

## Příkazy

```bash
.venv/bin/learning-knowledge check              # kontrola konzistence a čekající práce
.venv/bin/learning-knowledge access             # ověření placeného přístupu Codex bez modelového úkolu
.venv/bin/learning-knowledge update             # zpracování již uložených přepisů
.venv/bin/learning-knowledge run                # stažení, obohacení a archivace
.venv/bin/learning-knowledge scheduled          # týdenní kontrola a dohnání chyb
.venv/bin/learning-knowledge validate --panels  # kontrola všech uložených panelů a přepisů
```

Stejné CLI lze spustit přes `.venv/bin/python -B -m learning_app.knowledge_pipeline check`. Konfigurace vybírá cestu k Codexu, model, úsilí při uvažování, časový limit a datovou složku. Přihlašovací údaje Codexu se do projektu nekopírují.

**Podmínkou automatizace je aktuálně potvrzený placený plán přes účet ChatGPT.** Při neaktivním předplatném, přihlášení přes API klíč nebo neověřitelném přístupu se zpracování zastaví před stahováním a změnami syntézy. Důvod se vypíše, uloží a předá do místního upozornění Windows. API platba se jako náhrada nepoužije. Podrobnosti a obnovení práce popisuje [podmínka předplatného](docs/codex-subscription-guard.md).

## Windows plán

Plán kontroluje videa každé pondělí v 09:00 pražského času a zmeškaný nebo neúspěšný běh dohání po přihlášení. Počítač musí být zapnutý a dostupná relace Windows. Nativní plán předpokládá pražskou časovou zónu Windows.

V PowerShellu spusť skript z checkoutu a zadej vlastní Linuxovou cestu:

```powershell
.\scripts\windows\install_task.ps1 -ProjectRoot '/path/to/learning_ecosystem/knowledge_pipeline' -Distribution 'Ubuntu'
```

Parametr `-LinuxUser` je volitelný. Aktualizace existující úlohy vyžaduje výslovný parametr `-Replace`. Instalační skript používá aktuální účet Windows; SID ani uživatelské cesty se neverzují. Podrobnosti jsou v [technické referenci](docs/reference.md#windows-a-wsl).

## Testy a příprava push

```bash
.venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'
cd ..
git init -b main
git add .
knowledge_pipeline/.venv/bin/python knowledge_pipeline/scripts/check_repository.py --root .
git diff --cached --stat
```

Kontrola čte skutečný obsah Git indexu a odmítne lokální data, konfiguraci a rozpoznatelné formáty přihlašovacích tokenů. `.gitignore` vynechává celé datové a reportovací složky včetně budoucích výstupů. Společné GitHub Actions v nadřazené složce spouští lokální testy bez skutečných přepisů, sítě nebo přihlášení k modelu.

## Stav a omezení

Zpracování používá dvě modelová volání a programové kontroly časových opor, struktury a konzistence. Zachovává dosavadní výklad a ukládá obnovitelné transakce. Obsahová kontrola modelem není nezávislá odborná ani vědecká revize.

Lokální testy a úplná kontrola dosavadního archivu prošly. Úspěšný ostrý běh YouTube → model → archiv v auditním prostředí nebyl doložen. Rozhraní pro integraci, architekturu, provozní stav a obnovu popisuje jediná [technická reference](docs/reference.md). Přepisy, syntézy a osobní historie se při klonování nepřenášejí.
