"""Central configuration. Every path is absolute so the process can be started
from any working directory without silently reading or writing the wrong .env."""

import json
import os
from typing import Dict, List

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
LOG_FILE_PATH = os.path.join(BASE_DIR, "appium.log")
DB_PATH = os.path.join(BASE_DIR, "qai.db")

# Everything a run produces that is too big for SQLite: traces, videos, visual
# baselines and their diffs. Kept out of the database so a run can be deleted
# without rewriting the file, and so a trace can be served as a plain download.
DATA_DIR = os.path.join(BASE_DIR, "data")
ARTIFACT_DIR = os.path.join(DATA_DIR, "artifacts")
AUTH_DIR = os.path.join(DATA_DIR, "auth")
BASELINE_DIR = os.path.join(DATA_DIR, "baselines")

for _directory in (DATA_DIR, ARTIFACT_DIR, AUTH_DIR, BASELINE_DIR):
    os.makedirs(_directory, exist_ok=True)

load_dotenv(ENV_PATH)

APPIUM_HOST = os.environ.get("APPIUM_HOST", "http://localhost:4723")

# Driving a real iPhone means WebDriverAgent has to be signed, and a free Apple
# account cannot register the stock 'com.facebook.WebDriverAgentRunner' id
# because it belongs to someone else. Whoever re-signs it picks a unique id and
# names it here, so Appium looks for the build that actually exists. Empty for
# simulators and Android, which need no signing at all.
WDA_BUNDLE_ID = os.environ.get("WDA_BUNDLE_ID", "").strip()

# BrowserStack, for running against real devices nobody has to keep on a desk.
# Empty means the feature is simply not offered; nothing else changes.
BROWSERSTACK_USERNAME = os.environ.get("BROWSERSTACK_USERNAME", "").strip()
BROWSERSTACK_ACCESS_KEY = os.environ.get("BROWSERSTACK_ACCESS_KEY", "").strip()

# Whether BrowserStack may re-sign an uploaded build with its own certificate,
# which it does by default. It must not for these apps. Re-signing replaces the
# team prefix, and the entitlements that name it stop matching: the shared
# keychain group and the app groups are both lost, so the app fails its first
# keychain read with -34018 and exits before it has drawn anything. Read off a
# real session's device log:
#
#   container_create_or_lookup_app_group_path_by_app_group_identifier:
#       client is not entitled
#   Client explicitly specifies access group
#       35MYG4AN5S.com.turkishairlines…sharedKeychain but is only entitled …
#
# Left signed as it is, the same build opens and runs. An enterprise-signed
# build installs on any device, so nothing is given up by not re-signing it;
# a build signed with a development profile, which is limited to registered
# devices, is the case that would need this turned back on.
BROWSERSTACK_RESIGN_APP = (
    os.environ.get("BROWSERSTACK_RESIGN_APP", "false").strip().lower() == "true"
)

# How long BrowserStack lets a session sit without a command before ending it.
# Its own default is 90 seconds, and an agent spends longer than that on a
# single step whenever the model has to think about a full screen — so a run
# would lose the device mid-scenario and report it as a crash.
BROWSERSTACK_IDLE_TIMEOUT = int(os.environ.get("BROWSERSTACK_IDLE_TIMEOUT", "300"))

# The agent loop is bounded server-side as well as in the UI so a runaway model
# cannot keep driving the device (and burning API quota) indefinitely.
MAX_AGENT_STEPS = int(os.environ.get("MAX_AGENT_STEPS", "40"))

# Headers added to web requests whose URL contains the key — the same shape as a
# browser header extension's per-domain rule. Used for the QA access header an
# environment grants test traffic (e.g. a UAT host that only answers requests
# carrying it). Matching on the URL rather than sending them to everything is
# the point: a header scoped to one host must not leak to the third-party
# domains a page pulls in.
# Example: {"turkishairlines.com": {"akamai_access": "allowed"}}
WEB_EXTRA_HEADERS: Dict[str, Dict[str, str]] = json.loads(
    os.environ.get("WEB_EXTRA_HEADERS", "").strip() or "{}"
)

# Bundle id (iOS) / package (Android) for each named test build, so the quick
# "open on connect" buttons can launch a known build by id — including on a
# cloud device, where the app is not in any list to match by name. Fill this in
# once the ids are known.
# Example: {"ThyDev": "com.thy.dev", "ThyTest": "com.thy.test", "ThyReg": "com.thy.reg"}
THY_ENV_BUNDLE_IDS: Dict[str, str] = json.loads(
    os.environ.get("THY_ENV_BUNDLE_IDS", "").strip() or "{}"
)

# Answer the OS permission prompts (location, notifications, tracking) at the
# driver level so a freshly launched app lands on its own first screen. On
# Android the permissions are granted at install and the dialogs never appear;
# on iOS WebDriverAgent accepts each alert as it comes. Costs no model tokens.
# Set to "false" to test the permission prompts themselves.
MOBILE_AUTO_PERMISSIONS = os.environ.get("MOBILE_AUTO_PERMISSIONS", "true").strip().lower() != "false"

# How long one scenario-writing call may take end to end. A gateway that
# accepts the request and then never answers must fail loudly, not hold the
# tester's spinner open for half an hour. 300s matches the API timeout the
# corporate Claude Code config uses.
SCENARIO_TIMEOUT_S = int(os.environ.get("SCENARIO_TIMEOUT_S", "300"))

# Whether a Test Set run drives a headless browser. Off by default: the sites
# these suites are written against refuse one outright — Akamai drops the
# connection at the network layer and every scenario fails with
# ERR_HTTP2_PROTOCOL_ERROR, which reads as a site outage rather than a browser
# it would not talk to. Set to "true" where the target allows it; headless is
# faster and needs no display.
RUN_HEADLESS_DEFAULT = os.environ.get("RUN_HEADLESS_DEFAULT", "false").strip().lower() == "true"

# Only the local Vite dev server needs access. A wildcard here would let any
# website in the browser drive the connected phone.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173",
    ).split(",")
    if origin.strip()
]


def write_env(updates: Dict[str, str]) -> None:
    """Merge `updates` into .env, preserving every other line and comment.

    Also updates os.environ so the change takes effect without a restart.
    """
    remaining = dict(updates)
    lines: List[str] = []

    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                key = stripped.split("=", 1)[0] if "=" in stripped else None
                if key and key in remaining:
                    lines.append(f"{key}={remaining.pop(key)}\n")
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")

    for key, value in remaining.items():
        lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

    os.environ.update(updates)
