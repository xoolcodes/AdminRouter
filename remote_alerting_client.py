#!/usr/bin/env python3
"""Small Windows desktop companion for AdminRouter Remote Alerting."""
import json
import threading
import urllib.error
import urllib.request
import tkinter as tk
from tkinter import messagebox, ttk


class AlertingClient:
    def __init__(self, root):
        self.root = root
        self.root.title("AdminRouter Remote Alerting")
        self.root.resizable(False, False)
        self.cookie = None
        self.last_id = 0
        self.stop_event = threading.Event()
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        frame = ttk.Frame(self.root, padding=18)
        frame.grid()
        ttk.Label(frame, text="AdminRouter server").grid(row=0, column=0, sticky="w", pady=4)
        self.server = ttk.Entry(frame, width=38)
        self.server.insert(0, "http://127.0.0.1:8080")
        self.server.grid(row=0, column=1, pady=4)
        ttk.Label(frame, text="Admin username").grid(row=1, column=0, sticky="w", pady=4)
        self.username = ttk.Entry(frame, width=38)
        self.username.grid(row=1, column=1, pady=4)
        ttk.Label(frame, text="Admin password").grid(row=2, column=0, sticky="w", pady=4)
        self.password = ttk.Entry(frame, width=38, show="*")
        self.password.grid(row=2, column=1, pady=4)
        self.connect_button = ttk.Button(frame, text="Connect", command=self.connect)
        self.connect_button.grid(row=3, column=1, sticky="e", pady=(12, 4))
        self.status = ttk.Label(frame, text="Not connected")
        self.status.grid(row=4, column=0, columnspan=2, sticky="w", pady=4)

    def request(self, path, data=None):
        url = self.server.get().rstrip("/") + path
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        return urllib.request.urlopen(request, timeout=8)

    def connect(self):
        self.connect_button.configure(state="disabled")
        self.status.configure(text="Connecting...")
        threading.Thread(target=self.login, daemon=True).start()

    def login(self):
        try:
            body = json.dumps({"username": self.username.get(), "password": self.password.get()}).encode()
            response = self.request("/api/login", body)
            self.cookie = response.headers.get("Set-Cookie", "").split(";", 1)[0]
            self.root.after(0, lambda: self.status.configure(text="Connected. Waiting for alerts."))
            threading.Thread(target=self.poll, daemon=True).start()
        except (OSError, urllib.error.URLError) as error:
            self.root.after(0, lambda: self.connection_failed(str(error)))

    def connection_failed(self, error):
        self.connect_button.configure(state="normal")
        self.status.configure(text="Connection failed")
        messagebox.showerror("AdminRouter", "Could not connect to the server.\n" + error)

    def poll(self):
        while not self.stop_event.wait(5):
            try:
                response = self.request("/api/alerts?after=" + str(self.last_id))
                alerts = json.loads(response.read()).get("alerts", [])
                for alert in alerts:
                    self.last_id = max(self.last_id, int(alert["id"]))
                    self.root.after(0, lambda message=alert["message"]: messagebox.showinfo("AdminRouter alert", message))
            except (OSError, ValueError, urllib.error.URLError):
                self.root.after(0, lambda: self.status.configure(text="Connection lost; retrying..."))

    def close(self):
        self.stop_event.set()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    AlertingClient(window)
    window.mainloop()
