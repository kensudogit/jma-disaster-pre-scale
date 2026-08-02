locals {
  name = var.name_prefix

  ecs_resource_id = "service/${var.ecs_cluster_name}/${var.ecs_service_name}"

  #  Lambda 環境変数に載せる設定。ファイル配布を避け、Terraform を単一の真実にする。
  app_config = {
    service_name          = var.service_name
    region                = var.region
    dry_run               = var.dry_run
    poll_interval_minutes = var.poll_interval_minutes

    jma = {
      feed_urls               = var.jma_feed_urls
      request_timeout_seconds = 10
      max_retries             = 2
      retry_backoff_seconds   = 1
      user_agent              = "jma-disaster-pre-scale/1.0 (${var.service_name})"
      max_feed_bytes          = 8388608
      max_document_bytes      = 4194304
      max_documents_per_run   = 20
      target_area_codes       = var.target_area_codes
      target_area_names       = var.target_area_names
      supported_event_types   = var.supported_event_types
      accept_drill_messages   = false
    }

    scaling = merge(var.scaling_levels, {
      cooldown_minutes      = var.cooldown_minutes
      scale_in_step         = var.scale_in_step
      absolute_max_capacity = var.absolute_max_ecs_tasks
    })

    safety = {
      require_manual_approval_for_level_3 = var.require_manual_approval_for_level_3
      hold_on_feed_error                  = true
      allow_automatic_scale_in            = var.allow_automatic_scale_in
      absolute_max_ecs_tasks              = var.absolute_max_ecs_tasks
      absolute_max_aurora_acu             = var.absolute_max_aurora_acu
      baseline_reserve_tasks              = var.baseline_reserve_tasks
      lock_ttl_seconds                    = 900
      health_check_timeout_seconds        = 600
    }

    aws_resources = {
      ecs_cluster              = var.ecs_cluster_name
      ecs_service              = var.ecs_service_name
      ecs_scalable_resource_id = local.ecs_resource_id
      aurora_cluster_id        = var.aurora_cluster_identifier
      state_table              = aws_dynamodb_table.state.name
      notification_topic_arn   = aws_sns_topic.ops.arn
      approval_topic_arn       = aws_sns_topic.approval.arn
      state_machine_arn        = aws_sfn_state_machine.pre_scale.arn
      alb_target_group_arn     = var.alb_target_group_arn
    }
  }

  lambda_env = {
    CONFIG_JSON = jsonencode(local.app_config)
    LOG_LEVEL   = "INFO"
    DRY_RUN     = var.dry_run ? "true" : "false"
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "archive_file" "package" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/.build/jma_pre_scale.zip"
  excludes    = ["**/__pycache__/**", "**/*.pyc"]
}
