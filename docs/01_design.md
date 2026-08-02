# 事前スケール設計書

対象: 平時の利用が少なく、災害発生時に数万IDが一斉アクセスするシステム
制約: **既存アプリケーションのコード・DBスキーマ・業務ロジックを一切変更しない**

---

## 1. 実現可否

実現可能。ただし次の3点が前提となる。

| 前提 | 満たせない場合 |
|---|---|
| アプリケーションが水平スケール可能(ステートレス、またはセッション外部化済み) | 事前スケールしても台数を増やせない。まずセッションのExternalize が必要 |
| ECSサービスの `desiredCount` を外部から変更してよい | 本方式は使えない。ASG方式かEKS方式に切り替える |
| Aurora Serverless v2 の最小ACUを一時的に引き上げてよい | DB層が先に飽和する。Web層だけ増やしても意味がない |

**既存アプリケーションには一切触れない。** 本基盤はアプリケーションの「外側」から
AWSのコントロールプレーンAPIだけを叩く。コンテナイメージ、タスク定義、環境変数、
DBスキーマ、既存のALBルーティングはすべて現状のまま。

---

## 2. 前提条件と未確定事項

以下は環境ごとに確定が必要。未確定のままでは `dry_run: true` を外してはならない。

| 項目 | 確定要否 | 既定値 |
|---|---|---|
| ECSクラスタ名 / サービス名 | 必須 | — |
| Aurora Serverless v2 クラスタ識別子 | 必須(DB制御を使う場合) | — |
| 1タスクあたりの処理能力(RPS) | 必須 | 未計測 |
| タスク起動〜ヘルスチェック通過までの時間 | 必須 | 未計測 |
| 想定登録ID数 / 同時アクセス率 / ピークRPS | 必須 | 未計測 |
| 対象地域コード | 必須 | 空=全国 |
| Aurora最大接続数と1タスクあたりのプールサイズ | 必須 | 未計測 |
| 認証基盤のスループット上限 | 必須 | 未計測 |

**特に重要:** レベル別のタスク数(`scaling_levels`)は負荷試験の実測値で必ず置き換える。
同梱の既定値(2 / 5 / 15 / 40)は形式を示すためのプレースホルダである。

---

## 3. 推奨アーキテクチャ

```text
                    ┌──────────────────────────┐
                    │  気象庁 Atom フィード     │
                    │  extra.xml / eqvol.xml   │
                    └───────────┬──────────────┘
                                │ 毎分 条件付きGET (ETag / If-Modified-Since)
                    ┌───────────▼──────────────┐
  EventBridge ─────▶│  Poller Lambda           │
  Scheduler         │  取得→検証→重複排除→判定 │
  (rate 1 minute)   └───────────┬──────────────┘
                                │            ┌─────────────────┐
                                │◀──────────▶│ DynamoDB        │
                                │            │ 状態/重複排除/  │
                                │            │ ロック/監査     │
                                │            └─────────────────┘
                                │ SCALE_OUT / SCALE_IN のときだけ起動
                    ┌───────────▼──────────────┐
                    │  Step Functions          │
                    │  Plan → (承認) → Apply   │
                    │  → HealthCheck → Notify  │
                    └───────────┬──────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
  ┌───────────────┐   ┌──────────────────┐   ┌───────────────┐
  │ Aurora        │   │ Application      │   │ ECS Service   │
  │ ServerlessV2  │   │ Auto Scaling     │   │ desiredCount  │
  │ 最小ACU引上げ │   │ MinCapacity引上げ│   │ 引上げ        │
  └───────────────┘   └──────────────────┘   └───────────────┘
          │                     │                     │
          └─────────────────────┴─────────────────────┘
                                │
                    ┌───────────▼──────────────┐
                    │  既存システム(無改修)    │
                    └──────────────────────────┘
```

### 適用順序が重要な理由

拡張時は **Aurora → ECS MinCapacity → ECS DesiredCount** の順。

1. **Aurora が先**: Web層だけ増やしてDBが飽和すると、増えたタスクが全て
   接続待ちになり、かえって全体が悪化する(SKILL.md 禁止事項)。
2. **MinCapacity が DesiredCount より先**: 先に `desiredCount` を上げても、
   既存の Target Tracking ポリシーが「メトリクスが低い」と判断して数分後に
   縮小してしまう。MinCapacity を上げておけば下限が守られる。

縮小時は完全に逆順。

---

## 4. 既存システム無改修の実現方法

| やること | やらないこと |
|---|---|
| ECS の `desiredCount` を外から変更 | タスク定義・イメージ・環境変数の変更 |
| Application Auto Scaling の `MinCapacity` を外から変更 | 既存スケーリングポリシーの削除・置換 |
| Aurora の `ServerlessV2ScalingConfiguration` を変更 | DBスキーマ・パラメータグループ・接続文字列の変更 |
| ALB のターゲット健全性を **読み取り** | リスナールール・ターゲットグループの変更 |
| 別スタック(Lambda/SFN/DynamoDB)を新規作成 | 既存スタックへのリソース追加 |

IAM権限も上記に対応する最小権限のみを付与する(`terraform/iam.tf`)。
`ecs:UpdateService` は対象1サービスの ARN に限定、`rds:ModifyDBCluster` は
対象1クラスタの ARN に限定している。

### 前段保護(オプション、既存システム無改修を維持したまま追加可能)

本パッケージのスコープ外だが、以下は既存システムに手を入れずに追加できる。

- **CloudFront**: 静的コンテンツのキャッシュ。オリジンをALBにするだけ。
- **AWS WAF レートベースルール**: ALBにアタッチ。IP単位の流量制御。
- **AWS Shield Standard**: 追加設定不要で有効。
- **カスタムエラーページ**: CloudFront のエラーレスポンス設定。
- **仮想待合室**: CloudFront Functions / Lambda@Edge で実装。導入判断は別途。

---

## 5. 判定ルール

### 5.1 レベル定義

| レベル | 状態 | 意味 | 既定容量(ECSタスク / Aurora最小ACU) |
|---|---|---|---|
| LEVEL_0 | NORMAL | 平時。ただし常時予備容量を含む | 2 / 0.5 |
| LEVEL_1 | WATCH | 注意報相当 | 5 / 2 |
| LEVEL_2 | WARNING | 警報相当 | 15 / 8 |
| LEVEL_3 | EMERGENCY | 特別警報相当。**人の承認が必要** | 40 / 16 |
| — | HOLD | 取得失敗・手動停止。現在容量を維持 | 変更なし |
| — | COOLDOWN | 解除後の待機と段階縮小 | 段階的に低下 |

### 5.2 電文 → 重大度の写像

| 電文 | 判定材料 | 重大度 |
|---|---|---|
| 気象警報・注意報 | `Kind/Name` が「〜特別警報」で終わる | emergency_warning |
| 気象警報・注意報 | `Kind/Name` が「〜警報」で終わる | warning |
| 気象警報・注意報 | `Kind/Name` が「〜注意報」で終わる | advisory |
| 震度速報 / 震源・震度情報 | `Pref/MaxInt` が 6-, 6+, 7 | emergency_warning |
| 同上 | `MaxInt` が 5-, 5+ | warning |
| 同上 | `MaxInt` が 4 | advisory |
| 同上 | `MaxInt` が 1〜3 | none(判定対象外) |
| 津波警報・注意報・予報 | `Category/Kind/Name` = 大津波警報 / 津波警報 | emergency_warning |
| 同上 | = 津波注意報 | warning |
| 同上 | = 津波予報 | advisory |

コード番号(`Kind/Code`)ではなく名称の接尾辞で判定している。
気象庁のコード管理表は改訂されるが、「〜特別警報 / 〜警報 / 〜注意報」という
命名規則は運用指針で固定されており、より堅牢なため。

### 5.3 重大度 → レベル

| 重大度 | 既定レベル |
|---|---|
| none | LEVEL_0 |
| advisory | LEVEL_1 |
| warning | LEVEL_2 |
| emergency_warning | LEVEL_3 |

災害種別ごとに `severity_overrides` で上書き可能。
例: 地震は震度4(advisory)でも LEVEL_2 まで上げたい → `earthquake: {advisory: 2}`

### 5.4 最終判定表

判定は上から順に評価し、最初に該当した行で決まる。

| # | 条件 | アクション | 結果レベル |
|---|---|---|---|
| 1 | `automation_disabled = true` | HOLD | 現在レベル維持 |
| 2 | `forced_level` が設定済み | SCALE_OUT / SCALE_IN | 強制レベル(承認不要) |
| 3 | 全フィードの取得・検証に失敗 | **HOLD** | 現在レベル維持 |
| 4 | 対象電文なし / 対象外地域 / 対象外種別 / 訓練電文 | 平時ならNOOP、拡張中ならHOLD | 現在レベル維持 |
| 5 | 解除・取消のみ受信 | **HOLD**(クールダウン開始) | 現在レベル維持 |
| 6 | 要求レベル ≤ 現在レベル | NOOP | 現在レベル維持 |
| 7 | 要求レベル > 現在レベル | SCALE_OUT | 要求レベル |
| 8 | クールダウン中(満了前) | HOLD | 現在レベル維持 |
| 9 | クールダウン満了 かつ `allow_automatic_scale_in = true` | SCALE_IN | 1段階のみ低下 |

**レベルは単調増加しかしない。** 下げるのはクールダウン満了時か手動操作のときだけ。

### 5.5 対象地域の判定

コード体系が電文により異なるため、コードと名称の両方で照合する。

| 電文 | コード例 | 桁数 |
|---|---|---|
| 気象警報・注意報(府県予報区) | 130000 | 6 |
| 震度速報(府県) | 13 | 2 |
| 津波予報区 | 101 | 3 |

`target_area_codes` と `target_area_names` の**どちらか**に一致すれば対象。
両方とも空にすると全国が対象になる。

---

## 6. 実装手順

1. `config/config.example.yaml` を環境に合わせて編集する
2. `terraform/terraform.tfvars.example` を `terraform.tfvars` にコピーして編集する
   - **`dry_run = true` のままにする**
3. `terraform init && terraform plan` で差分を確認する
4. `terraform apply` でデプロイする
5. CloudWatch Logs で判定ログを1週間観察する(実容量は変わらない)
6. 判定結果が期待どおりであることを確認する
7. 負荷試験(`04_load_test_plan.md`)を実施し、`scaling_levels` を実測値に置き換える
8. 運用承認を得たうえで `dry_run = false` にして `terraform apply`
9. `allow_automatic_scale_in` は最低1ヶ月の運用実績を見てから有効化する

---

## 7. テスト計画

`tests/` に SKILL.md Phase 6 の全ケースを実装済み(94ケース)。

```bash
pytest              # pytest がある環境
python3 tests/run_tests.py   # pytest が無い環境(標準ライブラリのみ)
```

| Phase 6 の項目 | 対応テスト |
|---|---|
| XML正常受信 | `test_parser.py::test_気象警報を解析して地域単位のイベントになる` ほか |
| XML重複受信 | `test_end_to_end.py::test_2回目の実行では同じ電文を再取得しない` |
| XML取得失敗 | `test_end_to_end.py::test_全フィードの取得に失敗したらHOLDになる` |
| 訂正・取消・解除 | `test_parser.py` の該当3件 + `test_rules.py::test_解除を受信しても即時縮小しない` |
| 対象外地域 | `test_rules.py::test_対象外地域の特別警報では拡張しない` |
| Dry Run | `test_controller.py::test_DryRunでは実APIを一切呼ばない` |
| スケールAPI失敗 | `test_controller.py::test_全リソースの操作に失敗するとFAILEDになる` |
| 一部リソースだけ成功 | `test_controller.py::test_ECS更新に失敗しても他リソースの結果は保持される` |
| ヘルスチェック失敗 | `test_controller.py::test_ALBにhealthyターゲットが無ければunhealthy` |
| 数万ID相当の負荷試験 | `docs/04_load_test_plan.md`(実環境で別途実施) |
| 段階縮小 | `test_rules.py::test_クールダウン満了で一段ずつ縮小する` |
| 手動強制拡張 | `test_end_to_end.py::test_手動強制拡張は電文なしでも適用される` |
| 自動制御停止 | `test_end_to_end.py::test_自動制御停止中は電文を受けても拡張しない` |

---

## 8. リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| **地震は事前に分からない** | 事前スケールが間に合わない | `baseline_reserve_tasks` で常時予備容量を確保(設定で0にできないよう検証で強制)。震度速報は発生から約1.5分で配信されるため、その時点で拡張を開始できる |
| **タスク起動に時間がかかる** | 拡張完了前にアクセスが来る | 警報は実際の災害の数時間前に出る。Fargateのイメージ取得時間を短縮(イメージ軽量化 / SOCI インデックス)しておく |
| **DBが先に飽和する** | Web層だけ増やしても無意味 | Aurora を先に拡張。接続プール上限 × タスク数 が Aurora の最大接続数を超えない設計にする |
| **気象庁からのIP遮断** | 監視が完全停止 | 条件付きGET必須。1日10GB制限。`poll_interval_minutes >= 1` を Terraform の validation で強制 |
| **フィード取得失敗を縮小と誤認** | 災害時に容量が落ちる | `decide_on_feed_error()` は必ず HOLD を返す。テストで担保 |
| **解除直後の再発表** | 縮小 → 再拡張のフラッピング | クールダウン(既定120分)+ 段階縮小 + `allow_automatic_scale_in` 既定 false |
| **判定ロジックの誤りで暴走** | 意図しない大量課金 | `absolute_max_ecs_tasks` / `absolute_max_aurora_acu` で絶対上限。`reserved_concurrent_executions = 1` |
| **監視Lambdaが止まっている** | 誰も気づかない | `poller-not-invoked` アラーム(15分間起動なしで発報、`treat_missing_data = breaching`) |
| **同一イベントの多重実行** | 二重適用 | DynamoDB分散ロック + 条件付き書き込み + Step Functions の冪等な実行名 |
| **訓練・試験電文での誤作動** | 不要な拡張 | `Control/Status != 通常` を除外。テストで担保 |
| **承認が得られない** | 特別警報時に何もしない | 承認タイムアウト(既定10分)で1段下の容量を自動適用(`ApplyFallback`) |
| **CloudFront/WAF未導入** | 前段で吸収できない | 本パッケージのスコープ外。別途検討(第4章参照) |

---

## 9. 運用方法

- 平常時: CloudWatch ダッシュボードで監視Lambdaの起動を確認するのみ
- 警報発表時: SNS通知が届く。ダッシュボードで実容量の追随を確認
- 特別警報時: 承認要求メールが届く。10分以内に承認するか、放置すれば1段下で自動適用
- 異常時: `docs/03_incident_response.md` に従う
- 手動介入: `docs/02_runbook.md` のコマンドを使う

詳細は運用手順書を参照。

---

## 10. 次に作成すべき成果物

本パッケージに未収録で、実環境ごとに必要になるもの。

1. **容量計算書** — 1タスクあたりのRPS実測値から `scaling_levels` を逆算した根拠資料
2. **Aurora接続数設計書** — タスク数 × プールサイズ ≤ 最大接続数 の検証
3. **CloudFront / WAF 導入設計書** — 前段保護を追加する場合
4. **仮想待合室の要否判断書** — LEVEL_3 でも捌けない場合の最終手段
5. **既存アプリのステートレス性評価書** — セッション・共有ファイル・ローカルキャッシュの棚卸し
6. **災害訓練計画書** — 年1回、実際に手動強制拡張を実行する訓練
