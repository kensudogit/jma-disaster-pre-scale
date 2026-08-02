# 障害対応手順書

## 基本原則

**迷ったら容量は下げない。** 過剰な容量はコストの問題だが、
不足した容量は災害時にサービス停止を招く。

---

## 症状別対応

### A. アラーム `poller-not-invoked` が発報した

**意味:** 15分間、監視Lambdaが1度も起動していない。**事前スケールが機能していない状態。**

| 手順 | 確認 | 対処 |
|---|---|---|
| 1 | EventBridge Scheduler の状態 | `ENABLED` でなければ有効化 |
| 2 | Lambda の同時実行数上限 | `reserved_concurrent_executions = 1` が枯渇していないか |
| 3 | Lambda 関数の存在 | 誤削除されていないか |
| 4 | — | 復旧まで時間がかかる場合、気象情報を人手で確認し、必要なら手動強制拡張 |

```bash
# スケジューラの状態確認
aws scheduler get-schedule --name jma-pre-scale-poller

# 復旧できないときの応急処置(気象庁サイトを人が見て判断)
aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \
  --payload '{"op":"force_level","level":2,"operator":"あなたの名前"}' /dev/stdout
```

---

### B. アラーム `poller-errors` が発報した

**意味:** 監視Lambdaがエラーを返している。判定が行われていない可能性。

```bash
aws logs tail /aws/lambda/jma-pre-scale-poller --since 30m
```

| ログの内容 | 原因 | 対処 |
|---|---|---|
| `FeedError: 取得に失敗` | 気象庁側の障害 or ネットワーク | 容量は HOLD されている。気象庁の状況を確認し、復旧を待つ |
| `403 Forbidden` | **IP遮断の可能性** | 直ちに監視を停止し、ダウンロード量を調査(セクションD) |
| `ConfigError` | 設定不備 | `terraform apply` で設定を修正 |
| `AccessDenied` | IAM権限不足 | `terraform/iam.tf` を確認 |
| `LockNotAcquired` が続く | 前回の実行がロックを持ったまま異常終了 | TTL(15分)で自動解放される。急ぐ場合は下記 |

```bash
# ロックの強制解放(TTLを待てない場合のみ)
aws dynamodb delete-item --table-name jma-pre-scale-state \
  --key '{"pk":{"S":"LOCK#disaster-access-system"},"sk":{"S":"CURRENT"}}'
```

---

### C. アラーム `statemachine-failed` / `scaler-errors` が発報した

**意味:** 拡張の適用に失敗した。**現在容量は維持されている**(縮小はされていない)。

1. Step Functions のコンソールで失敗した実行を開く
2. どのステップで失敗したかを確認する

| 失敗ステップ | 想定原因 | 対処 |
|---|---|---|
| `ApplyScaling` (aurora) | Aurora が `modifying` 状態 / ACU上限 | 数分待って手動強制拡張で再実行 |
| `ApplyScaling` (application-autoscaling) | スケーラブルターゲット未登録 | ECSサービスに Auto Scaling が設定されているか確認 |
| `ApplyScaling` (ecs) | サービスがデプロイ中 / タスク定義の問題 | 既存デプロイの完了を待つ |
| `HealthCheck` = UNHEALTHY | タスクが起動できない | ECSタスクの停止理由を確認(セクションE) |
| `HealthCheck` = TIMEOUT | 起動が遅い | 容量不足(Fargateのキャパシティ)を疑う |

3. 原因を除去したうえで、手動で再実行する

```bash
aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \
  --payload '{"op":"force_level","level":3,"operator":"あなたの名前"}' /dev/stdout
```

**「一部だけ成功(PARTIAL)」の場合、成功した拡張は巻き戻されていない。**
再実行は冪等なので、同じ目標を再適用しても副作用はない。

---

### D. 気象庁から IP を遮断された(403 が継続)

**最優先で監視を停止する。** 遮断が長引くと復旧交渉が必要になる。

```bash
# 1. スケジューラを止める
aws scheduler update-schedule --name jma-pre-scale-poller --state DISABLED \
  --schedule-expression "rate(1 minute)" --flexible-time-window '{"Mode":"OFF"}' \
  --target "$(aws scheduler get-schedule --name jma-pre-scale-poller --query Target)"

# 2. 自動制御も止める
aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \
  --payload '{"op":"disable_automation","operator":"あなたの名前"}' /dev/stdout
```

**調査項目**

- 条件付きGET(ETag / If-Modified-Since)が実際に送信されているか
  → `DynamoDB` の `FEED#<service>` アイテムに `etag` が保存されているか確認
- 同じ電文を繰り返し取得していないか
  → `EVENT#<service>` の重複排除が機能しているか
- 他のシステムが同じ NAT Gateway / Egress IP を共有していないか
- Lambda が意図せず多重起動していないか

気象庁への連絡先: `jmaxml@met.kishou.go.jp`

---

### E. 拡張したのにタスクが起動しない

| 確認 | コマンド |
|---|---|
| タスクの停止理由 | `aws ecs describe-tasks --cluster <c> --tasks <t> --query 'tasks[].stoppedReason'` |
| サービスイベント | `aws ecs describe-services --cluster <c> --services <s> --query 'services[].events[:10]'` |

| 停止理由 | 対処 |
|---|---|
| `CannotPullContainerError` | ECR のスループット制限。VPCエンドポイント追加を検討 |
| サブネットのIP枯渇 | サブネットのCIDRを拡張(要ネットワーク変更) |
| Fargate のキャパシティ不足 | 複数AZに分散。`FARGATE_SPOT` の併用は災害時には非推奨 |
| ヘルスチェック失敗 | アプリ側の起動時間 > ALBの猶予期間。`healthCheckGracePeriodSeconds` の見直し(**既存サービス設定の変更にあたるため要承認**) |

---

### F. 拡張したのにレスポンスが改善しない

Web層以外がボトルネックになっている。

| 確認 | メトリクス | 対処 |
|---|---|---|
| DB接続数 | `AWS/RDS DatabaseConnections` | 最大接続数に張り付いていないか。タスク数 × プールサイズを計算 |
| DB容量 | `AWS/RDS ServerlessDatabaseCapacity` | 最大ACUに張り付いていないか |
| 認証基盤 | 各サービスのメトリクス | Cognito等のレート制限 |
| 外部API | アプリログ | サードパーティのレート制限 |

**Web層をさらに増やしても悪化するだけ。** この場合は手動で容量を維持し、
前段保護(CloudFront / WAF / 仮想待合室)の緊急導入を検討する。

---

### G. 想定外の拡張が起きた(誤判定)

1. まず自動制御を止める

```bash
aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \
  --payload '{"op":"disable_automation","operator":"あなたの名前"}' /dev/stdout
```

2. 原因の電文を特定する

```bash
aws logs filter-log-events --log-group-name /aws/lambda/jma-pre-scale-poller \
  --filter-pattern '{ $.action = "SCALE_OUT" }' --start-time <epoch_ms>
```

3. 監査ログの `decision.events` に、判定に使われた電文のURLと地域コードが残っている
4. `target_area_codes` / `severity_overrides` を修正して `terraform apply`
5. 自動制御を再開する

**容量はすぐには下げない。** 誤判定であっても、下げるのは状況を完全に把握してから。

---

## エスカレーション

| 状況 | 連絡先 |
|---|---|
| IP遮断 | 気象庁 情報基盤部情報政策課 / 社内ネットワーク管理者 |
| AWS側の障害 | AWS Support(Business以上) |
| 判定ロジックの不具合 | 本基盤の開発担当 |
| 既存アプリケーションの不具合 | アプリケーション開発担当 |
