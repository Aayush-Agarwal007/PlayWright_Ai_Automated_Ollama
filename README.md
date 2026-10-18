# SmartForm AI — Local Google Forms Autofill Assistant

Desktop app (PySide6) that analyzes a Google Form open in **your own Brave** (via Playwright/CDP),
fills what it can from `profile.json`, drafts open-ended answers with a **local Ollama** model, shows
everything for review, and **never clicks Submit/Send** — you do that.

## 1. Install (PowerShell, Windows 11, Python 3.11)

```powershell
cd path\to\smartform_ai
python -m venv .venv            # skip if your existing .venv is already here
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Playwright attaches to your running Brave, so `playwright install` is **not** required.

Copy your existing `profile.json` and `config.json` into this folder (they are never overwritten).
If `profile.json` is missing, one is created from `profile.example.json`.

## 2. Ollama
```powershell
ollama serve            # if not already running
ollama list             # should show qwen2.5:3b
ollama pull qwen2.5:3b  # only if missing
```

## 3. Brave with remote debugging
Close **all** Brave windows first, then:
```powershell
& "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222
```
Sign in to Google in that window if the form needs it. Check: open http://127.0.0.1:9222/json/version.

## 4. Launch
```powershell
python app.py
```
Header dots show Ollama / model / Brave status. Paste the form URL → **Analyze Form** → review the table
(select a row to edit, confirm, skip, pick an option, or regenerate a draft) → **Fill Form** → review in
Brave → submit yourself.

## How answers are chosen
1. Deterministic aliases (email/name/phone/branch/batch/…; no AI call needed).
2. Ambiguous wording → Ollama classifies into an allow-listed canonical field (validated in Python;
   low confidence is discarded).
3. Profile value present → used (`Profile`). For choices only real on-page options are selected; close but
   inexact matches are marked **Needs review**.
4. Open-ended + no profile value → short AI draft (`AI draft`, yellow). Only non-sensitive context
   (skills, projects, interests…) is sent to the local model.
5. Unknown factual questions (CGPA, dates, "did you get a shortlisting mail", declarations…) are **never**
   invented → `Needs review`.
Enrollment / roll / registration numbers are separate fields and are not interchangeable.

## Status of support
| Control | State |
|---|---|
| Short answer, paragraph, email/phone/number, date input | implemented |
| Radio, checkbox, linear scale (as radio), native + Google custom dropdown | implemented |
| Multiple-choice / checkbox grids, time pickers | detected, reported, **not filled** |
| File upload | detected, left pending (modular future phase) |
| Multi-section forms | current section only; move on manually and Analyze again |

After each fill the real field/selection state is read back; mismatches are reported as **Failed**.

## Tests
```powershell
python -m pytest -q
python -m compileall -q app.py services ui tests
```
Unit tests mock Ollama and the browser. **Live Google Forms / Brave interaction was not tested in
development** (no Brave/Playwright browser in the build sandbox): Google's DOM selectors are the most
likely thing to need adjustment (`services/form_inspector.py`, `services/form_filler.py`).

## Layout
`app.py` · `services/` (ollama, profile, matcher, planner, browser, inspector, filler, validation) ·
`ui/` (main window, review panel, profile & settings dialogs, workers) · `tests/` · `logs/smartform.log`
(field names and statuses only, no personal values).

Optional: `pip install pyinstaller` then `pyinstaller --noconsole --name SmartFormAI app.py`.
