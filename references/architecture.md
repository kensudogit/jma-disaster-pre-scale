# 標準アーキテクチャ

## コンポーネント

|コンポーネント|役割|
|---|---|
|EventBridge Scheduler|XML監視Lambdaの定期起動|
|Lambda Poller|Atom/XML取得、検証、新着判定|
|DynamoDB|重複排除、状態、ロック、最終処理時刻|
|Step Functions|段階的スケール、再試行、分岐、ロールバック|
|Lambda Controller|ECS/EC2/EKS/RDS等のAPI制御|
|CloudWatch|メトリクス、ログ、アラーム|
|SNS|運用通知|
|CloudTrail|操作証跡|
|CloudFront/WAF|前段キャッシュ、流量制御、攻撃防御|

## 状態遷移

```text
NORMAL -> WATCH -> WARNING -> EMERGENCY -> COOLDOWN -> NORMAL
                  \-> HOLD
```

- NORMAL: 平時容量
- WATCH: 注意情報、軽微な拡張
- WARNING: 警報、標準拡張
- EMERGENCY: 特別警報等、最大想定容量
- HOLD: XML取得失敗や手動維持
- COOLDOWN: 解除後の待機と段階縮小

## 非機能要件

- 冪等性
- 最小権限IAM
- 監査証跡
- Dry Run
- 手動オーバーライド
- 最大容量上限
- タイムアウトと再試行
- マルチAZ
- 既存アプリケーション無改修
