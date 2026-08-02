"""Railway / ローカル向けの軽量 HTTP 面。

本システムの本番は AWS Lambda + Step Functions だが、Railpack は
start command が無いとビルドに失敗する。ここでは健全性確認と dry-run
ポーリングを提供し、Railway 上でも起動できるようにする。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# Railway では DynamoDB が無い前提でインメモリ状態を使う
os.environ.setdefault("STATE_BACKEND", "memory")
os.environ.setdefault("DRY_RUN", "true")

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from jma_pre_scale.config import load
from jma_pre_scale.models import Action
from jma_pre_scale.notifier import audit_log, build_audit_entry
from jma_pre_scale.orchestrator import Poller
from jma_pre_scale.state import LockNotAcquired, build_store

app = FastAPI(
    title="JMA Disaster Pre-Scale",
    version="1.0.0",
    description="気象庁防災情報XML 事前スケール制御 (Railway ops surface)",
)


def _status_payload() -> dict[str, Any]:
    cfg = load()
    store = build_store(cfg)
    state = store.get_state()
    return {
        "service": cfg.service_name,
        "dry_run": cfg.dry_run,
        "region": cfg.region,
        "state_backend": os.environ.get("STATE_BACKEND", "auto"),
        "current_level": int(state.current_level),
        "system_state": state.system_state.value,
        "automation_disabled": state.automation_disabled,
        "last_reason": state.last_reason,
        "feed_urls": list(cfg.jma.feed_urls),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "note": "本番スケール適用は AWS (Terraform/Lambda)。ここは監視・dry-run 用。",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/status")
def api_status() -> dict[str, Any]:
    try:
        return _status_payload()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/poll")
def api_poll() -> dict[str, Any]:
    """1回分のフィード取得〜判定を実行する(既定 dry-run)。"""
    cfg = load()
    store = build_store(cfg)
    try:
        token = store.acquire_lock(cfg.safety.lock_ttl_seconds, owner="railway-web")
    except LockNotAcquired:
        return {"skipped": True, "reason": "lock_not_acquired"}
    try:
        outcome = Poller(cfg, store).run()
        decision = outcome.decision
        payload = decision.to_dict()
        audit_log(
            phase="poll",
            action=decision.action.value,
            level=payload.get("level_name"),
            reason=decision.reason,
            new_events=len(outcome.new_events),
            skipped_duplicates=outcome.skipped_duplicates,
            documents_fetched=outcome.documents_fetched,
            fetch_errors=outcome.fetch_errors,
            dry_run=cfg.dry_run,
        )
        store.record_audit(
            build_audit_entry(phase="poll", decision=payload, error="; ".join(outcome.fetch_errors))
        )
        return {
            "skipped": False,
            "dry_run": cfg.dry_run,
            "decision": payload,
            "would_scale": decision.action in (Action.SCALE_OUT, Action.SCALE_IN),
            "skipped_duplicates": outcome.skipped_duplicates,
            "documents_fetched": outcome.documents_fetched,
            "fetch_errors": outcome.fetch_errors,
        }
    finally:
        store.release_lock(token)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    try:
        st = _status_payload()
        err = ""
        status_text = json.dumps(st, ensure_ascii=False, indent=2)
    except Exception as exc:  # pragma: no cover
        err = str(exc)
        status_text = "{}"
    err_html = f'<p class="err">{err}</p>' if err else ""
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>JMA Pre-Scale</title>
<style>
body{{font-family:system-ui,sans-serif;margin:2rem;background:#0f1419;color:#e8eef5}}
.card{{background:#1c2430;border:1px solid #2a3544;border-radius:12px;padding:1.2rem;max-width:720px}}
.badge{{display:inline-block;padding:.2rem .6rem;border-radius:999px;border:1px solid #2a8f7e;color:#3cb8a0;font-size:.8rem}}
button{{font:inherit;cursor:pointer;border-radius:8px;border:0;background:#1f6f63;color:white;padding:.5rem .9rem;margin-top:.8rem}}
pre{{background:#11161d;padding:.8rem;border-radius:8px;overflow:auto;font-size:.85rem}}
.err{{color:#e07a7a}}
a{{color:#7eb8ff}}
</style></head><body>
<div class="card">
  <h1>JMA Disaster Pre-Scale</h1>
  <p>気象庁防災情報XMLを契機とした AWS 事前スケール制御基盤の ops 面です。</p>
  <p><span class="badge">Railway / dry-run</span></p>
  {err_html}
  <pre id="status">{status_text}</pre>
  <button type="button" onclick="runPoll()">dry-run ポーリング実行</button>
  <p><a href="/health">/health</a> · <a href="/api/v1/status">/api/v1/status</a> · <a href="/docs">/docs</a></p>
  <p style="color:#8b9bb0;font-size:.85rem">実スケール適用は Terraform / Lambda 側で行います。ここでの poll は判定ログ中心です。</p>
</div>
<script>
async function runPoll() {{
  const res = await fetch('/api/v1/poll', {{method:'POST'}});
  const data = await res.json();
  document.getElementById('status').textContent = JSON.stringify(data, null, 2);
}}
</script>
</body></html>"""
