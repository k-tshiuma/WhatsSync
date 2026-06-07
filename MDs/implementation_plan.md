# Implementation Plan: Standalone Desktop Application

This plan outlines the technical design, framework choices, and code changes required to convert the terminal-based WhatsApp automation application into a standalone Windows desktop application (`.exe`).

## Design & Architecture Overview

Instead of running interactive command prompts in the terminal, we will build a graphical interface using **CustomTkinter** (a modern, styled Tkinter wrapper with built-in dark mode and high-resolution support).

```mermaid
graph TD
    subgraph GUI Thread (Main Thread)
        A[App Window] -->|Select Label / Trigger| B[Orchestration Controls]
        C[Log Monitor] <--|Updates via Custom Event / Queue| E
    end
    subgraph Background Automation Thread
        E[Automation Runner] -->|1. Fetch Contacts| F[Google Contacts API]
        E -->|2. Validate/Normalize| G[Phonenumbers Parser]
        E -->|3. Open Browser| H[WhatsApp Automation Webdriver]
    end
```

### Key Architectural Enhancements
1.  **Threaded Execution**: Running automation inside a background thread allows the GUI to remain responsive, updates the status text box in real-time, and lets the user cancel execution or monitor progress easily.
2.  **Robust Path Handling**: Adjust path configurations so resources (like Jinja templates, flyers, or session tokens) are resolved relative to the user's execution directory or bundled inside PyInstaller's temporary directories.
3.  **Real-Time Logging Console**: An embedded log panel inside the window displaying info logs, skipped numbers, progress ratios, and errors.

---

## Proposed Changes

We will introduce a desktop entry point, refactor path-dependent code, and update configuration files.

### 1. Requirements

#### [MODIFY] [requirements.txt](file:///c:/xampp/htdocs/DEV-PRI/WHATSAPP/requirements.txt)
*   Add `customtkinter` and `pyinstaller` dependencies.

### 2. Base Configuration & Session Directories

#### [MODIFY] [settings.py](file:///c:/xampp/htdocs/DEV-PRI/WHATSAPP/app/config/settings.py)
*   Update paths to resolve correctly inside a packaged executable environment.
*   Redirect `whatsapp_session` and `data` folder caches to safe, persistent local directories (e.g., standard `%APPDATA%` on Windows or the directory of the executable), avoiding deletion when the application closes.

### 3. Desktop GUI Entrypoint

#### [NEW] [gui.py](file:///c:/xampp/htdocs/DEV-PRI/WHATSAPP/app/gui.py)
*   Create a modern multi-pane layout using CustomTkinter:
    *   **Settings Panel**: Allows configuring `WHATSAPP_GROUP_INVITE_LINK`, `DEFAULT_COUNTRY`, and `FLYER_PATH` directly in the UI.
    *   **Google Contacts Panel**: Lists available groups with a reload button and allows selecting the target label.
    *   **Execution Panel**: Shows active progress bars, recipient count, and real-time logs.
    *   **Control Panel**: "Start Automation", "Pause/Resume", and "Open Browser" buttons.

### 4. Integration Updates

#### [MODIFY] [google_contacts.py](file:///c:/xampp/htdocs/DEV-PRI/WHATSAPP/app/integrations/google_contacts.py)
*   Refactor `get_google_credentials` to locate `client_secrets.json` and output `token.pickle` relative to the safe persistent directory.

#### [MODIFY] [whatsapp_automation.py](file:///c:/xampp/htdocs/DEV-PRI/WHATSAPP/app/integrations/whatsapp_automation.py)
*   Ensure that selenium uses the new persistent user-data path and handles background threads properly.

---

## Verification Plan

### Automated Build Testing
*   Verify code compilation and library imports.
*   Run PyInstaller compile command:
    ```powershell
    pyinstaller --noconfirm --onedir --windowed --add-data "templates;templates" app/gui.py
    ```

### Manual Verification
1.  Launch the compiled executable (`gui.exe`).
2.  Verify labels load into the dropdown list from Google Contacts.
3.  Choose a contact label and press the "Start Automation" button.
4.  Confirm that the Chrome browser opens, waits for the WhatsApp login verification, and progresses through sending group invitation messages while the GUI log panel updates in real-time.
