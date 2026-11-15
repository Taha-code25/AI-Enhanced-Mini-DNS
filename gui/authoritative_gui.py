
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import json
import ipaddress
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from authoritative.server import (
    AuthoritativeServer,
    DEFAULT_RECORDS_PATH,
)
from protocol.protocol import load_config, normalize_domain, MAX_TTL


class AuthoritativeGUI:

    def __init__(self, root):
        self.root = root
        self.root.title("AI-Enhanced Mini DNS - Authoritative Server")
        self.root.geometry("850x650")
        self.root.configure(bg="#eef2f7")

        self.config = load_config()
        self.server = None
        self.server_thread = None

        self.records_path = DEFAULT_RECORDS_PATH

        self.create_interface()
        self.load_records()

        self.root.protocol("WM_DELETE_WINDOW", self.close_window)

    def create_interface(self):

        heading = tk.Label(
            self.root,
            text="AI-Enhanced Mini DNS",
            font=("Arial", 22, "bold"),
            bg="#eef2f7",
            fg="#17365d"
        )
        heading.pack(pady=(15, 3))

        tk.Label(
            self.root,
            text="Authoritative Server Management",
            font=("Arial", 11),
            bg="#eef2f7",
            fg="#526579"
        ).pack(pady=(0, 15))

        # Server controls
        server_frame = tk.LabelFrame(
            self.root,
            text="Server Configuration",
            bg="white",
            padx=15,
            pady=12
        )
        server_frame.pack(fill="x", padx=20, pady=5)

        tk.Label(
            server_frame, text="Server IP:",
            bg="white"
        ).grid(row=0, column=0, padx=5, pady=5)

        self.host_entry = ttk.Entry(server_frame, width=20)
        self.host_entry.grid(row=0, column=1, padx=5)
        self.host_entry.insert(0, self.config.auth_host)

        tk.Label(
            server_frame, text="Port:",
            bg="white"
        ).grid(row=0, column=2, padx=5)

        self.port_entry = ttk.Entry(server_frame, width=10)
        self.port_entry.grid(row=0, column=3, padx=5)
        self.port_entry.insert(0, str(self.config.auth_port))

        self.start_button = ttk.Button(
            server_frame,
            text="Start Server",
            command=self.start_server
        )
        self.start_button.grid(row=0, column=4, padx=10)

        self.stop_button = ttk.Button(
            server_frame,
            text="Stop Server",
            command=self.stop_server,
            state="disabled"
        )
        self.stop_button.grid(row=0, column=5, padx=5)

        self.status_label = tk.Label(
            server_frame,
            text="● STOPPED",
            fg="#dc2626",
            bg="white",
            font=("Arial", 10, "bold")
        )
        self.status_label.grid(row=1, column=0, columnspan=6, pady=10)

        # Record table
        records_frame = tk.LabelFrame(
            self.root,
            text="DNS Records",
            bg="white",
            padx=10,
            pady=10
        )
        records_frame.pack(fill="both", expand=True, padx=20, pady=10)

        columns = ("domain", "ip", "ttl")

        self.tree = ttk.Treeview(
            records_frame,
            columns=columns,
            show="headings",
            height=8
        )

        self.tree.heading("domain", text="Domain Name")
        self.tree.heading("ip", text="IPv4 Address")
        self.tree.heading("ttl", text="TTL (seconds)")

        self.tree.column("domain", width=350)
        self.tree.column("ip", width=200)
        self.tree.column("ttl", width=120)

        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select_record)

        # Record form
        form_frame = tk.LabelFrame(
            self.root,
            text="Manage DNS Record",
            bg="white",
            padx=15,
            pady=12
        )
        form_frame.pack(fill="x", padx=20, pady=5)

        tk.Label(
            form_frame, text="Domain:",
            bg="white"
        ).grid(row=0, column=0, padx=5, pady=5)

        self.domain_entry = ttk.Entry(form_frame, width=30)
        self.domain_entry.grid(row=0, column=1, padx=5)

        tk.Label(
            form_frame, text="IP Address:",
            bg="white"
        ).grid(row=1, column=0, padx=5, pady=5)

        self.ip_entry = ttk.Entry(form_frame, width=30)
        self.ip_entry.grid(row=1, column=1, padx=5)

        tk.Label(
            form_frame, text="TTL:",
            bg="white"
        ).grid(row=2, column=0, padx=5, pady=5)

        self.ttl_entry = ttk.Entry(form_frame, width=30)
        self.ttl_entry.grid(row=2, column=1, padx=5)

        button_frame = tk.Frame(form_frame, bg="white")
        button_frame.grid(row=3, column=0, columnspan=2, pady=12)

        ttk.Button(
            button_frame,
            text="Add Record",
            command=self.add_record
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="Update Selected",
            command=self.update_record
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="Delete Selected",
            command=self.delete_record
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="Clear Fields",
            command=self.clear_fields
        ).pack(side="left", padx=5)

    def load_records(self):

        for item in self.tree.get_children():
            self.tree.delete(item)

        try:
            with self.records_path.open(encoding="utf-8") as file:
                records = json.load(file)

            for record in records:
                self.tree.insert(
                    "",
                    tk.END,
                    values=(
                        record["domain"],
                        record["ip"],
                        record["ttl"]
                    )
                )

        except Exception as exc:
            messagebox.showerror(
                "Record Loading Error",
                str(exc)
            )

    def select_record(self, event):

        selected = self.tree.selection()

        if not selected:
            return

        values = self.tree.item(selected[0], "values")

        self.clear_fields()

        self.domain_entry.insert(0, values[0])
        self.ip_entry.insert(0, values[1])
        self.ttl_entry.insert(0, values[2])

    def get_record_data(self):

        domain = normalize_domain(
            self.domain_entry.get().strip()
        )

        ip = str(
            ipaddress.IPv4Address(
                self.ip_entry.get().strip()
            )
        )

        ttl = int(self.ttl_entry.get().strip())

        if not 0 <= ttl <= MAX_TTL:
            raise ValueError(
                f"TTL must be between 0 and {MAX_TTL}."
            )

        return domain, ip, ttl

    def save_records(self, records):

        with self.records_path.open("w", encoding="utf-8") as file:
            json.dump(records, file, indent=2)

        self.load_records()

    def apply_record_change(self, records):

        was_running = self.server is not None

        if was_running:
            self.stop_server()

        try:
            self.save_records(records)

            if was_running:
                self.start_server()

        except Exception as exc:
            messagebox.showerror(
                "Save Error",
                str(exc)
            )

    def add_record(self):

        try:
            domain, ip, ttl = self.get_record_data()

            with self.records_path.open(encoding="utf-8") as file:
                records = json.load(file)

            if any(r["domain"] == domain for r in records):
                raise ValueError("This domain already exists.")

            records.append({
                "domain": domain,
                "ip": ip,
                "ttl": ttl
            })

            self.apply_record_change(records)
            self.clear_fields()

        except Exception as exc:
            messagebox.showerror("Invalid Record", str(exc))

    def update_record(self):

        selected = self.tree.selection()

        if not selected:
            messagebox.showwarning(
                "No Selection",
                "Select a record from the table first."
            )
            return

        try:
            old_values = self.tree.item(
                selected[0], "values"
            )

            old_domain = old_values[0]
            domain, ip, ttl = self.get_record_data()

            with self.records_path.open(encoding="utf-8") as file:
                records = json.load(file)

            if domain != old_domain and any(
                r["domain"] == domain for r in records
            ):
                raise ValueError("This domain already exists.")

            for record in records:
                if record["domain"] == old_domain:
                    record.update({
                        "domain": domain,
                        "ip": ip,
                        "ttl": ttl
                    })
                    break

            self.apply_record_change(records)
            self.clear_fields()

        except Exception as exc:
            messagebox.showerror("Update Error", str(exc))

    def delete_record(self):

        selected = self.tree.selection()

        if not selected:
            messagebox.showwarning(
                "No Selection",
                "Select a record to delete."
            )
            return

        values = self.tree.item(selected[0], "values")
        domain = values[0]

        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete {domain}?"
        ):
            return

        try:
            with self.records_path.open(encoding="utf-8") as file:
                records = json.load(file)

            records = [
                record for record in records
                if record["domain"] != domain
            ]

            self.apply_record_change(records)
            self.clear_fields()

        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc))

    def clear_fields(self):

        self.domain_entry.delete(0, tk.END)
        self.ip_entry.delete(0, tk.END)
        self.ttl_entry.delete(0, tk.END)

        for item in self.tree.selection():
            self.tree.selection_remove(item)

    def start_server(self):

        if self.server is not None:
            return

        host = self.host_entry.get().strip()

        try:
            port = int(self.port_entry.get().strip())

            if not 1 <= port <= 65535:
                raise ValueError("Invalid port.")

            server = AuthoritativeServer(
                host,
                port,
                self.records_path
            )

            self.server = server

            self.server_thread = threading.Thread(
                target=server.serve_forever,
                daemon=True
            )

            self.server_thread.start()

            self.status_label.config(
                text=f"● RUNNING at {server.address[0]}:{server.address[1]}",
                fg="#15803d"
            )

            self.start_button.config(state="disabled")
            self.stop_button.config(state="normal")

        except Exception as exc:
            self.server = None
            messagebox.showerror(
                "Server Start Error",
                str(exc)
            )

    def stop_server(self):

        if self.server is None:
            return

        self.server.shutdown()

        if self.server_thread is not None:
            self.server_thread.join(timeout=1)

            if self.server_thread.is_alive():
                messagebox.showerror(
                    "Shutdown Error",
                    "Server is still stopping. Please try again."
                )
                return

        self.server = None
        self.server_thread = None

        self.status_label.config(
            text="● STOPPED",
            fg="#dc2626"
        )

        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

    def close_window(self):

        self.stop_server()
        self.root.destroy()


if __name__ == "__main__":

    root = tk.Tk()
    app = AuthoritativeGUI(root)
    root.mainloop()
