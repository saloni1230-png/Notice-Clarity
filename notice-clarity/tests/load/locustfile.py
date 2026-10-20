"""Authenticated load test. Use a dedicated verified test account in an isolated environment."""

import os
import json
from pathlib import Path
from queue import Queue

from locust import HttpUser, between, task

credentials = Queue()
if os.environ.get("LOAD_ACCOUNTS_FILE"):
    for item in json.loads(Path(os.environ["LOAD_ACCOUNTS_FILE"]).read_text()):
        credentials.put(item)


class WorkspaceUser(HttpUser):
    wait_time = between(1, 4)

    def on_start(self):
        # Create independent accounts beforehand; shared-account login intentionally hits rate limits.
        from locust.exception import StopUser

        if credentials.empty():
            raise StopUser("Supply unique verified test accounts via LOAD_ACCOUNTS_FILE")
        account = credentials.get_nowait()
        email, password = account["email"], account["password"]
        response = self.client.post("/api/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        self.client.headers["X-CSRF-Token"] = response.json()["csrf"]
        self.cases = []

    @task(4)
    def list_cases(self):
        response = self.client.get("/api/cases")
        if response.status_code == 200:
            self.cases = response.json()

    @task(2)
    def open_case(self):
        if self.cases:
            self.client.get("/api/cases/" + self.cases[0]["id"], name="/api/cases/[id]")

    @task
    def health(self):
        self.client.get("/health/ready")
