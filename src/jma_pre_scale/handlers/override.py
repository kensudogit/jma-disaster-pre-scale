"""手動オーバーライド API。SKILL.md「手動オーバーライドを最優先する」。

呼び出し例(いずれも運用者が AWS CLI / コンソールから実行):

  aws lambda invoke --function-name <fn> \
      --payload '{"op":"force_level","level":3}' out.json
  aws lambda invoke --function-name <fn> \
      --payload '{"op":"clear_force"}' out.json
  aws lambda invoke --function-name <fn> \
      --payload '{"op":"disable_automation"}' out.json
  aws lambda invoke --function-name <fn> \
      --payload '{"op":"enable_automation"}' out.json
  aws lambda invoke --function-name <fn> --payload '{"op":"status"}' out.json
"""
from __future__ import annotations

import dataclasses
from typing import Any

from ..models import ScaleLevel
from ..notifier import audit_log
from ._common import get_config, get_notifier, get_store

_OPS = {"force_level", "clear_force", "disable_automation", "enable_automation", "status"}


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    op = str(event.get("op", "status"))
    if op not in _OPS:
        raise ValueError(f"未知の操作です: {op} (有効: {sorted(_OPS)})")

    store = get_store()
    state = store.get_state()

    if op == "status":
        return {"state": state.to_item()}

    if op == "force_level":
        level = ScaleLevel(int(event["level"]))
        new = dataclasses.replace(state, forced_level=int(level))
        detail = f"強制レベル {level.name} を設定しました"
    elif op == "clear_force":
        new = dataclasses.replace(state, forced_level=None)
        detail = "強制レベルを解除しました"
    elif op == "disable_automation":
        new = dataclasses.replace(state, automation_disabled=True)
        detail = "自動制御を停止しました"
    else:
        new = dataclasses.replace(state, automation_disabled=False)
        detail = "自動制御を再開しました"

    saved = store.put_state(dataclasses.replace(new, last_reason=f"manual: {detail}"))
    audit_log(phase="override", op=op, detail=detail,
              operator=str(event.get("operator", "unknown")))
    store.record_audit({"phase": "override", "op": op, "detail": detail,
                        "operator": str(event.get("operator", "unknown"))})
    get_notifier().notify(
        f"[{get_config().service_name}] 手動オーバーライド: {op}",
        {"detail": detail, "state": saved.to_item()},
    )
    return {"ok": True, "detail": detail, "state": saved.to_item()}
