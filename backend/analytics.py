"""PostHog product analytics for Voyce: the browser loader served at /analytics.js, and
server-side events for what happens outside the app (approvals from email, autopilot posts,
payments).

Config: POSTHOG_PROJECT_TOKEN (or PROJECT_TOKEN) and POSTHOG_HOST (default: PostHog US,
where Voyce's project lives). With no token everything here is a no-op.

Never sent to PostHog: approve and invite tokens (the browser strips /approve/<token> and
/connect/<token> from every URL property before an event leaves), and input values in session
recordings (masked). Pages that carry tokens in their URL don't load the loader at all.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

import requests

DEFAULT_HOST = "https://us.i.posthog.com"


def _token() -> str:
    return (os.getenv("POSTHOG_PROJECT_TOKEN") or os.getenv("PROJECT_TOKEN") or "").strip().strip('"').strip("'")


def _host() -> str:
    return (os.getenv("POSTHOG_HOST") or DEFAULT_HOST).strip().rstrip("/")


def _off() -> bool:
    # Test runs point Mongo at a dead local port; their events must never reach PostHog.
    return not _token() or os.getenv("MONGO_URI", "").startswith("mongodb://127.0.0.1:1")


# PostHog's own loader snippet, verbatim from posthog.com/docs/libraries/js (Oct 2026).
_SNIPPET = r"""!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],Object.defineProperty(u,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e}}),Object.defineProperty(u.people,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(){return u.toString(1)+".people (stub)"}}),o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagResult isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);"""

# Voyce's helpers: vTrack(event, props) for named events, vIdentify(user) after sign-in,
# vReset() on sign-out. Safe to call when analytics is off (they do nothing).
_HELPERS = r"""
window.vTrack = function (event, props) { try { if (window.posthog && posthog.capture) posthog.capture(event, props || {}); } catch (e) {} };
window.vIdentify = function (u) {
  try {
    if (!u || !u.id || !window.posthog) return;
    var g = u.gen_info || {};
    posthog.identify(u.id, { email: u.email || undefined, name: u.name || undefined,
      plan: g.plan || u.plan || "free", plan_region: g.plan_region || u.plan_region || undefined });
  } catch (e) {}
};
window.vReset = function () { try { if (window.posthog && posthog.reset) posthog.reset(); } catch (e) {} };
(function () { try { vIdentify(JSON.parse(localStorage.getItem("cm_user") || "null")); } catch (e) {} })();
"""

# Strip approve/invite tokens from every URL-ish property, including the first-touch
# ones PostHog sets on the person ($initial_referrer and friends).
_SANITIZE = r"""function (ev) {
  try {
    var rx = /\/(approve|connect)\/[^\/?#\s]+/g;
    var scrub = function (o) { if (!o) return; for (var k in o) { if (typeof o[k] === "string") o[k] = o[k].replace(rx, "/$1/[token]"); } };
    scrub(ev && ev.properties); scrub(ev && ev.$set); scrub(ev && ev.$set_once);
  } catch (e) {}
  return ev;
}"""


def loader_js() -> str:
    """The script every analytics page loads (<script src="/analytics.js">)."""
    if not _token():
        return ("/* Voyce analytics is off: no PostHog token. */\n"
                "window.vTrack = window.vIdentify = window.vReset = function () {};\n")
    cfg = {"api_host": _host(), "defaults": "2026-05-30", "person_profiles": "identified_only",
           "session_recording": {"maskAllInputs": True}}
    init = json.dumps(cfg)[:-1] + ', "before_send": ' + _SANITIZE + "}"
    return _SNIPPET + "\nposthog.init(" + json.dumps(_token()) + ", " + init + ");\n" + _HELPERS


def capture(distinct_id: str | None, event: str, props: dict | None = None) -> None:
    """Fire-and-forget server event. Never raises, never blocks the request."""
    if _off() or not distinct_id:
        return
    payload = {"api_key": _token(), "event": event, "distinct_id": str(distinct_id),
               "timestamp": datetime.now(timezone.utc).isoformat(),
               "properties": {**(props or {}), "$lib": "voyce-server"}}

    def _send():
        try:
            requests.post(f"{_host()}/i/v0/e/", json=payload, timeout=6)
        except Exception:
            logging.debug("posthog capture failed for %s", event)

    threading.Thread(target=_send, daemon=True).start()
