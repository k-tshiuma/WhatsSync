import os
import sys
import time
import threading
import logging
import customtkinter as ctk
from tkinter import filedialog
from app.config.settings import settings
from app.utils.logger import logger
from app.integrations.google_contacts import (
    get_people_service, list_contact_labels, get_contacts_from_label,
    load_cached_credentials, get_account_email, sign_out, client_secrets_present,
    get_label_member_count
)
from app.services.contact_service import validate_and_deduplicate_contacts
from app.services.whatsapp_message_builder import build_registration_message, render_caption
from app.integrations.whatsapp_automation import WhatsAppAutomation

# Modern Dark Theme Palette
BG_COLOR = "#0B0F19"
SIDEBAR_BG = "#111625"
CARD_BG = "#182235"
CARD_BORDER = "#2D3748"
INPUT_BG = "#1F2937"
INPUT_BORDER = "#374151"
TEXT_PRIMARY = "#FFFFFF"
TEXT_MUTED = "#9CA3AF"

ACCENT_GREEN = "#10B981"
ACCENT_BLUE = "#3B82F6"
ACCENT_RED = "#EF4444"
ACCENT_CYAN = "#06B6D4"

TERMINAL_BG = "#050810"

# Configure customtkinter aesthetics
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

LOG_TAG_COLORS = {
    "INFO": "#10B981",     # Green
    "SUCCESS": "#34D399",  # Bright green
    "SYNC": "#60A5FA",     # Blue
    "SYSTEM": "#22D3EE",   # Cyan
    "TASK": "#E5E7EB",     # White
    "WARNING": "#F59E0B",  # Yellow
    "ERROR": "#EF4444",    # Red
    "DEBUG": "#9CA3AF"     # Muted gray
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
        msg = self.format(record)
        # Safely schedule the UI write on the main thread
        self.textbox.after(0, self._safe_write, msg, record.levelname)

    def _safe_write(self, msg, levelname):
        try:
            self.textbox.configure(state="normal")
            
            tag = classify_log_line(msg, levelname)
            self.textbox.insert("end", msg + "\n", tag)
            self.textbox.configure(state="disabled")
            self.textbox.see("end")
        except Exception:
            pass

class CampaignDialog(ctk.CTkToplevel):
    """Modal shown on START TASK to name the campaign and choose its attachment before it runs."""
    NAME_MAX = 80
    DESCRIPTION_MAX = 250
    # WhatsApp's own caption limit
    CAPTION_MAX = 1024
    ATTACHMENT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".pdf")

    def __init__(self, master, label_name, contact_count, on_confirm, default_attachment=""):
        super().__init__(master)
        self.on_confirm = on_confirm
        self.label_name = label_name

        self.title("New Campaign")
        self.configure(fg_color=BG_COLOR)
        self.resizable(False, False)
        self.transient(master)

        card = ctk.CTkFrame(self, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        card.pack(padx=16, pady=16, fill="both", expand=True)

        title_lbl = ctk.CTkLabel(card, text="📢  New Campaign", font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"), text_color="#FFFFFF")
        title_lbl.pack(padx=24, pady=(22, 2), anchor="w")
        desc_lbl = ctk.CTkLabel(card, text="Name this run and choose what to send with the message.", font=ctk.CTkFont(family="Segoe UI", size=12), text_color=TEXT_MUTED)
        desc_lbl.pack(padx=24, pady=(0, 16), anchor="w")

        # Selected label and its contact count
        info_box = ctk.CTkFrame(card, fg_color="#101726", corner_radius=8)
        info_box.pack(padx=24, pady=(0, 16), fill="x")
        info_box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(info_box, text="CONTACT LABEL", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=TEXT_MUTED).grid(row=0, column=0, padx=15, pady=(12, 4), sticky="w")
        ctk.CTkLabel(info_box, text=label_name, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color=TEXT_PRIMARY).grid(row=0, column=1, padx=15, pady=(12, 4), sticky="e")
        ctk.CTkLabel(info_box, text="CONTACTS", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=TEXT_MUTED).grid(row=1, column=0, padx=15, pady=(4, 12), sticky="w")
        self.count_lbl = ctk.CTkLabel(info_box, text="", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        self.count_lbl.grid(row=1, column=1, padx=15, pady=(4, 12), sticky="e")

        # Campaign name (required)
        ctk.CTkLabel(card, text="Campaign Name", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color="#FFFFFF").pack(padx=24, pady=(0, 4), anchor="w")
        self.name_input = ctk.CTkEntry(card, placeholder_text="e.g. Spring Semester Registration", height=38, fg_color=INPUT_BG, border_color=INPUT_BORDER, text_color=TEXT_PRIMARY)
        self.name_input.pack(padx=24, pady=(0, 12), fill="x")

        # Short description (optional, length-limited)
        desc_header = ctk.CTkFrame(card, fg_color="transparent")
        desc_header.pack(padx=24, pady=(0, 4), fill="x")
        ctk.CTkLabel(desc_header, text="Short Description (optional)", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color="#FFFFFF").pack(side="left")
        self.desc_counter_lbl = ctk.CTkLabel(desc_header, text=f"0 / {self.DESCRIPTION_MAX}", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_MUTED)
        self.desc_counter_lbl.pack(side="right")

        self.desc_input = ctk.CTkTextbox(card, height=60, fg_color=INPUT_BG, border_color=INPUT_BORDER, border_width=2, text_color=TEXT_PRIMARY, wrap="word", font=ctk.CTkFont(family="Segoe UI", size=13))
        self.desc_input.pack(padx=24, pady=(0, 12), fill="x")
        self.desc_input.bind("<KeyRelease>", lambda e: self._enforce_limit(self.desc_input, self.DESCRIPTION_MAX, self.desc_counter_lbl))

        # Attachment (optional): image or PDF sent after the message
        ctk.CTkLabel(card, text="Attachment (optional)", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color="#FFFFFF").pack(padx=24, pady=(0, 4), anchor="w")
        attach_row = ctk.CTkFrame(card, fg_color="transparent")
        attach_row.pack(padx=24, pady=(0, 2), fill="x")
        attach_row.grid_columnconfigure(0, weight=1)

        self.attachment_input = ctk.CTkEntry(attach_row, placeholder_text="No file selected", height=38, fg_color=INPUT_BG, border_color=INPUT_BORDER, text_color=TEXT_PRIMARY)
        self.attachment_input.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.attachment_input.bind("<KeyRelease>", lambda e: self._update_caption_state())

        browse_btn = ctk.CTkButton(
            attach_row, text="Browse", width=80, height=38, fg_color="transparent", hover_color="#1E293B", text_color="#FFFFFF",
            border_color="#374151", border_width=1, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), command=self._browse_attachment
        )
        browse_btn.grid(row=0, column=1, padx=(0, 8))

        clear_btn = ctk.CTkButton(
            attach_row, text="✕", width=38, height=38, fg_color="transparent", hover_color="#271C22", text_color=ACCENT_RED,
            border_color="#374151", border_width=1, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), command=self._clear_attachment
        )
        clear_btn.grid(row=0, column=2)

        ctk.CTkLabel(card, text="PNG, JPG, JPEG or PDF", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_MUTED).pack(padx=24, pady=(0, 8), anchor="w")

        # Caption shown under the attachment
        caption_header = ctk.CTkFrame(card, fg_color="transparent")
        caption_header.pack(padx=24, pady=(0, 4), fill="x")
        ctk.CTkLabel(caption_header, text="Attachment Caption (optional)", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color="#FFFFFF").pack(side="left")
        self.caption_counter_lbl = ctk.CTkLabel(caption_header, text=f"0 / {self.CAPTION_MAX}", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_MUTED)
        self.caption_counter_lbl.pack(side="right")

        self.caption_input = ctk.CTkTextbox(card, height=60, fg_color=INPUT_BG, border_color=INPUT_BORDER, border_width=2, text_color=TEXT_PRIMARY, wrap="word", font=ctk.CTkFont(family="Segoe UI", size=13))
        self.caption_input.pack(padx=24, pady=(0, 2), fill="x")
        self.caption_input.bind("<KeyRelease>", lambda e: self._enforce_limit(self.caption_input, self.CAPTION_MAX, self.caption_counter_lbl))
        self.caption_hint_lbl = ctk.CTkLabel(card, text="Use {{name}} to insert each contact's name.", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_MUTED)
        self.caption_hint_lbl.pack(padx=24, pady=(0, 6), anchor="w")

        if default_attachment:
            self.attachment_input.insert(0, default_attachment)
        self._update_caption_state()

        self.error_lbl = ctk.CTkLabel(card, text="", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=ACCENT_RED)
        self.error_lbl.pack(padx=24, pady=(0, 6), anchor="w")

        # Actions
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(padx=24, pady=(0, 22), fill="x")
        actions.grid_columnconfigure((0, 1), weight=1)

        cancel_btn = ctk.CTkButton(
            actions, text="Cancel", height=40, fg_color="transparent", hover_color="#1E293B", text_color="#FFFFFF",
            border_color="#374151", border_width=1, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), command=self.destroy
        )
        cancel_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.confirm_btn = ctk.CTkButton(
            actions, text="▶  START CAMPAIGN", height=40, fg_color=ACCENT_GREEN, hover_color="#0D9488", text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), command=self._confirm
        )
        self.confirm_btn.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.set_contact_count(contact_count, checking=True)

        self.name_input.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self.destroy())

        # Center over the main window, then make modal once it is visible
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_reqheight()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(100, self._make_modal)

    EMPTY_LABEL_ERROR = "This label has no contacts. Choose another label."

    def set_contact_count(self, contact_count, checking=False):
        """Show the label's contact count; checking=True marks it as cached while a fresh count loads."""
        text = f"{contact_count:,} contact{'' if contact_count == 1 else 's'}"
        if checking:
            text += "  (checking...)"
        self.count_lbl.configure(text=text, text_color=ACCENT_GREEN if contact_count else ACCENT_RED)

        if contact_count:
            # Only clear the empty-label error, not a validation error the user is looking at
            if self.error_lbl.cget("text") == self.EMPTY_LABEL_ERROR:
                self.error_lbl.configure(text="")
            self.confirm_btn.configure(state="normal")
        else:
            self.error_lbl.configure(text=self.EMPTY_LABEL_ERROR)
            self.confirm_btn.configure(state="disabled")

    def _make_modal(self):
        try:
            self.grab_set()
            self.lift()
            self.name_input.focus_set()
        except Exception:
            pass

    def _enforce_limit(self, textbox, max_chars, counter_lbl):
        text = textbox.get("1.0", "end-1c")
        if len(text) > max_chars:
            textbox.delete(f"1.0+{max_chars}c", "end")
            text = text[:max_chars]
        counter_lbl.configure(text=f"{len(text)} / {max_chars}")

    def _browse_attachment(self):
        filename = filedialog.askopenfilename(
            parent=self,
            title="Select Attachment",
            filetypes=[("Images and PDF", "*.png *.jpg *.jpeg *.pdf"), ("Images", "*.png *.jpg *.jpeg"), ("PDF", "*.pdf")]
        )
        if filename:
            self.attachment_input.delete(0, "end")
            self.attachment_input.insert(0, filename)
            self._update_caption_state()

    def _clear_attachment(self):
        self.attachment_input.delete(0, "end")
        self._update_caption_state()

    def _update_caption_state(self):
        # A caption only makes sense with an attachment
        has_attachment = bool(self.attachment_input.get().strip())
        self.caption_input.configure(state="normal" if has_attachment else "disabled",
                                     text_color=TEXT_PRIMARY if has_attachment else TEXT_MUTED)

    def _confirm(self):
        if self.confirm_btn.cget("state") == "disabled":
            return
        name = self.name_input.get().strip()
        if not name:
            self.error_lbl.configure(text="Please enter a campaign name.")
            self.name_input.focus_set()
            return
        if len(name) > self.NAME_MAX:
            self.error_lbl.configure(text=f"Campaign name must be {self.NAME_MAX} characters or fewer.")
            return
        self._enforce_limit(self.desc_input, self.DESCRIPTION_MAX, self.desc_counter_lbl)
        description = self.desc_input.get("1.0", "end-1c").strip()

        attachment = self.attachment_input.get().strip()
        caption = ""
        if attachment:
            attachment = os.path.abspath(attachment)
            if not attachment.lower().endswith(self.ATTACHMENT_EXTENSIONS):
                self.error_lbl.configure(text="Attachment must be a PNG, JPG, JPEG or PDF file.")
                return
            if not os.path.isfile(attachment):
                self.error_lbl.configure(text="Attachment file not found.")
                return
            self._enforce_limit(self.caption_input, self.CAPTION_MAX, self.caption_counter_lbl)
            caption = self.caption_input.get("1.0", "end-1c").strip()

        self.destroy()
        self.on_confirm(self.label_name, name, description, attachment or None, caption)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window settings
        self.title("WhatsApp Automation Portal")
        self.geometry("1200x800")
        self.minsize(1050, 750)
        self.configure(fg_color=BG_COLOR)

        # Application state
        self.labels_list = []
        self.running_thread = None
        self.is_paused = False
        self.is_stopped = False
        self.start_time = None
        self.elapsed_seconds = 0
        self.sent_count = 0
        self.failures_count = 0
        self.total_contacts_count = 0

        # Grid configuration for main window layout
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0, minsize=260)  # Left Sidebar
        self.grid_columnconfigure(1, weight=1)              # Main Content

        # Initialize main frames
        self.create_sidebar()
        self.create_main_container()

        # Connect logging bridge
        self.setup_logging_bridge()

        # Load Google Contact labels on start
        self.load_labels_async()

    # ----------------------------------------------------
    # Sidebar Navigation Creation
    # ----------------------------------------------------
    def create_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, fg_color=SIDEBAR_BG, corner_radius=0, border_width=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(9, weight=1)  # Push settings & support to bottom

        # Logo and Title Header
        logo_container = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        logo_container.grid(row=0, column=0, padx=20, pady=30, sticky="ew")

        # Lightning Bolt Logo Icon
        logo_icon = ctk.CTkLabel(
            logo_container,
            text="⚡",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#FFFFFF",
            fg_color=ACCENT_GREEN,
            corner_radius=8,
            width=36,
            height=36
        )
        logo_icon.grid(row=0, column=0, rowspan=2, padx=(0, 10))

        logo_title = ctk.CTkLabel(
            logo_container,
            text="Admin Panel",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#FFFFFF"
        )
        logo_title.grid(row=0, column=1, sticky="w")

        logo_subtitle = ctk.CTkLabel(
            logo_container,
            text="Power User",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED
        )
        logo_subtitle.grid(row=1, column=1, sticky="w")

        # Sidebar Buttons (Dashboard, Campaigns, History, Logs)
        self.sidebar_buttons = {}
        nav_items = [
            ("Dashboard", "⊞", "dashboard"),
            ("Campaigns", "📢", "campaigns"),
            ("History", "🕒", "history"),
            ("Logs", "📄", "logs")
        ]

        for i, (name, icon, key) in enumerate(nav_items):
            btn = ctk.CTkButton(
                self.sidebar_frame,
                text=f"  {icon}  {name}",
                anchor="w",
                height=45,
                fg_color="transparent",
                text_color=TEXT_MUTED,
                hover_color="#1E293B",
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                command=lambda k=key: self.switch_page(k)
            )
            btn.grid(row=i+1, column=0, padx=15, pady=5, sticky="ew")
            self.sidebar_buttons[key] = btn

        # Highlight default tab (Dashboard)
        self.active_tab = "dashboard"
        self.sidebar_buttons["dashboard"].configure(
            fg_color="#1E293B",
            text_color=ACCENT_GREEN
        )

        # Bottom Sidebar Buttons
        self.start_new_task_btn = ctk.CTkButton(
            self.sidebar_frame,
            text="＋ Start New Task",
            height=40,
            fg_color=ACCENT_GREEN,
            hover_color="#0D9488",
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=lambda: self.switch_page("dashboard")
        )
        self.start_new_task_btn.grid(row=10, column=0, padx=15, pady=(20, 10), sticky="ew")

        self.settings_btn = ctk.CTkButton(
            self.sidebar_frame,
            text="  ⚙️  Settings",
            anchor="w",
            height=40,
            fg_color="transparent",
            text_color=TEXT_MUTED,
            hover_color="#1E293B",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self.switch_page("settings")
        )
        self.settings_btn.grid(row=11, column=0, padx=15, pady=5, sticky="ew")
        self.sidebar_buttons["settings"] = self.settings_btn

        self.support_btn = ctk.CTkButton(
            self.sidebar_frame,
            text="  ❓  Support",
            anchor="w",
            height=40,
            fg_color="transparent",
            text_color=TEXT_MUTED,
            hover_color="#1E293B",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self.switch_page("support")
        )
        self.support_btn.grid(row=12, column=0, padx=15, pady=(5, 20), sticky="ew")
        self.sidebar_buttons["support"] = self.support_btn


    # ----------------------------------------------------
    # Main Container and Header Creation
    # ----------------------------------------------------
    def create_main_container(self):
        self.main_container = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.main_container.grid(row=0, column=1, sticky="nsew", padx=25, pady=0)
        
        self.main_container.grid_rowconfigure(0, weight=0)  # Top Header
        self.main_container.grid_rowconfigure(1, weight=1)  # Active Page Frame
        self.main_container.grid_columnconfigure(0, weight=1)

        # Header Frame
        header_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        header_frame.grid(row=0, column=0, pady=(20, 15), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        header_frame.grid_columnconfigure(1, weight=0)

        # Left Header - Title and Version Badge
        left_header = ctk.CTkFrame(header_frame, fg_color="transparent")
        left_header.grid(row=0, column=0, sticky="w")

        title_lbl = ctk.CTkLabel(
            left_header,
            text="WhatsApp Automation Portal",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=ACCENT_GREEN
        )
        title_lbl.pack(side="left")

        version_badge = ctk.CTkFrame(left_header, fg_color="#1E293B", corner_radius=4)
        version_badge.pack(side="left", padx=10)
        version_lbl = ctk.CTkLabel(
            version_badge,
            text="V2.4.0",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=TEXT_MUTED
        )
        version_lbl.pack(padx=6, pady=2)

        # Right Header - System Status dot and Action Icons
        right_header = ctk.CTkFrame(header_frame, fg_color="transparent")
        right_header.grid(row=0, column=1, sticky="e")

        # System Active status dot
        status_dot = ctk.CTkLabel(
            right_header,
            text="● System Active",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=ACCENT_GREEN
        )
        status_dot.pack(side="left", padx=(0, 20))

        # Icon labels (utilizing unicode characters)
        bell_icon = ctk.CTkLabel(right_header, text="🔔", font=ctk.CTkFont(size=16), text_color=TEXT_MUTED, cursor="hand2")
        bell_icon.pack(side="left", padx=10)

        cog_icon = ctk.CTkLabel(right_header, text="⚙️", font=ctk.CTkFont(size=16), text_color=TEXT_MUTED, cursor="hand2")
        cog_icon.pack(side="left", padx=10)

        user_icon = ctk.CTkLabel(
            right_header,
            text="👤",
            font=ctk.CTkFont(size=16),
            text_color="#FFFFFF",
            fg_color="#1E293B",
            corner_radius=20,
            width=32,
            height=32
        )
        user_icon.pack(side="left", padx=(10, 0))

        # ----------------------------------------------------
        # Pages Definition
        # ----------------------------------------------------
        self.pages_container = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.pages_container.grid(row=1, column=0, sticky="nsew", pady=(0, 20))
        self.pages_container.grid_rowconfigure(0, weight=1)
        self.pages_container.grid_columnconfigure(0, weight=1)

        # Create different pages
        self.create_dashboard_page()
        self.create_settings_page()
        self.create_logs_page()
        self.create_placeholder_page("campaigns", "Campaign Manager - Setup Message Sequences & Schedules")
        self.create_placeholder_page("history", "Execution History - Archived Run Records")
        self.create_placeholder_page("support", "Support Desk - Documentation & Feedback Channels")

        # Display dashboard page first
        self.current_page_frame = self.dashboard_page
        self.current_page_frame.grid(row=0, column=0, sticky="nsew")

    # ----------------------------------------------------
    # Page Navigation Switcher
    # ----------------------------------------------------
    def switch_page(self, page_key):
        # Remove current active page
        if self.current_page_frame:
            self.current_page_frame.grid_forget()

        # Update sidebar button states
        for key, btn in self.sidebar_buttons.items():
            if key == page_key:
                btn.configure(fg_color="#1E293B", text_color=ACCENT_GREEN)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT_MUTED)

        # Display selected page
        page_mapping = {
            "dashboard": self.dashboard_page,
            "campaigns": self.campaigns_page,
            "history": self.history_page,
            "logs": self.logs_page,
            "settings": self.settings_page,    # Direct config panel
            "support": self.support_page
        }

        self.current_page_frame = page_mapping.get(page_key, self.dashboard_page)
        self.current_page_frame.grid(row=0, column=0, sticky="nsew")

        if page_key == "logs":
            self.load_log_file()
        elif page_key == "settings":
            self.refresh_account_status_async()

    # ----------------------------------------------------
    # Page: Dashboard View (Main Layout)
    # ----------------------------------------------------
    def create_dashboard_page(self):
        self.dashboard_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        
        self.dashboard_page.grid_rowconfigure(0, weight=0)  # Metrics Cards
        self.dashboard_page.grid_rowconfigure(1, weight=3)  # Left & Right Section Cards
        self.dashboard_page.grid_rowconfigure(2, weight=4)  # Live System Terminal Box
        self.dashboard_page.grid_columnconfigure(0, weight=4)
        self.dashboard_page.grid_columnconfigure(1, weight=6)

        # ----------------------------------------------------
        # Metrics cards (3 columns)
        # ----------------------------------------------------
        metrics_frame = ctk.CTkFrame(self.dashboard_page, fg_color="transparent")
        metrics_frame.grid(row=0, column=0, columnspan=2, pady=(0, 15), sticky="ew")
        metrics_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Card 1: Contacts Found
        self.contacts_card = ctk.CTkFrame(metrics_frame, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        self.contacts_card.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        self.create_metric_widget(self.contacts_card, "Contacts Found", "👥", "0", "", ACCENT_BLUE)

        # Card 2: Messages Sent
        self.messages_card = ctk.CTkFrame(metrics_frame, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        self.messages_card.grid(row=0, column=1, padx=5, sticky="nsew")
        self.create_metric_widget(self.messages_card, "Messages Sent", "✈️", "0", "", ACCENT_GREEN)

        # Card 3: Success Rate
        self.success_card = ctk.CTkFrame(metrics_frame, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        self.success_card.grid(row=0, column=2, padx=(10, 0), sticky="nsew")
        self.create_metric_widget(self.success_card, "Success Rate", "🛡️", "—", "", ACCENT_GREEN)

        # ----------------------------------------------------
        # Card Left: Google Integration Section
        # ----------------------------------------------------
        self.google_card = ctk.CTkFrame(self.dashboard_page, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        self.google_card.grid(row=1, column=0, padx=(0, 10), pady=(0, 15), sticky="nsew")
        self.google_card.grid_columnconfigure(0, weight=1)

        # Header Info
        google_icon = ctk.CTkLabel(
            self.google_card,
            text="🔄  Google Integration",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#FFFFFF"
        )
        google_icon.pack(padx=20, pady=(20, 2), anchor="w")

        google_desc = ctk.CTkLabel(
            self.google_card,
            text="Sync contacts for automation",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED
        )
        google_desc.pack(padx=20, pady=(0, 20), anchor="w")

        # Select Contact Label Area
        label_title = ctk.CTkLabel(
            self.google_card,
            text="SELECT CONTACT LABEL",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=TEXT_MUTED
        )
        label_title.pack(padx=20, pady=(10, 2), anchor="w")

        self.label_dropdown = ctk.CTkOptionMenu(
            self.google_card,
            values=["Click 'Refresh Google Contacts'"],
            fg_color=INPUT_BG,
            button_color="#374151",
            button_hover_color="#4B5563",
            dropdown_fg_color=INPUT_BG,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            dropdown_font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=TEXT_PRIMARY,
            height=38,
            command=self.on_label_selected
        )
        self.label_dropdown.pack(padx=20, pady=(0, 15), fill="x")

        # Sync status box frame
        status_box = ctk.CTkFrame(self.google_card, fg_color="#101726", corner_radius=8)
        status_box.pack(padx=20, pady=(0, 20), fill="x")
        status_box.grid_columnconfigure(0, weight=1)
        status_box.grid_columnconfigure(1, weight=0)

        # Sync Status Row
        lbl_status_title = ctk.CTkLabel(status_box, text="SYNC STATUS", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=TEXT_MUTED)
        lbl_status_title.grid(row=0, column=0, padx=15, pady=(12, 4), sticky="w")
        self.sync_status_val = ctk.CTkLabel(status_box, text="Not synced", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=TEXT_MUTED)
        self.sync_status_val.grid(row=0, column=1, padx=15, pady=(12, 4), sticky="e")

        # Last Sync Row
        lbl_sync_title = ctk.CTkLabel(status_box, text="Last Sync", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_MUTED)
        lbl_sync_title.grid(row=1, column=0, padx=15, pady=4, sticky="w")
        self.sync_time_val = ctk.CTkLabel(status_box, text="Never", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_PRIMARY)
        self.sync_time_val.grid(row=1, column=1, padx=15, pady=4, sticky="e")

        # Label Count Row
        lbl_count_title = ctk.CTkLabel(status_box, text="Label Count", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_MUTED)
        lbl_count_title.grid(row=2, column=0, padx=15, pady=(4, 12), sticky="w")
        self.label_count_val = ctk.CTkLabel(status_box, text="—", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_PRIMARY)
        self.label_count_val.grid(row=2, column=1, padx=15, pady=(4, 12), sticky="e")

        # Refresh Action Button
        self.load_labels_btn = ctk.CTkButton(
            self.google_card,
            text="REFRESH GOOGLE CONTACTS",
            fg_color="transparent",
            hover_color="#1E293B",
            text_color="#FFFFFF",
            border_color="#374151",
            border_width=1,
            height=38,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.load_labels_async
        )
        self.load_labels_btn.pack(padx=20, pady=(0, 20), fill="x")

        # ----------------------------------------------------
        # Card Right: Automation Console Section
        # ----------------------------------------------------
        self.console_card = ctk.CTkFrame(self.dashboard_page, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        self.console_card.grid(row=1, column=1, padx=(10, 0), pady=(0, 15), sticky="nsew")
        self.console_card.grid_columnconfigure(0, weight=1)

        # Header title area
        console_header = ctk.CTkFrame(self.console_card, fg_color="transparent")
        console_header.pack(padx=20, pady=(20, 5), fill="x")
        console_header.grid_columnconfigure(0, weight=1)
        console_header.grid_columnconfigure(1, weight=0)

        # Console Title and Active Sequence Name
        title_box = ctk.CTkFrame(console_header, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")
        console_title_lbl = ctk.CTkLabel(
            title_box,
            text="⚙️  Automation Console",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#FFFFFF"
        )
        console_title_lbl.pack(anchor="w")

        self.seq_name_lbl = ctk.CTkLabel(
            title_box,
            text="Active Campaign: 'Idle'",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED
        )
        self.seq_name_lbl.pack(anchor="w")

        # Running indicator badge (upper right)
        self.running_badge = ctk.CTkFrame(console_header, fg_color="#1A2D42", corner_radius=14)
        self.running_badge.grid(row=0, column=1, sticky="e")
        self.running_badge_lbl = ctk.CTkLabel(
            self.running_badge,
            text="●  IDLE",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#9CA3AF"
        )
        self.running_badge_lbl.pack(padx=12, pady=5)

        # Progress details (percentage + counts)
        prog_detail_frame = ctk.CTkFrame(self.console_card, fg_color="transparent")
        prog_detail_frame.pack(padx=20, pady=(20, 5), fill="x")
        prog_detail_frame.grid_columnconfigure(0, weight=1)
        prog_detail_frame.grid_columnconfigure(1, weight=0)

        self.prog_percent_lbl = ctk.CTkLabel(
            prog_detail_frame,
            text="0%",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color="#FFFFFF"
        )
        self.prog_percent_lbl.grid(row=0, column=0, sticky="w")

        self.prog_count_lbl = ctk.CTkLabel(
            prog_detail_frame,
            text="0 / 0 Processed",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=TEXT_MUTED
        )
        self.prog_count_lbl.grid(row=0, column=1, sticky="e")

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(self.console_card, progress_color=ACCENT_GREEN, fg_color="#1F2937", height=8)
        self.progress_bar.pack(padx=20, pady=(5, 10), fill="x")
        self.progress_bar.set(0.0)

        # Live status info label
        self.live_status_lbl = ctk.CTkLabel(
            self.console_card,
            text="Waiting to launch sequence...",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=ACCENT_CYAN
        )
        self.live_status_lbl.pack(padx=20, pady=(2, 12), anchor="w")

        # Control Panel Buttons Frame (Start task, Pause, Stop)
        controls_frame = ctk.CTkFrame(self.console_card, fg_color="transparent")
        controls_frame.pack(padx=20, pady=10, fill="x")
        controls_frame.grid_columnconfigure(0, weight=5)
        controls_frame.grid_columnconfigure(1, weight=3)
        controls_frame.grid_columnconfigure(2, weight=3)

        self.start_btn = ctk.CTkButton(
            controls_frame,
            text="▶  START TASK",
            fg_color=ACCENT_GREEN,
            hover_color="#0D9488",
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=40,
            command=self.start_automation
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.pause_btn = ctk.CTkButton(
            controls_frame,
            text="⏸  PAUSE",
            fg_color="transparent",
            hover_color="#1E293B",
            text_color="#FFFFFF",
            border_color="#374151",
            border_width=1,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=40,
            command=self.toggle_pause
        )
        self.pause_btn.grid(row=0, column=1, padx=3, sticky="ew")

        self.stop_btn = ctk.CTkButton(
            controls_frame,
            text="⏹  STOP",
            fg_color="transparent",
            hover_color="#271C22",
            text_color=ACCENT_RED,
            border_color=ACCENT_RED,
            border_width=1,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=40,
            command=self.stop_automation_run
        )
        self.stop_btn.grid(row=0, column=2, padx=(6, 0), sticky="ew")

        # Bottom Statistics Row inside Console
        stats_frame = ctk.CTkFrame(self.console_card, fg_color="transparent")
        stats_frame.pack(padx=20, pady=(15, 15), fill="x")
        stats_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Elapsed
        self.create_sub_stat(stats_frame, 0, "ELAPSED", "00:00:00")
        # Avg speed
        self.create_sub_stat(stats_frame, 1, "AVG SPEED", "0.0 msg/s")
        # Failures
        self.create_sub_stat(stats_frame, 2, "FAILURES", "0", val_color=ACCENT_RED)

        # ----------------------------------------------------
        # Card Bottom: Live System Terminal
        # ----------------------------------------------------
        self.terminal_card = ctk.CTkFrame(self.dashboard_page, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        self.terminal_card.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self.terminal_card.grid_columnconfigure(0, weight=1)
        self.terminal_card.grid_rowconfigure(1, weight=1)

        # Terminal Header (Title, level selector dropdown, clear trash button)
        terminal_header = ctk.CTkFrame(self.terminal_card, fg_color="transparent")
        terminal_header.grid(row=0, column=0, padx=20, pady=(12, 8), sticky="ew")
        terminal_header.grid_columnconfigure(0, weight=1)
        terminal_header.grid_columnconfigure(1, weight=0)

        terminal_title = ctk.CTkLabel(
            terminal_header,
            text="💻  Live System Terminal",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#FFFFFF"
        )
        terminal_title.grid(row=0, column=0, sticky="w")

        terminal_actions = ctk.CTkFrame(terminal_header, fg_color="transparent")
        terminal_actions.grid(row=0, column=1, sticky="e")

        log_level_lbl = ctk.CTkLabel(
            terminal_actions,
            text="LOGGING: VERBOSE",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=TEXT_MUTED
        )
        log_level_lbl.pack(side="left", padx=15)

        clear_btn = ctk.CTkButton(
            terminal_actions,
            text="🗑️",
            width=30,
            height=30,
            fg_color="transparent",
            hover_color="#1E293B",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=14),
            command=self.clear_terminal_logs
        )
        clear_btn.pack(side="left")

        # Scrolling terminal textbox
        self.log_textbox = ctk.CTkTextbox(self.terminal_card, fg_color=TERMINAL_BG, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.log_textbox.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        self.log_textbox.configure(state="disabled")

        # Configure textbox logging tags color coding
        configure_log_tags(self.log_textbox)

    # Helper to build a metric card sub-widget
    def create_metric_widget(self, parent_frame, title, icon, value, sub_value, icon_color):
        parent_frame.grid_columnconfigure(0, weight=1)
        parent_frame.grid_columnconfigure(1, weight=0)

        # Metric Title
        lbl_title = ctk.CTkLabel(
            parent_frame,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=TEXT_MUTED
        )
        lbl_title.grid(row=0, column=0, padx=18, pady=(15, 5), sticky="w")

        # Metric Icon
        lbl_icon = ctk.CTkLabel(
            parent_frame,
            text=icon,
            font=ctk.CTkFont(size=18),
            text_color=icon_color,
            fg_color="#1E293B",
            corner_radius=20,
            width=36,
            height=36
        )
        lbl_icon.grid(row=0, column=1, padx=18, pady=(15, 5), sticky="e")

        # Metric Core Large Value
        # Keep references to update them dynamically later
        if title == "Contacts Found":
            self.contacts_val_lbl = ctk.CTkLabel(parent_frame, text=value, font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"), text_color="#FFFFFF")
            self.contacts_val_lbl.grid(row=1, column=0, padx=18, pady=(0, 15), sticky="w")
        elif title == "Messages Sent":
            self.messages_val_lbl = ctk.CTkLabel(parent_frame, text=value, font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"), text_color="#FFFFFF")
            self.messages_val_lbl.grid(row=1, column=0, padx=18, pady=(0, 15), sticky="w")
        else:
            self.success_val_lbl = ctk.CTkLabel(parent_frame, text=value, font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"), text_color="#FFFFFF")
            self.success_val_lbl.grid(row=1, column=0, padx=18, pady=(0, 15), sticky="w")

        # Metric Trend Muted Text
        lbl_sub = ctk.CTkLabel(
            parent_frame,
            text=sub_value,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=ACCENT_GREEN
        )
        lbl_sub.grid(row=1, column=1, padx=18, pady=(0, 15), sticky="e")

    # Helper to build details on the console stats row
    def create_sub_stat(self, parent, column, title, default_val, val_color="#FFFFFF"):
        sub_frame = ctk.CTkFrame(parent, fg_color="transparent")
        sub_frame.grid(row=0, column=column, sticky="nsew")
        
        lbl_title = ctk.CTkLabel(
            sub_frame,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
            text_color=TEXT_MUTED
        )
        lbl_title.pack(anchor="center")

        lbl_val = ctk.CTkLabel(
            sub_frame,
            text=default_val,
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=val_color
        )
        lbl_val.pack(anchor="center")

        # Store widget links to update dynamic status
        if title == "ELAPSED":
            self.elapsed_val_lbl = lbl_val
        elif title == "AVG SPEED":
            self.avg_speed_val_lbl = lbl_val
        elif title == "FAILURES":
            self.failures_val_lbl = lbl_val

    # ----------------------------------------------------
    # Page: Settings View
    # ----------------------------------------------------
    def create_settings_page(self):
        self.settings_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.settings_page.grid_columnconfigure(0, weight=1)
        self.settings_page.grid_rowconfigure(0, weight=1)

        # Config Panel card (scrollable so all sections fit at the minimum window size)
        config_card = ctk.CTkScrollableFrame(self.settings_page, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        config_card.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        config_card.grid_columnconfigure(0, weight=1)

        # Page Title
        title_lbl = ctk.CTkLabel(
            config_card,
            text="⚙️  Application Configuration",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#FFFFFF"
        )
        title_lbl.pack(padx=30, pady=(30, 2), anchor="w")

        desc_lbl = ctk.CTkLabel(
            config_card,
            text="Modify settings for direct routing, flyers, and regional defaults.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED
        )
        desc_lbl.pack(padx=30, pady=(0, 25), anchor="w")

        # Divider line
        divider = ctk.CTkFrame(config_card, height=1, fg_color=CARD_BORDER)
        divider.pack(padx=30, pady=(0, 25), fill="x")

        # Google Account (credentials) section
        account_lbl = ctk.CTkLabel(config_card, text="Google Account", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color="#FFFFFF")
        account_lbl.pack(padx=30, pady=(0, 4), anchor="w")

        account_box = ctk.CTkFrame(config_card, fg_color="#101726", corner_radius=8)
        account_box.pack(padx=30, pady=(0, 15), fill="x")
        account_box.grid_columnconfigure(0, weight=1)
        account_box.grid_columnconfigure((1, 2), weight=0)

        self.account_status_lbl = ctk.CTkLabel(
            account_box,
            text="Checking...",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=TEXT_MUTED
        )
        self.account_status_lbl.grid(row=0, column=0, padx=15, pady=(12, 2), sticky="w")

        self.client_secrets_lbl = ctk.CTkLabel(
            account_box,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED
        )
        self.client_secrets_lbl.grid(row=1, column=0, padx=15, pady=(0, 12), sticky="w")

        self.account_signin_btn = ctk.CTkButton(
            account_box,
            text="Sign In",
            width=130,
            height=34,
            fg_color=ACCENT_BLUE,
            hover_color="#2563EB",
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.sign_in_google
        )
        self.account_signin_btn.grid(row=0, column=1, rowspan=2, padx=(0, 8), pady=12)

        self.account_signout_btn = ctk.CTkButton(
            account_box,
            text="Sign Out",
            width=100,
            height=34,
            fg_color="transparent",
            hover_color="#271C22",
            text_color=ACCENT_RED,
            border_color=ACCENT_RED,
            border_width=1,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.sign_out_google
        )
        self.account_signout_btn.grid(row=0, column=2, rowspan=2, padx=(0, 15), pady=12)

        # 1. WhatsApp Group Invite Link input
        invite_lbl = ctk.CTkLabel(config_card, text="WhatsApp Group Invite Link", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color="#FFFFFF")
        invite_lbl.pack(padx=30, pady=(10, 4), anchor="w")
        self.invite_input = ctk.CTkEntry(config_card, placeholder_text="https://chat.whatsapp.com/...", height=38, fg_color=INPUT_BG, border_color=INPUT_BORDER, text_color=TEXT_PRIMARY)
        self.invite_input.pack(padx=30, pady=(0, 15), fill="x")
        self.invite_input.insert(0, settings.WHATSAPP_GROUP_INVITE_LINK or "")

        # 2. Flyer Attachment input + browse button
        flyer_lbl = ctk.CTkLabel(config_card, text="Flyer Image Attachment", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color="#FFFFFF")
        flyer_lbl.pack(padx=30, pady=(10, 4), anchor="w")

        self.flyer_path_frame = ctk.CTkFrame(config_card, fg_color="transparent")
        self.flyer_path_frame.pack(padx=30, pady=(0, 15), fill="x")
        self.flyer_path_frame.grid_columnconfigure(0, weight=1)
        self.flyer_path_frame.grid_columnconfigure(1, weight=0)

        self.flyer_input = ctk.CTkEntry(self.flyer_path_frame, placeholder_text="./data/flyer.png", height=38, fg_color=INPUT_BG, border_color=INPUT_BORDER, text_color=TEXT_PRIMARY)
        self.flyer_input.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.flyer_input.insert(0, settings.FLYER_PATH or "")

        self.flyer_btn = ctk.CTkButton(
            self.flyer_path_frame,
            text="Browse File",
            width=100,
            height=38,
            fg_color="transparent",
            hover_color="#1E293B",
            text_color="#FFFFFF",
            border_color="#374151",
            border_width=1,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.browse_flyer
        )
        self.flyer_btn.grid(row=0, column=1, sticky="e")

        # 3. Default Country dropdown input
        country_lbl = ctk.CTkLabel(config_card, text="Default Phone Country Code (E.164 parsing)", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), text_color="#FFFFFF")
        country_lbl.pack(padx=30, pady=(10, 4), anchor="w")
        self.country_dropdown = ctk.CTkOptionMenu(
            config_card,
            values=["IL", "US", "FR", "IN", "GB"],
            height=38,
            fg_color=INPUT_BG,
            button_color="#374151",
            button_hover_color="#4B5563",
            dropdown_fg_color=INPUT_BG,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=TEXT_PRIMARY
        )
        self.country_dropdown.pack(padx=30, pady=(0, 30), fill="x")
        self.country_dropdown.set(settings.DEFAULT_COUNTRY or "IL")

        # Divider line
        divider2 = ctk.CTkFrame(config_card, height=1, fg_color=CARD_BORDER)
        divider2.pack(padx=30, pady=(0, 25), fill="x")

        # Save Configuration button
        self.save_cfg_btn = ctk.CTkButton(
            config_card,
            text="Save Configuration Settings",
            fg_color=ACCENT_GREEN,
            hover_color="#0D9488",
            text_color="#FFFFFF",
            height=40,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=self.save_current_settings
        )
        self.save_cfg_btn.pack(padx=30, pady=(0, 30), fill="x")

    # ----------------------------------------------------
    # Page: Logs View (contents of the log file on disk)
    # ----------------------------------------------------
    def create_logs_page(self):
        self.logs_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.logs_page.grid_columnconfigure(0, weight=1)
        self.logs_page.grid_rowconfigure(0, weight=1)

        logs_card = ctk.CTkFrame(self.logs_page, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        logs_card.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        logs_card.grid_columnconfigure(0, weight=1)
        logs_card.grid_rowconfigure(1, weight=1)

        logs_header = ctk.CTkFrame(logs_card, fg_color="transparent")
        logs_header.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        logs_header.grid_columnconfigure(0, weight=1)

        logs_title = ctk.CTkLabel(
            logs_header,
            text="📄  Application Logs",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#FFFFFF"
        )
        logs_title.grid(row=0, column=0, sticky="w")

        self.logs_path_lbl = ctk.CTkLabel(
            logs_header,
            text=self.get_log_file_path(),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED
        )
        self.logs_path_lbl.grid(row=1, column=0, sticky="w")

        logs_actions = ctk.CTkFrame(logs_header, fg_color="transparent")
        logs_actions.grid(row=0, column=1, rowspan=2, sticky="e")

        refresh_logs_btn = ctk.CTkButton(
            logs_actions,
            text="⟳  Refresh",
            width=110,
            height=34,
            fg_color="transparent",
            hover_color="#1E293B",
            text_color="#FFFFFF",
            border_color="#374151",
            border_width=1,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.load_log_file
        )
        refresh_logs_btn.pack(side="left", padx=(0, 8))

        open_logs_btn = ctk.CTkButton(
            logs_actions,
            text="Open Folder",
            width=110,
            height=34,
            fg_color="transparent",
            hover_color="#1E293B",
            text_color="#FFFFFF",
            border_color="#374151",
            border_width=1,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.open_log_folder
        )
        open_logs_btn.pack(side="left")

        self.logs_textbox = ctk.CTkTextbox(logs_card, fg_color=TERMINAL_BG, wrap="none", font=ctk.CTkFont(family="Consolas", size=12))
        self.logs_textbox.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        configure_log_tags(self.logs_textbox)
        self.logs_textbox.configure(state="disabled")

    # Helper to construct secondary placeholder layouts
    def create_placeholder_page(self, page_key, text):
        frame = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        
        # Centered card
        card = ctk.CTkFrame(frame, fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1, corner_radius=12)
        card.pack(expand=True, padx=40, pady=40, fill="both")
        
        lbl = ctk.CTkLabel(
            card,
            text=text,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#FFFFFF"
        )
        lbl.pack(expand=True)
        
        # Assign frame references
        if page_key == "campaigns":
            self.campaigns_page = frame
        elif page_key == "history":
            self.history_page = frame
        elif page_key == "support":
            self.support_page = frame

    # ----------------------------------------------------
    # Logging Bridge Setup
    # ----------------------------------------------------
    def setup_logging_bridge(self):
        log_handler = TextboxLogHandler(self.log_textbox)
        log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'))
        logger.addHandler(log_handler)

    # ----------------------------------------------------
    # GUI Interaction Functions
    # ----------------------------------------------------
    def browse_flyer(self):
        filename = filedialog.askopenfilename(
            title="Select Flyer Image",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.gif"), ("All Files", "*.*")]
        )
        if filename:
            self.flyer_input.delete(0, "end")
            self.flyer_input.insert(0, filename)

    def clear_terminal_logs(self):
        try:
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.configure(state="disabled")
        except Exception:
            pass

    def textbox_log(self, text):
        logger.info(text)

    # ----------------------------------------------------
    # Save Settings logic (.env writing)
    # ----------------------------------------------------
    def save_current_settings(self):
        invite_link = self.invite_input.get().strip()
        flyer_path = self.flyer_input.get().strip()
        country = self.country_dropdown.get().strip()

        if not invite_link:
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

        # Update local runtime configurations
        settings.WHATSAPP_GROUP_INVITE_LINK = invite_link
        settings.FLYER_PATH = flyer_path
        settings.DEFAULT_COUNTRY = country

        self.textbox_log("SYSTEM: Configuration settings successfully saved to .env file.")

    # ----------------------------------------------------
    # Load Google Labels logic (Async)
    # ----------------------------------------------------
    def load_labels_async(self):
        self.load_labels_btn.configure(state="disabled")
        self.account_signin_btn.configure(state="disabled")
        self.account_signout_btn.configure(state="disabled")
        self.sync_status_val.configure(text="Syncing...  🔄", text_color="#3B82F6")
        self.textbox_log("SYNC: Fetching contacts labels from Google Contacts API...")
        threading.Thread(target=self._load_labels_worker, daemon=True).start()

    def _load_labels_worker(self):
        try:
            service = get_people_service()
            self.labels_list = list_contact_labels(service)
            label_names = [label.get('formattedName', label.get('name', 'Unnamed Label')) for label in self.labels_list]
            
            if not label_names:
                self.textbox_log("SYNC: Error - No labels found in your Google Contacts account.")
                self.update_ui_state("Sync Error  ❌", "normal", status_color=ACCENT_RED)
                return

            self.label_dropdown.after(0, self._populate_labels_dropdown, label_names)
            self.update_ui_state("Synced  ✅", "normal", last_sync="Just now", status_color=ACCENT_GREEN)
            self.textbox_log(f"SYNC: Successfully loaded {len(label_names)} Google Contact labels.")
        except Exception as e:
            logger.error(f"SYNC: Error loading contact groups: {e}")
            self.update_ui_state("Sync Fail  ❌", "normal", status_color=ACCENT_RED)
        finally:
            # A sync may have just signed someone in (or failed to), so refresh the Settings panel
            self._refresh_account_status_worker()

    def _populate_labels_dropdown(self, values):
        # Keep the user's choice across refreshes if that label still exists
        current = self.label_dropdown.get()
        selected = current if current in values else (values[0] if values else "")
        self.label_dropdown.configure(values=values)
        self.label_dropdown.set(selected)
        # set() doesn't fire the dropdown command, so refresh the count for the selected label
        if selected:
            self.on_label_selected(selected)

    def _find_label(self, label_name):
        return next(
            (g for g in self.labels_list if g.get('formattedName', g.get('name', 'Unnamed Label')) == label_name),
            None
        )

    def on_label_selected(self, label_name):
        group = self._find_label(label_name)
        if not group:
            return
        # memberCount is omitted by the API when the label is empty
        count = group.get('memberCount', 0)
        self.contacts_val_lbl.configure(text=f"{count:,}")
        self.label_count_val.configure(text=f"{count:,} Contacts")

    def update_ui_state(self, sync_status_text, button_state, last_sync=None, status_color=ACCENT_GREEN):
        self.sync_status_val.after(0, lambda: self.sync_status_val.configure(text=sync_status_text, text_color=status_color))
        self.load_labels_btn.after(0, lambda: self.load_labels_btn.configure(state=button_state))
        if last_sync:
            self.sync_time_val.after(0, lambda: self.sync_time_val.configure(text=last_sync))

    # ----------------------------------------------------
    # Google Account (Settings page)
    # ----------------------------------------------------
    def refresh_account_status_async(self):
        threading.Thread(target=self._refresh_account_status_worker, daemon=True).start()

    def _refresh_account_status_worker(self):
        secrets_text = "OAuth client: client_secrets.json found" if client_secrets_present() \
            else "OAuth client: client_secrets.json missing from the app folder"
        try:
            creds = load_cached_credentials()
            if not creds:
                status, color, signed_in = "Not signed in", TEXT_MUTED, False
            else:
                email = get_account_email(creds)
                status = f"Signed in as {email}" if email else "Signed in"
                color, signed_in = ACCENT_GREEN, True
        except Exception as e:
            logger.error(f"SYSTEM: Could not read Google account status: {e}")
            status, color, signed_in = "Sign-in expired or invalid", ACCENT_RED, False
        self.account_status_lbl.after(0, self._apply_account_status, status, color, secrets_text, signed_in)

    def _apply_account_status(self, status, color, secrets_text, signed_in):
        self.account_status_lbl.configure(text=status, text_color=color)
        self.client_secrets_lbl.configure(text=secrets_text)
        self.account_signin_btn.configure(text="Switch Account" if signed_in else "Sign In", state="normal")
        self.account_signout_btn.configure(state="normal" if signed_in else "disabled")

    def _automation_running(self):
        if self.running_thread and self.running_thread.is_alive():
            self.textbox_log("SYSTEM: Error - Stop the running task before changing the Google account.")
            return True
        return False

    def sign_in_google(self):
        if self._automation_running():
            return
        # For "Switch Account", drop the current token so the browser sign-in opens
        sign_out()
        self.account_status_lbl.configure(text="Waiting for browser sign-in...", text_color=ACCENT_BLUE)
        self.textbox_log("SYSTEM: Opening Google sign-in in your browser...")
        self.load_labels_async()

    def sign_out_google(self):
        if self._automation_running():
            return
        sign_out()
        self.labels_list = []
        self.label_dropdown.configure(values=["Click 'Refresh Google Contacts'"])
        self.label_dropdown.set("Click 'Refresh Google Contacts'")
        self.contacts_val_lbl.configure(text="0")
        self.label_count_val.configure(text="—")
        self.sync_status_val.configure(text="Not signed in", text_color=TEXT_MUTED)
        self.sync_time_val.configure(text="Never")
        self.textbox_log("SYSTEM: Signed out of Google.")
        self.refresh_account_status_async()

    # ----------------------------------------------------
    # Logs page
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
        if not self.running_thread or not self.running_thread.is_alive():
            return
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.pause_btn.configure(text="▶  RESUME", fg_color=ACCENT_BLUE, border_color=ACCENT_BLUE)
            self.running_badge_lbl.configure(text="●  PAUSED", text_color="#F59E0B")
            self.running_badge.configure(fg_color="#3B2E1E")
            self.textbox_log("SYSTEM: Automation paused by user.")
        else:
            self.pause_btn.configure(text="⏸  PAUSE", fg_color="transparent", border_color="#374151")
            self.running_badge_lbl.configure(text="●  RUNNING", text_color="#3B82F6")
            self.running_badge.configure(fg_color="#1A2D42")
            self.textbox_log("SYSTEM: Automation resumed.")

    def stop_automation_run(self):
        if not self.running_thread or not self.running_thread.is_alive():
            return
        self.is_stopped = True
        self.textbox_log("SYSTEM: Stop command received. Halting automation sequence...")

    # ----------------------------------------------------
    # Run automation timer update
    # ----------------------------------------------------
    def update_timer(self):
        if self.running_thread and self.running_thread.is_alive():
            if not self.is_paused:
                self.elapsed_seconds += 1
                hours = self.elapsed_seconds // 3600
                minutes = (self.elapsed_seconds % 3600) // 60
                seconds = self.elapsed_seconds % 60
                self.elapsed_val_lbl.configure(text=f"{hours:02d}:{minutes:02d}:{seconds:02d}")
                
                # Calculate speed
                total_processed = self.sent_count + self.failures_count
                if self.elapsed_seconds > 0:
                    avg_speed = total_processed / self.elapsed_seconds
                    self.avg_speed_val_lbl.configure(text=f"{avg_speed:.1f} msg/s")
            
            self.after(1000, self.update_timer)

    # ----------------------------------------------------
    # Start automation process
    # ----------------------------------------------------
    def start_automation(self):
        selected_label = self.label_dropdown.get()
        if selected_label == "Click 'Refresh Google Contacts'" or not selected_label:
            self.textbox_log("SYSTEM: Error - Please select a valid Google Contact label first.")
            return
        if self.running_thread and self.running_thread.is_alive():
            return

        group = self._find_label(selected_label)
        contact_count = group.get('memberCount', 0) if group else 0
        # Pre-fill the attachment with the flyer from Settings, if it exists and is a supported type
        default_attachment = settings.RESOLVED_FLYER_PATH
        if not (os.path.isfile(default_attachment) and default_attachment.lower().endswith(CampaignDialog.ATTACHMENT_EXTENSIONS)):
            default_attachment = ""
        dialog = CampaignDialog(self, selected_label, contact_count, on_confirm=self._launch_campaign, default_attachment=default_attachment)

        # The cached count dates from the last label refresh, so re-check it with Google
        if group and group.get('resourceName'):
            threading.Thread(target=self._refresh_label_count_worker, args=(group, dialog), daemon=True).start()
        else:
            dialog.set_contact_count(contact_count)

    def _refresh_label_count_worker(self, group, dialog):
        try:
            count = get_label_member_count(get_people_service(), group['resourceName'])
        except Exception as e:
            logger.warning(f"SYNC: Could not refresh contact count for '{group.get('formattedName')}': {e}")
            count = group.get('memberCount', 0)
        self.after(0, self._apply_label_count, group, dialog, count)

    def _apply_label_count(self, group, dialog, count):
        group['memberCount'] = count
        name = group.get('formattedName', group.get('name', 'Unnamed Label'))
        if self.label_dropdown.get() == name:
            self.on_label_selected(name)
        # The user may have closed the window while the count was loading
        if dialog.winfo_exists():
            dialog.set_contact_count(count)

    def _launch_campaign(self, selected_label, campaign_name, campaign_description, attachment_path, caption):
        self.textbox_log(f"SYSTEM: Campaign '{campaign_name}' started on label '{selected_label}'.")
        if campaign_description:
            self.textbox_log(f"SYSTEM: Campaign description: {campaign_description}")
        if attachment_path:
            self.textbox_log(f"SYSTEM: Attachment: {os.path.basename(attachment_path)}" + (" (with caption)" if caption else ""))
        else:
            self.textbox_log("SYSTEM: No attachment. Sending text messages only.")

        # Ensure active settings are saved first
        self.save_current_settings()

        # Update initial states
        self.start_btn.configure(state="disabled")
        self.progress_bar.set(0.0)
        self.prog_percent_lbl.configure(text="0%")
        self.prog_count_lbl.configure(text="0 / 0 Processed")
        self.elapsed_val_lbl.configure(text="00:00:00")
        self.avg_speed_val_lbl.configure(text="0.0 msg/s")
        self.failures_val_lbl.configure(text="0")
        
        self.is_paused = False
        self.is_stopped = False
        self.elapsed_seconds = 0
        self.sent_count = 0
        self.failures_count = 0
        self.total_contacts_count = 0

        self.seq_name_lbl.configure(text=f"Active Campaign: '{campaign_name}'")
        self.running_badge_lbl.configure(text="●  RUNNING", text_color="#3B82F6")
        self.running_badge.configure(fg_color="#1A2D42")

        # Spawn dispatcher thread
        self.running_thread = threading.Thread(
            target=self._automation_worker,
            args=(selected_label, attachment_path, caption),
            daemon=True
        )
        self.running_thread.start()
        
        # Start timer updates
        self.after(1000, self.update_timer)

    def _automation_worker(self, label_name, attachment_path=None, caption=""):
        try:
            logger.info("SYSTEM: Initializing automation sequence...")
            
            # Step 1: Find match for label resource name
            selected_group = None
            for group in self.labels_list:
                name = group.get('formattedName', group.get('name', 'Unnamed Label'))
                if name == label_name:
                    selected_group = group
                    break

            if not selected_group:
                logger.error("SYSTEM: Selected contact label could not be found.")
                self.cleanup_console_idle()
                return

            resource_name = selected_group.get('resourceName')
            logger.info(f"SYSTEM: Target Resource Path: {resource_name}")

            # Step 2: Fetch contacts
            self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text=f"Fetching contacts from '{label_name}'..."))
            people_service = get_people_service()
            raw_contacts = get_contacts_from_label(people_service, resource_name)
            
            if not raw_contacts:
                logger.warning(f"SYNC: No contacts found under label: {label_name}")
                self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Completed (Empty Label)"))
                self.cleanup_console_idle()
                return

            # Step 3: Validate and Deduplicate
            self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Processing contact details..."))
            processed_contacts = validate_and_deduplicate_contacts(raw_contacts)
            
            if not processed_contacts:
                logger.warning("SYNC: No valid or unique phone numbers found after processing.")
                self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Completed (No valid numbers)"))
                self.cleanup_console_idle()
                return

            self.total_contacts_count = len(processed_contacts)
            logger.info(f"SYNC: Successfully fetched {self.total_contacts_count} contacts from Google API")
            
            # Update metrics cards label count
            self.label_count_val.after(0, lambda: self.label_count_val.configure(text=f"{self.total_contacts_count} Contacts"))
            self.contacts_val_lbl.after(0, lambda: self.contacts_val_lbl.configure(text=f"{self.total_contacts_count:,}"))

            # Step 4: WhatsApp automation login
            self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Launching WhatsApp Automation Browser..."))
            wa_automation = WhatsAppAutomation()
            
            try:
                wa_automation.launch_browser()
                logger.info("SUCCESS: WhatsApp Web socket connection established")
                self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Waiting for WhatsApp Web login..."))
                
                # Check login or await manual QR scans
                if not wa_automation.wait_for_login():
                    self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Action Required (Scan QR Code)"))
                    
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
                        self.cleanup_console_idle()
                        return

                    if not logged_in:
                        logger.error("SYSTEM: Authentication check timed out. User was not logged into WhatsApp Web.")
                        self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Timeout Login"))
                        self.cleanup_console_idle()
                        return

                self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Logged In. Preparing to dispatch..."))
                
                if attachment_path and not os.path.exists(attachment_path):
                    logger.warning(f"SYSTEM: Attachment not found at '{attachment_path}'. Sending messages without attachment.")
                    attachment_path = None
                elif attachment_path:
                    logger.info(f"DEBUG: Loading attachment '{os.path.basename(attachment_path)}' ({os.path.getsize(attachment_path)/(1024*1024):.1f}MB)")

                # Step 5: Send Messages loop
                for i, contact in enumerate(processed_contacts):
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

                    student_name = contact['name']
                    student_phone = contact['phone']
                    message = build_registration_message(student_name)

                    # Update dispatch status
                    self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(
                        text=f"Currently messaging: {student_name} ({student_phone})..."
                    ))
                    
                    progress_pct = (i + 1) / self.total_contacts_count
                    self.progress_bar.after(0, lambda p=progress_pct: self.progress_bar.set(p))
                    self.prog_percent_lbl.after(0, lambda p=int(progress_pct*100): self.prog_percent_lbl.configure(text=f"{p}%"))
                    self.prog_count_lbl.after(0, lambda count=i+1, total=self.total_contacts_count: self.prog_count_lbl.configure(text=f"{count} / {total} Processed"))

                    logger.info(f"TASK: Sending message to {student_name} ({student_phone})")
                    success = wa_automation.send_message(
                        student_phone, message,
                        attachment_path=attachment_path,
                        caption=render_caption(caption, student_name) if caption else None
                    )
                    
                    if success:
                        self.sent_count += 1
                        self.messages_val_lbl.after(0, lambda: self.messages_val_lbl.configure(text=f"{self.sent_count:,}"))
                    else:
                        logger.error(f"SYSTEM: Failed to dispatch message to {student_name} ({student_phone}).")
                        self.failures_count += 1
                        self.failures_val_lbl.after(0, lambda: self.failures_val_lbl.configure(text=str(self.failures_count)))

                    # Update success rate label
                    total_attempted = self.sent_count + self.failures_count
                    if total_attempted > 0:
                        rate = (self.sent_count / total_attempted) * 100
                        self.success_val_lbl.after(0, lambda r=rate: self.success_val_lbl.configure(text=f"{r:.1f}%"))

                    # Wait cooling delay
                    delay_seconds = 5
                    logger.info(f"SYSTEM: Cooling down for {delay_seconds} seconds to comply with anti-spam rate limits...")
                    
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
                    self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Status: Stopped by User", text_color=ACCENT_RED))
                else:
                    logger.info("SYSTEM: All student invitations have been processed successfully.")
                    self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Status: All Messages Dispatched", text_color=ACCENT_GREEN))
                    self.progress_bar.after(0, lambda: self.progress_bar.set(1.0))
                    self.prog_percent_lbl.after(0, lambda: self.prog_percent_lbl.configure(text="100%"))

            except Exception as e:
                logger.error(f"SYSTEM: Error during browser automation processing: {e}")
                self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Error during sending", text_color=ACCENT_RED))
            finally:
                logger.info("SYSTEM: Shutting down browser backend.")
                wa_automation.close_browser()

        except Exception as e:
            logger.error(f"SYSTEM: Critical execution error: {e}")
            self.live_status_lbl.after(0, lambda: self.live_status_lbl.configure(text="Critical Error", text_color=ACCENT_RED))
        finally:
            self.cleanup_console_idle()

    def cleanup_console_idle(self):
        self.start_btn.after(0, lambda: self.start_btn.configure(state="normal"))
        self.running_badge_lbl.after(0, lambda: self.running_badge_lbl.configure(text="●  IDLE", text_color="#9CA3AF"))
        self.running_badge.after(0, lambda: self.running_badge.configure(fg_color="#1A2D42"))

if __name__ == "__main__":
    app = App()
    app.mainloop()
