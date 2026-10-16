import json
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import threading
import time
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
import sys

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(get_base_path(), "config.json")
CREDENTIALS_FILE = os.path.join(get_base_path(), "credentials.json")
TOKEN_FILE = os.path.join(get_base_path(), "token.json")

#===========Google security configuration===========#
SCOPES = ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.send"]

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as file:
            data = json.load(file)
            if "blocked_addresses" not in data:
                data["blocked_addresses"] = {"@fake-uncc-admissions.com": "Instant Trash"}
            if "blocked_keywords" not in data:
                data["blocked_keywords"] = {"Kirby Alison": "Archive + Label"}
            if "shield_active" not in data:
                data["shield_active"] = True
            if "sources" not in data:
                data["sources"] = {}
            return data
            
    return {
        "sources": {},
        "blocked_addresses": {"@fake-uncc-admissions.com": "Instant Trash"},
        "blocked_keywords": {"Kirby Alison": "Archive + Label"},
        "shield_active": True
    }

def save_config(config):
    with open(CONFIG_FILE, "w") as file:
        json.dump(config, file, indent=4)


class GmailManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Advanced Gmail Manager")
        self.geometry("950x700")
        self.resizable(True, True)
        self.configure(bg="#f4f6f9")
        # Set window icon (use base path to make sure the .ico is found correctly)
        icon_path = os.path.join(get_base_path(), "gmail_management_icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception as e:
                self.log_activity(f"Could not load window icon: {e}")

        self.config_data = load_config()
        self.sidebar_expanded = True
        self.selected_items = set()
        self.source_checkbox_vars = {}
        self.source_action_vars = {} 
        self.current_view_name = "dashboard"
        self.polling_active = False
        self.poll_thread = None
        self.active_canvas = None
        self.poll_var = tk.BooleanVar(value=False)
        
        # Activity log buffer
        self.activity_logs = []
        
        # Daily summary tracking stats
        self.daily_stats = {"trashed": 0, "archived": 0, "rules_hit": 0}

        # Dynamic Labels Cache
        self.available_labels = ["Archive + Label", "Instant Trash", "Do Nothing (Allow)"]

        # --- LAYOUT CONTAINER FRAMES ---
        self.sidebar_frame = tk.Frame(self, bg="#2c3e50", width=220)
        self.sidebar_frame.pack(side="left", fill="y")
        self.sidebar_frame.pack_propagate(False)

        self.main_container = tk.Frame(self, bg="#f4f6f9")
        self.main_container.pack(side="right", expand=True, fill="both")

        self.build_sidebar()
        self.build_top_bar()
        
        self.content_view = tk.Frame(self.main_container, bg="#ffffff")
        self.content_view.pack(expand=True, fill="both", padx=20, pady=20)
        
        # --- GLOBAL SCROLL & HOTKEYS ---
        self.bind_all("<MouseWheel>", self._on_mouse_wheel)
        self.bind_all("<Up>", self._on_arrow_up)
        self.bind_all("<Down>", self._on_arrow_down)

        # Letter shortcuts
        self.bind_all("t", lambda e: self.handle_hotkey("Instant Trash"))
        self.bind_all("T", lambda e: self.handle_hotkey("Instant Trash"))
        self.bind_all("a", lambda e: self.handle_hotkey("Archive + Label"))
        self.bind_all("A", lambda e: self.handle_hotkey("Archive + Label"))
        self.bind_all("d", lambda e: self.handle_hotkey("Do Nothing (Allow)"))
        self.bind_all("D", lambda e: self.handle_hotkey("Do Nothing (Allow)"))

        # Number-key quick shortcuts for selected items
        self.bind_all("1", lambda e: self.handle_hotkey("Instant Trash"))
        self.bind_all("2", lambda e: self.handle_hotkey("Archive + Label"))
        self.bind_all("3", lambda e: self.handle_hotkey("Do Nothing (Allow)"))

        # Handle window close button for System Tray minimization
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        # Setup system tray icon in background thread
        threading.Thread(target=self.setup_tray_icon, daemon=True).start()

        self.show_dashboard_view()
        self.log_activity("Application initialized successfully.")
        self.fetch_dynamic_labels()

    def log_activity(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self.activity_logs.append(entry)
        print(entry)
        if hasattr(self, 'log_text_widget') and self.log_text_widget.winfo_exists():
            self.log_text_widget.config(state="normal")
            self.log_text_widget.insert(tk.END, entry + "\n")
            self.log_text_widget.see(tk.END)
            self.log_text_widget.config(state="disabled")

    def create_tray_image(self):
        image = Image.new('RGB', (64, 64), color="#2c3e50")
        dc = ImageDraw.Draw(image)
        dc.rectangle((16, 16, 48, 48), fill="#3498db")
        return image

    def setup_tray_icon(self):
        icon_image = self.create_tray_image()
        menu = (
            item('Open Dashboard', self.show_window_from_tray),
            item('Exit Completely', self.exit_application)
        )
        self.tray_icon = pystray.Icon("GmailManager", icon_image, "Advanced Gmail Manager", menu)
        self.tray_icon.run()

    def hide_to_tray(self):
        self.withdraw()
        self.log_activity("Application minimized to system tray.")

    def show_window_from_tray(self, icon, item):
        self.deiconify()
        self.lift()
        self.focus_force()
        self.log_activity("Application restored from system tray.")

    def exit_application(self, icon, item):
        self.tray_icon.stop()
        self.destroy()

    def fetch_dynamic_labels(self):
        def _fetch():
            try:
                if os.path.exists(TOKEN_FILE):
                    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                    if creds and creds.valid:
                        service = build('gmail', 'v1', credentials=creds)
                        results = service.users().labels().list(userId='me').execute()
                        labels = results.get('labels', [])
                        custom_labels = [l['name'] for l in labels if l['type'] == 'user']
                        self.available_labels = ["Archive + Label", "Instant Trash", "Do Nothing (Allow)"] + custom_labels
                        self.log_activity(f"Fetched {len(custom_labels)} custom Gmail labels successfully.")
            except Exception as e:
                self.log_activity(f"Error fetching Gmail labels: {e}")
        threading.Thread(target=_fetch, daemon=True).start()

    def _on_mouse_wheel(self, event):
        if self.active_canvas:
            try:
                self.active_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

    def _on_arrow_up(self, event):
        if self.focus_get() == self.search_entry:
            return
        if self.active_canvas:
            try:
                self.active_canvas.yview_scroll(-1, "units")
            except Exception:
                pass

    def _on_arrow_down(self, event):
        if self.focus_get() == self.search_entry:
            return
        if self.active_canvas:
            try:
                self.active_canvas.yview_scroll(1, "units")
            except Exception:
                pass

    def setup_scrollable_content(self):
        for widget in self.content_view.winfo_children():
            widget.destroy()

        container = tk.Frame(self.content_view, bg="#ffffff")
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg="#ffffff", highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
        
        scrollable_frame = tk.Frame(canvas, bg="#ffffff")
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.active_canvas = canvas
        return scrollable_frame

    def build_sidebar(self):
        for widget in self.sidebar_frame.winfo_children():
            widget.destroy()

        toggle_text = "☰ Menu" if self.sidebar_expanded else "☰"
        toggle_btn = tk.Button(
            self.sidebar_frame, 
            text=toggle_text, 
            bg="#34495e", 
            fg="white", 
            font=("sans-serif", 11, "bold"),
            bd=0,
            command=self.toggle_sidebar
        )
        toggle_btn.pack(pady=15, padx=10, anchor="w" if self.sidebar_expanded else "center")

        nav_items = [
            ("📊", "Dashboard", lambda: self.show_dashboard_view()),
            ("🗂️", "Granular Sources", lambda: self.show_granular_sources_view()),
            ("🛡️", "Precautionary Shield", lambda: self.show_precautionary_shield_view()),
            ("📈", "Sender Statistics", lambda: self.show_sender_statistics_view()),
            ("📝", "Activity Log", lambda: self.show_activity_log_view())
        ]

        for icon, label, cmd in nav_items:
            display_text = f"{icon}  {label}" if self.sidebar_expanded else icon
            btn = tk.Button(
                self.sidebar_frame, 
                text=display_text, 
                bg="#2c3e50", 
                fg="#ecf0f1", 
                font=("sans-serif", 11),
                bd=0,
                anchor="w" if self.sidebar_expanded else "center",
                command=cmd
            )
            btn.pack(fill="x", pady=5, padx=10)

        auth_btn = tk.Button(
            self.sidebar_frame,
            text="🔐 Authorize Gmail",
            bg="#27ae60",
            fg="white",
            font=("sans-serif", 10, "bold"),
            bd=0,
            command=self.on_save_and_auth
        )
        auth_btn.pack(side="bottom", fill="x", pady=15, padx=10)

    def toggle_sidebar(self):
        if self.sidebar_expanded:
            self.sidebar_frame.config(width=60)
            self.sidebar_expanded = False
        else:
            self.sidebar_frame.config(width=220)
            self.sidebar_expanded = True
        self.build_sidebar()

    def build_top_bar(self):
        top_bar = tk.Frame(self.main_container, bg="#ffffff", height=60)
        top_bar.pack(fill="x", padx=20, pady=(20, 0))
        top_bar.pack_propagate(False)

        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            top_bar, 
            textvariable=self.search_var, 
            font=("sans-serif", 12),
            bg="#f8f9fa",
            bd=1,
            relief="solid"
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 15), ipady=5)
        
        self.search_var.trace("w", self.on_search_typed)

        search_btn = tk.Button(top_bar, text="🔍 Search", bg="#3498db", fg="white", font=("sans-serif", 10, "bold"), bd=0, padx=15)
        search_btn.pack(side="right")

    def on_search_typed(self, *args):
        query = self.search_var.get().lower()
        if self.current_view_name == "granular":
            for source, row_widget in self.source_row_widgets.items():
                if query in source.lower():
                    row_widget.pack(fill="x", pady=5)
                else:
                    row_widget.pack_forget()
        elif self.current_view_name == "shield":
            for item, row_widget in self.shield_row_widgets.items():
                if query in item.lower():
                    row_widget.pack(fill="x", pady=3)
                else:
                    row_widget.pack_forget()

    def show_dashboard_view(self):
        self.current_view_name = "dashboard"
        scroll_frame = self.setup_scrollable_content()

        title = tk.Label(scroll_frame, text="System Dashboard & Overview", font=("sans-serif", 16, "bold"), bg="#ffffff")
        title.pack(anchor="w", pady=(0, 5))

        subtitle = tk.Label(scroll_frame, text="📊 High-level status of your automated inbox rules, protection shield, and backup controls.", font=("sans-serif", 9, "italic"), fg="#7f8c8d", bg="#ffffff")
        subtitle.pack(anchor="w", pady=(0, 20))

        cards_frame = tk.Frame(scroll_frame, bg="#ffffff")
        cards_frame.pack(fill="x", pady=(0, 20))

        shield_status = "Active 🛡️" if self.config_data.get("shield_active", True) else "Bypassed ⚠️"
        shield_color = "#e8f8f5" if self.config_data.get("shield_active", True) else "#f9ebea"

        self.create_stat_card(cards_frame, "Precautionary Shield", shield_status, shield_color, 0)
        self.create_stat_card(cards_frame, "Tracked Sources", str(len(self.config_data.get("sources", {}))), "#ebf5fb", 1)
        self.create_stat_card(cards_frame, "Blocked Rules", str(len(self.config_data.get("blocked_addresses", {})) + len(self.config_data.get("blocked_keywords", {}))), "#fef9e7", 2)

        backup_box = tk.Frame(scroll_frame, bg="#f8f9fa", pady=15, padx=20, relief="solid", bd=1)
        backup_box.pack(fill="x", pady=(0, 20))

        backup_title = tk.Label(backup_box, text="Configuration Backup & Portability", font=("sans-serif", 12, "bold"), bg="#f8f9fa")
        backup_title.pack(anchor="w", pady=(0, 5))

        backup_desc = tk.Label(backup_box, text="Export your config rules to a backup file or import an existing setup from another location.", font=("sans-serif", 10), fg="#555555", bg="#f8f9fa")
        backup_desc.pack(anchor="w", pady=(0, 10))

        backup_btn_row = tk.Frame(backup_box, bg="#f8f9fa")
        backup_btn_row.pack(anchor="w", fill="x")

        export_btn = tk.Button(backup_btn_row, text="📤 Export Config Rules", bg="#27ae60", fg="white", font=("sans-serif", 10, "bold"), bd=0, padx=15, pady=8, command=self.export_config_file)
        export_btn.pack(side="left", padx=(0, 15))

        import_btn = tk.Button(backup_btn_row, text="📥 Import Config Rules", bg="#e67e22", fg="white", font=("sans-serif", 10, "bold"), bd=0, padx=15, pady=8, command=self.import_config_file)
        import_btn.pack(side="left")

        sync_box = tk.Frame(scroll_frame, bg="#f8f9fa", pady=15, padx=20, relief="solid", bd=1)
        sync_box.pack(fill="x")

        sync_title = tk.Label(sync_box, text="Inbox Synchronization & Background Polling", font=("sans-serif", 12, "bold"), bg="#f8f9fa")
        sync_title.pack(anchor="w", pady=(0, 5))

        sync_desc = tk.Label(sync_box, text="Run rules manually or enable background polling to check your inbox automatically every 30 minutes.", font=("sans-serif", 10), fg="#555555", bg="#f8f9fa")
        sync_desc.pack(anchor="w", pady=(0, 10))

        btn_row = tk.Frame(sync_box, bg="#f8f9fa")
        btn_row.pack(anchor="w", fill="x")

        run_sync_btn = tk.Button(btn_row, text="⚡ Run Gmail Sync Now", bg="#2980b9", fg="white", font=("sans-serif", 10, "bold"), bd=0, padx=15, pady=8, command=self.on_save_and_auth)
        run_sync_btn.pack(side="left", padx=(0, 15))

        self.poll_var.set(self.polling_active)

        poll_chk = tk.Checkbutton(
            btn_row,
            text="Enable Background Auto-Polling (Every 30 mins)",
            variable=self.poll_var,
            bg="#f8f9fa",
            font=("sans-serif", 10, "bold"),
            command=lambda: self.toggle_auto_polling(self.poll_var.get())
        )
        poll_chk.pack(side="left")

    def export_config_file(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")], initialfile="config_backup.json")
        if file_path:
            try:
                save_config(self.config_data)
                with open(file_path, "w") as f:
                    json.dump(self.config_data, f, indent=4)
                messagebox.showinfo("Success", "Configuration exported successfully!")
                self.log_activity(f"Configuration exported to {file_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export config: {e}")

    def import_config_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if file_path:
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                self.config_data = data
                save_config(self.config_data)
                messagebox.showinfo("Success", "Configuration imported successfully! Please refresh view.")
                self.log_activity(f"Configuration imported from {file_path}")
                self.show_dashboard_view()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to import config: {e}")

    def show_activity_log_view(self):
        self.current_view_name = "logs"
        scroll_frame = self.setup_scrollable_content()

        title = tk.Label(scroll_frame, text="System Activity Log", font=("sans-serif", 16, "bold"), bg="#ffffff")
        title.pack(anchor="w", pady=(0, 5))

        subtitle = tk.Label(scroll_frame, text="📝 Live audit log of automated rule executions and background polling status.", font=("sans-serif", 9, "italic"), fg="#7f8c8d", bg="#ffffff")
        subtitle.pack(anchor="w", pady=(0, 15))

        log_container = tk.Frame(scroll_frame, bg="#ffffff")
        log_container.pack(fill="both", expand=True)

        self.log_text_widget = scrolledtext.ScrolledText(log_container, wrap=tk.WORD, width=100, height=25, font=("Consolas", 10), bg="#1e1e1e", fg="#dcdcdc", insertbackground="white")
        self.log_text_widget.pack(fill="both", expand=True)

        for entry in self.activity_logs:
            self.log_text_widget.insert(tk.END, entry + "\n")
        self.log_text_widget.config(state="disabled")
        self.log_text_widget.see(tk.END)

    def toggle_auto_polling(self, val):
        if val:
            self.polling_active = True
            if not self.poll_thread or not self.poll_thread.is_alive():
                self.poll_thread = threading.Thread(target=self.background_poll_loop, daemon=True)
                self.poll_thread.start()
                self.log_activity("Background auto-polling started (running every 30 minutes).")
        else:
            self.polling_active = False
            self.log_activity("Background auto-polling stopped.")

    def background_poll_loop(self):
        last_daily_summary_day = None
        while getattr(self, 'polling_active', False):
            try:
                self.log_activity("Background poller triggered: checking Gmail...")
                if os.path.exists(TOKEN_FILE):
                    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                    if creds and creds.valid:
                        service = build('gmail', 'v1', credentials=creds)
                        self.run_gmail_sync(creds)

                        now = datetime.now()
                        if now.hour >= 18 and last_daily_summary_day != now.date():
                            self.send_daily_summary_report(service)
                            last_daily_summary_day = now.date()
            except Exception as e:
                self.log_activity(f"Background polling error: {e}")
            
            for _ in range(1800):
                if not getattr(self, 'polling_active', False):
                    break
                time.sleep(1)

    def send_daily_summary_report(self, service):
        try:
            self.log_activity("Compiling daily 6PM-to-6PM summary report...")
            now = datetime.now()
            end_time = now.replace(hour=18, minute=0, second=0, microsecond=0)
            start_time = end_time - timedelta(days=1)
            
            q = f"after:{int(start_time.timestamp())} before:{int(end_time.timestamp())}"
            
            results = service.users().messages().list(userId='me', q=q).execute()
            msg_count = len(results.get('messages', []))

            email_body = f"""
            <html>
              <body style="font-family: Arial, sans-serif; color: #333;">
                <h2 style="color: #2c3e50;">📊 Daily Gmail Manager Summary Report</h2>
                <p><strong>Window:</strong> {start_time.strftime('%Y-%m-%d %I:%M %p')} to {end_time.strftime('%Y-%m-%d %I:%M %p')}</p>
                <hr style="border: 0; border-top: 1px solid #ccc;" />
                <ul>
                  <li><strong>Total Messages Scanned in Window:</strong> {msg_count}</li>
                  <li><strong>Messages Moved to Trash:</strong> {self.daily_stats['trashed']}</li>
                  <li><strong>Messages Archived/Labeled:</strong> {self.daily_stats['archived']}</li>
                  <li><strong>Total Rules Triggered:</strong> {self.daily_stats['rules_hit']}</li>
                </ul>
                <p style="font-size: 11px; color: #777;">Automated report generated by your Advanced Gmail Manager Desktop App.</p>
              </body>
            </html>
            """
            
            from email.mime.text import MIMEText
            import base64
            
            message = MIMEText(email_body, 'html')
            message['to'] = 'me'
            message['subject'] = f"Gmail Manager Daily Report ({now.strftime('%b %d, %Y')})"
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            service.users().messages().send(userId='me', body={'raw': raw_message}).execute()
            self.log_activity("Daily summary email report successfully sent to your inbox.")
            
            self.daily_stats = {"trashed": 0, "archived": 0, "rules_hit": 0}
        except Exception as e:
            self.log_activity(f"Failed to send daily summary report: {e}")

    def create_stat_card(self, parent, title, value, bg_color, col_idx):
        card = tk.Frame(parent, bg=bg_color, pady=15, padx=15, relief="solid", bd=1)
        card.grid(row=0, column=col_idx, sticky="nsew", padx=(0, 15))
        parent.grid_columnconfigure(col_idx, weight=1)

        t_lbl = tk.Label(card, text=title, font=("sans-serif", 10, "bold"), fg="#555555", bg=bg_color)
        t_lbl.pack(anchor="w")

        v_lbl = tk.Label(card, text=value, font=("sans-serif", 18, "bold"), fg="#2c3e50", bg=bg_color)
        v_lbl.pack(anchor="w", pady=(5, 0))

    def show_granular_sources_view(self):
        self.current_view_name = "granular"
        scroll_frame = self.setup_scrollable_content()

        self.selected_items.clear()
        self.source_checkbox_vars.clear()
        self.source_action_vars.clear()
        self.source_row_widgets = {}

        title = tk.Label(scroll_frame, text="Granular Sources & Action Rules", font=("sans-serif", 16, "bold"), bg="#ffffff")
        title.pack(anchor="w", pady=(0, 5))

        subtitle = tk.Label(scroll_frame, text="💡 Alphabetized major senders. Filter by action status or select items for batch operations.", font=("sans-serif", 9, "italic"), fg="#7f8c8d", bg="#ffffff")
        subtitle.pack(anchor="w", pady=(0, 10))

        filter_bar = tk.Frame(scroll_frame, bg="#ffffff")
        filter_bar.pack(fill="x", pady=(0, 15))

        tk.Label(filter_bar, text="Filter Action:", font=("sans-serif", 10, "bold"), bg="#ffffff").pack(side="left", padx=(0, 10))
        
        self.granular_filter_choice = tk.StringVar(value="All")
        filter_dropdown = tk.OptionMenu(
            filter_bar,
            self.granular_filter_choice,
            "All",
            "Instant Trash",
            "Archive + Label",
            "Do Nothing (Allow)",
            command=lambda val: self.render_granular_rows(val)
        )
        filter_dropdown.config(bg="#f8f9fa", font=("sans-serif", 9), bd=1)
        filter_dropdown.pack(side="left")

        self.granular_rows_box = tk.Frame(scroll_frame, bg="#ffffff")
        self.granular_rows_box.pack(fill="both", expand=True)

        self.granular_loading_lbl = tk.Label(self.granular_rows_box, text="Scanning inbox for major senders dynamically... Please wait.", font=("sans-serif", 10, "italic"), fg="#7f8c8d", bg="#ffffff")
        self.granular_loading_lbl.pack(anchor="w", pady=10)

        self.build_batch_action_bar()

        threading.Thread(target=self._fetch_and_render_granular_sources, daemon=True).start()

    def _fetch_and_render_granular_sources(self):
        discovered_senders = set(self.config_data.get("sources", {}).keys())
        try:
            if os.path.exists(TOKEN_FILE):
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                if creds and creds.valid:
                    service = build('gmail', 'v1', credentials=creds)
                    results = service.users().messages().list(userId='me', q="label:INBOX", maxResults=50).execute()
                    messages = results.get('messages', [])
                    for msg in messages:
                        msg_data = service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['From']).execute()
                        headers = msg_data.get('payload', {}).get('headers', [])
                        sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
                        if sender:
                            discovered_senders.add(sender)
        except Exception as e:
            self.log_activity(f"Error fetching dynamic senders: {e}")

        sources_dict = self.config_data.setdefault("sources", {})
        for sender in discovered_senders:
            if sender not in sources_dict:
                sources_dict[sender] = "Do Nothing (Allow)"
        save_config(self.config_data)

        self.after(0, lambda: self.render_granular_rows(self.granular_filter_choice.get()))

    def render_granular_rows(self, filter_status):
        if hasattr(self, 'granular_loading_lbl') and self.granular_loading_lbl.winfo_exists():
            self.granular_loading_lbl.destroy()

        for widget in self.granular_rows_box.winfo_children():
            widget.destroy()

        self.source_row_widgets.clear()
        self.source_checkbox_vars.clear()
        self.source_action_vars.clear()

        sources_dict = self.config_data.get("sources", {})
        sorted_sources = sorted(sources_dict.items(), key=lambda x: x[0].lower())

        for source, current_action in sorted_sources:
            if filter_status != "All" and current_action != filter_status:
                continue

            row_frame = tk.Frame(self.granular_rows_box, bg="#f8f9fa", pady=6, padx=10)
            row_frame.pack(fill="x", pady=5)
            self.source_row_widgets[source] = row_frame

            var = tk.IntVar(value=1 if source in self.selected_items else 0)
            self.source_checkbox_vars[source] = var

            chk = tk.Checkbutton(
                row_frame, 
                variable=var, 
                bg="#f8f9fa", 
                command=lambda s=source, v=var: self.toggle_selection(s, v)
            )
            chk.pack(side="left", padx=(0, 10))

            lbl = tk.Label(row_frame, text=source, font=("sans-serif", 11), bg="#f8f9fa")
            lbl.pack(side="left")

            action_var = tk.StringVar(value=current_action)
            self.source_action_vars[source] = action_var

            action_dropdown = tk.OptionMenu(
                row_frame, 
                action_var, 
                *self.available_labels,
                command=lambda val, s=source: self.update_source_action(s, val)
            )
            action_dropdown.config(bg="#ffffff", font=("sans-serif", 10), bd=0)
            action_dropdown.pack(side="right")

    def build_batch_action_bar(self):
        if hasattr(self, 'batch_action_bar') and self.batch_action_bar.winfo_exists():
            self.batch_action_bar.destroy()

        self.batch_action_bar = tk.Frame(self.content_view, bg="#2980b9", pady=8, padx=15)
        
        self.batch_label = tk.Label(self.batch_action_bar, text=f"{len(self.selected_items)} items selected", font=("sans-serif", 10, "bold"), fg="white", bg="#2980b9")
        self.batch_label.pack(side="left")

        self.batch_action_choice = tk.StringVar(value="Archive + Label")
        batch_dropdown = tk.OptionMenu(
            self.batch_action_bar,
            self.batch_action_choice,
            *self.available_labels
        )
        batch_dropdown.config(bg="#ffffff", fg="#2980b9", font=("sans-serif", 9, "bold"), bd=0)
        batch_dropdown.pack(side="right", padx=(10, 0))

        apply_btn = tk.Button(
            self.batch_action_bar, 
            text="Apply to Selected", 
            bg="#ffffff", 
            fg="#2980b9", 
            font=("sans-serif", 9, "bold"),
            bd=0,
            padx=10,
            command=lambda: self.apply_batch_action(self.batch_action_choice.get())
        )
        apply_btn.pack(side="right")

        if len(self.selected_items) > 0:
            self.batch_action_bar.pack(side="bottom", fill="x", pady=(10, 0))
        else:
            self.batch_action_bar.pack_forget()

    def update_source_action(self, source, val):
        self.config_data["sources"][source] = val
        save_config(self.config_data)

    def show_precautionary_shield_view(self):
        self.current_view_name = "shield"
        scroll_frame = self.setup_scrollable_content()

        self.shield_row_widgets = {}

        title = tk.Label(scroll_frame, text="Precautionary Shield & Keywords", font=("sans-serif", 16, "bold"), bg="#ffffff")
        title.pack(anchor="w", pady=(0, 5))

        subtitle = tk.Label(scroll_frame, text="🛡️ Block exact addresses/domains or keywords. Use master kill-switch to temporarily bypass filters.", font=("sans-serif", 9, "italic"), fg="#7f8c8d", bg="#ffffff")
        subtitle.pack(anchor="w", pady=(0, 15))

        master_frame = tk.Frame(scroll_frame, bg="#e8f8f5", pady=10, padx=15, relief="solid", bd=1)
        master_frame.pack(fill="x", pady=(0, 20))

        self.shield_var = tk.BooleanVar(value=self.config_data.get("shield_active", True))
        shield_chk = tk.Checkbutton(
            master_frame, 
            text="Precautionary Shield Active (Uncheck to temporarily bypass filters for password resets)", 
            variable=self.shield_var, 
            bg="#e8f8f5",
            font=("sans-serif", 10, "bold"),
            command=self.toggle_shield_status
        )
        shield_chk.pack(anchor="w")

        self.shield_list_container = tk.Frame(scroll_frame, bg="#ffffff")
        self.shield_list_container.pack(fill="both", expand=True)

        addr_label = tk.Label(self.shield_list_container, text="Blocked Email Addresses / Domains", font=("sans-serif", 12, "bold"), bg="#ffffff")
        addr_label.pack(anchor="w", pady=(5, 5))

        addr_input_frame = tk.Frame(self.shield_list_container, bg="#ffffff")
        addr_input_frame.pack(fill="x", pady=(0, 10))

        self.addr_entry = tk.Entry(addr_input_frame, font=("sans-serif", 11), bg="#f8f9fa", bd=1, relief="solid")
        self.addr_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=3)

        add_addr_btn = tk.Button(addr_input_frame, text="Add Address/Domain", bg="#e74c3c", fg="white", font=("sans-serif", 9, "bold"), bd=0, padx=10, command=self.add_blocked_address)
        add_addr_btn.pack(side="right")

        self.addr_list_frame = tk.Frame(self.shield_list_container, bg="#ffffff")
        self.addr_list_frame.pack(fill="x", pady=(0, 15))
        
        for addr, action in self.config_data.get("blocked_addresses", {}).items():
            self.render_blocked_item(self.addr_list_frame, addr, action, is_address=True)

        kw_label = tk.Label(self.shield_list_container, text="Blocked Keywords / Phrases", font=("sans-serif", 12, "bold"), bg="#ffffff")
        kw_label.pack(anchor="w", pady=(5, 5))

        kw_input_frame = tk.Frame(self.shield_list_container, bg="#ffffff")
        kw_input_frame.pack(fill="x", pady=(0, 10))

        self.kw_entry = tk.Entry(kw_input_frame, font=("sans-serif", 11), bg="#f8f9fa", bd=1, relief="solid")
        self.kw_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=3)

        add_kw_btn = tk.Button(kw_input_frame, text="Add Keyword", bg="#e74c3c", fg="white", font=("sans-serif", 9, "bold"), bd=0, padx=10, command=self.add_blocked_keyword)
        add_kw_btn.pack(side="right")

        self.kw_list_frame = tk.Frame(self.shield_list_container, bg="#ffffff")
        self.kw_list_frame.pack(fill="x", pady=(0, 10))

        for kw, action in self.config_data.get("blocked_keywords", {}).items():
            self.render_blocked_item(self.kw_list_frame, kw, action, is_address=False)

    def show_sender_statistics_view(self):
        self.current_view_name = "stats"
        scroll_frame = self.setup_scrollable_content()

        title = tk.Label(scroll_frame, text="Sender Statistics & Inbox Volume", font=("sans-serif", 16, "bold"), bg="#ffffff")
        title.pack(anchor="w", pady=(0, 5))

        subtitle = tk.Label(scroll_frame, text="📈 Live analysis of up to 50 top email volume sources. Scroll through senders and manage shield status.", font=("sans-serif", 9, "italic"), fg="#7f8c8d", bg="#ffffff")
        subtitle.pack(anchor="w", pady=(0, 10))

        time_frame_bar = tk.Frame(scroll_frame, bg="#ffffff")
        time_frame_bar.pack(fill="x", pady=(0, 15))

        tk.Label(time_frame_bar, text="Timeframe:", font=("sans-serif", 10, "bold"), bg="#ffffff").pack(side="left", padx=(0, 10))
        
        self.timeframe_choice = tk.StringVar(value="Last 30 Days")
        time_dropdown = tk.OptionMenu(
            time_frame_bar, 
            self.timeframe_choice, 
            "Last 7 Days", 
            "Last 30 Days", 
            "Last 90 Days", 
            "All Time",
            command=lambda val: self.load_live_stats(val)
        )
        time_dropdown.config(bg="#f8f9fa", font=("sans-serif", 9), bd=1)
        time_dropdown.pack(side="left")

        self.stats_list_container = tk.Frame(scroll_frame, bg="#ffffff")
        self.stats_list_container.pack(fill="both", expand=True)

        self.load_live_stats("Last 30 Days")

    def load_live_stats(self, timeframe):
        for widget in self.stats_list_container.winfo_children():
            widget.destroy()

        loading_lbl = tk.Label(self.stats_list_container, text=f"Scanning Gmail inbox for ({timeframe}) [Limit 50]... Please wait.", font=("sans-serif", 10, "italic"), fg="#7f8c8d", bg="#ffffff")
        loading_lbl.pack(anchor="w", pady=10)
        self.update_idletasks()

        query_map = {
            "Last 7 Days": "newer_than:7d",
            "Last 30 Days": "newer_than:30d",
            "Last 90 Days": "newer_than:90d",
            "All Time": ""
        }
        q_filter = query_map.get(timeframe, "newer_than:30d")

        top_senders = []
        try:
            if os.path.exists(TOKEN_FILE):
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                if creds and creds.valid:
                    service = build('gmail', 'v1', credentials=creds)
                    results = service.users().messages().list(userId='me', q=q_filter, maxResults=50).execute()
                    messages = results.get('messages', [])
                    
                    sender_counts = {}
                    for msg in messages:
                        msg_data = service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['From']).execute()
                        headers = msg_data.get('payload', {}).get('headers', [])
                        sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
                        sender_counts[sender] = sender_counts.get(sender, 0) + 1

                    sorted_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)
                    top_senders = [(s, f"{count} emails") for s, count in sorted_senders]
        except Exception as e:
            self.log_activity(f"Error fetching live stats: {e}")

        loading_lbl.destroy()

        if not top_senders:
            empty_lbl = tk.Label(self.stats_list_container, text="No messages found for this timeframe or Gmail is not authorized yet.", font=("sans-serif", 10), fg="#e74c3c", bg="#ffffff")
            empty_lbl.pack(anchor="w", pady=10)
            return

        for sender, volume in top_senders:
            self.render_stat_row(sender, volume)

    def render_stat_row(self, sender, volume):
        row = tk.Frame(self.stats_list_container, bg="#f8f9fa", pady=8, padx=12)
        row.pack(fill="x", pady=4)

        lbl = tk.Label(row, text=f"{sender}  —  ({volume})", font=("sans-serif", 10, "bold"), bg="#f8f9fa")
        lbl.pack(side="left")

        in_shield = sender in self.config_data.get("blocked_addresses", {})

        btn_text = "🛡️ In Shield (Click to Remove)" if in_shield else "🛡️ Move to Shield"
        btn_bg = "#2980b9" if in_shield else "#e74c3c"

        block_btn = tk.Button(
            row, 
            text=btn_text, 
            bg=btn_bg, 
            fg="white", 
            font=("sans-serif", 9, "bold"), 
            bd=0, 
            padx=8,
            command=lambda s=sender, r=row, v=volume: self.toggle_shield_sender(s, r, v)
        )
        block_btn.pack(side="right")

    def toggle_shield_sender(self, sender, row_widget, volume):
        blocked_dict = self.config_data.setdefault("blocked_addresses", {})
        
        if sender in blocked_dict:
            del blocked_dict[sender]
            save_config(self.config_data)
            self.log_activity(f"Removed '{sender}' from shield.")
        else:
            blocked_dict[sender] = "Instant Trash"
            save_config(self.config_data)
            self.log_activity(f"Added '{sender}' to shield.")

        row_widget.destroy()
        self.render_stat_row(sender, volume)

    def toggle_shield_status(self):
        self.config_data["shield_active"] = self.shield_var.get()
        save_config(self.config_data)
        self.log_activity(f"Shield status changed: Active = {self.config_data['shield_active']}")

    def render_blocked_item(self, parent, text_val, default_action, is_address=True):
        row = tk.Frame(parent, bg="#f8f9fa", pady=5, padx=10)
        row.pack(fill="x", pady=3)
        self.shield_row_widgets[text_val] = row

        lbl = tk.Label(row, text=text_val, font=("sans-serif", 11), bg="#f8f9fa")
        lbl.pack(side="left")

        action_var = tk.StringVar(value=default_action)
        dropdown = tk.OptionMenu(
            row, 
            action_var, 
            *self.available_labels,
            command=lambda val, t=text_val: self.update_item_action(t, val, is_address)
        )
        dropdown.config(bg="#ffffff", font=("sans-serif", 9), bd=0)
        dropdown.pack(side="right", padx=(10, 0))

        del_btn = tk.Button(
            row, 
            text="❌ Remove", 
            bg="#fadbd8", 
            fg="#c0392b", 
            font=("sans-serif", 9), 
            bd=0, 
            command=lambda: self.remove_blocked_item(row, text_val, is_address)
        )
        del_btn.pack(side="right")

    def update_item_action(self, key_name, new_action, is_address):
        target_dict = "blocked_addresses" if is_address else "blocked_keywords"
        if key_name in self.config_data[target_dict]:
            self.config_data[target_dict][key_name] = new_action
            save_config(self.config_data)

    def add_blocked_address(self):
        val = self.addr_entry.get().strip()
        if val and val not in self.config_data["blocked_addresses"]:
            self.config_data["blocked_addresses"][val] = "Instant Trash"
            save_config(self.config_data)
            self.render_blocked_item(self.addr_list_frame, val, "Instant Trash", is_address=True)
            self.addr_entry.delete(0, tk.END)
            self.log_activity(f"Added blocked address: {val}")

    def add_blocked_keyword(self):
        val = self.kw_entry.get().strip()
        if val and val not in self.config_data["blocked_keywords"]:
            self.config_data["blocked_keywords"][val] = "Instant Trash"
            save_config(self.config_data)
            self.render_blocked_item(self.kw_list_frame, val, "Instant Trash", is_address=False)
            self.kw_entry.delete(0, tk.END)
            self.log_activity(f"Added blocked keyword: {val}")

    def remove_blocked_item(self, row_widget, key_name, is_address):
        target_dict = "blocked_addresses" if is_address else "blocked_keywords"
        if key_name in self.config_data[target_dict]:
            del self.config_data[target_dict][key_name]
            save_config(self.config_data)
        row_widget.destroy()
        if key_name in self.shield_row_widgets:
            del self.shield_row_widgets[key_name]
        self.log_activity(f"Removed blocked rule: {key_name}")

    def toggle_selection(self, source, var):
        if var.get() == 1:
            self.selected_items.add(source)
        else:
            self.selected_items.discard(source)
        
        self.build_batch_action_bar()

    def handle_hotkey(self, action_name):
        if self.focus_get() == self.search_entry:
            return
        
        if self.selected_items:
            self.apply_batch_action(action_name)

    def apply_batch_action(self, action_name):
        for source in self.selected_items:
            if source in self.source_action_vars:
                self.source_action_vars[source].set(action_name)
                self.update_source_action(source, action_name)

    def on_save_and_auth(self):
        save_config(self.config_data)
        self.log_activity("Preferences saved successfully.")
        threading.Thread(target=self.run_gmail_auth, daemon=True).start()

    def run_gmail_auth(self):
        creds = None
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
                
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())

        self.log_activity("Successfully authenticated with Gmail API.")
        self.run_gmail_sync(creds)

    def run_gmail_sync(self, creds):
        self.log_activity("Starting Gmail Sync & Rule Enforcement...")
        service = build('gmail', 'v1', credentials=creds)
        
        results = service.users().messages().list(userId='me', q="label:INBOX", maxResults=10).execute()
        messages = results.get('messages', [])
        
        self.log_activity(f"Scanning {len(messages)} inbox messages against your rules...")

        shield_active = self.config_data.get("shield_active", True)
        blocked_addresses = self.config_data.get("blocked_addresses", {})
        blocked_keywords = self.config_data.get("blocked_keywords", {})
        sources = self.config_data.get("sources", {})

        for msg in messages:
            msg_id = msg['id']
            msg_data = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            
            headers = msg_data.get('payload', {}).get('headers', [])
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
            snippet = msg_data.get('snippet', '')
            
            action_to_apply = None

            if shield_active:
                for addr, action in blocked_addresses.items():
                    if addr.lower() in sender.lower():
                        action_to_apply = action
                        self.daily_stats['rules_hit'] += 1
                        self.log_activity(f"🛡️ Shield Block Address match [{addr}] -> Action: {action}")
                        break
                
                if not action_to_apply:
                    for kw, action in blocked_keywords.items():
                        if kw.lower() in sender.lower() or kw.lower() in subject.lower() or kw.lower() in snippet.lower():
                            action_to_apply = action
                            self.daily_stats['rules_hit'] += 1
                            self.log_activity(f"🛡️ Shield Keyword match [{kw}] -> Action: {action}")
                            break

            if not action_to_apply:
                for src, action in sources.items():
                    if src.lower() in sender.lower() and action != "Do Nothing (Allow)":
                        action_to_apply = action
                        self.daily_stats['rules_hit'] += 1
                        self.log_activity(f"🗂️ Granular Source match [{src}] -> Action: {action}")
                        break

            if action_to_apply == "Instant Trash":
                service.users().messages().batchModify(
                    userId='me',
                    body={'ids': [msg_id], 'addLabelIds': ['TRASH'], 'removeLabelIds': ['INBOX']}
                ).execute()
                self.daily_stats['trashed'] += 1
                self.log_activity(f"🗑️ Moved message ID {msg_id} to Trash.")
                
            elif action_to_apply == "Archive + Label":
                service.users().messages().batchModify(
                    userId='me',
                    body={'ids': [msg_id], 'removeLabelIds': ['INBOX']}
                ).execute()
                self.daily_stats['archived'] += 1
                self.log_activity(f"📦 Archived message ID {msg_id} from Inbox.")
            elif action_to_apply and action_to_apply not in ["Do Nothing (Allow)", "Instant Trash"]:
                try:
                    labels_res = service.users().labels().list(userId='me').execute()
                    all_labels = labels_res.get('labels', [])
                    label_id = next((l['id'] for l in all_labels if l['name'].lower() == action_to_apply.lower()), None)
                    if label_id:
                        service.users().messages().batchModify(
                            userId='me',
                            body={'ids': [msg_id], 'addLabelIds': [label_id], 'removeLabelIds': ['INBOX']}
                        ).execute()
                        self.daily_stats['archived'] += 1
                        self.log_activity(f"🏷️ Archived & applied label '{action_to_apply}' to message ID {msg_id}.")
                except Exception as ex:
                    self.log_activity(f"Error applying custom label: {ex}")

        self.log_activity("Gmail Sync Completed Successfully!")


#===========window configuration===========#
if __name__ == "__main__":
    app = GmailManagerApp()
    app.mainloop()