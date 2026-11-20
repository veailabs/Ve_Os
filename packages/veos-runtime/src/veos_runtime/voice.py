"""Explicitly configured API voice broker; never a subscription fallback."""

import json
import os
import re
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from .integrations import NoRedirect
from .store import Refused


class Voice:
  def __init__(self):
    self.lock = threading.Lock()
    self.attempts = []

  def status(self):
    configured = os.environ.get("VEOS_VOICE_API_ENABLED") == "true"
    endpoint = os.environ.get("VEOS_VOICE_SESSION_URL", "")
    token = os.environ.get("VEOS_VE_ACCESS_TOKEN", "")
    return {
      "available": configured and bool(endpoint and token),
      "funding": "separately_configured_api",
      "reason": None
      if configured and endpoint and token
      else "Voice has not been configured on this computer.",
      "max_session_seconds": 600,
    }

  def mint(self):
    if not self.status()["available"]:
      raise Refused(self.status()["reason"])
    endpoint = os.environ["VEOS_VOICE_SESSION_URL"]
    parsed = urlsplit(endpoint)
    if (
      parsed.scheme != "https"
      or parsed.hostname != "backend.ve.ai"
      or parsed.port not in {None, 443}
      or parsed.username
      or parsed.password
      or parsed.query
      or parsed.fragment
      or not re.fullmatch(r"/[A-Za-z0-9_./-]*/core/voice/sessions", parsed.path)
    ):
      raise Refused("Configure the authenticated Ve core voice session endpoint")
    with self.lock:
      now = time.monotonic()
      self.attempts = [t for t in self.attempts if now - t < 900]
      if len(self.attempts) >= 3:
        raise Refused(
          "Voice connection attempt limit reached; wait before trying again"
        )
      self.attempts.append(now)
    req = urllib.request.Request(
      endpoint,
      json.dumps(
        {"version": "ve.voice.session.request.v1", "executionTarget": "local_harness"}
      ).encode(),
      {
        "Authorization": "Bearer " + os.environ["VEOS_VE_ACCESS_TOKEN"],
        "Content-Type": "application/json",
      },
      method="POST",
    )
    try:
      with urllib.request.build_opener(NoRedirect()).open(req, timeout=15) as response:
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
          raise ValueError("Invalid response")
        data = json.loads(raw)
      secret = data["clientSecret"]
      if (
        data.get("version") != "ve.voice.session.v1"
        or data.get("provider") != "openai"
        or data.get("transport") != "webrtc"
        or data.get("executionTarget") != "local_harness"
        or data.get("connectUrl") != "https://api.openai.com/v1/realtime/calls"
      ):
        raise ValueError("Invalid contract")
      if (
        not isinstance(secret["value"], str)
        or not re.fullmatch(r"ek_[A-Za-z0-9_-]{10,4096}", secret["value"])
        or type(secret["expiresAt"]) is not int
        or not time.time() + 5 < secret["expiresAt"] <= time.time() + 120
      ):
        raise ValueError("Invalid temporary credential")
      if not isinstance(data.get("model"), str) or not 1 <= len(data["model"]) <= 200:
        raise ValueError("Invalid model")
      return {
        "clientSecret": secret,
        "connectUrl": data["connectUrl"],
        "model": data["model"],
        "maxSessionSeconds": 600,
      }
    except Exception:  # noqa: BLE001 — Credential/provider failures never enter transcripts.
      raise Refused(
        "Voice session was not established. Check Ve sign-in and the configured voice service; no automatic retry was made"
      ) from None
