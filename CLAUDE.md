# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows desktop tool that pulls contacts from a chosen Google Contacts label, normalizes and dedupes their phone numbers, and sends each contact a templated WhatsApp registration message (with an optional flyer image) by driving WhatsApp Web through Selenium/Chrome. The current UI is a CustomTkinter app (`app/gui.py`). `app/main.py` is the older interactive console version of the same flow and is kept as a fallback.

## Commands

Run everything from the repo root, because all imports are absolute (`from app.…`). Use the existing `.venv`.

```powershell
.venv\Scripts\activate
pip install -r requirements.txt

python -m app.gui     # desktop GUI (primary entry point)
python -m app.main    # legacy console flow

# Build the Windows bundle (one-dir, windowed) into dist/gui/
pyinstaller --noconfirm gui.spec
```

The repo has no test suite, linter or formatter config. `gui.spec` is gitignored (`*.spec`), so changes to it are local only.

## Architecture

Pipeline (the GUI and the console version run the same steps):
1. `integrations/google_contacts.py`: OAuth via `client_secrets.json` with `InstalledAppFlow`. The token is cached in `data/token.pickle` (read-only contacts scope). `get_contacts_from_label` lists **all** connections and filters by `contactGroupMembership` on the client side. It prefers the `mobile` number and otherwise uses the first one.
2. `services/contact_service.py`: normalizes numbers to E.164 with `phonenumbers` using `DEFAULT_COUNTRY`, and dedupes by normalized number.
3. `services/whatsapp_message_builder.py`: renders the Jinja2 template `templates/registration_message.txt` with `name` and `group_link`. If rendering fails, it falls back to a hardcoded message.
4. `integrations/whatsapp_automation.py`: Selenium Chrome with a persistent `--user-data-dir` (the WhatsApp session folder), so the QR login is kept between runs. It opens `web.whatsapp.com/send?phone=…`, types the message, then attaches the flyer through the hidden file `<input>`. It uses `webdriver_manager` to get chromedriver. The DOM selectors break easily: each step tries a list of fallback CSS/XPath selectors (`data-testid`, `data-icon`, `aria-label`). When WhatsApp Web changes, add new selectors to those lists instead of replacing them.

Configuration (`app/config/settings.py`):
- A pydantic-settings `Settings` singleton loaded from `.env`. `find_env_file()` checks the base path, then up to two parent directories, then the current working directory. `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `WHATSAPP_GROUP_INVITE_LINK` are required, so importing anything from `app` fails if they are missing.
- There are two path roots, and the difference matters for the PyInstaller build:
  - `BASE_PATH`: the directory containing `.env`, or the exe/repo root. Use it for user/runtime files: `.env`, `client_secrets.json`, `data/`, the WhatsApp session and the flyer.
  - `APP_PATH`: `sys._MEIPASS` when frozen. Use it only for bundled read-only resources (`templates/`).
- Use `RESOLVED_WHATSAPP_SESSION_PATH` and `RESOLVED_FLYER_PATH` rather than the raw settings. The flyer resolver also tries `.jpg`/`.jpeg`/`.png`/`.gif` variants.
- The GUI's "Save settings" button rewrites `WHATSAPP_GROUP_INVITE_LINK`, `FLYER_PATH` and `DEFAULT_COUNTRY` in `.env` in place and changes the `settings` singleton at runtime.

GUI (`app/gui.py`, a single `App(ctk.CTk)` class):
- Sidebar pages (dashboard, settings, placeholders) are switched with `switch_page`. The theme colors are module-level constants at the top of the file.
- Slow work (loading labels, the automation run) runs in daemon `threading.Thread`s. Widget updates from worker threads must go through `widget.after(0, …)`.
- `TextboxLogHandler` is attached to the root logger and mirrors all `logger` output into the in-app terminal. Log lines are prefixed (`SYSTEM:`, `SYNC:` …) by convention.
- Pause and stop are cooperative: `_automation_worker` polls the `self.is_paused` / `self.is_stopped` flags between steps and during its sleeps. Any new long-running step must check these flags too.

Logging (`app/utils/logger.py`) writes to `logs/app.log`, relative to the **current working directory**, and to the console.

## Local, gitignored state

`.env`, `client_secrets.json`, `data/token.pickle`, `whatsapp_session/` (the Chrome profile), `logs/`, `build/` and `dist/` are all gitignored. Delete `data/token.pickle` to force Google re-authentication, and delete `whatsapp_session/` to force a new WhatsApp QR login. Design notes and implementation plans are in `MDs/`.
