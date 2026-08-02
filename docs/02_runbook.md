# 運用手順書

## 0. 前提

- 環境変数 `FN=$(terraform -chdir=terraform output -raw override_function_name)` を設定しておく
- すべての手動操作は監査ログ(DynamoDB / CloudWatch Logs)に記録される
- `operator` には必ず自分の名前を入れる

---

## 1. 日常運用

### 1.1 毎日確認すること

| 確認項目 | 場所 | 正常な状態 |
|---|---|---|
| 監視Lambdaの起動 | CloudWatch ダッシュボード「監視Lambda 起動/エラー」 | 5分あたり5回前後、エラー0 |
| 現在の制御状態 | 下記 `status` コマンド | `current_level: 0`, `automation_disabled: false` |
| アラーム | CloudWatch Alarms | すべて OK |

### 1.2 現在状態の確認

```bash
aws lambda invoke --function-name $FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"op":"status"}' /dev/stdout
```

出力例:

```json
{"state": {"current_level": 0, "system_state": "NORMAL",
           "cooldown_until": null, "forced_level": null,
           "automation_disabled": false, "version": 12}}
```

### 1.3 直近の判定履歴

```bash
aws logs tail /aws/lambda/jma-pre-scale-poller --since 1h --follow \
  --filter-pattern '{ $.log_type = "jma_pre_scale_audit" }'
```

---

## 2. 警報発表時

自動で処理される。運用者の作業は**確認のみ**。

1. SNS通知(件名: `[サービス名] 事前スケール SCALE_OUT HEALTHY`)を受信
2. ダッシュボード「ECS 稼働タスク数」で `DesiredTaskCount` と `RunningTaskCount` の追随を確認
3. `RunningTaskCount` が10分経っても増えない場合 → セクション5へ

---

## 3. 特別警報発表時(LEVEL_3、承認が必要)

1. 件名 `[承認要求] 事前スケール LEVEL_3 適用可否` のメールを受信
2. 本文の `reason` と `plan` を確認する
3. **承認する場合** — 本文中の `approve_command` をそのまま実行

```bash
aws stepfunctions send-task-success \
  --task-token "<メール本文のtaskToken>" \
  --task-output '{"approved":true}'
```

4. **拒否する場合** — 何もしない。10分後にタイムアウトし、
   自動で1段下(LEVEL_2)の容量が適用される。完全に拒否したい場合はセクション4.3。

> 承認期限は10分。判断がつかないときは放置してよい。放置しても容量は
> LEVEL_2 まで上がるため「何もされない」状態にはならない。

---

## 4. 手動介入

### 4.1 強制的に拡張する(気象庁電文より先に判断したいとき)

```bash
aws lambda invoke --function-name $FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"op":"force_level","level":3,"operator":"yamada"}' /dev/stdout
```

強制レベルは**電文判定より優先**され、承認も不要になる。
設定中は電文による自動判定が完全に無視される点に注意。

### 4.2 強制レベルを解除する

```bash
aws lambda invoke --function-name $FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"op":"clear_force","operator":"yamada"}' /dev/stdout
```

解除しても**容量はその場では下がらない**。次回の判定でクールダウンが始まる。

### 4.3 自動制御を完全に停止する

```bash
aws lambda invoke --function-name $FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"op":"disable_automation","operator":"yamada"}' /dev/stdout
```

停止中は電文を受信しても一切の容量変更を行わない(現在容量は維持)。
メンテナンス中や、判定ロジックの不具合が疑われるときに使う。

### 4.4 自動制御を再開する

```bash
aws lambda invoke --function-name $FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"op":"enable_automation","operator":"yamada"}' /dev/stdout
```

### 4.5 手動で平時容量へ戻す

自動縮小(`allow_automatic_scale_in`)を無効にしている場合、
災害収束後は手動で戻す。**必ず状況が完全に収束してから**行う。

```bash
# 段階的に下げる。一気に LEVEL_0 にはしない。
aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \
  --payload '{"op":"force_level","level":2,"operator":"yamada"}' /dev/stdout
# 30分待って問題なければ
aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \
  --payload '{"op":"force_level","level":1,"operator":"yamada"}' /dev/stdout
# さらに30分待って
aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \
  --payload '{"op":"force_level","level":0,"operator":"yamada"}' /dev/stdout
# 最後に強制を解除
aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \
  --payload '{"op":"clear_force","operator":"yamada"}' /dev/stdout
```

---

## 5. 変更管理

### 5.1 容量設定を変更する

`terraform/terraform.tfvars` の `scaling_levels` を編集し、
`terraform plan` の差分を確認してから apply する。

Lambda の環境変数(`CONFIG_JSON`)が更新されるだけで、
実行中のスケール状態には影響しない。次回の判定から新しい値が使われる。

### 5.2 Dry Run を解除する(本番制御の有効化)

**チェックリスト(すべて満たしてから実施)**

- [ ] Dry Run で1週間以上、判定ログを観察した
- [ ] 対象地域の警報で意図どおり LEVEL_2 が出ている
- [ ] 対象外地域の電文で拡張判定が出ていない
- [ ] 負荷試験を実施し、`scaling_levels` を実測値に置き換えた
- [ ] Aurora の最大接続数が LEVEL_3 のタスク数に耐えることを確認した
- [ ] SNS通知の宛先が正しく、購読が Confirmed になっている
- [ ] `absolute_max_ecs_tasks` がコスト上許容できる値になっている
- [ ] 運用承認を得た

```bash
# terraform.tfvars で dry_run = false に変更してから
terraform -chdir=terraform plan
terraform -chdir=terraform apply
```

**解除直後は必ず手動テストを行う**(セクション4.1で LEVEL_1 を強制 → 実容量の変化を確認 → 元に戻す)。

### 5.3 自動縮小を有効にする

最低1ヶ月の運用実績を見てから。`allow_automatic_scale_in = true` にする。
有効化後は、解除電文の受信から `cooldown_minutes`(既定120分)後に
`scale_in_step`(既定5タスク)ずつ縮小が始まる。

---

## 6. デプロイ

```bash
cd terraform
terraform init
terraform plan    # 差分を必ず目視確認
terraform apply
```

初回デプロイ後は SNS 購読確認メールが届くので、必ず Confirm する。

### ロールバック

```bash
git revert <commit>
terraform apply
```

デプロイのロールバックでは**実容量は変わらない**。
容量を戻したい場合はセクション4.5を使う。

---

## 7. 定期作業

| 頻度 | 作業 |
|---|---|
| 毎日 | ダッシュボード確認(1.1) |
| 毎月 | 監査ログのレビュー。想定外の判定がなかったか |
| 四半期 | 気象庁のコード管理表・技術資料の更新確認(https://xml.kishou.go.jp/revise.html) |
| 半年 | 負荷試験の再実施。容量設定の見直し |
| 年1回 | 災害訓練。手動強制拡張を実際に実行し、手順書の妥当性を確認 |
