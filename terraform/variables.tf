variable "region" {
  description = "デプロイ先リージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "name_prefix" {
  description = "リソース名の接頭辞"
  type        = string
  default     = "jma-pre-scale"
}

variable "service_name" {
  description = "対象サービス名。DynamoDB のパーティションキーにも使う"
  type        = string
  default     = "disaster-access-system"
}

variable "dry_run" {
  description = "true の間は一切の実APIを呼ばない。初期導入では必ず true のままにする"
  type        = bool
  default     = true
}

variable "poll_interval_minutes" {
  description = "気象庁フィードの監視間隔(分)。高頻度フィードは毎分更新"
  type        = number
  default     = 1

  validation {
    condition     = var.poll_interval_minutes >= 1
    error_message = "1分未満の監視は気象庁の帯域制限に抵触する恐れがあります。"
  }
}

# ---------------------------------------------------------------- 既存リソース
# いずれも「参照するだけ」で、既存アプリケーションの構成そのものは変更しない。

variable "ecs_cluster_name" {
  description = "既存の ECS クラスタ名"
  type        = string
}

variable "ecs_service_name" {
  description = "既存の ECS サービス名(Fargate)"
  type        = string
}

variable "aurora_cluster_identifier" {
  description = "既存の Aurora Serverless v2 クラスタ識別子。未使用なら空文字"
  type        = string
  default     = ""
}

variable "alb_target_group_arn" {
  description = "ヘルスチェック対象の ALB ターゲットグループ ARN。未使用なら空文字"
  type        = string
  default     = ""
}

# ---------------------------------------------------------------- 判定設定

variable "jma_feed_urls" {
  description = "監視する気象庁 Atom フィード。随時(警報・注意報)と地震火山"
  type        = list(string)
  default = [
    "https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
    "https://www.data.jma.go.jp/developer/xml/feed/eqvol.xml",
  ]
}

variable "target_area_codes" {
  description = "対象地域コード。空リストなら全国を対象にする"
  type        = list(string)
  default     = []
}

variable "target_area_names" {
  description = "対象地域名。コード体系が異なる電文の補助一致に使う"
  type        = list(string)
  default     = []
}

variable "supported_event_types" {
  description = "対象とする災害種別"
  type        = list(string)
  default     = ["heavy_rain", "flood", "typhoon", "earthquake", "tsunami", "storm", "snow", "high_tide", "landslide"]
}

variable "scaling_levels" {
  description = <<-EOT
    レベル別の目標容量。値は絶対値で指定する。
    level_0 は平時容量だが、地震等の突発災害に備えた常時予備容量を含む。
  EOT
  type = map(object({
    ecs_desired_count = number
    ecs_min_capacity  = number
    aurora_min_acu    = number
    aurora_max_acu    = number
  }))
  default = {
    level_0 = { ecs_desired_count = 2, ecs_min_capacity = 2, aurora_min_acu = 0.5, aurora_max_acu = 8 }
    level_1 = { ecs_desired_count = 5, ecs_min_capacity = 5, aurora_min_acu = 2, aurora_max_acu = 16 }
    level_2 = { ecs_desired_count = 15, ecs_min_capacity = 15, aurora_min_acu = 8, aurora_max_acu = 32 }
    level_3 = { ecs_desired_count = 40, ecs_min_capacity = 40, aurora_min_acu = 16, aurora_max_acu = 64 }
  }
}

variable "absolute_max_ecs_tasks" {
  description = "ECS タスク数の絶対上限。暴走時の最後の歯止め"
  type        = number
  default     = 50
}

variable "absolute_max_aurora_acu" {
  description = "Aurora ACU の絶対上限"
  type        = number
  default     = 64
}

variable "baseline_reserve_tasks" {
  description = "常時確保する予備タスク数。地震等でゼロ台起動に依存しないため 1 以上必須"
  type        = number
  default     = 2

  validation {
    condition     = var.baseline_reserve_tasks >= 1
    error_message = "突発災害に備え、常時予備容量は 1 以上にしてください。"
  }
}

variable "cooldown_minutes" {
  description = "解除受信後、縮小を開始するまでの待機時間"
  type        = number
  default     = 120
}

variable "scale_in_step" {
  description = "1回の縮小で減らすタスク数"
  type        = number
  default     = 5
}

variable "require_manual_approval_for_level_3" {
  description = "LEVEL_3(特別警報等)の適用に人の承認を必須にする"
  type        = bool
  default     = true
}

variable "allow_automatic_scale_in" {
  description = "クールダウン満了後の自動縮小を許可する。初期導入では false 推奨"
  type        = bool
  default     = false
}

variable "notification_emails" {
  description = "通知先メールアドレス。空なら購読を作らない"
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch Logs の保持日数"
  type        = number
  default     = 400
}
