"""Named planning snapshots in Atlas; imagery and credentials stay outside MongoDB."""

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
from threading import Lock

import certifi
from bson import BSON
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from solar_address import InputError

SNAPSHOT_FIELDS = {
    "request", "analysis", "summary", "roofs", "existing_panels", "blocked_zones",
    "roof_selection", "proposal", "panel_recommendations", "impact", "report",
    "preview_files", "imagery", "demo", "search_seconds", "completed_at",
    "assumptions", "notes", "detector", "existing_panel_detector",
}
PRIVATE_FIELDS = {"api_key", "key", "password", "token", "access_token", "mongodb_uri", "connection_string"}
SAFE_FOLDER = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
PROJECT_ID = re.compile(r"[a-f0-9]{24}\Z")
SUMMARY_FIELDS = {
    "name", "created_at", "updated_at", "address", "demo", "radius_meters",
    "panel_model", "panel_count", "budget_usd", "energy_target_kwh_year",
    "estimated_energy_kwh_year", "estimated_cost_usd", "annual_bill_savings_usd",
}


class ProjectStorageError(Exception):
    """Stable public errors that never contain a connection URI or password."""


def settings(env_file=None):
    values = {}
    path = Path(env_file or Path(__file__).with_name(".env"))
    if path.is_file():
        for line in path.read_text().splitlines():
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and not key.startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    for key in ("MONGODB_URI", "MONGODB_DATABASE"):
        if key in os.environ:
            values[key] = os.environ[key].strip()
    return values


def sanitize(value):
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if k.lower() not in PRIVATE_FIELDS}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    return deepcopy(value)


def snapshot(result):
    clean = sanitize({k: v for k, v in result.items() if k in SNAPSHOT_FIELDS})
    # Download URLs can carry short-lived Google tokens. Keep only imagery provenance.
    clean["imagery"] = {
        k: v for k, v in clean.get("imagery", {}).items()
        if k in {"imageryQuality", "imageryDate", "imageryProcessedDate"}
    }
    clean.get("request", {}).pop("solar_request_url", None)
    return clean


def project_summary(document):
    return {k: v for k, v in document.items() if k in SUMMARY_FIELDS} | {
        "id": document["_id"]
    }


class MongoProjectStore:
    def __init__(self, uri, database="photogrid", collection=None):
        self._uri, self._database = uri, database
        self._client = None
        self._collection = collection
        self._lock = Lock()

    def collection(self):
        # Connect only when used; an offline Atlas cluster must not prevent app startup.
        with self._lock:
            if self._collection is None:
                self._client = MongoClient(
                    self._uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=4000,
                    connectTimeoutMS=4000, socketTimeoutMS=5000,
                )
                self._collection = self._client[self._database]["projects"]
            return self._collection

    def save(self, name, result, asset_job_id):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise InputError("Enter a project name between 1 and 80 characters.")
        if not isinstance(asset_job_id, str) or not SAFE_FOLDER.fullmatch(asset_job_id):
            raise InputError("This analysis has an invalid imagery reference.")
        plan = result.get("proposal") or {}
        model = plan.get("panel_model") or {}
        now = datetime.now(timezone.utc).isoformat()
        document = {
            "_id": secrets.token_hex(12), "schema_version": 1,
            "name": " ".join(name.split()), "created_at": now, "updated_at": now,
            "address": result["request"]["address"], "demo": bool(result.get("demo")),
            "radius_meters": result["request"].get("radius_meters"),
            "panel_model": model.get("model_name"), "panel_count": plan.get("panel_count"),
            "budget_usd": plan.get("budget_usd"),
            "energy_target_kwh_year": plan.get("energy_target_kwh_year"),
            "estimated_energy_kwh_year": plan.get("estimated_energy_kwh_year"),
            "estimated_cost_usd": plan.get("estimated_cost_usd"),
            "annual_bill_savings_usd": (result.get("impact") or {}).get("annual_bill_savings_usd"),
            "asset_job_id": asset_job_id, "result": snapshot(result),
        }
        if len(BSON.encode(document)) > 15 * 1024 * 1024:
            raise InputError("This plan is too large to save. Try a smaller search radius.")
        try:
            self.collection().insert_one(document)
        except (PyMongoError, ValueError):
            raise ProjectStorageError("Could not save to Atlas. Check the connection and try again; your local plan is unchanged.") from None
        return project_summary(document)

    def list(self):
        try:
            cursor = self.collection().find({"schema_version": 1}, {"result": 0, "asset_job_id": 0, "schema_version": 0})
            return [project_summary(doc) for doc in cursor.sort("updated_at", -1).limit(100)]
        except (PyMongoError, ValueError):
            raise ProjectStorageError("Saved projects could not load. Check the Atlas connection; local planning still works.") from None

    def get(self, project_id):
        if not PROJECT_ID.fullmatch(project_id):
            return None
        try:
            return self.collection().find_one({"_id": project_id, "schema_version": 1})
        except (PyMongoError, ValueError):
            raise ProjectStorageError("This project could not open. Check the Atlas connection and try again.") from None

    def close(self):
        if self._client is not None:
            self._client.close()


def configured_store(env_file=None):
    values = settings(env_file)
    uri = values.get("MONGODB_URI", "")
    if not uri or "<db_password>" in uri:
        return None
    return MongoProjectStore(uri, values.get("MONGODB_DATABASE") or "photogrid")
