
import tkinter as tk
from tkinter import ttk, messagebox
import csv
import errno
import sys
import time
from dataclasses import replace
from pathlib import Path

# Allow importing existing project modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from protocol.protocol import load_config
from resolver.resolver import ResolverServer
from resolver.cache import DNSCache
from ai.cache_policy import PredictiveCache


class ResolverGUI:

    def __init__(self, root):
        self.root = root
        self.root.title("AI-Enhanced Mini DNS - Resolver")
        self.root.geometry("950x780")
        self.root.configure(bg="#eef2f7")

        self.server = None

        self.log_path = PROJECT_ROOT / "results" / "gui-resolver.csv"

        self.create_interface()
        self.root.protocol("WM_DELETE_WINDOW", self.close_window)

        self.root.after(500, self.refresh_display)

    def create_interface(self):

        # Heading
        tk.Label(
            self.root,
            text="AI-Enhanced Mini DNS",
            font=("Arial", 22, "bold"),
            bg="#eef2f7",
            fg="#17365d"
        ).pack(pady=(15, 3))

        tk.Label(
            self.root,
            text="Resolver Control Panel",
            font=("Arial", 11),
            bg="#eef2f7",
            fg="#526579"
        ).pack(pady=(0, 12))

        # Configuration frame
        config_frame = tk.LabelFrame(
            self.root,
            text="Resolver Configuration",
            font=("Arial", 11, "bold"),
            bg="white",
            padx=12,
            pady=10
        )
        config_frame.pack(fill="x", padx=20, pady=5)

        self.bind_host = tk.StringVar(value="0.0.0.0")
        self.resolver_port = tk.StringVar(value="5300")
        self.auth_host = tk.StringVar(value="127.0.0.1")
        self.auth_port = tk.StringVar(value="5301")
        self.policy = tk.StringVar(value="AI")
        self.capacity = tk.StringVar(value="100")
        self.model_path = tk.StringVar(
            value="results/gui-training/model.pkl"
        )

        self.add_field(config_frame, "Resolver Bind IP", self.bind_host, 0, 0)
        self.add_field(config_frame, "Resolver Port", self.resolver_port, 0, 2)
        self.add_field(config_frame, "Authoritative Server IP", self.auth_host, 1, 0)
        self.add_field(config_frame, "Authoritative Port", self.auth_port, 1, 2)

        tk.Label(
            config_frame,
            text="Cache Policy",
            bg="white",
            font=("Arial", 9, "bold")
        ).grid(row=2, column=0, sticky="w", pady=8)

        self.policy_box = ttk.Combobox(
            config_frame,
            textvariable=self.policy,
            values=["AI", "LRU", "None"],
            state="readonly",
            width=18
        )
        self.policy_box.grid(row=2, column=1, sticky="w", padx=5)
        self.policy_box.bind("<<ComboboxSelected>>", self.update_policy_fields)

        tk.Label(
            config_frame,
            text="Cache Capacity",
            bg="white",
            font=("Arial", 9, "bold")
        ).grid(row=2, column=2, sticky="w", pady=8)

        self.capacity_entry = ttk.Entry(
            config_frame,
            textvariable=self.capacity,
            width=20
        )
        self.capacity_entry.grid(row=2, column=3, sticky="w", padx=5)

        tk.Label(
            config_frame,
            text="AI Model Path",
            bg="white",
            font=("Arial", 9, "bold")
        ).grid(row=3, column=0, sticky="w", pady=8)

        self.model_entry = ttk.Entry(
            config_frame,
            textvariable=self.model_path,
            width=55
        )
        self.model_entry.grid(
            row=3, column=1, columnspan=3,
            sticky="ew", padx=5
        )

        config_frame.columnconfigure(1, weight=1)
        config_frame.columnconfigure(3, weight=1)

        # Control buttons
        button_frame = tk.Frame(self.root, bg="#eef2f7")
        button_frame.pack(fill="x", padx=20, pady=8)

        self.start_button = ttk.Button(
            button_frame,
            text="Start Resolver",
            command=self.start_server
        )
        self.start_button.pack(side="left", padx=5)

        self.stop_button = ttk.Button(
            button_frame,
            text="Stop Resolver",
            command=self.stop_server,
            state="disabled"
        )
        self.stop_button.pack(side="left", padx=5)

        self.clear_button = ttk.Button(
            button_frame,
            text="Clear Cache",
            command=self.clear_cache
        )
        self.clear_button.pack(side="left", padx=5)

        self.status_label = tk.Label(
            button_frame,
            text="Status: Stopped",
            font=("Arial", 10, "bold"),
            bg="#eef2f7",
            fg="red"
        )
        self.status_label.pack(side="right", padx=10)

        # Statistics
        stats_frame = tk.LabelFrame(
            self.root,
            text="Resolver Statistics",
            font=("Arial", 11, "bold"),
            bg="white",
            padx=10,
            pady=8
        )
        stats_frame.pack(fill="x", padx=20, pady=5)

        self.total_label = tk.Label(
            stats_frame, text="Total Queries: 0",
            bg="white", font=("Arial", 10, "bold")
        )
        self.total_label.pack(side="left", padx=20)

        self.hit_label = tk.Label(
            stats_frame, text="Cache Hits: 0",
            bg="white", font=("Arial", 10, "bold")
        )
        self.hit_label.pack(side="left", padx=20)

        self.miss_label = tk.Label(
            stats_frame, text="Cache Misses: 0",
            bg="white", font=("Arial", 10, "bold")
        )
        self.miss_label.pack(side="left", padx=20)

        # Cache table
        cache_frame = tk.LabelFrame(
            self.root,
            text="Live Cache Entries",
            font=("Arial", 11, "bold"),
            bg="white",
            padx=10,
            pady=8
        )
        cache_frame.pack(fill="both", expand=True, padx=20, pady=5)

        columns = ("domain", "ip", "ttl")

        self.cache_table = ttk.Treeview(
            cache_frame,
            columns=columns,
            show="headings",
            height=6
        )

        self.cache_table.heading("domain", text="Domain")
        self.cache_table.heading("ip", text="IP Address")
        self.cache_table.heading("ttl", text="Remaining TTL")

        self.cache_table.column("domain", width=400)
        self.cache_table.column("ip", width=200)
        self.cache_table.column("ttl", width=150)

        self.cache_table.pack(fill="both", expand=True)

        # Live event log
        log_frame = tk.LabelFrame(
            self.root,
            text="Live DNS Event Log",
            font=("Arial", 11, "bold"),
            bg="white",
            padx=10,
            pady=8
        )
        log_frame.pack(fill="both", expand=True, padx=20, pady=5)

        self.log_text = tk.Text(
            log_frame,
            height=9,
            font=("Consolas", 9),
            state="disabled"
        )
        self.log_text.pack(fill="both", expand=True)

    def add_field(self, parent, label, variable, row, column):

        tk.Label(
            parent,
            text=label,
            bg="white",
            font=("Arial", 9, "bold")
        ).grid(row=row, column=column, sticky="w", pady=8)

        ttk.Entry(
            parent,
            textvariable=variable,
            width=20
        ).grid(row=row, column=column + 1, sticky="w", padx=5)

    def update_policy_fields(self, event=None):

        if self.policy.get() == "None":
            self.capacity_entry.configure(state="disabled")
            self.model_entry.configure(state="disabled")

        elif self.policy.get() == "LRU":
            self.capacity_entry.configure(state="normal")
            self.model_entry.configure(state="disabled")

        else:
            self.capacity_entry.configure(state="normal")
            self.model_entry.configure(state="normal")

    def start_server(self):

        if self.server is not None:
            return

        try:
            port = int(self.resolver_port.get())
            auth_port = int(self.auth_port.get())

            if not (1 <= port <= 65535 and 1 <= auth_port <= 65535):
                raise ValueError("Ports must be between 1 and 65535.")

            selected_policy = self.policy.get()

            if selected_policy == "None":
                cache = DNSCache(0)
                capacity = 0

            else:
                capacity = int(self.capacity.get())

                if capacity <= 0:
                    raise ValueError("Cache capacity must be greater than zero.")

                if selected_policy == "LRU":
                    cache = DNSCache(capacity)

                else:
                    model_file = PROJECT_ROOT / self.model_path.get()

                    if not model_file.is_file():
                        raise FileNotFoundError(
                            f"AI model not found:\n{model_file}"
                        )

                    cache = PredictiveCache.from_file(
                        capacity,
                        model_file
                    )

            # Preserve existing configuration while applying GUI settings
            config = replace(
                load_config(),
                resolver_host=self.bind_host.get().strip(),
                resolver_port=port,
                auth_host=self.auth_host.get().strip(),
                auth_port=auth_port,
                cache_capacity=capacity
            )

            self.server = ResolverServer(
                config=config,
                cache=cache,
                query_log_path=self.log_path,
                reset_query_log=True
            )

            try:
                self.server.start()
            except OSError as exc:
                if exc.errno in (errno.EADDRINUSE, 10048, 10013):
                    raise RuntimeError(
                        f"Resolver port {port} is unavailable. Close any other "
                        "resolver using it, or choose a different Resolver Port "
                        "and enter the same port in the client.\n\n"
                        f"{exc}"
                    ) from exc
                raise

            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")

            self.status_label.configure(
                text=f"Status: Running on {self.server.address}",
                fg="green"
            )

        except Exception as exc:
            self.server = None
            messagebox.showerror("Resolver Error", str(exc))

    def stop_server(self):

        if self.server is not None:
            self.server.stop()
            self.server = None

        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

        self.status_label.configure(
            text="Status: Stopped",
            fg="red"
        )

        self.refresh_cache()

    def clear_cache(self):

        if self.server is not None:
            self.server.cache.clear()
            self.refresh_cache()

    def refresh_cache(self):

        for item in self.cache_table.get_children():
            self.cache_table.delete(item)

        if self.server is None:
            return

        try:
            entries = list(self.server.cache.entries.items())

            for domain, entry in entries:
                ttl_left = max(0, int(entry.expiry - time.monotonic()))

                if ttl_left > 0:
                    self.cache_table.insert(
                        "",
                        "end",
                        values=(domain, entry.ip, f"{ttl_left} seconds")
                    )

        except RuntimeError:
            # Cache may be changing while the server handles a request
            pass

    def refresh_logs(self):

        if not self.log_path.exists():
            return

        try:
            with self.log_path.open(
                "r", newline="", encoding="utf-8"
            ) as file:
                # The resolver may still be writing the final row.
                rows = [
                    row for row in csv.DictReader(file)
                    if row.get("status") is not None
                ]

            total = sum(
                row["event"] == "QUERY_RECEIVED" for row in rows
            )
            hits = sum(
                row["event"] == "CACHE_HIT" for row in rows
            )
            misses = sum(
                row["event"] == "CACHE_MISS" for row in rows
            )

            self.total_label.configure(text=f"Total Queries: {total}")
            self.hit_label.configure(text=f"Cache Hits: {hits}")
            self.miss_label.configure(text=f"Cache Misses: {misses}")

            recent_rows = rows[-20:]

            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")

            for row in recent_rows:
                line = (
                    f'{row["event"]:<18} '
                    f'{row["domain"]:<32} '
                    f'{row["status"] or "-"}'
                )
                self.log_text.insert("end", line + "\n")

            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        except (OSError, KeyError):
            pass

    def refresh_display(self):

        self.refresh_cache()
        self.refresh_logs()

        self.root.after(500, self.refresh_display)

    def close_window(self):

        self.stop_server()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = ResolverGUI(root)
    app.update_policy_fields()
    root.mainloop()
