# 監査ログ仕様

## 1. 保存先

| 保存先 | 内容 | 保持期間 |
|---|---|---|
| CloudWatch Logs | 構造化JSONログ(全フェーズ) | `log_retention_days`(既定400日) |
| DynamoDB `AUDIT#<service>#<YYYYMM>` | 判定と適用の前後状態 | TTL 400日 |
| CloudTrail | AWS API 呼び出しの証跡(`ecs:UpdateService` 等) | アカウントの設定に従う |
| Step Functions 実行履歴 | 各ステップの入出力 | 90日 |

CloudTrail は本パッケージでは作成しない。**アカウント全体で有効になっていることを前提**とする。
有効でない場合は組織のセキュリティ標準に従って別途設定すること。

---

## 2. 構造化ログ形式

すべての監査ログは以下の共通フィールドを持つ。

```json
{
  "schema_version": "1.0",
  "log_type": "jma_pre_scale_audit",
  "phase": "poll | plan | apply | healthcheck | notify | override"
}
```

CloudWatch Logs Insights での抽出:

```
fields @timestamp, phase, action, level, reason
| filter log_type = "jma_pre_scale_audit"
| sort @timestamp desc
```

### 2.1 `phase = "poll"` — 判定

| フィールド | 型 | 説明 |
|---|---|---|
| `action` | string | `SCALE_OUT` / `HOLD` / `SCALE_IN` / `NOOP` |
| `level` | string | 判定後のレベル名 |
| `reason` | string | 判定根拠(日本語)。どの電文がトリガーになったか |
| `new_events` | number | 今回新規に解析した電文由来イベント数 |
| `skipped_duplicates` | number | 重複排除でスキップした電文数 |
| `documents_fetched` | number | 実際に取得した電文数 |
| `fetch_errors` | array | 取得・検証に失敗したURLとエラー内容 |
| `dry_run` | bool | Dry Run 中かどうか |

### 2.2 `phase = "plan"` — 適用計画

| フィールド | 説明 |
|---|---|
| `plan.ecs.from` / `.to` | ECS 希望数の変更前後 |
| `plan.ecs_min_capacity.from` / `.to` | Auto Scaling 最小容量の変更前後 |
| `plan.aurora_min_acu.from` / `.to` | Aurora 最小ACUの変更前後 |

### 2.3 `phase = "apply"` — 適用

| フィールド | 説明 |
|---|---|
| `status` | `SUCCEEDED` / `PARTIAL` / `FAILED` / `DRY_RUN` |
| `action` | `SCALE_OUT` / `SCALE_IN` |
| `target` | 適用した目標容量(絶対値) |
| `dry_run` | Dry Run 中かどうか |

### 2.4 `phase = "healthcheck"`

| フィールド | 説明 |
|---|---|
| `status` | `HEALTHY` / `IN_PROGRESS` / `UNHEALTHY` / `TIMEOUT` |
| `attempts` | 試行回数 |
| `detail` | ECS の running/desired、ALB の healthy/total |

### 2.5 `phase = "override"` — 手動操作

| フィールド | 説明 |
|---|---|
| `op` | `force_level` / `clear_force` / `disable_automation` / `enable_automation` |
| `detail` | 操作内容(日本語) |
| `operator` | 操作者名。**運用手順で必ず指定させる** |

---

## 3. DynamoDB 監査アイテム

```text
pk = AUDIT#<service_name>#<YYYYMM>
sk = <ISO8601タイムスタンプ>#<UUID>
```

| 属性 | 説明 |
|---|---|
| `schema_version` | `"1.0"` |
| `phase` | フェーズ名 |
| `execution_id` | Lambda リクエストID |
| `decision` | 判定内容(action / level / reason / events) |
| `capacity_before` | **操作前の実容量**(ECS / Auto Scaling / Aurora) |
| `capacity_after` | **操作後の実容量** |
| `apply_result` | ステップごとの成否と before/after |
| `error` | エラー内容(あれば) |
| `ttl` | 400日後のUNIX時刻 |

`decision.events` には判定に使われた電文が全て入る。
各イベントには `source_url`(気象庁の電文URL)、`area_code`、`severity`、
`info_type` が含まれるため、**後から「なぜこの拡張が起きたか」を電文まで遡れる**。

### 月次レビュー用クエリ

```bash
aws dynamodb query --table-name jma-pre-scale-state \
  --key-condition-expression "pk = :pk" \
  --expression-attribute-values '{":pk":{"S":"AUDIT#disaster-access-system#202608"}}' \
  --output json
```

---

## 4. 追跡できること

| 問い | 参照先 |
|---|---|
| いつ、どのレベルに変更されたか | `phase=apply` の `target` と タイムスタンプ |
| なぜ変更されたか | `phase=poll` の `reason` と `decision.events[].source_url` |
| 変更前後の実容量 | `capacity_before` / `capacity_after` |
| 誰が手動操作したか | `phase=override` の `operator` |
| 実際にどのAWS APIが呼ばれたか | CloudTrail(`ecs:UpdateService`, `rds:ModifyDBCluster` 等) |
| 承認したのは誰か | Step Functions 実行履歴 + CloudTrail の `states:SendTaskSuccess` |
| 失敗したステップはどれか | `apply_result.steps[]` の `ok` / `detail` |

---

## 5. 保持と削除

- DynamoDB は TTL で自動削除(400日)
- CloudWatch Logs は保持期間で自動削除(既定400日)
- 監査要件で長期保管が必要な場合は、CloudWatch Logs のサブスクリプションフィルタで
  S3 + Glacier へエクスポートする(本パッケージには含まない)

**注意:** DynamoDB テーブルは `deletion_protection_enabled = true` かつ
`prevent_destroy = true` を設定している。テーブルの削除は監査証跡の消失を意味するため、
意図的に解除しない限り削除できない。
