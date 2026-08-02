# JMA Disaster Pre-Scale

気象庁防災情報XMLを外部監視し、**既存アプリケーションを一切変更せずに**
AWSインフラを災害アクセス集中前に事前拡張する制御基盤。

`SKILL.md` の設計方針に沿った実装一式です。

対象構成: **ECS Fargate + Aurora Serverless v2**

---

## クイックスタート

```bash
# 1. テストを実行(AWS接続不要)
python3 tests/run_tests.py        # または pytest

# 2. 設定を編集
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
vi terraform/terraform.tfvars     # dry_run = true のまま編集

# 3. デプロイ
cd terraform && terraform init && terraform plan && terraform apply
```

**初期導入では `dry_run = true` を維持してください。** この間、実APIは一切呼ばれず、
判定ログだけが記録されます。負荷試験と運用承認を経てから `false` にします。

---

## 構成

```
jma-disaster-pre-scale/
├── SKILL.md                     Skill本体(原本)
├── README.md
├── config/
│   ├── config.yaml              実行時設定
│   └── config.example.yaml      設定例(全項目コメント付き)
├── src/jma_pre_scale/
│   ├── models.py                ドメインモデル(レベル / イベント / 判定結果)
│   ├── config.py                設定ロードと検証
│   ├── feed.py                  Atom取得(条件付きGET / 再試行 / サイズ上限)
│   ├── parser.py                JMAXML解析(警報・注意報 / 震度 / 津波)
│   ├── rules.py                 判定ルール ★安全側の原則はすべてここ
│   ├── state.py                 状態 / 重複排除 / 分散ロック / 監査(DynamoDB)
│   ├── controller.py            ECS Fargate + Aurora Serverless v2 制御
│   ├── orchestrator.py          取得→検証→重複排除→判定の一連の流れ
│   ├── notifier.py              SNS通知と構造化監査ログ
│   └── handlers/                Lambda ハンドラ(薄いアダプタ層)
│       ├── poller.py            EventBridge から毎分起動
│       ├── planner.py           現況取得と適用計画
│       ├── scaler.py            実適用
│       ├── healthcheck.py       拡張後の健全性確認
│       ├── notify.py            成功・失敗通知
│       └── override.py          手動オーバーライドAPI
├── statemachine/
│   └── pre_scale.asl.json       Step Functions 定義
├── terraform/                   IaC 一式(最小権限IAM込み)
├── tests/                       94ケース。SKILL.md Phase 6 の全項目を網羅
└── docs/
    ├── 01_design.md             事前スケール設計書 + 判定ルール表
    ├── 02_runbook.md            運用手順書
    ├── 03_incident_response.md  障害対応手順書
    ├── 04_load_test_plan.md     負荷試験計画
    └── 05_audit_log_spec.md     監査ログ仕様
```

---

## 動作概要

```
気象庁 Atom フィード (毎分, 条件付きGET)
   ↓
Poller Lambda … 取得 → 検証 → 重複排除 → 判定
   ↓ (拡張/縮小が必要なときだけ)
Step Functions … 計画 → 承認 → 適用 → ヘルスチェック → 通知
   ↓
Aurora 最小ACU → ECS MinCapacity → ECS DesiredCount  (この順序が重要)
   ↓
既存システム(無改修)
```

### レベル定義

| レベル | 契機 | 既定容量 |
|---|---|---|
| LEVEL_0 | 平時(常時予備容量を含む) | 2タスク / 0.5 ACU |
| LEVEL_1 | 注意報 / 震度4 | 5タスク / 2 ACU |
| LEVEL_2 | 警報 / 震度5弱-5強 / 津波注意報 | 15タスク / 8 ACU |
| LEVEL_3 | 特別警報 / 震度6弱以上 / 津波警報 | 40タスク / 16 ACU(**承認必須**) |

---

## 既存システムへの影響

| 変更しないもの | 理由 |
|---|---|
| アプリケーションのソースコード | 本基盤は外側からAWS APIのみを操作 |
| コンテナイメージ / タスク定義 | `desiredCount` だけを変更 |
| DBスキーマ / 業務データ形式 | Aurora のACU設定のみを変更 |
| 既存のスケーリングポリシー | `MinCapacity` の引き上げのみ。ポリシーは残す |
| ALB のリスナー / ルーティング | ターゲット健全性を読み取るだけ |

IAM も対象1サービス・対象1クラスタのARNに限定した最小権限です(`terraform/iam.tf`)。

---

## 安全設計

`SKILL.md` の最重要制約は、すべてコードとテストで担保しています。

| 制約 | 実装 | テスト |
|---|---|---|
| XML取得失敗時は縮小せず現在容量を維持 | `rules.decide_on_feed_error()` | `test_全フィードの取得に失敗したらHOLDになる` |
| 解除受信でも即時縮小せずクールダウン | `rules._hold_or_cooldown()` | `test_解除を受信しても即時縮小しない` |
| 地震等に備えた常時予備容量 | `config.validate()` が0を拒否 | `test_常時予備容量を下回らない` |
| 最大容量の上限 | `rules.clamp_target()` | `test_絶対上限を超える容量はクランプされる` |
| 手動オーバーライド最優先 | `rules.decide()` 冒頭 | `test_自動制御停止が最優先される` |
| 同一イベントの多重実行防止 | DynamoDB 条件付き書き込み + 分散ロック | `test_同じ電文IDは一度しか処理されない` |
| 未検証XMLをインフラ操作へ繋げない | `parser` が必須項目・DOCTYPE・サイズを検証 | `test_DOCTYPEやENTITYを含む電文は拒否する` |
| 訓練・試験電文で作動しない | `Control/Status != 通常` を除外 | `test_訓練電文ではインフラを操作しない` |
| 本番変更前の Dry Run | `controller.apply()` が dry_run で全APIを回避 | `test_DryRunでは実APIを一切呼ばない` |

---

## 気象庁フィード利用上の注意

- **1日10GB以上のダウンロードでIPが遮断されます。**
  本実装は ETag / If-Modified-Since による条件付きGETと、DynamoDBによる
  電文単位の重複排除を必ず経由します。この仕組みを外さないでください。
- 監視間隔は1分以上(Terraform の validation で強制)。高頻度フィードは毎分更新です。
- 利用にあたっては[ご利用にあたっての留意事項](https://xml.kishou.go.jp/considerationforxml.pdf)を確認してください。

---

## 実環境で必ず確定すること

このパッケージは**動作するひな型**です。以下は環境ごとに実測・確定してください。

1. `scaling_levels` の各値 — 負荷試験の実測値に置き換える(`docs/04_load_test_plan.md`)
2. `target_area_codes` / `target_area_names` — 対象地域
3. `baseline_reserve_tasks` — 地震発生から拡張完了までを耐えられる台数
4. Aurora の最大接続数 vs タスク数 × 接続プールサイズ
5. 既存アプリケーションのステートレス性(セッション・共有ファイル・ローカルキャッシュ)

---

## 参考

- [気象庁防災情報XMLフォーマット](https://xml.kishou.go.jp/)
- [PULL型提供(Atomフィード)](https://xml.kishou.go.jp/xmlpull.html)
- [技術資料・コード管理表](https://xml.kishou.go.jp/tec_material.html)
"# jma-disaster-pre-scale" 
