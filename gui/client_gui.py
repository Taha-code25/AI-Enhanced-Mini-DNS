
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import sys
from pathlib import Path

# Allow importing the existing client module
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from client.client import DnsClient


class ClientGUI:

    def __init__(self, root):
        self.root = root
        self.root.title("AI-Enhanced Mini DNS - Client")
        self.root.geometry("650x550")
        self.root.resizable(False, False)

        self.root.configure(bg="#eef2f7")

        self.create_interface()

    def create_interface(self):

        # Heading
        heading = tk.Label(
            self.root,
            text="AI-Enhanced Mini DNS",
            font=("Arial", 22, "bold"),
            bg="#eef2f7",
            fg="#17365d"
        )
        heading.pack(pady=(20, 5))

        subtitle = tk.Label(
            self.root,
            text="Client DNS Lookup Interface",
            font=("Arial", 11),
            bg="#eef2f7",
            fg="#526579"
        )
        subtitle.pack(pady=(0, 20))

        # Main input frame
        input_frame = tk.Frame(self.root, bg="white", padx=20, pady=20)
        input_frame.pack(fill="x", padx=30)

        # Domain input
        tk.Label(
            input_frame,
            text="Domain Name",
            font=("Arial", 10, "bold"),
            bg="white"
        ).pack(anchor="w")

        self.domain_entry = ttk.Entry(input_frame, font=("Arial", 12))
        self.domain_entry.pack(fill="x", pady=(5, 15))
        self.domain_entry.insert(0, "portal.university.test")

        # Resolver configuration
        tk.Label(
            input_frame,
            text="Resolver IP Address",
            font=("Arial", 10, "bold"),
            bg="white"
        ).pack(anchor="w")

        self.host_entry = ttk.Entry(input_frame, font=("Arial", 11))
        self.host_entry.pack(fill="x", pady=(5, 12))
        self.host_entry.insert(0, "127.0.0.1")

        tk.Label(
            input_frame,
            text="Resolver Port",
            font=("Arial", 10, "bold"),
            bg="white"
        ).pack(anchor="w")

        self.port_entry = ttk.Entry(input_frame, font=("Arial", 11))
        self.port_entry.pack(fill="x", pady=(5, 15))
        self.port_entry.insert(0, "5300")

        # Buttons
        button_frame = tk.Frame(input_frame, bg="white")
        button_frame.pack(fill="x")

        self.resolve_button = ttk.Button(
            button_frame,
            text="Resolve Domain",
            command=self.start_lookup
        )
        self.resolve_button.pack(side="left", expand=True, fill="x", padx=(0, 5))

        ttk.Button(
            button_frame,
            text="Clear",
            command=self.clear_result
        ).pack(side="left", expand=True, fill="x", padx=(5, 0))

        # Result area
        result_frame = tk.LabelFrame(
            self.root,
            text="DNS Resolution Result",
            font=("Arial", 11, "bold"),
            bg="white",
            padx=20,
            pady=15
        )
        result_frame.pack(fill="both", expand=True, padx=30, pady=20)

        self.status_label = tk.Label(
            result_frame,
            text="Waiting for a DNS query...",
            font=("Arial", 11, "bold"),
            bg="white",
            fg="#526579"
        )
        self.status_label.pack(anchor="w", pady=(0, 10))

        self.result_text = tk.Text(
            result_frame,
            height=9,
            font=("Consolas", 11),
            bg="#f5f7fa",
            relief="flat",
            wrap="word"
        )
        self.result_text.pack(fill="both", expand=True)
        self.result_text.config(state="disabled")

    def start_lookup(self):

        domain = self.domain_entry.get().strip()
        host = self.host_entry.get().strip()
        port_text = self.port_entry.get().strip()

        if not domain or not host or not port_text:
            messagebox.showwarning(
                "Missing Information",
                "Please enter the domain, resolver IP and port."
            )
            return

        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid Port",
                "Please enter a port between 1 and 65535."
            )
            return

        self.resolve_button.config(state="disabled")
        self.status_label.config(
            text="Resolving domain...",
            fg="#d97706"
        )

        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, "Sending DNS query to resolver...")
        self.result_text.config(state="disabled")

        threading.Thread(
            target=self.perform_lookup,
            args=(domain, host, port),
            daemon=True
        ).start()

    def perform_lookup(self, domain, host, port):

        try:
            client = DnsClient(
                host=host,
                port=port,
                timeout=1.0,
                retries=2
            )

            result = client.lookup(domain)

            self.root.after(
                0,
                lambda: self.display_result(result)
            )

        except Exception as exc:
            self.root.after(
                0,
                lambda error=str(exc): self.display_error(error)
            )

    def display_result(self, result):

        self.resolve_button.config(state="normal")

        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)

        if result.ok:

            self.status_label.config(
                text="DNS Resolution Successful",
                fg="#15803d"
            )

            output = (
                f"Domain       : {result.domain}\n"
                f"IP Address   : {result.ip}\n"
                f"TTL          : {result.ttl} seconds\n"
                f"Latency      : {result.latency_ms:.2f} ms\n"
                f"Attempts     : {result.attempts}\n"
                f"Status       : SUCCESS"
            )

        else:

            self.status_label.config(
                text="DNS Resolution Failed",
                fg="#dc2626"
            )

            output = (
                f"Domain       : {result.domain}\n"
                f"Status       : FAILED\n"
                f"Error Type   : {result.error_name}\n"
                f"Error        : {result.error}\n"
                f"Latency      : {result.latency_ms:.2f} ms\n"
                f"Attempts     : {result.attempts}"
            )

        self.result_text.insert(tk.END, output)
        self.result_text.config(state="disabled")

    def display_error(self, error):

        self.resolve_button.config(state="normal")
        self.status_label.config(
            text="Client Error",
            fg="#dc2626"
        )

        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, error)
        self.result_text.config(state="disabled")

    def clear_result(self):

        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.config(state="disabled")

        self.status_label.config(
            text="Waiting for a DNS query...",
            fg="#526579"
        )


if __name__ == "__main__":

    root = tk.Tk()
    app = ClientGUI(root)
    root.mainloop()
