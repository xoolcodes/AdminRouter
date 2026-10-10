#!/usr/bin/env python3
"""AdminRouter desktop companion with local service discovery."""
import concurrent.futures
import ipaddress
import json
import socket
import threading
import urllib.error
import urllib.request
import tkinter as tk
from tkinter import messagebox


BLUE = "#123b72"
BLUE_DARK = "#0b2850"
WHITE = "#ffffff"
INK = "#182534"
MUTED = "#d7e3f2"
GREEN = "#72d39a"
DISCOVERY_PORT = 39872


class AlertingClient:
    def __init__(self, root):
        self.root = root
        self.root.title("AdminRouter Alerting")
        self.root.geometry("520x320")
        self.root.minsize(520, 320)
        self.root.configure(bg=BLUE)
        self.server = None
        self.cookie = None
        self.last_id = 0
        self.stop_event = threading.Event()
        self.scanning = True
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self.discover, daemon=True).start()

    def label(self, parent, text, size=11, color=WHITE, **kwargs):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, font=("Segoe UI", size), **kwargs)

    def build_ui(self):
        header = tk.Frame(self.root, bg=BLUE_DARK, height=88)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.label(header, "AdminRouter", 22, anchor="w").pack(fill="x", padx=24, pady=(16, 0))
        self.label(header, "Remote Alerting companion", 10, MUTED, anchor="w").pack(fill="x", padx=25)

        body = tk.Frame(self.root, bg=BLUE, padx=28, pady=22)
        body.pack(fill="both", expand=True)
        self.label(body, "Network address", 10, MUTED, anchor="w").pack(fill="x")
        self.address = tk.Label(body, text="Scanning local addresses...", bg=BLUE, fg=WHITE, anchor="w", font=("Segoe UI", 12))
        self.address.pack(fill="x", pady=(4, 16))
        self.label(body, "Username", 10, MUTED, anchor="w").pack(fill="x")
        self.username = tk.Entry(body, bg=WHITE, fg=INK, insertbackground=INK, relief="flat", font=("Segoe UI", 12))
        self.username.pack(fill="x", ipady=8, pady=(4, 16))
        self.connect_button = tk.Button(body, text="Connect", command=self.connect, state="disabled", bg=WHITE, fg=BLUE_DARK, activebackground=MUTED, relief="flat", borderwidth=0, font=("Segoe UI", 11, "bold"), cursor="hand2")
        self.connect_button.pack(anchor="e", ipadx=18, ipady=7)
        self.status = self.label(body, "Looking for AdminRouter...", 10, MUTED, anchor="w")
        self.status.pack(fill="x", pady=(14, 0))

    def local_candidates(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
            sock.close()
            network = ipaddress.ip_network(f"{address}/24", strict=False)
            return [str(host) for host in network.hosts()]
        except OSError:
            return ["127.0.0.1"]

    def probe(self, address):
        url = f"http://{address}:{DISCOVERY_PORT}/info/creditngtgtu"
        try:
            response = urllib.request.urlopen(url, timeout=0.35)
            data = json.loads(response.read())
            if data.get("service") == "AdminRouter":
                return address, int(data.get("port", 8080)), data
        except (OSError, ValueError, urllib.error.URLError):
            return None
        return None

    def discover(self):
        candidates = self.local_candidates()
        while not self.stop_event.is_set() and self.server is None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
                for result in pool.map(self.probe, candidates):
                    if result:
                        address, port, data = result
                        self.server = f"http://{address}:{port}"
                        self.root.after(0, lambda: self.discovery_found(data))
                        return
            self.root.after(0, lambda: self.status.configure(text="Still looking for AdminRouter..."))
            self.stop_event.wait(2)

    def discovery_found(self, data):
        self.address.configure(text=self.server)
        self.status.configure(text="Server found. Enter your username to connect.", fg=MUTED)
        self.connect_button.configure(state="normal")
        if data.get("username"):
            self.username.insert(0, data["username"])
            self.username.focus_set()

    def connect(self):
        username = self.username.get().strip()
        if not username:
            messagebox.showwarning("AdminRouter", "Enter your username first.")
            return
        self.connect_button.configure(state="disabled")
        self.status.configure(text="Connecting...", fg=MUTED)
        threading.Thread(target=self.verify_username, args=(username,), daemon=True).start()

    def verify_username(self, username):
        try:
            request = urllib.request.Request(self.server + "/api/alerts?after=0", headers={"X-AdminRouter-Username": username})
            urllib.request.urlopen(request, timeout=5)
            self.root.after(0, lambda: self.status.configure(text="Connected. Waiting for alerts.", fg=GREEN))
            threading.Thread(target=self.poll, args=(username,), daemon=True).start()
        except (OSError, urllib.error.URLError) as error:
            self.root.after(0, lambda: self.connection_failed(str(error)))

    def connection_failed(self, error):
        self.connect_button.configure(state="normal")
        self.status.configure(text="Username was not accepted.", fg="#ffb3b3")
        messagebox.showerror("AdminRouter", "Could not connect with that username.\n" + error)

    def poll(self, username):
        while not self.stop_event.wait(5):
            try:
                request = urllib.request.Request(self.server + "/api/alerts?after=" + str(self.last_id), headers={"X-AdminRouter-Username": username})
                response = urllib.request.urlopen(request, timeout=8)
                alerts = json.loads(response.read()).get("alerts", [])
                for alert in alerts:
                    self.last_id = max(self.last_id, int(alert["id"]))
                    self.root.after(0, lambda message=alert["message"]: messagebox.showinfo("AdminRouter alert", message))
            except (OSError, ValueError, urllib.error.URLError):
                self.root.after(0, lambda: self.status.configure(text="Connection lost; looking again...", fg="#ffb3b3"))

    def close(self):
        self.stop_event.set()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    AlertingClient(window)
    window.mainloop()
