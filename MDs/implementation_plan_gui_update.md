# Implementation Plan - WhatsApp Automation GUI Redesign

Redesign the Python CustomTkinter GUI (`app/gui.py`) to match the style and layout of the user's mockup image (`screen.png`). This involves updating the app to a multi-page interface with a professional dark theme, vibrant accents, metrics dashboards, modular configurations, and colored terminal outputs.

## User Review Required

> [!NOTE]
> **Page Navigation Structure**:
> - We will implement a modern left sidebar containing navigation items: **Dashboard**, **Google Sync**, **Automations**, **Campaigns**, and **History**.
> - **Dashboard**: Will match the mockup's metrics layout, Google integration selection, and the main automation console.
> - **Automations** / **Settings**: Will contain the configuration fields from the original application (invite link, default country, flyer path browse) so that users can still edit and save these settings to `.env`.

> [!IMPORTANT]
> **Real-time Logging Styling**:
> The mockup shows a colorful live terminal output. We will implement colored logging tags within the `CTkTextbox` log stream (e.g., green for `INFO`/`SUCCESS`, blue for `SYNC`, pink/red for `WARN`, cyan for `SYSTEM`, etc.).

## Proposed Changes

### GUI Redesign

#### [MODIFY] [gui.py](file:///c:/xampp/htdocs/DEV-PRI/WHATSAPP/app/gui.py)

- **Window Setup**: Resize window to `1200x800` (min size `1000x700`) to accommodate the new dashboard. Set custom dark theme colors directly on frames and widgets to bypass default Tkinter gray borders.
- **Color Palette Setup**:
  - Main Background: `#0B0F19` (Vibrant deep dark)
  - Sidebar Background: `#111625` (Dark slate)
  - Card/Container Background: `#182235` (Sleek card color)
  - Inputs & Dropdowns: `#1F2937`
  - Accent Green: `#10B981` (Emerald green)
  - Accent Blue: `#3B82F6` (Electric blue)
  - Accent Red: `#EF4444` (Vibrant red)
  - Text Primary: `#F3F4F6`
  - Text Muted: `#9CA3AF`
- **Sidebar Integration**:
  - Add Logo frame at top left with green `⚡` icon and title `Admin Panel`, subtitle `Power User`.
  - Add navigation menu buttons with modern hover-effects, bold styles, and subtle symbols.
  - Add bottom sidebar action "+ Start New Task", "Settings", and "Support".
- **Main View Layout**:
  - Header: Implement header with app title, version badge `V2.4.0`, status dot `System Active`, and action icons (bell, gear, user avatar).
  - Page Switcher: Create multiple sub-page frames. Hook sidebar buttons to switch between pages.
  - **Dashboard Page Content**:
    - **Metrics cards**: 3 columns at the top:
      1. Contacts Found (`12,842` or actual loaded counts +4.2%)
      2. Messages Sent (`8,921` or actual session counts +12%)
      3. Success Rate (`99.2%` or actual session calculations "Optimal")
    - **Middle Section**:
      - Left card: Google Integration. Dropdown for selecting contact label, sync status summary (Synced status, Last Sync, Label count), and a modern "REFRESH GOOGLE CONTACTS" button.
      - Right card: Automation Console. Displays sequence name, status indicator badge (e.g., IDLE/RUNNING), progress labels (`68%`, `2,789 / 4,102 Processed`), green progress bar, currently messaging text, control buttons (Start with Play, Pause, Stop), and session stats (Elapsed, Avg speed, Failures).
    - **Bottom Section**:
      - Live System Terminal: Dark terminal box with color-coded logging entries depending on level/log pattern (INFO, SYNC, SUCCESS, WARN, DEBUG, TASK, SYSTEM), dropdown selector for log verbosity, and clear logs button.
  - **Automations / Settings Page Content**:
    - Relocate input configurations: WhatsApp Group Invite Link, Flyer Image path attachment (with Browse button), Default Phone Country Code.
    - Add "Save Configurations" button to write values to `.env`.

### Dependencies & Setup

- Run `pip install customtkinter` inside the virtual environment to ensure the framework is fully installed.

## Verification Plan

### Automated Tests
- Run `python app/gui.py` to ensure the interface launches without errors.
- Validate logger output routing and configuration saving.

### Manual Verification
- Test selecting and loading Google Contact labels.
- Verify that clicking different sidebar tabs (Dashboard, Automations, Settings) updates the view correctly.
- Run the WhatsApp automation task flow to verify that it updates:
  - Progress bar & percentages
  - Status labels
  - Elapsed timer / Speed calculation
  - Color-coded logs in the terminal
  - Config values saved in `.env`
