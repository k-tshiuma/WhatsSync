import os
import re
import json
import math
import time
import shutil
import threading
import logging
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
from app.config.settings import settings
from app.utils.logger import logger
from app.integrations.google_contacts import (
    get_people_service, list_contact_labels, get_contacts_from_label,
    load_cached_credentials, get_account_email, sign_out, client_secrets_present
)
from app.services.contact_service import analyze_contacts
from app.services.whatsapp_message_builder import render_custom_text
from app.integrations.whatsapp_automation import WhatsAppAutomation

# Light theme: WhatsApp green sidebar, white main panel
ACCENT = "#008069"            # Sidebar, primary buttons, progress (white text on it passes contrast)
ACCENT_HOVER = "#006D59"
ACCENT_DISABLED = "#8FC9BE"
ACCENT_BRIGHT = "#25D366"     # Status dots only, never behind text
SIDEBAR_HOVER = "#0F8F77"
BG = "#FFFFFF"
SURFACE = "#F0F2F5"           # Table headers, tiles, read-only fields
BORDER = "#E9EDEF"
INPUT_BORDER = "#D1D7DB"
TEXT = "#111B21"
TEXT_MUTED = "#54656F"
TEXT_PLACEHOLDER = "#8696A0"
SUCCESS_TEXT = "#0B5C4B"
DANGER = "#B0002A"
DANGER_BG = "#FDE7EC"
DANGER_BORDER = "#F1B5C3"
WARN_BG = "#FFF4D6"
WARN_TEXT = "#6B4200"
WARN_DOT = "#E9A100"
BUBBLE = "#D9FDD3"            # WhatsApp's outgoing message green, preview only
CHAT_BG = "#EFEAE2"
MEDIA_BG = "#C8D3CD"
AVATAR_BG = "#DFE5E7"

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"
# Widest the centered page content gets on a large window
CONTENT_MAX_WIDTH = 1140

# How long WhatsApp Web stays open after a run so sent messages can sync to the phone
SYNC_WAIT_SECONDS = 5 * 60

MODE_LABEL = "Contacts label"
MODE_GROUP = "WhatsApp groups"
STEP_ORDER = ["audience", "message", "review"]
STEP_NAMES = ["Audience", "Message", "Review and send"]

NAME_MAX = 80
MESSAGE_MAX = 4096
# WhatsApp's own caption limit
CAPTION_MAX = 1024
ATTACHMENT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".pdf", ".docx")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
# Formats Tk can show as a thumbnail without Pillow
THUMBNAIL_EXTENSIONS = (".png", ".gif")

COUNTRIES = [
    ("US", "United States"), ("GB", "United Kingdom"), ("IL", "Israel"), ("FR", "France"),
    ("IN", "India"), ("ZA", "South Africa"), ("CD", "DR Congo"), ("NG", "Nigeria"), ("KE", "Kenya"),
]

HISTORY_FILE = os.path.join(settings.BASE_PATH, "data", "history.json")
HISTORY_MAX = 200
TEMPLATE_FILE = os.path.join(settings.APP_PATH, "templates", "registration_message.txt")
DEFAULT_MESSAGE = (
    "Hello {{name}},\n\nRegistration for the new semester has started.\n\n"
    "Please see the attached flyer for details.\n\n"
    "Join the WhatsApp group here:\n{{group_link}}\n\nThank you."
)

# Recipient table: label and row tag per send status
STATUS_LABELS = {"queued": "Queued", "sending": "Sending", "sent": "Sent", "failed": "Failed", "skipped": "Skipped"}

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("green")

LOG_TAG_COLORS = {
    "INFO": TEXT,
    "SUCCESS": SUCCESS_TEXT,
    "SYNC": "#1F5FA8",
    "SYSTEM": TEXT_MUTED,
    "TASK": TEXT,
    "WARNING": "#8A5300",
    "ERROR": DANGER,
    "DEBUG": "#667781"
}

def configure_log_tags(textbox):
    for tag, color in LOG_TAG_COLORS.items():
        textbox.tag_config(tag, foreground=color)

def classify_log_line(msg, levelname=""):
    """Map a log line to a terminal styling tag by its prefix keyword or level."""
    if "SUCCESS" in msg:
        return "SUCCESS"
    elif "SYNC" in msg:
        return "SYNC"
    elif "SYSTEM" in msg:
        return "SYSTEM"
    elif "TASK" in msg:
        return "TASK"
    elif "WARN" in msg:
        return "WARNING"
    elif "ERROR" in msg or "CRITICAL" in msg:
        return "ERROR"
    elif levelname == "DEBUG" or "DEBUG:" in msg:
        return "DEBUG"
    return "INFO"

class TextboxLogHandler(logging.Handler):
    """Custom logging handler to stream records into a CustomTkinter Textbox safely from any thread."""
    def __init__(self, textbox):
        super().__init__()
        self.textbox = textbox

    def emit(self, record):
        # Classify on the full line (it carries the level name), but show just the time and message
        tag = classify_log_line(self.format(record), record.levelname)
        msg = f"{self.formatter.formatTime(record, '%H:%M:%S')}  {record.getMessage()}"
        # Safely schedule the UI write on the main thread
        self.textbox.after(0, self._safe_write, msg, tag)

    def _safe_write(self, msg, tag):
        try:
            self.textbox.configure(state="normal")
            self.textbox.insert("end", msg + "\n", tag)
            self.textbox.configure(state="disabled")
            self.textbox.see("end")
        except Exception:
            pass

# ----------------------------------------------------
# Small helpers
# ----------------------------------------------------
def load_message_template():
    try:
        with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        logger.warning(f"SYSTEM: Could not read the message template, using the built-in text: {e}")
        return DEFAULT_MESSAGE

def fill_placeholders(text, name):
    """Preview-only stand-in for render_custom_text: same placeholders, but silent while the user is mid-typing."""
    text = re.sub(r"\{\{\s*name\s*\}\}", lambda m: name, text)
    return re.sub(r"\{\{\s*group_link\s*\}\}", lambda m: settings.WHATSAPP_GROUP_INVITE_LINK or "", text)

def session_saved():
    # Chrome creates the Default profile folder the first time WhatsApp Web runs in this session folder
    return os.path.isdir(os.path.join(settings.RESOLVED_WHATSAPP_SESSION_PATH, "Default"))

def country_name(code):
    return dict(COUNTRIES).get(code, code)

def font(size=14, weight="normal", family=FONT_FAMILY):
    return ctk.CTkFont(family=family, size=size, weight=weight)

def make_card(parent, **kwargs):
    return ctk.CTkFrame(parent, fg_color=BG, border_color=BORDER, border_width=1, corner_radius=12, **kwargs)

BUTTON_STYLES = {
    "primary": dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF", border_width=0),
    "secondary": dict(fg_color=BG, hover_color=SURFACE, text_color=TEXT, border_color=INPUT_BORDER, border_width=1),
    "danger": dict(fg_color=BG, hover_color=DANGER_BG, text_color=DANGER, border_color=DANGER_BORDER, border_width=1),
    "link": dict(fg_color="transparent", hover_color=SURFACE, text_color=SUCCESS_TEXT, border_width=0),
}

def make_button(parent, text, command, kind="primary", **kwargs):
    options = dict(height=44, corner_radius=10, font=font(14, "bold"), text_color_disabled=TEXT_PLACEHOLDER)
    options.update(BUTTON_STYLES[kind])
    options.update(kwargs)
    return ctk.CTkButton(parent, text=text, command=command, **options)

def set_button_enabled(button, enabled, kind="primary"):
    # CTkButton keeps its fill when disabled, so fade primary buttons by hand
    button.configure(state="normal" if enabled else "disabled")
    if kind == "primary":
        button.configure(fg_color=ACCENT if enabled else ACCENT_DISABLED)

def make_label(parent, text="", size=14, weight="normal", color=TEXT, **kwargs):
    kwargs.setdefault("anchor", "w")
    kwargs.setdefault("justify", "left")
    return ctk.CTkLabel(parent, text=text, font=font(size, weight), text_color=color, **kwargs)

def make_entry(parent, **kwargs):
    return ctk.CTkEntry(parent, height=44, corner_radius=8, fg_color=BG, border_color=INPUT_BORDER, border_width=1,
                        text_color=TEXT, placeholder_text_color=TEXT_PLACEHOLDER, font=font(14), **kwargs)

def make_textbox(parent, height, **kwargs):
    options = dict(height=height, corner_radius=8, fg_color=BG, border_color=INPUT_BORDER, border_width=1,
                   text_color=TEXT, font=font(14), wrap="word")
    options.update(kwargs)
    return ctk.CTkTextbox(parent, **options)

def auto_wrap(label, container, padding=0):
    """Keep a label's text wrapped to its container's width as the window resizes."""
    def on_resize(event):
        scale = ctk.ScalingTracker.get_widget_scaling(label)
        label.configure(wraplength=max(80, int((event.width - padding * scale) / scale)))
    container.bind("<Configure>", on_resize, add=True)

def make_tree(parent, columns, height):
    """A light ttk table with a CTk scrollbar. columns: (key, heading, width, stretch)."""
    frame = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
    frame.grid_columnconfigure(0, weight=1)
    frame.grid_rowconfigure(0, weight=1)
    scale = ctk.ScalingTracker.get_widget_scaling(parent)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height, style="WS.Treeview", selectmode="browse")
    for key, heading, width, stretch in columns:
        tree.heading(key, text=heading.upper(), anchor="w")
        tree.column(key, width=int(width * scale), minwidth=int(40 * scale), stretch=stretch, anchor="w")
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ctk.CTkScrollbar(frame, command=tree.yview, fg_color="transparent", button_color=BORDER, button_hover_color=INPUT_BORDER)
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scrollbar.set)
    tree.tag_configure("sending", background="#FFF8E5")
    tree.tag_configure("failed", foreground=DANGER)
    tree.tag_configure("skipped", foreground=TEXT_MUTED)
    tree.tag_configure("muted", foreground=TEXT_MUTED)
    return frame, tree

class CenteredColumn(ctk.CTkFrame):
    """Transparent frame whose `inner` frame stays centered and at most max_width wide."""
    def __init__(self, master, max_width=CONTENT_MAX_WIDTH, **kwargs):
        super().__init__(master, fg_color="transparent", corner_radius=0, **kwargs)
        self.max_width = max_width
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.inner = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.inner.grid(row=0, column=1, sticky="nsew")
        self.bind("<Configure>", self._center)

    def _center(self, event):
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        pad = max(0, int((event.width - self.max_width * scale) / 2))
        self.grid_columnconfigure(0, minsize=pad)
        self.grid_columnconfigure(2, minsize=pad)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window settings
        self.title("WhatsSync")
        # Fit the window on smaller or high-DPI screens (geometry is in unscaled units, the screen size is not)
        scale = ctk.ScalingTracker.get_window_scaling(self)
        width = min(1200, int(self.winfo_screenwidth() / scale) - 40)
        height = min(800, int(self.winfo_screenheight() / scale) - 120)
        self.geometry(f"{width}x{height}+20+10")
        self.minsize(min(1050, width), min(700, height))
        self.configure(fg_color=BG)

        # Google labels and the selected audience
        self.labels_list = []
        self.label_by_display = {}
        self.mode = "label"
        self.audience = None            # analyze_contacts() result for the selected label
        self.audience_loading = False
        self.audience_error = None
        self._audience_request = 0
        self.google_signed_in = False

        # Campaign form and wizard state
        self.page = "send"
        self.phase = "audience"
        self.preview_index = 0
        self.attachment_path = ""
        self.thumbnail = None
        self._thumbnail_path = None
        self._preview_job = None
        self.show_recipient_list = False
        self.log_visible = True
        self.app_logs_visible = False

        # Automation run state
        self.run = None
        self.running_thread = None
        self.is_paused = False
        self.is_stopped = False
        self.sync_waiting = False
        self.skip_sync_wait = False
        # Closing the window mid-run must not leave the WhatsApp browser running: it would
        # hold the Chrome profile and make the next run fail to start
        self.active_wa = None

        self.history = self._load_history()

        self._setup_ttk_style()

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)  # Sidebar
        self.grid_columnconfigure(1, weight=1)  # Main panel

        self.create_sidebar()
        self.create_main()

        # Connect logging bridge
        self.setup_logging_bridge()

        self.show_page("send")
        self.show_phase("audience")
        self.refresh_session_status()

        # Load Google Contact labels once mainloop runs: a worker calling after() before then raises
        # "main thread is not in main loop" (happens in the faster-starting exe)
        self.after(200, self.load_labels_async)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _ui(self, fn, *args):
        """Run fn on the Tk main thread; worker threads must not touch widgets directly."""
        self.after(0, fn, *args)

    def _setup_ttk_style(self):
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("WS.Treeview", background=BG, fieldbackground=BG, foreground=TEXT,
                        rowheight=int(36 * scale), font=(FONT_FAMILY, -int(14 * scale)), borderwidth=0, relief="flat")
        # Negative sizes are pixels: ttk fonts don't follow CTk's scaling on their own
        style.configure("WS.Treeview.Heading", background=SURFACE, foreground=TEXT_MUTED,
                        font=(FONT_FAMILY, -int(12 * scale), "bold"), relief="flat", borderwidth=0, padding=(8, 8))
        style.map("WS.Treeview.Heading", background=[("active", BORDER)])
        style.map("WS.Treeview", background=[("selected", "#E7F5F1")], foreground=[("selected", TEXT)])
        # Drop the default sunken border around the table
        style.layout("WS.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    # ----------------------------------------------------
    # Sidebar
    # ----------------------------------------------------
    def create_sidebar(self):
        sidebar = ctk.CTkFrame(self, fg_color=ACCENT, corner_radius=0, width=220)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, padx=26, pady=(28, 24), sticky="w")
        ctk.CTkLabel(brand, text="W", width=34, height=34, corner_radius=10, fg_color="#FFFFFF",
                     text_color=ACCENT, font=font(18, "bold")).pack(side="left")
        ctk.CTkLabel(brand, text="WhatsSync", text_color="#FFFFFF", font=font(19, "bold")).pack(side="left", padx=(12, 0))

        self.nav_buttons = {}
        for i, (key, text) in enumerate([("send", "Send"), ("history", "History"), ("settings", "Settings")]):
            btn = ctk.CTkButton(
                sidebar, text=text, anchor="w", height=44, corner_radius=10,
                fg_color="transparent", hover_color=SIDEBAR_HOVER, text_color="#FFFFFF",
                font=font(15), command=lambda k=key: self.show_page(k)
            )
            btn.grid(row=i + 1, column=0, padx=16, pady=3, sticky="ew")
            self.nav_buttons[key] = btn

    # ----------------------------------------------------
    # Main panel: header and pages
    # ----------------------------------------------------
    def create_main(self):
        main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(main, fg_color=BG, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header_col = CenteredColumn(header)
        header_col.pack(fill="x")
        inner = header_col.inner
        inner.grid_columnconfigure(0, weight=1)

        titles = ctk.CTkFrame(inner, fg_color="transparent")
        titles.grid(row=0, column=0, padx=32, pady=20, sticky="w")
        self.page_title_lbl = make_label(titles, size=22, weight="bold")
        self.page_title_lbl.pack(anchor="w")
        self.page_sub_lbl = make_label(titles, size=13, color=TEXT_MUTED)
        self.page_sub_lbl.pack(anchor="w")

        chips = ctk.CTkFrame(inner, fg_color="transparent")
        chips.grid(row=0, column=1, padx=32, pady=20, sticky="e")
        self.google_chip = self._make_chip(chips, "Google · Checking...", TEXT_PLACEHOLDER)
        self.google_chip[0].pack(side="left", padx=(0, 8))
        self.whatsapp_chip = self._make_chip(chips, "WhatsApp · Checking...", TEXT_PLACEHOLDER)
        self.whatsapp_chip[0].pack(side="left")

        tk.Frame(main, height=1, bg=BORDER).grid(row=1, column=0, sticky="ew")

        self.page_host = ctk.CTkFrame(main, fg_color=BG, corner_radius=0)
        self.page_host.grid(row=2, column=0, sticky="nsew")
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)

        self.pages = {
            "send": self.create_send_page(),
            "history": self.create_history_page(),
            "settings": self.create_settings_page(),
        }

    def _make_chip(self, parent, text, dot_color):
        """Status pill (dot + text) that opens Settings when clicked."""
        chip = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=20, height=40, cursor="hand2")
        dot = ctk.CTkFrame(chip, width=8, height=8, corner_radius=4, fg_color=dot_color)
        dot.pack(side="left", padx=(14, 8), pady=16)
        lbl = ctk.CTkLabel(chip, text=text, font=font(13), text_color=TEXT, cursor="hand2")
        lbl.pack(side="left", padx=(0, 14))
        for widget in (chip, dot, lbl):
            widget.bind("<Button-1>", lambda e: self.show_page("settings"))
        return chip, dot, lbl

    def _set_chip(self, chip, text, dot_color):
        chip[1].configure(fg_color=dot_color)
        chip[2].configure(text=text)

    def new_page(self):
        """A scrolling page whose content is centered; returns (page, body)."""
        page = ctk.CTkScrollableFrame(self.page_host, fg_color=BG, corner_radius=0,
                                      scrollbar_button_color=BORDER, scrollbar_button_hover_color=INPUT_BORDER)
        column = CenteredColumn(page)
        column.pack(fill="x", expand=True)
        body = ctk.CTkFrame(column.inner, fg_color="transparent")
        body.pack(fill="x", padx=32, pady=(28, 48))
        body.grid_columnconfigure(0, weight=1)
        return page, body

    def show_page(self, key):
        for page_key, page in self.pages.items():
            if page_key != key:
                page.grid_forget()
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        self.page = key

        for nav_key, btn in self.nav_buttons.items():
            if nav_key == key:
                btn.configure(fg_color="#FFFFFF", hover_color="#FFFFFF", text_color=ACCENT, font=font(15, "bold"))
            else:
                btn.configure(fg_color="transparent", hover_color=SIDEBAR_HOVER, text_color="#FFFFFF", font=font(15))

        if key == "settings":
            self.refresh_account_status_async()
            self.refresh_session_status()
            if self.app_logs_visible:
                self.load_log_file()
        elif key == "history":
            self.render_history()
        self.update_header()

    def update_header(self):
        if self.page == "history":
            title, sub = "History", "Every campaign you have run, newest first."
        elif self.page == "settings":
            title, sub = "Settings", "Accounts, sending defaults and logs."
        elif self.phase == "running":
            title, sub = "Sending", "Keep this window open until WhatsApp closes."
        elif self.phase == "done":
            title, sub = "Campaign results", "What happened with each recipient."
        else:
            title, sub = "New campaign", "Send a WhatsApp message to a Google Contacts label or to your groups."
        self.page_title_lbl.configure(text=title)
        self.page_sub_lbl.configure(text=sub)

    # ----------------------------------------------------
    # Page: Send (audience -> message -> review -> run)
    # ----------------------------------------------------
    def create_send_page(self):
        page, body = self.new_page()
        self.send_page = page

        self.create_steps(body)
        self.phase_frames = {
            "audience": self.create_audience_step(body),
            "message": self.create_message_step(body),
            "review": self.create_review_step(body),
            "run": self.create_run_view(body),
        }
        return page

    def show_phase(self, phase):
        self.phase = phase
        for frame in self.phase_frames.values():
            frame.grid_forget()
        key = "run" if phase in ("running", "done") else phase
        self.phase_frames[key].grid(row=1, column=0, sticky="ew")

        if phase in STEP_ORDER:
            self.steps_frame.grid(row=0, column=0, pady=(0, 24), sticky="w")
            self.update_steps()
        else:
            self.steps_frame.grid_forget()

        if phase == "message":
            self.update_preview()
        elif phase == "review":
            self.fill_review()
        self.update_header()
        try:
            self.send_page._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    def create_steps(self, parent):
        self.steps_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.step_widgets = []
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        size = int(28 * scale)
        for i, name in enumerate(STEP_NAMES):
            # Plain canvas: a CTkLabel pads its text by the corner radius, so it can't be a true circle
            circle = tk.Canvas(self.steps_frame, width=size, height=size, bg=BG, highlightthickness=0)
            circle.create_oval(0, 0, size - 1, size - 1, tags="dot", width=0)
            circle.create_text(size / 2, size / 2, text=str(i + 1), tags="num", font=(FONT_FAMILY, -int(13 * scale), "bold"))
            circle.pack(side="left")
            lbl = ctk.CTkLabel(self.steps_frame, text=name, font=font(14))
            lbl.pack(side="left", padx=(10, 0))
            for widget in (circle, lbl):
                widget.bind("<Button-1>", lambda e, idx=i: self.go_to_step(idx))
            if i < len(STEP_NAMES) - 1:
                tk.Frame(self.steps_frame, width=int(40 * scale), height=1, bg=INPUT_BORDER).pack(side="left", padx=int(14 * scale))
            self.step_widgets.append((circle, lbl))

    def update_steps(self):
        current = STEP_ORDER.index(self.phase)
        for i, (circle, lbl) in enumerate(self.step_widgets):
            reached = i <= current
            circle.itemconfigure("dot", fill=ACCENT if reached else SURFACE)
            circle.itemconfigure("num", text="✓" if i < current else str(i + 1), fill="#FFFFFF" if reached else TEXT_MUTED)
            circle.configure(cursor="hand2" if i < current else "")
            lbl.configure(text_color=TEXT if reached else TEXT_MUTED,
                          font=font(14, "bold" if i == current else "normal"),
                          cursor="hand2" if i < current else "")

    def go_to_step(self, index):
        if self.phase in STEP_ORDER and index < STEP_ORDER.index(self.phase):
            self.show_phase(STEP_ORDER[index])

    def _footer(self, parent, row, back_command=None):
        """Footer row under a step: optional Back on the left, error text and the next button on the right."""
        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.grid(row=row, column=0, columnspan=2, pady=(24, 0), sticky="ew")
        footer.grid_columnconfigure(1, weight=1)
        if back_command:
            make_button(footer, "Back", back_command, "secondary", width=96).grid(row=0, column=0, sticky="w")
        error_lbl = make_label(footer, color=DANGER, weight="bold")
        error_lbl.grid(row=0, column=1, padx=12, sticky="e")
        return footer, error_lbl

    # Step 1: audience
    def create_audience_step(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure((0, 1), weight=1, uniform="audience")

        left = make_card(frame)
        left.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        content = ctk.CTkFrame(left, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=24)

        make_label(content, "Who should receive it?", 17, "bold").pack(fill="x")
        desc = make_label(content, "Pick a Google Contacts label, or type the names of WhatsApp groups you belong to.", color=TEXT_MUTED)
        desc.pack(fill="x", pady=(4, 0))
        auto_wrap(desc, content)

        self.mode_selector = ctk.CTkSegmentedButton(
            content, values=[MODE_LABEL, MODE_GROUP], command=self.on_mode_change, height=40, corner_radius=10,
            fg_color=SURFACE, selected_color=BG, selected_hover_color=BG,
            unselected_color=SURFACE, unselected_hover_color=BORDER, text_color=TEXT, font=font(14, "bold")
        )
        self.mode_selector.pack(fill="x", pady=(18, 20))
        self.mode_selector.set(MODE_LABEL)

        self.label_mode_frame = ctk.CTkFrame(content, fg_color="transparent")
        make_label(self.label_mode_frame, "Google Contacts label", 13, "bold").pack(fill="x", pady=(0, 6))
        row = ctk.CTkFrame(self.label_mode_frame, fg_color="transparent")
        row.pack(fill="x")
        self.label_menu = ctk.CTkOptionMenu(
            row, values=["Loading labels..."], command=self.on_label_selected, height=44, corner_radius=8,
            fg_color=SURFACE, button_color=SURFACE, button_hover_color=BORDER, text_color=TEXT,
            dropdown_fg_color=BG, dropdown_text_color=TEXT, dropdown_hover_color=SURFACE,
            font=font(14), dropdown_font=font(14), dynamic_resizing=False
        )
        self.label_menu.pack(side="left", fill="x", expand=True)
        self.refresh_btn = make_button(row, "Refresh", self.load_labels_async, "secondary", width=96)
        self.refresh_btn.pack(side="left", padx=(8, 0))
        self.sync_lbl = make_label(self.label_mode_frame, "Syncing with Google...", 13, color=TEXT_MUTED)
        self.sync_lbl.pack(fill="x", pady=(8, 0))
        auto_wrap(self.sync_lbl, self.label_mode_frame)
        self.label_mode_frame.pack(fill="x")

        self.group_mode_frame = ctk.CTkFrame(content, fg_color="transparent")
        make_label(self.group_mode_frame, "Group names", 13, "bold").pack(fill="x", pady=(0, 6))
        self.groups_input = make_textbox(self.group_mode_frame, height=130, wrap="none")
        self.groups_input.pack(fill="x")
        self.groups_input.bind("<KeyRelease>", lambda e: self.refresh_audience_view())
        groups_hint = make_label(self.group_mode_frame, "One per line, exactly as shown in WhatsApp. You must be a member of each group.", 13, color=TEXT_MUTED)
        groups_hint.pack(fill="x", pady=(8, 0))
        auto_wrap(groups_hint, self.group_mode_frame)

        right = make_card(frame)
        right.grid(row=0, column=1, padx=(10, 0), sticky="nsew")
        stats = ctk.CTkFrame(right, fg_color="transparent")
        stats.pack(fill="both", expand=True, padx=24, pady=24)
        make_label(stats, "After cleanup", 17, "bold").pack(fill="x")

        tiles = ctk.CTkFrame(stats, fg_color="transparent")
        tiles.pack(fill="x", pady=(16, 0))
        tiles.grid_columnconfigure((0, 1, 2), weight=1, uniform="tiles")
        self.stat_tiles = []
        for col, caption in enumerate(["will receive it", "duplicates removed", "invalid numbers"]):
            tile = ctk.CTkFrame(tiles, fg_color=SURFACE, corner_radius=10)
            tile.grid(row=0, column=col, padx=(0 if col == 0 else 6, 0 if col == 2 else 6), sticky="nsew")
            value = make_label(tile, "—", 30, "bold")
            value.pack(fill="x", padx=16, pady=(14, 0))
            caption_lbl = make_label(tile, caption, 13, color=TEXT_MUTED)
            caption_lbl.pack(fill="x", padx=16, pady=(2, 14))
            auto_wrap(caption_lbl, tile, 32)
            self.stat_tiles.append(value)

        self.audience_status_lbl = make_label(stats, "", 13, color=TEXT_MUTED)
        self.audience_status_lbl.pack(fill="x", pady=(12, 0))
        auto_wrap(self.audience_status_lbl, stats, 48)

        self.skipped_box = ctk.CTkFrame(stats, fg_color=WARN_BG, corner_radius=10)
        self.skipped_lbl = make_label(self.skipped_box, "", 13, color=WARN_TEXT)
        self.skipped_lbl.pack(fill="x", padx=14, pady=12)
        auto_wrap(self.skipped_lbl, self.skipped_box, 28)

        self.list_toggle_btn = make_button(stats, "Show recipients", self.toggle_recipient_list, "secondary", width=160)
        self.list_toggle_btn.pack(anchor="w", pady=(16, 0))
        self.recipient_list_frame, self.recipient_tree = make_tree(
            stats, [("name", "Name", 200, True), ("phone", "Number", 160, True)], height=8
        )

        _, self.audience_error_lbl = self._footer(frame, 1)
        make_button(self.audience_error_lbl.master, "Continue  ›", self.to_message_step).grid(row=0, column=2, sticky="e")
        return frame

    def on_mode_change(self, value):
        self.mode = "label" if value == MODE_LABEL else "group"
        if self.mode == "label":
            self.group_mode_frame.pack_forget()
            self.label_mode_frame.pack(fill="x")
        else:
            self.label_mode_frame.pack_forget()
            self.group_mode_frame.pack(fill="x")
        self.preview_index = 0
        self.audience_error_lbl.configure(text="")
        self.refresh_audience_view()

    def group_names(self):
        names = []
        for line in self.groups_input.get("1.0", "end-1c").splitlines():
            name = line.strip()
            if name and name not in names:
                names.append(name)
        return names

    def current_recipients(self):
        if self.mode == "group":
            return [{"name": name, "phone": "WhatsApp group"} for name in self.group_names()]
        if self.audience and not self.audience_loading:
            return self.audience["valid"]
        return []

    def audience_text(self):
        if self.mode == "group":
            return f"WhatsApp groups · {len(self.group_names())}"
        return f"Label · {self.audience['label']}" if self.audience else "Label"

    def refresh_audience_view(self):
        self.skipped_box.pack_forget()
        if self.mode == "group":
            lines = [l.strip() for l in self.groups_input.get("1.0", "end-1c").splitlines() if l.strip()]
            unique = len(self.group_names())
            values = (str(unique), str(len(lines) - unique), "0")
            status = "Each group must already exist in your WhatsApp, and you must be a member."
        elif self.audience_loading:
            values = ("…", "…", "…")
            status = "Checking the contacts in this label..."
        elif self.audience_error:
            values = ("—", "—", "—")
            status = f"Could not read this label's contacts: {self.audience_error}"
        elif self.audience:
            a = self.audience
            values = (str(len(a["valid"])), str(len(a["duplicates"])), str(len(a["invalid"])))
            status = f"Numbers saved without a country code are read as {country_name(settings.DEFAULT_COUNTRY)} numbers."
            if a["invalid"]:
                shown = a["invalid"][:8]
                lines = [f"{c['name']} ({c['phone'] or 'no number'})" for c in shown]
                if len(a["invalid"]) > len(shown):
                    lines.append(f"and {len(a['invalid']) - len(shown)} more")
                self.skipped_lbl.configure(text="Skipped, not a valid phone number:\n" + "\n".join(lines))
                self.skipped_box.pack(fill="x", pady=(12, 0), before=self.list_toggle_btn)
        else:
            values = ("—", "—", "—")
            status = "Choose a label to see who will receive the message." if self.labels_list else ""

        for tile, value in zip(self.stat_tiles, values):
            tile.configure(text=value)
        self.audience_status_lbl.configure(text=status)

        if self.show_recipient_list:
            self.recipient_tree.delete(*self.recipient_tree.get_children())
            for r in self.current_recipients():
                self.recipient_tree.insert("", "end", values=(r["name"], r["phone"]))

    def toggle_recipient_list(self):
        self.show_recipient_list = not self.show_recipient_list
        if self.show_recipient_list:
            self.recipient_list_frame.pack(fill="x", pady=(14, 0))
            self.list_toggle_btn.configure(text="Hide recipients")
        else:
            self.recipient_list_frame.pack_forget()
            self.list_toggle_btn.configure(text="Show recipients")
        self.refresh_audience_view()

    def to_message_step(self):
        if self.mode == "label":
            if self.audience_loading:
                self.audience_error_lbl.configure(text="Still checking this label's contacts. Try again in a moment.")
                return
            if not self.audience or not self.audience["valid"]:
                self.audience_error_lbl.configure(text="This label has no contacts with a valid phone number. Choose another label.")
                return
        elif not self.group_names():
            self.audience_error_lbl.configure(text="Enter at least one WhatsApp group name.")
            return
        self.audience_error_lbl.configure(text="")
        self.show_phase("message")

    # Step 2: message
    def create_message_step(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure((0, 1), weight=1, uniform="message")

        form_card = make_card(frame)
        form_card.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        form = ctk.CTkFrame(form_card, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=24, pady=24)

        make_label(form, "Campaign name", 13, "bold").pack(fill="x", pady=(0, 6))
        self.name_input = make_entry(form, placeholder_text="e.g. Spring Semester Registration")
        self.name_input.pack(fill="x")

        message_header = ctk.CTkFrame(form, fg_color="transparent")
        message_header.pack(fill="x", pady=(20, 6))
        make_label(message_header, "Message", 13, "bold").pack(side="left")
        self.message_counter_lbl = make_label(message_header, "", 12, color=TEXT_MUTED)
        self.message_counter_lbl.pack(side="right")
        self.message_input = make_textbox(form, height=230)
        self.message_input.pack(fill="x")
        self.message_input.bind("<KeyRelease>", lambda e: self._on_text_edit(self.message_input, MESSAGE_MAX, self.message_counter_lbl))
        message_hint = make_label(form, "{{name}} becomes each recipient's name. {{group_link}} becomes your group invite link.", 13, color=TEXT_MUTED)
        message_hint.pack(fill="x", pady=(6, 0))
        auto_wrap(message_hint, form)

        make_label(form, "Attachment", 13, "bold").pack(fill="x", pady=(20, 6))
        attach_row = ctk.CTkFrame(form, fg_color="transparent")
        attach_row.pack(fill="x")
        attach_display = ctk.CTkFrame(attach_row, fg_color=SURFACE, border_color=INPUT_BORDER, border_width=1, corner_radius=8, height=44)
        attach_display.pack(side="left", fill="x", expand=True)
        attach_display.pack_propagate(False)
        self.attachment_lbl = make_label(attach_display, "No file chosen", color=TEXT_MUTED)
        self.attachment_lbl.pack(fill="both", expand=True, padx=12)
        make_button(attach_row, "Browse", self.browse_attachment, "secondary", width=90).pack(side="left", padx=(8, 0))
        self.remove_attachment_btn = make_button(attach_row, "✕", lambda: self.set_attachment(""), "danger", width=44)
        self.attach_hint_lbl = make_label(form, "PNG and JPG are sent as photos, PDF and DOCX as documents.", 13, color=TEXT_MUTED)
        self.attach_hint_lbl.pack(fill="x", pady=(6, 0))
        auto_wrap(self.attach_hint_lbl, form)
        self.default_flyer_btn = make_button(form, "", self.use_default_flyer, "link", height=36, anchor="w")

        caption_header = ctk.CTkFrame(form, fg_color="transparent")
        caption_header.pack(fill="x", pady=(20, 6))
        make_label(caption_header, "Attachment caption", 13, "bold").pack(side="left")
        self.caption_counter_lbl = make_label(caption_header, "", 12, color=TEXT_MUTED)
        self.caption_counter_lbl.pack(side="right")
        self.caption_input = make_textbox(form, height=70)
        self.caption_input.pack(fill="x")
        self.caption_input.bind("<KeyRelease>", lambda e: self._on_text_edit(self.caption_input, CAPTION_MAX, self.caption_counter_lbl))
        self.caption_hint_lbl = make_label(form, "Optional. Choose an attachment to add a caption.", 13, color=TEXT_MUTED)
        self.caption_hint_lbl.pack(fill="x", pady=(6, 0))

        self.create_preview(frame)

        _, self.message_error_lbl = self._footer(frame, 1, back_command=lambda: self.show_phase("audience"))
        make_button(self.message_error_lbl.master, "Continue  ›", self.to_review_step).grid(row=0, column=2, sticky="e")

        self.reset_campaign_form()
        return frame

    def create_preview(self, parent):
        card = make_card(parent)
        card.grid(row=0, column=1, padx=(10, 0), sticky="nsew")

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 10))
        self.avatar_lbl = ctk.CTkLabel(top, text="", width=40, height=40, corner_radius=20, fg_color=AVATAR_BG,
                                       text_color=TEXT_MUTED, font=font(14, "bold"))
        self.avatar_lbl.pack(side="left")
        who = ctk.CTkFrame(top, fg_color="transparent")
        who.pack(side="left", fill="x", expand=True, padx=12)
        self.preview_name_lbl = make_label(who, "", 15, "bold")
        self.preview_name_lbl.pack(fill="x")
        self.preview_pos_lbl = make_label(who, "", 12, color=TEXT_MUTED)
        self.preview_pos_lbl.pack(fill="x")
        for text, step in (("›", 1), ("‹", -1)):
            ctk.CTkButton(top, text=text, width=40, height=40, corner_radius=20, fg_color="transparent",
                          hover_color=SURFACE, text_color=TEXT_MUTED, font=font(22),
                          command=lambda s=step: self.step_preview(s)).pack(side="right")

        self.chat_area = ctk.CTkFrame(card, fg_color=CHAT_BG, corner_radius=10)
        self.chat_area.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.chat_area.grid_columnconfigure(0, weight=1)
        self.chat_area.grid_rowconfigure(0, minsize=int(440 * ctk.ScalingTracker.get_widget_scaling(self)))
        bubbles = ctk.CTkFrame(self.chat_area, fg_color="transparent")
        bubbles.grid(row=0, column=0, padx=16, pady=20, sticky="new")

        self.text_bubble = ctk.CTkFrame(bubbles, fg_color=BUBBLE, corner_radius=8)
        self.bubble_text_lbl = make_label(self.text_bubble, "", 14, wraplength=320)
        self.bubble_text_lbl.pack(fill="x", padx=10, pady=(8, 0))
        make_label(self.text_bubble, "now", 11, color=TEXT_MUTED, anchor="e").pack(fill="x", padx=10, pady=(0, 6))

        self.media_bubble = ctk.CTkFrame(bubbles, fg_color=BUBBLE, corner_radius=8)
        self.media_box = ctk.CTkFrame(self.media_bubble, fg_color=MEDIA_BG, corner_radius=6, width=280, height=170)
        self.media_box.pack(padx=4, pady=(4, 0))
        self.media_box.pack_propagate(False)
        self.media_lbl = ctk.CTkLabel(self.media_box, text="", font=font(13), text_color="#3B4A54")
        self.media_lbl.pack(expand=True)
        self.media_image = tk.Label(self.media_box, bd=0, bg=MEDIA_BG)
        self.media_caption_lbl = make_label(self.media_bubble, "", 14, wraplength=270)
        make_label(self.media_bubble, "now", 11, color=TEXT_MUTED, anchor="e").pack(side="bottom", fill="x", padx=10, pady=(0, 6))

        def wrap_bubbles(event):
            scale = ctk.ScalingTracker.get_widget_scaling(self)
            width = max(160, int(event.width / scale * 0.8))
            self.bubble_text_lbl.configure(wraplength=width - 20)
        self.chat_area.bind("<Configure>", wrap_bubbles, add=True)

    def reset_campaign_form(self):
        self.name_input.delete(0, "end")
        self.message_input.delete("1.0", "end")
        self.message_input.insert("1.0", load_message_template())
        self.caption_input.configure(state="normal")
        self.caption_input.delete("1.0", "end")
        self.preview_index = 0
        self.set_attachment("")
        self._on_text_edit(self.message_input, MESSAGE_MAX, self.message_counter_lbl)
        self._on_text_edit(self.caption_input, CAPTION_MAX, self.caption_counter_lbl)

    def _on_text_edit(self, textbox, max_chars, counter_lbl):
        text = textbox.get("1.0", "end-1c")
        if len(text) > max_chars:
            textbox.delete(f"1.0+{max_chars}c", "end")
            text = text[:max_chars]
        counter_lbl.configure(text=f"{len(text):,} / {max_chars:,}")
        # Re-render the preview once typing pauses
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(150, self.update_preview)

    def browse_attachment(self):
        filename = filedialog.askopenfilename(
            parent=self,
            title="Select Attachment",
            filetypes=[("Images and documents", "*.png *.jpg *.jpeg *.pdf *.docx"), ("Images", "*.png *.jpg *.jpeg"), ("PDF", "*.pdf"), ("Word document", "*.docx")]
        )
        if filename:
            self.set_attachment(filename)

    def default_flyer_path(self):
        path = settings.RESOLVED_FLYER_PATH
        return path if path and os.path.isfile(path) else None

    def use_default_flyer(self):
        path = self.default_flyer_path()
        if path:
            self.set_attachment(path)

    def set_attachment(self, path):
        self.attachment_path = path
        has_attachment = bool(path)
        self.attachment_lbl.configure(text=os.path.basename(path) if has_attachment else "No file chosen",
                                      text_color=TEXT if has_attachment else TEXT_MUTED)
        if has_attachment:
            self.remove_attachment_btn.pack(side="left", padx=(8, 0))
        else:
            self.remove_attachment_btn.pack_forget()

        # A caption only makes sense with an attachment
        self.caption_input.configure(state="normal" if has_attachment else "disabled",
                                     fg_color=BG if has_attachment else SURFACE)
        self.caption_hint_lbl.configure(text="Optional. {{name}} works here too." if has_attachment
                                        else "Optional. Choose an attachment to add a caption.")

        default_flyer = self.default_flyer_path()
        if default_flyer and os.path.abspath(default_flyer) != os.path.abspath(path or "."):
            self.default_flyer_btn.configure(text=f"Use the default flyer ({os.path.basename(default_flyer)})")
            self.default_flyer_btn.pack(anchor="w", pady=(2, 0), after=self.attach_hint_lbl)
        else:
            self.default_flyer_btn.pack_forget()
        self.update_preview()

    def step_preview(self, step):
        count = len(self.current_recipients())
        if count:
            self.preview_index = (min(self.preview_index, count - 1) + step) % count
        self.update_preview()

    def _load_thumbnail(self, path):
        if self._thumbnail_path == path:
            return self.thumbnail
        self.thumbnail, self._thumbnail_path = None, path
        if path.lower().endswith(THUMBNAIL_EXTENSIONS):
            try:
                image = tk.PhotoImage(file=path)
                scale = ctk.ScalingTracker.get_widget_scaling(self)
                factor = max(1, math.ceil(image.width() / (276 * scale)), math.ceil(image.height() / (166 * scale)))
                self.thumbnail = image.subsample(factor)
            except Exception:
                self.thumbnail = None
        return self.thumbnail

    def update_preview(self):
        self._preview_job = None
        recipients = self.current_recipients()
        count = len(recipients)
        index = min(self.preview_index, count - 1) if count else 0
        recipient = recipients[index] if count else {"name": "Recipient", "phone": ""}
        name = recipient["name"]

        initials = "".join(word[0] for word in name.split()[:2]).upper() or "?"
        self.avatar_lbl.configure(text=initials)
        self.preview_name_lbl.configure(text=name)
        self.preview_pos_lbl.configure(text=f"Preview {index + 1} of {count}" if count else "No recipients yet")

        self.text_bubble.pack_forget()
        self.media_bubble.pack_forget()
        message = self.message_input.get("1.0", "end-1c").strip()
        if message:
            self.bubble_text_lbl.configure(text=fill_placeholders(message, name))
            self.text_bubble.pack(anchor="e", pady=(0, 8))

        if self.attachment_path:
            filename = os.path.basename(self.attachment_path)
            thumbnail = self._load_thumbnail(self.attachment_path)
            if thumbnail:
                self.media_lbl.pack_forget()
                self.media_image.configure(image=thumbnail)
                self.media_image.place(relx=0.5, rely=0.5, anchor="center")
            else:
                self.media_image.place_forget()
                kind = "Photo" if filename.lower().endswith(IMAGE_EXTENSIONS) else "Document"
                self.media_lbl.configure(text=f"{kind}\n{filename}")
                self.media_lbl.pack(expand=True)
            caption = self.caption_input.get("1.0", "end-1c").strip()
            if caption:
                self.media_caption_lbl.configure(text=fill_placeholders(caption, name))
                self.media_caption_lbl.pack(fill="x", padx=10, pady=(6, 0), after=self.media_box)
            else:
                self.media_caption_lbl.pack_forget()
            self.media_bubble.pack(anchor="e")

    def to_review_step(self):
        name = self.name_input.get().strip()
        if not name:
            self.message_error_lbl.configure(text="Give the campaign a name.")
            self.name_input.focus_set()
            return
        if len(name) > NAME_MAX:
            self.message_error_lbl.configure(text=f"Campaign name must be {NAME_MAX} characters or fewer.")
            return
        if self.attachment_path:
            if not self.attachment_path.lower().endswith(ATTACHMENT_EXTENSIONS):
                self.message_error_lbl.configure(text="Attachment must be a PNG, JPG, JPEG, PDF or DOCX file.")
                return
            if not os.path.isfile(self.attachment_path):
                self.message_error_lbl.configure(text="Attachment file not found.")
                return
        if not self.message_input.get("1.0", "end-1c").strip() and not self.attachment_path:
            self.message_error_lbl.configure(text="Write a message or choose an attachment.")
            return
        self.message_error_lbl.configure(text="")
        self.show_phase("review")

    # Step 3: review
    def create_review_step(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        card = make_card(frame)
        card.grid(row=0, column=0, sticky="ew")
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=24)
        make_label(content, "Ready to send", 17, "bold").pack(fill="x")

        summary = ctk.CTkFrame(content, fg_color="transparent")
        summary.pack(fill="x", pady=(16, 0))
        summary.grid_columnconfigure(0, minsize=int(150 * ctk.ScalingTracker.get_widget_scaling(self)))
        summary.grid_columnconfigure(1, weight=1)
        self.review_rows = {}
        for row, (key, title) in enumerate([("campaign", "Campaign"), ("audience", "Audience"), ("recipients", "Recipients"),
                                            ("message", "Message"), ("attachment", "Attachment"), ("caption", "Caption")]):
            key_lbl = make_label(summary, title, color=TEXT_MUTED)
            key_lbl.grid(row=row, column=0, pady=6, sticky="nw")
            value_lbl = make_label(summary, "", weight="bold" if key == "campaign" else "normal")
            value_lbl.grid(row=row, column=1, pady=6, sticky="ew")
            self.review_rows[key] = (key_lbl, value_lbl)
        auto_wrap(self.review_rows["message"][1], summary, 170)
        auto_wrap(self.review_rows["caption"][1], summary, 170)

        tk.Frame(content, height=1, bg=BORDER).pack(fill="x", pady=18)
        self.review_google_lbl = make_label(content, "✓  Google Contacts synced", color=SUCCESS_TEXT)
        self.review_wa_lbl = make_label(content, "✓  Saved WhatsApp Web session found", color=SUCCESS_TEXT)
        self.review_wa_box = ctk.CTkFrame(content, fg_color=WARN_BG, corner_radius=10)
        wa_warning = make_label(self.review_wa_box, "No saved WhatsApp Web session yet. When sending starts, Chrome opens WhatsApp Web: "
                                "scan the QR code with your phone within 2 minutes.", color=WARN_TEXT)
        wa_warning.pack(fill="x", padx=14, pady=12)
        auto_wrap(wa_warning, self.review_wa_box, 28)

        _, self.review_error_lbl = self._footer(frame, 1, back_command=lambda: self.show_phase("message"))
        self.start_btn = make_button(self.review_error_lbl.master, "▶  Start sending", self.start_campaign, height=48)
        self.start_btn.grid(row=0, column=2, sticky="e")
        return frame

    def fill_review(self):
        recipients = self.current_recipients()
        noun = "contact" if self.mode == "label" else "group"
        message_lines = [l for l in self.message_input.get("1.0", "end-1c").strip().splitlines() if l.strip()]
        message = "\n".join(message_lines[:2]) + ("\n…" if len(message_lines) > 2 else "") if message_lines else "(no text, attachment only)"
        caption = self.caption_input.get("1.0", "end-1c").strip() if self.attachment_path else ""

        values = {
            "campaign": self.name_input.get().strip(),
            "audience": self.audience_text(),
            "recipients": f"{len(recipients):,} {noun}{'' if len(recipients) == 1 else 's'}",
            "message": message,
            "attachment": os.path.basename(self.attachment_path) if self.attachment_path else "None",
            "caption": caption,
        }
        for key, (key_lbl, value_lbl) in self.review_rows.items():
            value_lbl.configure(text=values[key])
            visible = key != "caption" or bool(caption)
            for widget in (key_lbl, value_lbl):
                widget.grid() if visible else widget.grid_remove()

        for widget in (self.review_google_lbl, self.review_wa_lbl, self.review_wa_box):
            widget.pack_forget()
        if self.mode == "label":
            self.review_google_lbl.pack(fill="x", pady=(0, 8))
        if session_saved():
            self.review_wa_lbl.pack(fill="x")
        else:
            self.review_wa_box.pack(fill="x")

        busy = self.running_thread is not None and self.running_thread.is_alive()
        self.start_btn.configure(text=f"▶  Start sending to {len(recipients):,}")
        set_button_enabled(self.start_btn, not busy)
        self.review_error_lbl.configure(text="The previous run is still closing WhatsApp. Try again in a moment." if busy else "")

    # Run view (sending and results)
    def create_run_view(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=3, uniform="run")
        frame.grid_columnconfigure(1, weight=2, uniform="run")

        status_card = make_card(frame)
        status_card.grid(row=0, column=0, columnspan=2, sticky="ew")
        status = ctk.CTkFrame(status_card, fg_color="transparent")
        status.pack(fill="both", expand=True, padx=24, pady=24)
        status.grid_columnconfigure(0, weight=1)

        self.run_state_lbl = make_label(status, "", 12, "bold", color=SUCCESS_TEXT)
        self.run_state_lbl.grid(row=0, column=0, sticky="w")
        self.run_name_lbl = make_label(status, "", 20, "bold")
        self.run_name_lbl.grid(row=1, column=0, sticky="w")
        self.run_audience_lbl = make_label(status, "", color=TEXT_MUTED)
        self.run_audience_lbl.grid(row=2, column=0, sticky="w")

        self.run_controls = ctk.CTkFrame(status, fg_color="transparent")
        self.run_controls.grid(row=0, column=1, rowspan=3, sticky="ne")
        self.pause_btn = make_button(self.run_controls, "Pause", self.toggle_pause, "secondary", width=110)
        self.pause_btn.pack(side="left")
        self.stop_btn = make_button(self.run_controls, "Stop", self.stop_automation_run, "danger", width=110)
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.progress_bar = ctk.CTkProgressBar(status, height=10, corner_radius=5, progress_color=ACCENT, fg_color=SURFACE)
        self.progress_bar.grid(row=3, column=0, columnspan=2, pady=(20, 0), sticky="ew")
        self.progress_bar.set(0)

        counts = ctk.CTkFrame(status, fg_color="transparent")
        counts.grid(row=4, column=0, columnspan=2, pady=(12, 0), sticky="w")
        self.count_processed_lbl = make_label(counts, "", weight="bold")
        self.count_processed_lbl.pack(side="left")
        self.count_sent_lbl = make_label(counts, "", color=SUCCESS_TEXT)
        self.count_sent_lbl.pack(side="left", padx=(24, 0))
        self.count_failed_lbl = make_label(counts, "", color=DANGER)
        self.count_failed_lbl.pack(side="left", padx=(24, 0))
        self.count_skipped_lbl = make_label(counts, "", color=TEXT_MUTED)
        self.count_skipped_lbl.pack(side="left", padx=(24, 0))

        self.live_status_lbl = make_label(status, "", 13, color=TEXT_MUTED)
        self.live_status_lbl.grid(row=5, column=0, columnspan=2, pady=(6, 0), sticky="ew")

        # Results card, shown once the run has ended
        self.done_card = ctk.CTkFrame(frame, fg_color=SURFACE, corner_radius=12)
        done = ctk.CTkFrame(self.done_card, fg_color="transparent")
        done.pack(fill="both", expand=True, padx=24, pady=24)
        self.done_title_lbl = make_label(done, "", 17, "bold")
        self.done_title_lbl.pack(fill="x")
        self.done_text_lbl = make_label(done, "", color=TEXT_MUTED)
        self.done_text_lbl.pack(fill="x", pady=(6, 0))
        auto_wrap(self.done_text_lbl, done)
        actions = ctk.CTkFrame(done, fg_color="transparent")
        actions.pack(fill="x", pady=(18, 0))
        self.retry_btn = make_button(actions, "Retry failed", self.retry_failed)
        make_button(actions, "New campaign", self.new_campaign, "secondary", width=140).pack(side="right")
        make_button(actions, "View history", lambda: self.show_page("history"), "secondary", width=130).pack(side="right", padx=(0, 10))
        self.retry_btn.pack(side="left")

        recipients_card = make_card(frame)
        recipients_card.grid(row=2, column=0, padx=(0, 10), pady=(20, 0), sticky="nsew")
        make_label(recipients_card, "Recipients", 15, "bold").pack(fill="x", padx=20, pady=(16, 12))
        tree_frame, self.run_tree = make_tree(recipients_card, [
            ("name", "Name", 130, True), ("phone", "Number", 120, True), ("status", "Status", 76, False),
            ("detail", "Detail", 110, True), ("time", "Time", 52, False)
        ], height=12)
        tree_frame.pack(fill="both", expand=True, padx=(1, 1), pady=(0, 12))

        log_card = make_card(frame)
        log_card.grid(row=2, column=1, padx=(10, 0), pady=(20, 0), sticky="nsew")
        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.pack(fill="x", padx=(20, 8), pady=(8, 4))
        make_label(log_header, "Activity log", 15, "bold").pack(side="left")
        self.log_toggle_btn = make_button(log_header, "Hide", self.toggle_log, "link", width=64, font=font(13, "bold"))
        self.log_toggle_btn.pack(side="right")
        self.log_textbox = make_textbox(log_card, height=430, fg_color=SURFACE, border_width=0,
                                        font=font(12, family=MONO_FAMILY))
        self.log_textbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        configure_log_tags(self.log_textbox)
        self.log_textbox.configure(state="disabled")
        return frame

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_textbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))
            self.log_toggle_btn.configure(text="Hide")
        else:
            self.log_textbox.pack_forget()
            self.log_toggle_btn.configure(text="Show")

    def set_run_state(self, text, color):
        self.run_state_lbl.configure(text=text.upper(), text_color=color)

    def set_live_status(self, text):
        self.live_status_lbl.configure(text=text)

    def update_run_counts(self):
        targets = self.run["targets"]
        total = len(targets)
        sent = sum(t["status"] == "sent" for t in targets)
        failed = sum(t["status"] == "failed" for t in targets)
        skipped = sum(t["status"] == "skipped" for t in targets)
        done = sent + failed + skipped
        self.progress_bar.set(done / total if total else 0)
        self.count_processed_lbl.configure(text=f"{done:,} of {total:,} processed")
        self.count_sent_lbl.configure(text=f"{sent:,} sent")
        self.count_failed_lbl.configure(text=f"{failed:,} failed")
        self.count_skipped_lbl.configure(text=f"{skipped:,} skipped" if skipped else "")
        return sent, failed, skipped

    def set_target_status(self, index, status, detail=""):
        target = self.run["targets"][index]
        target["status"] = status
        target["detail"] = detail
        if status in ("sent", "failed"):
            target["time"] = datetime.now().strftime("%H:%M")
        self.run_tree.item(target["iid"], values=(target["name"], target["number"], STATUS_LABELS[status], detail, target["time"]),
                           tags=(status,))
        if status == "sending":
            self.run_tree.see(target["iid"])
        self.update_run_counts()

    # ----------------------------------------------------
    # Page: History
    # ----------------------------------------------------
    def create_history_page(self):
        page, body = self.new_page()
        card = make_card(body)
        card.grid(row=0, column=0, sticky="ew")
        tree_frame, self.history_tree = make_tree(card, [
            ("date", "Date", 130, False), ("name", "Campaign", 240, True), ("audience", "Audience", 220, True),
            ("sent", "Sent", 70, False), ("failed", "Failed", 70, False), ("skipped", "Skipped", 80, False), ("total", "Total", 70, False)
        ], height=16)
        tree_frame.pack(fill="both", expand=True, padx=1, pady=(1, 12))
        self.history_empty_lbl = make_label(card, "No campaigns yet. Each run shows up here when it ends.", color=TEXT_MUTED)
        return page

    def render_history(self):
        self.history_tree.delete(*self.history_tree.get_children())
        for entry in self.history:
            try:
                date = datetime.fromisoformat(entry["date"]).strftime("%b %d, %H:%M")
            except (KeyError, ValueError):
                date = entry.get("date", "")
            self.history_tree.insert("", "end", values=(
                date, entry.get("name", ""), entry.get("audience", ""), entry.get("sent", 0),
                entry.get("failed", 0), entry.get("skipped", 0), entry.get("total", 0)
            ))
        if self.history:
            self.history_empty_lbl.pack_forget()
        else:
            self.history_empty_lbl.pack(fill="x", padx=20, pady=(0, 16))

    def _load_history(self):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
            return history if isinstance(history, list) else []
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            logger.warning(f"SYSTEM: Could not read campaign history: {e}")
            return []

    def _save_history(self):
        try:
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.history[:HISTORY_MAX], f, indent=2)
        except OSError as e:
            logger.warning(f"SYSTEM: Could not save campaign history: {e}")

    # ----------------------------------------------------
    # Page: Settings
    # ----------------------------------------------------
    def create_settings_page(self):
        page, body = self.new_page()

        # Accounts
        accounts = make_card(body)
        accounts.grid(row=0, column=0, sticky="ew")
        content = ctk.CTkFrame(accounts, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=(24, 20))
        content.grid_columnconfigure(0, weight=1)
        make_label(content, "Accounts", 17, "bold").grid(row=0, column=0, columnspan=2, sticky="w")

        google = ctk.CTkFrame(content, fg_color="transparent")
        google.grid(row=1, column=0, pady=(16, 16), sticky="ew")
        make_label(google, "Google Contacts", 15, "bold").pack(fill="x")
        self.account_status_lbl = make_label(google, "Checking...", 13, color=TEXT_MUTED)
        self.account_status_lbl.pack(fill="x", pady=(2, 0))
        self.client_secrets_lbl = make_label(google, "", 12, color=TEXT_MUTED)
        self.client_secrets_lbl.pack(fill="x")
        google_actions = ctk.CTkFrame(content, fg_color="transparent")
        google_actions.grid(row=1, column=1, sticky="e")
        self.account_signin_btn = make_button(google_actions, "Sign in", self.sign_in_google, "secondary", width=140)
        self.account_signin_btn.pack(side="left")
        self.account_signout_btn = make_button(google_actions, "Sign out", self.sign_out_google, "danger", width=100)
        self.account_signout_btn.pack(side="left", padx=(8, 0))

        tk.Frame(content, height=1, bg=BORDER).grid(row=2, column=0, columnspan=2, sticky="ew")

        whatsapp = ctk.CTkFrame(content, fg_color="transparent")
        whatsapp.grid(row=3, column=0, pady=(16, 0), sticky="ew")
        make_label(whatsapp, "WhatsApp Web", 15, "bold").pack(fill="x")
        self.session_status_lbl = make_label(whatsapp, "", 13, color=TEXT_MUTED)
        self.session_status_lbl.pack(fill="x", pady=(2, 0))
        self.clear_session_btn = make_button(content, "Clear session", self.clear_whatsapp_session, "danger", width=140)
        self.clear_session_btn.grid(row=3, column=1, pady=(16, 0), sticky="e")

        # Sending defaults
        defaults = make_card(body)
        defaults.grid(row=1, column=0, pady=(20, 0), sticky="ew")
        form = ctk.CTkFrame(defaults, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=24, pady=24)
        make_label(form, "Sending defaults", 17, "bold").pack(fill="x")
        make_label(form, "Used by every campaign. The invite link fills in {{group_link}}.", color=TEXT_MUTED).pack(fill="x", pady=(4, 0))

        make_label(form, "Default country for phone numbers", 13, "bold").pack(fill="x", pady=(20, 6))
        current_country = settings.DEFAULT_COUNTRY or "US"
        countries = COUNTRIES if current_country in dict(COUNTRIES) else COUNTRIES + [(current_country, current_country)]
        self.country_by_display = {f"{name} ({code})": code for code, name in countries}
        self.country_menu = ctk.CTkOptionMenu(
            form, values=list(self.country_by_display), height=44, width=320, corner_radius=8,
            fg_color=SURFACE, button_color=SURFACE, button_hover_color=BORDER, text_color=TEXT,
            dropdown_fg_color=BG, dropdown_text_color=TEXT, dropdown_hover_color=SURFACE,
            font=font(14), dropdown_font=font(14), dynamic_resizing=False
        )
        self.country_menu.pack(anchor="w")
        self.country_menu.set(next(d for d, c in self.country_by_display.items() if c == current_country))
        make_label(form, "Numbers saved without a country code are read as this country.", 13, color=TEXT_MUTED).pack(fill="x", pady=(6, 0))

        make_label(form, "WhatsApp group invite link", 13, "bold").pack(fill="x", pady=(20, 6))
        self.invite_input = make_entry(form, placeholder_text="https://chat.whatsapp.com/...")
        self.invite_input.pack(fill="x")
        self.invite_input.insert(0, settings.WHATSAPP_GROUP_INVITE_LINK or "")

        make_label(form, "Default flyer", 13, "bold").pack(fill="x", pady=(20, 6))
        flyer_row = ctk.CTkFrame(form, fg_color="transparent")
        flyer_row.pack(fill="x")
        self.flyer_input = make_entry(flyer_row, placeholder_text="./data/flyer.png")
        self.flyer_input.pack(side="left", fill="x", expand=True)
        self.flyer_input.insert(0, settings.FLYER_PATH or "")
        make_button(flyer_row, "Browse", self.browse_flyer, "secondary", width=90).pack(side="left", padx=(8, 0))
        make_label(form, "Offered as a one-click attachment when you write a campaign.", 13, color=TEXT_MUTED).pack(fill="x", pady=(6, 0))

        save_row = ctk.CTkFrame(form, fg_color="transparent")
        save_row.pack(fill="x", pady=(24, 0))
        make_button(save_row, "Save settings", self.save_current_settings, width=150).pack(side="left")
        self.save_status_lbl = make_label(save_row, "", weight="bold", color=SUCCESS_TEXT)
        self.save_status_lbl.pack(side="left", padx=14)

        # Application logs (the full log file)
        logs = make_card(body)
        logs.grid(row=2, column=0, pady=(20, 0), sticky="ew")
        logs_content = ctk.CTkFrame(logs, fg_color="transparent")
        logs_content.pack(fill="both", expand=True, padx=(24, 12), pady=12)
        logs_content.grid_columnconfigure(0, weight=1)
        make_label(logs_content, "Application logs", 17, "bold").grid(row=0, column=0, sticky="w")
        self.logs_path_lbl = make_label(logs_content, self.get_log_file_path(), 12, color=TEXT_MUTED)
        self.logs_path_lbl.grid(row=1, column=0, sticky="w")
        self.app_logs_toggle_btn = make_button(logs_content, "Show", self.toggle_app_logs, "link", width=70)
        self.app_logs_toggle_btn.grid(row=0, column=1, rowspan=2, sticky="e")

        self.app_logs_frame = ctk.CTkFrame(logs_content, fg_color="transparent")
        self.logs_textbox = make_textbox(self.app_logs_frame, height=300, fg_color=SURFACE, border_width=0,
                                         wrap="none", font=font(12, family=MONO_FAMILY))
        self.logs_textbox.pack(fill="x", pady=(12, 10))
        configure_log_tags(self.logs_textbox)
        self.logs_textbox.configure(state="disabled")
        log_actions = ctk.CTkFrame(self.app_logs_frame, fg_color="transparent")
        log_actions.pack(fill="x", pady=(0, 6))
        make_button(log_actions, "Refresh", self.load_log_file, "secondary", width=100).pack(side="left")
        make_button(log_actions, "Open logs folder", self.open_log_folder, "secondary", width=150).pack(side="left", padx=(8, 0))
        return page

    def toggle_app_logs(self):
        self.app_logs_visible = not self.app_logs_visible
        if self.app_logs_visible:
            self.app_logs_frame.grid(row=2, column=0, columnspan=2, padx=(0, 12), sticky="ew")
            self.app_logs_toggle_btn.configure(text="Hide")
            self.load_log_file()
        else:
            self.app_logs_frame.grid_forget()
            self.app_logs_toggle_btn.configure(text="Show")

    # ----------------------------------------------------
    # Logging Bridge Setup
    # ----------------------------------------------------
    def setup_logging_bridge(self):
        log_handler = TextboxLogHandler(self.log_textbox)
        log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'))
        logger.addHandler(log_handler)

    def clear_activity_log(self):
        try:
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.configure(state="disabled")
        except Exception:
            pass

    def textbox_log(self, text):
        logger.info(text)

    # ----------------------------------------------------
    # Settings: save (.env writing)
    # ----------------------------------------------------
    def browse_flyer(self):
        filename = filedialog.askopenfilename(
            title="Select Flyer Image",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.gif"), ("All Files", "*.*")]
        )
        if filename:
            self.flyer_input.delete(0, "end")
            self.flyer_input.insert(0, filename)

    def save_current_settings(self):
        invite_link = self.invite_input.get().strip()
        flyer_path = self.flyer_input.get().strip()
        country = self.country_by_display.get(self.country_menu.get(), settings.DEFAULT_COUNTRY)

        if not invite_link:
            self.save_status_lbl.configure(text="The WhatsApp group invite link cannot be empty.", text_color=DANGER)
            self.textbox_log("SYSTEM: Error - WhatsApp Group Invite Link cannot be empty.")
            return

        settings_file = os.path.join(settings.BASE_PATH, ".env")
        lines = []
        if os.path.exists(settings_file):
            with open(settings_file, "r") as f:
                lines = f.readlines()

        config_keys = {
            "WHATSAPP_GROUP_INVITE_LINK": invite_link,
            "FLYER_PATH": flyer_path,
            "DEFAULT_COUNTRY": country
        }

        new_lines = []
        updated_keys = set()

        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, val = stripped.split("=", 1)
                key = key.strip()
                if key in config_keys:
                    new_lines.append(f"{key}={config_keys[key]}\n")
                    updated_keys.add(key)
                    continue
            new_lines.append(line)

        for key, val in config_keys.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={val}\n")

        with open(settings_file, "w") as f:
            f.writelines(new_lines)

        country_changed = country != settings.DEFAULT_COUNTRY

        # Update local runtime configurations
        settings.WHATSAPP_GROUP_INVITE_LINK = invite_link
        settings.FLYER_PATH = flyer_path
        settings.DEFAULT_COUNTRY = country

        self.save_status_lbl.configure(text="Saved", text_color=SUCCESS_TEXT)
        self.textbox_log("SYSTEM: Configuration settings successfully saved to .env file.")

        # The default flyer offer and the preview's invite link may have changed
        self.set_attachment(self.attachment_path)
        # Numbers are validated against the default country, so re-check the selected label
        if country_changed and self.mode == "label" and self.label_menu.get() in self.label_by_display:
            self.on_label_selected(self.label_menu.get())

    # ----------------------------------------------------
    # Google labels and the selected label's contacts
    # ----------------------------------------------------
    def load_labels_async(self):
        self.refresh_btn.configure(state="disabled")
        self.account_signin_btn.configure(state="disabled")
        self.account_signout_btn.configure(state="disabled")
        self.sync_lbl.configure(text="Syncing with Google...", text_color=TEXT_MUTED)
        self.textbox_log("SYNC: Fetching contacts labels from Google Contacts API...")
        threading.Thread(target=self._load_labels_worker, daemon=True).start()

    def _load_labels_worker(self):
        try:
            service = get_people_service()
            labels = list_contact_labels(service)
            if not labels:
                self.textbox_log("SYNC: Error - No labels found in your Google Contacts account.")
                self._ui(self._labels_failed, "No labels found in your Google Contacts account.")
                return
            self._ui(self._populate_labels, labels)
            self.textbox_log(f"SYNC: Successfully loaded {len(labels)} Google Contact labels.")
        except Exception as e:
            logger.error(f"SYNC: Error loading contact groups: {e}")
            self._ui(self._labels_failed, "Could not load labels. Check the Google account in Settings.")
        finally:
            # A sync may have just signed someone in (or failed to), so refresh the account status
            self._refresh_account_status_worker()

    def _label_name(self, label):
        return label.get('formattedName', label.get('name', 'Unnamed Label'))

    def _populate_labels(self, labels):
        previous = self.label_by_display.get(self.label_menu.get())
        self.labels_list = labels
        self.label_by_display = {}
        for label in labels:
            # memberCount is omitted by the API when the label is empty
            display = f"{self._label_name(label)} ({label.get('memberCount', 0):,})"
            while display in self.label_by_display:
                display += " "
            self.label_by_display[display] = label

        displays = list(self.label_by_display)
        # Keep the user's choice across refreshes if that label still exists
        selected = next((d for d, l in self.label_by_display.items()
                         if previous and l.get('resourceName') == previous.get('resourceName')), displays[0])
        self.label_menu.configure(values=displays)
        self.label_menu.set(selected)
        self.refresh_btn.configure(state="normal")
        self.sync_lbl.configure(text=f"Synced with Google at {datetime.now().strftime('%H:%M')}", text_color=TEXT_MUTED)
        # set() doesn't fire the menu command, so load the selected label's contacts
        self.on_label_selected(selected)

    def _labels_failed(self, message):
        self.refresh_btn.configure(state="normal")
        self.sync_lbl.configure(text=message, text_color=DANGER)
        self.refresh_audience_view()

    def on_label_selected(self, display):
        label = self.label_by_display.get(display)
        if not label:
            return
        self._audience_request += 1
        self.audience = None
        self.audience_error = None
        self.audience_loading = True
        self.preview_index = 0
        self.audience_error_lbl.configure(text="")
        self.refresh_audience_view()
        threading.Thread(target=self._analyze_label_worker, args=(label, self._audience_request), daemon=True).start()

    def _analyze_label_worker(self, label, request):
        result, error = None, None
        try:
            raw_contacts = get_contacts_from_label(get_people_service(), label['resourceName'])
            result = analyze_contacts(raw_contacts)
            result["label"] = self._label_name(label)
            logger.info(f"SYNC: '{result['label']}': {len(result['valid'])} contacts to message, "
                        f"{len(result['duplicates'])} duplicates, {len(result['invalid'])} invalid numbers.")
        except Exception as e:
            logger.error(f"SYNC: Could not read contacts for '{self._label_name(label)}': {e}")
            error = str(e)
        self._ui(self._apply_audience, request, result, error)

    def _apply_audience(self, request, result, error):
        # Ignore results for a label the user has already moved away from
        if request != self._audience_request:
            return
        self.audience_loading = False
        self.audience = result
        self.audience_error = error
        self.refresh_audience_view()

    # ----------------------------------------------------
    # Google account and WhatsApp session (Settings page)
    # ----------------------------------------------------
    def refresh_account_status_async(self):
        threading.Thread(target=self._refresh_account_status_worker, daemon=True).start()

    def _refresh_account_status_worker(self):
        secrets_text = "OAuth client: client_secrets.json found" if client_secrets_present() \
            else "OAuth client: client_secrets.json missing from the app folder"
        email = None
        try:
            creds = load_cached_credentials()
            if not creds:
                status, color, signed_in = "Not signed in", TEXT_MUTED, False
            else:
                email = get_account_email(creds)
                status = f"Signed in as {email} · read-only access" if email else "Signed in · read-only access"
                color, signed_in = TEXT_MUTED, True
        except Exception as e:
            logger.error(f"SYSTEM: Could not read Google account status: {e}")
            status, color, signed_in = "Sign-in expired or invalid", DANGER, False
        self._ui(self._apply_account_status, status, color, secrets_text, signed_in, email)

    def _apply_account_status(self, status, color, secrets_text, signed_in, email):
        self.google_signed_in = signed_in
        self.account_status_lbl.configure(text=status, text_color=color)
        self.client_secrets_lbl.configure(text=secrets_text)
        self.account_signin_btn.configure(text="Switch account" if signed_in else "Sign in", state="normal")
        self.account_signout_btn.configure(state="normal" if signed_in else "disabled")
        if signed_in:
            self._set_chip(self.google_chip, f"Google · {email}" if email else "Google · Signed in", ACCENT_BRIGHT)
        else:
            self._set_chip(self.google_chip, "Google · Not signed in", WARN_DOT)

    def _automation_running(self, action="changing the Google account"):
        if self.running_thread and self.running_thread.is_alive():
            self.textbox_log(f"SYSTEM: Error - Stop the running task before {action}.")
            return True
        return False

    def sign_in_google(self):
        if self._automation_running():
            return
        # For "Switch account", drop the current token so the browser sign-in opens
        sign_out()
        self.account_status_lbl.configure(text="Waiting for browser sign-in...", text_color=TEXT_MUTED)
        self.textbox_log("SYSTEM: Opening Google sign-in in your browser...")
        self.load_labels_async()

    def sign_out_google(self):
        if self._automation_running():
            return
        sign_out()
        self.labels_list = []
        self.label_by_display = {}
        self._audience_request += 1
        self.audience = None
        self.audience_loading = False
        self.label_menu.configure(values=["Sign in to Google first"])
        self.label_menu.set("Sign in to Google first")
        self.sync_lbl.configure(text="Signed out. Sign in from Settings to load your labels.", text_color=TEXT_MUTED)
        self.refresh_audience_view()
        self.textbox_log("SYSTEM: Signed out of Google.")
        self.refresh_account_status_async()

    def refresh_session_status(self):
        if session_saved():
            self.session_status_lbl.configure(text="Saved session found. The login is kept between runs, so usually no QR code is needed.")
            self._set_chip(self.whatsapp_chip, "WhatsApp · Session saved", ACCENT_BRIGHT)
            self.clear_session_btn.configure(state="normal")
        else:
            self.session_status_lbl.configure(text="No saved session. You will scan a QR code with your phone when the next campaign starts.")
            self._set_chip(self.whatsapp_chip, "WhatsApp · QR scan needed", WARN_DOT)
            self.clear_session_btn.configure(state="disabled")

    def clear_whatsapp_session(self):
        if self._automation_running("clearing the WhatsApp session"):
            return
        confirmed = messagebox.askyesno(
            "Clear WhatsApp session",
            "This signs WhatsApp Web out of WhatsSync and deletes its saved Chrome profile.\n\n"
            "You will need to scan a QR code with your phone on the next run. Continue?",
            parent=self
        )
        if not confirmed:
            return
        self.clear_session_btn.configure(state="disabled")
        self.session_status_lbl.configure(text="Clearing the session...")
        threading.Thread(target=self._clear_session_worker, daemon=True).start()

    def _clear_session_worker(self):
        path = settings.RESOLVED_WHATSAPP_SESSION_PATH
        try:
            # Chrome locks its profile, so close any WhatsApp browser still using it first
            WhatsAppAutomation().close_leftover_browsers()
            shutil.rmtree(path)
            logger.info("SYSTEM: WhatsApp session cleared. Scan the QR code on the next run.")
        except Exception as e:
            logger.error(f"SYSTEM: Could not clear the WhatsApp session folder '{path}': {e}")
        self._ui(self.refresh_session_status)

    # ----------------------------------------------------
    # Logs (Settings page)
    # ----------------------------------------------------
    def get_log_file_path(self):
        # Use the file handler's own path, since logs/ is relative to the launch directory
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.FileHandler):
                return handler.baseFilename
        return os.path.abspath(os.path.join("logs", "app.log"))

    def load_log_file(self, max_lines=1000):
        path = self.get_log_file_path()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-max_lines:]
        except FileNotFoundError:
            lines = ["Log file not created yet.\n"]

        self.logs_textbox.configure(state="normal")
        self.logs_textbox.delete("1.0", "end")
        for line in lines:
            # File lines look like "time - logger - LEVEL - message"
            parts = line.split(" - ", 3)
            levelname = parts[2] if len(parts) == 4 else ""
            self.logs_textbox.insert("end", line, classify_log_line(line, levelname))
        self.logs_textbox.configure(state="disabled")
        self.logs_textbox.see("end")

    def open_log_folder(self):
        folder = os.path.dirname(self.get_log_file_path())
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    # ----------------------------------------------------
    # Pause / Resume / Stop Actions
    # ----------------------------------------------------
    def toggle_pause(self):
        if not self.running_thread or not self.running_thread.is_alive() or self.sync_waiting:
            return
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.pause_btn.configure(text="Resume")
            self.set_run_state("Paused", WARN_TEXT)
            self.textbox_log("SYSTEM: Automation paused by user.")
        else:
            self.pause_btn.configure(text="Pause")
            self.set_run_state("Sending", SUCCESS_TEXT)
            self.textbox_log("SYSTEM: Automation resumed.")

    def stop_automation_run(self):
        if not self.running_thread or not self.running_thread.is_alive():
            return
        if self.sync_waiting:
            # Sending is already done; Stop now only cuts the phone sync wait short
            self.skip_sync_wait = True
            self.textbox_log("SYSTEM: WARN: Closing WhatsApp early. Recently sent messages may not appear on the phone yet.")
            return
        self.is_stopped = True
        self.set_run_state("Stopping", DANGER)
        self.textbox_log("SYSTEM: Stop command received. Halting automation sequence...")

    # ----------------------------------------------------
    # Starting a campaign
    # ----------------------------------------------------
    def start_campaign(self):
        if self.running_thread and self.running_thread.is_alive():
            return
        message = self.message_input.get("1.0", "end-1c").strip()
        caption = self.caption_input.get("1.0", "end-1c").strip() if self.attachment_path else ""

        targets = []
        for recipient in self.current_recipients():
            name = recipient["name"]
            is_group = self.mode == "group"
            targets.append({
                "kind": "group" if is_group else "phone",
                "to": name if is_group else recipient["phone"],
                "name": name,
                "number": recipient["phone"],
                "display": f"group '{name}'" if is_group else f"{name} ({recipient['phone']})",
                "message": render_custom_text(message, name) if message else "",
                "caption": render_custom_text(caption, name) if caption else None,
            })

        self.begin_run({
            "name": self.name_input.get().strip(),
            "audience": self.audience_text(),
            "attachment_path": os.path.abspath(self.attachment_path) if self.attachment_path else None,
            "caption": bool(caption),
            "targets": targets,
        })

    def begin_run(self, run):
        for target in run["targets"]:
            target.update(status="queued", detail="", time="")
        self.run = run
        self.is_paused = False
        self.is_stopped = False
        self.sync_waiting = False
        self.skip_sync_wait = False

        # Fresh table, counters and log for this run
        self.run_tree.delete(*self.run_tree.get_children())
        for target in run["targets"]:
            target["iid"] = self.run_tree.insert("", "end", values=(target["name"], target["number"], "Queued", "", ""), tags=("queued",))
        self.clear_activity_log()
        self.run_name_lbl.configure(text=run["name"])
        self.run_audience_lbl.configure(text=run["audience"])
        self.set_run_state("Sending", SUCCESS_TEXT)
        self.set_live_status("Starting...")
        self.pause_btn.configure(text="Pause", state="normal")
        self.stop_btn.configure(text="Stop", state="normal")
        self.run_controls.grid()
        self.done_card.grid_forget()
        self.update_run_counts()
        self.show_page("send")
        self.show_phase("running")

        self.textbox_log(f"SYSTEM: Campaign '{run['name']}' started for {len(run['targets'])} recipient(s): {run['audience']}.")
        if run["attachment_path"]:
            self.textbox_log(f"SYSTEM: Attachment: {os.path.basename(run['attachment_path'])}" + (" (with caption)" if run["caption"] else ""))
        else:
            self.textbox_log("SYSTEM: No attachment. Sending text messages only.")

        self.running_thread = threading.Thread(target=self._automation_worker, args=(run,), daemon=True)
        self.running_thread.start()

    def _automation_worker(self, run):
        targets = run["targets"]
        try:
            logger.info("SYSTEM: Initializing automation sequence...")
            attachment_path = run["attachment_path"]

            # WhatsApp automation login
            self._ui(self.set_live_status, "Opening WhatsApp Web in Chrome...")
            wa_automation = WhatsAppAutomation()
            self.active_wa = wa_automation

            try:
                wa_automation.launch_browser()
                logger.info("SUCCESS: WhatsApp Web socket connection established")
                self._ui(self.set_live_status, "Waiting for WhatsApp Web to load...")

                # Check login or await manual QR scans
                if not wa_automation.wait_for_login():
                    self._ui(self.set_live_status, "Scan the QR code in the Chrome window with your phone.")

                    logged_in = False
                    for attempt in range(24): # Await up to 120s
                        # Check for Stop/Pause during QR wait
                        if self.is_stopped:
                            break
                        while self.is_paused:
                            if self.is_stopped:
                                break
                            time.sleep(0.5)

                        time.sleep(5)
                        logger.info(f"SYSTEM: Re-checking login state (Attempt {attempt+1}/24)...")
                        if wa_automation.wait_for_login():
                            logged_in = True
                            break

                    if self.is_stopped:
                        logger.info("SYSTEM: Login cancelled by user stop.")
                        run["end_reason"] = "Stopped before sending"
                        return

                    if not logged_in:
                        logger.error("SYSTEM: Authentication check timed out. User was not logged into WhatsApp Web.")
                        run["end_reason"] = "WhatsApp Web login timed out"
                        return

                self._ui(self.set_live_status, "Logged in. Sending...")

                if attachment_path and not os.path.exists(attachment_path):
                    logger.warning(f"SYSTEM: Attachment not found at '{attachment_path}'. Sending messages without attachment.")
                    attachment_path = None
                elif attachment_path:
                    logger.info(f"DEBUG: Loading attachment '{os.path.basename(attachment_path)}' ({os.path.getsize(attachment_path)/(1024*1024):.1f}MB)")

                # Send Messages loop
                for i, target in enumerate(targets):
                    # Check for Stop/Pause
                    if self.is_stopped:
                        logger.info("SYSTEM: Automation stopped by user.")
                        break
                    while self.is_paused:
                        if self.is_stopped:
                            break
                        time.sleep(0.5)
                    if self.is_stopped:
                        logger.info("SYSTEM: Automation stopped by user.")
                        break

                    display = target["display"]
                    self._ui(self.set_target_status, i, "sending", "Opening chat")
                    self._ui(self.set_live_status, f"Messaging {display}...")

                    logger.info(f"TASK: Sending message to {display}")
                    if target["kind"] == "group":
                        success = wa_automation.send_to_group(
                            target["to"], target["message"],
                            attachment_path=attachment_path, caption=target["caption"]
                        )
                    else:
                        success = wa_automation.send_message(
                            target["to"], target["message"],
                            attachment_path=attachment_path, caption=target["caption"]
                        )

                    if success:
                        self._ui(self.set_target_status, i, "sent", "Message and attachment sent" if attachment_path else "Message sent")
                    else:
                        logger.error(f"SYSTEM: Failed to dispatch message to {display}.")
                        self._ui(self.set_target_status, i, "failed", "Could not send (see log)")

                    # No cooldown needed after the last recipient
                    if i == len(targets) - 1:
                        break

                    # Wait cooling delay
                    delay_seconds = 5
                    logger.info(f"SYSTEM: Cooling down for {delay_seconds} seconds to comply with anti-spam rate limits...")
                    self._ui(self.set_live_status, f"Waiting {delay_seconds} seconds before the next message...")

                    # Responsive sleep interval
                    for _ in range(delay_seconds * 2):
                        if self.is_stopped:
                            break
                        while self.is_paused:
                            if self.is_stopped:
                                break
                            time.sleep(0.5)
                        time.sleep(0.5)

                if self.is_stopped:
                    run["end_reason"] = "Stopped before sending"
                else:
                    logger.info("SYSTEM: All messages have been processed.")

            except Exception as e:
                logger.error(f"SYSTEM: Error during browser automation processing: {e}")
                run["end_reason"] = "The run ended with an error (see log)"
            finally:
                # Anything sent (even by a run that was stopped or failed part-way) needs time
                # to sync from WhatsApp Web to the phone before the browser closes
                if wa_automation.sends_clicked > 0:
                    self._wait_for_phone_sync()
                logger.info("SYSTEM: Shutting down browser backend.")
                wa_automation.close_browser()
                self.active_wa = None

        except Exception as e:
            logger.error(f"SYSTEM: Critical execution error: {e}")
            run["end_reason"] = "The run ended with an error (see log)"
        finally:
            self._ui(self.finish_run, run)

    def _wait_for_phone_sync(self):
        self.sync_waiting = True
        self.skip_sync_wait = False
        minutes = SYNC_WAIT_SECONDS // 60
        logger.info(f"SYSTEM: Keeping WhatsApp Web open for {minutes} minutes so messages sync to the phone. Press STOP to close sooner.")
        self._ui(self._enter_sync_wait)
        try:
            remaining = SYNC_WAIT_SECONDS
            while remaining > 0 and not self.skip_sync_wait:
                left = f"{remaining // 60}:{remaining % 60:02d}"
                self._ui(self.set_live_status, f"Letting messages sync to your phone. WhatsApp closes in {left}.")
                time.sleep(1)
                remaining -= 1
            if not self.skip_sync_wait:
                logger.info("SYSTEM: Phone sync wait finished.")
        finally:
            self.sync_waiting = False

    def _enter_sync_wait(self):
        self.set_run_state("Syncing with phone", SUCCESS_TEXT)
        self.pause_btn.configure(state="disabled")
        self.stop_btn.configure(text="Close WhatsApp now")

    def finish_run(self, run):
        if run is not self.run:
            return
        reason = run.get("end_reason") or "Not sent"
        for i, target in enumerate(run["targets"]):
            if target["status"] in ("queued", "sending"):
                self.set_target_status(i, "skipped", reason)
        sent, failed, skipped = self.update_run_counts()
        total = len(run["targets"])
        stopped = self.is_stopped

        if stopped:
            state, color, title = "Stopped", TEXT_MUTED, "Campaign stopped"
        elif sent == 0 and (failed or skipped):
            state, color, title = "Did not finish", DANGER, "Nothing was sent"
        elif failed:
            state, color, title = "Finished", TEXT_MUTED, f"Finished, with {failed:,} to retry"
        else:
            state, color, title = "Finished", SUCCESS_TEXT, "All messages sent"
        self.set_run_state(state, color)
        self.set_live_status("WhatsApp Web is closed." if not skipped or sent else reason + ".")

        text = f"{sent:,} of {total:,} received the message."
        failed_targets = [t for t in run["targets"] if t["status"] == "failed"]
        if failed_targets:
            shown = failed_targets[:10]
            text += " These did not go through:\n" + "\n".join(f"{t['name']} · {t['detail']}" for t in shown)
            if len(failed_targets) > len(shown):
                text += f"\nand {len(failed_targets) - len(shown)} more"
        self.done_title_lbl.configure(text=title)
        self.done_text_lbl.configure(text=text)
        if failed_targets:
            self.retry_btn.configure(text=f"Retry failed ({len(failed_targets):,})")
            self.retry_btn.pack(side="left")
        else:
            self.retry_btn.pack_forget()
        self.run_controls.grid_remove()
        self.done_card.grid(row=1, column=0, columnspan=2, pady=(20, 0), sticky="ew")

        self.history.insert(0, {
            "date": datetime.now().isoformat(timespec="seconds"),
            "name": run["name"], "audience": run["audience"],
            "sent": sent, "failed": failed, "skipped": skipped, "total": total, "stopped": stopped,
        })
        self._save_history()
        self.phase = "done"
        self.update_header()

    def retry_failed(self):
        if not self.run or (self.running_thread and self.running_thread.is_alive()):
            return
        failed = [dict(t) for t in self.run["targets"] if t["status"] == "failed"]
        if not failed:
            return
        name = self.run["name"]
        self.begin_run(dict(self.run, name=name if name.endswith("(retry)") else f"{name} (retry)",
                            targets=failed, end_reason=None))

    def new_campaign(self):
        if self.running_thread and self.running_thread.is_alive():
            return
        self.reset_campaign_form()
        self.show_phase("audience")

    def on_close(self):
        wa = self.active_wa
        if wa:
            if self.sync_waiting:
                logger.warning("SYSTEM: App closed during the phone sync wait. Recently sent messages may not appear on the phone yet.")
            self.is_stopped = True
            self.skip_sync_wait = True
            wa.close_browser()
        self.destroy()

if __name__ == "__main__":
    app = App()
    app.mainloop()
