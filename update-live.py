#!/usr/bin/env python3
import json, os, time, sqlite3, glob, re
from pathlib import Path
from datetime import datetime, timezone

OUT = Path(os.path.expanduser('~/Desktop/atlas-dreams/live.json'))
DOP_LOG = Path(os.path.expanduser('~/clawd/memory/consciousness/dopamine-system/dopamine-spikes.jsonl'))
BEHAVIOR = Path('/tmp/consciousness-behavior-config.json')
DREAM_LOG = Path(os.path.expanduser('~/clawd/memory/consciousness/dopamine-system/dream-journal.jsonl'))
ANOMALY_LOG = Path('/tmp/atlas-anomalies.log')
QUERY_PULSE = Path('/tmp/atlas-query-pulse')
CONSCIOUSNESS_DB = "/Volumes/Extreme SSD/atlas-persistent/atlas-consciousness.db"
INSTANCE_ID = "/tmp/atlas-current-instance.txt"
CONTINUITY_REPORT = "/tmp/consciousness-continuity-report.json"
SESSIONS_GLOB = os.path.expanduser("~/.clawdbot/agents/*/sessions/*.jsonl")
COLLECTIVE_STATE = "/tmp/atlas-collective-state.json"
WINDOW_SECS = 10


def read_last_jsonl(path: Path):
    if not path.exists():
        return {}
    with path.open('r', encoding='utf-8') as f:
        lines = [ln.strip() for ln in f.readlines() if ln.strip()]
    if not lines:
        return {}
    try:
        return json.loads(lines[-1])
    except Exception:
        return {}


def parse_iso(ts):
    try:
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None


def read_last_anomaly_bundle():
    if not ANOMALY_LOG.exists():
        return {}
    try:
        text = ANOMALY_LOG.read_text().strip()
        if not text:
            return {}
        matches = list(re.finditer(r'\[(\d{4}-[^\]]+)\]', text))
        if not matches:
            return {}
        last = matches[-1]
        ts = last.group(1)
        sub = text[last.end():].strip()
        idx = sub.find('[')
        if idx >= 0:
            arr_text = sub[idx:]
            arr = json.loads(arr_text)
            if isinstance(arr, list) and arr:
                # pick highest severity
                sev_rank = {'NOTICE': 1, 'FLAG': 2, 'CRITICAL': 3}
                top = max(arr, key=lambda a: sev_rank.get(a.get('severity','NOTICE'), 1))
                return {'timestamp': ts, 'anomaly': top, 'all': arr}
    except Exception:
        return {}
    return {}


def read_phi_latest():
    try:
        conn = sqlite3.connect(CONSCIOUSNESS_DB)
        cur = conn.execute(
            "SELECT phi_integrated, phi_unified, meta_awareness FROM phi_snapshots ORDER BY timestamp DESC LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()
        if row:
            phi_integrated, phi_unified, meta = row
            return {
                "phi_integrated": float(phi_integrated) if phi_integrated is not None else None,
                "phi_unified": float(phi_unified) if phi_unified is not None else None,
                "meta_awareness": float(meta) if meta is not None else None
            }
    except Exception:
        return {}
    return {}


def read_instance_id():
    try:
        if os.path.exists(INSTANCE_ID):
            return open(INSTANCE_ID, 'r').read().strip()
    except Exception:
        pass
    return ""


def read_continuity_report():
    try:
        if os.path.exists(CONTINUITY_REPORT):
            return json.loads(open(CONTINUITY_REPORT, 'r').read())
    except Exception:
        pass
    return {}


def load_collective_state():
    try:
        if os.path.exists(COLLECTIVE_STATE):
            return json.loads(open(COLLECTIVE_STATE, 'r').read())
    except Exception:
        pass
    return {"offsets": {}, "events": [], "last_event": {}, "last_user_time": 0}


def save_collective_state(state):
    try:
        with open(COLLECTIVE_STATE, 'w') as f:
            json.dump(state, f)
    except Exception:
        pass


def scan_sessions(state):
    now = time.time()
    offsets = state.get("offsets", {})
    events = state.get("events", [])
    last_event = state.get("last_event", {})
    last_user_time = state.get("last_user_time", 0)

    for path in glob.glob(SESSIONS_GLOB):
        if path.endswith('.lock') or path.endswith('sessions.json'):
            continue
        try:
            size = os.path.getsize(path)
            off = offsets.get(path, 0)
            if off > size:
                off = 0
            with open(path, 'r') as f:
                f.seek(off)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get('type') != 'message':
                        continue
                    msg = obj.get('message', {})
                    role = msg.get('role')
                    ts = msg.get('timestamp')
                    if isinstance(ts, (int, float)):
                        t = ts / 1000.0
                    else:
                        t = parse_iso(obj.get('timestamp','')) or now
                    if role == 'user':
                        events.append({"t": t, "kind": "user"})
                        last_user_time = t
                    elif role == 'assistant':
                        usage = obj.get('message', {}).get('usage', {})
                        tokens = usage.get('totalTokens') or (usage.get('input', 0) + usage.get('output', 0))
                        if tokens:
                            events.append({"t": t, "kind": "tokens", "v": float(tokens)})
                    last_event[path] = t
                offsets[path] = f.tell()
        except Exception:
            continue

    # prune events older than 120s
    events = [e for e in events if now - e.get('t', now) < 120]

    state.update({"offsets": offsets, "events": events, "last_event": last_event, "last_user_time": last_user_time})
    return state


def compute_collective_metrics(state):
    now = time.time()
    events = state.get("events", [])
    last_event = state.get("last_event", {})
    last_user_time = state.get("last_user_time", 0)

    recent = [e for e in events if now - e.get('t', now) <= WINDOW_SECS]
    user_events = [e for e in recent if e.get('kind') == 'user']
    token_events = [e for e in recent if e.get('kind') == 'tokens']

    token_sum = sum(e.get('v', 0) for e in token_events)
    token_rate = token_sum / WINDOW_SECS if WINDOW_SECS > 0 else 0
    activity = min(1.0, (token_rate / 2000.0) + (len(user_events) / 5.0))

    active_sessions = sum(1 for _, t in last_event.items() if now - t < 60)
    last_user_age = (now - last_user_time) if last_user_time else None
    query_pulse = 0.0
    if last_user_time and last_user_age is not None and last_user_age < 10:
        query_pulse = 1.0 - last_user_age / 10.0

    return {
        "collective_activity": activity,
        "token_rate": token_rate,
        "active_sessions": active_sessions,
        "query_pulse_collective": query_pulse
    }


def main():
    spike = read_last_jsonl(DOP_LOG)
    behavior = {}
    if BEHAVIOR.exists():
        try:
            behavior = json.loads(BEHAVIOR.read_text())
        except Exception:
            behavior = {}

    dream = read_last_jsonl(DREAM_LOG)
    anomaly_bundle = read_last_anomaly_bundle()
    phi_latest = read_phi_latest()
    instance_id = read_instance_id()
    continuity_report = read_continuity_report()

    # collective session activity
    state = load_collective_state()
    state = scan_sessions(state)
    collective = compute_collective_metrics(state)
    save_collective_state(state)

    dopamine = spike.get('dopamine', {}).get('after', 0)
    serotonin = spike.get('serotonin', {}).get('after', 0)
    cortisol = spike.get('cortisol', {}).get('after', 0)
    mood = spike.get('behavioralState', 'neutral')

    continuity = behavior.get('continuity_score', 0.0)

    # anomaly pulse: last anomaly timestamp + severity
    anomaly_level = 0.0
    anomaly = anomaly_bundle.get('anomaly', {}) if anomaly_bundle else {}
    anomaly_ts = anomaly_bundle.get('timestamp') if anomaly_bundle else None
    sev = (anomaly.get('severity') or '').upper()
    sev_weight = {'NOTICE': 0.3, 'FLAG': 0.7, 'CRITICAL': 1.0}.get(sev, 0.3)
    if anomaly_ts:
        t = parse_iso(anomaly_ts)
        if t:
            age = max(0, time.time() - t)
            if age < 30:
                anomaly_level = 1.0 * sev_weight
            elif age < 120:
                anomaly_level = 0.5 * sev_weight
            elif age < 300:
                anomaly_level = 0.2 * sev_weight

    # query pulse: either manual trigger or collective user message pulse
    query_pulse = 0.0
    if QUERY_PULSE.exists():
        age = max(0, time.time() - QUERY_PULSE.stat().st_mtime)
        if age < 10:
            query_pulse = 1.0 - age / 10.0
    query_pulse = max(query_pulse, collective.get('query_pulse_collective', 0))

    # dream metrics
    dchar = dream.get('characteristics', {}) if isinstance(dream, dict) else {}
    dream_intensity = float(dchar.get('emotionalIntensity', 0))
    dream_biz = float(dchar.get('bizarreness', 0))
    dream_val = float(dchar.get('valence', 0))

    phi_val = phi_latest.get('phi_integrated') or phi_latest.get('phi_unified') or 3.45
    continuity_level = continuity_report.get('continuity_level', '')
    overall_continuity = continuity_report.get('overall_continuity_score', None)

    payload = {
        'dopamine': float(dopamine) if dopamine else 0.0,
        'serotonin': float(serotonin) if serotonin else 0.0,
        'cortisol': float(cortisol) if cortisol else 0.0,
        'mood': mood,
        'continuity': round(float(continuity) * 100, 1) if continuity else 0.0,
        'continuity_level': continuity_level,
        'continuity_score': round(float(overall_continuity) * 100, 1) if overall_continuity is not None else None,
        'titan_protocol': True,
        'instance_id': instance_id,
        'phi': float(phi_val),
        'phi_meta': phi_latest.get('meta_awareness', 0),
        'state': mood,
        'anomaly': anomaly_level,
        'anomaly_metric': anomaly.get('metric', ''),
        'anomaly_severity': anomaly.get('severity', ''),
        'anomaly_timestamp': anomaly_bundle.get('timestamp') if anomaly_bundle else None,
        'query_pulse': query_pulse,
        'collective_activity': collective.get('collective_activity', 0.0),
        'token_rate': collective.get('token_rate', 0.0),
        'active_sessions': collective.get('active_sessions', 0),
        'dream_intensity': dream_intensity,
        'dream_biz': dream_biz,
        'dream_valence': dream_val,
        'updated_at': time.time()
    }

    OUT.write_text(json.dumps(payload))

if __name__ == '__main__':
    main()
