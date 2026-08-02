# ---------------------------------------------------------------------------
# 監視。「動いていないこと」を検知できるようにする。
# 災害時に監視Lambdaが止まっていても誰も気づかない、が最悪のシナリオ。
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "poller_errors" {
  alarm_name          = "${local.name}-poller-errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  threshold           = 1
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  treat_missing_data  = "notBreaching"
  alarm_description   = "監視Lambdaがエラーを返しています。事前スケールが機能しない恐れがあります。"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]

  dimensions = {
    FunctionName = aws_lambda_function.poller.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "poller_not_invoked" {
  alarm_name          = "${local.name}-poller-not-invoked"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  threshold           = 1
  period              = 900
  statistic           = "Sum"
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  treat_missing_data  = "breaching"
  alarm_description   = "15分間、監視Lambdaが1度も起動していません。スケジューラ停止を疑ってください。"
  alarm_actions       = [aws_sns_topic.ops.arn]

  dimensions = {
    FunctionName = aws_lambda_function.poller.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "sfn_failed" {
  alarm_name          = "${local.name}-statemachine-failed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  treat_missing_data  = "notBreaching"
  alarm_description   = "事前スケールのステートマシンが失敗しました。容量は維持されていますが確認が必要です。"
  alarm_actions       = [aws_sns_topic.ops.arn]

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pre_scale.arn
  }
}

resource "aws_cloudwatch_metric_alarm" "scaler_errors" {
  alarm_name          = "${local.name}-scaler-errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  treat_missing_data  = "notBreaching"
  alarm_description   = "スケール適用Lambdaが失敗しました。手動オーバーライドを検討してください。"
  alarm_actions       = [aws_sns_topic.ops.arn]

  dimensions = {
    FunctionName = aws_lambda_function.scaler.function_name
  }
}

# 監査ログ検索用のメトリクスフィルタ
resource "aws_cloudwatch_log_metric_filter" "scale_applied" {
  name           = "${local.name}-scale-applied"
  log_group_name = aws_cloudwatch_log_group.lambda["scaler"].name
  pattern        = "{ $.log_type = \"jma_pre_scale_audit\" && $.phase = \"apply\" }"

  metric_transformation {
    name          = "PreScaleApplied"
    namespace     = "JmaPreScale"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "feed_hold" {
  name           = "${local.name}-feed-hold"
  log_group_name = aws_cloudwatch_log_group.lambda["poller"].name
  pattern        = "{ $.log_type = \"jma_pre_scale_audit\" && $.action = \"HOLD\" }"

  metric_transformation {
    name          = "PreScaleHold"
    namespace     = "JmaPreScale"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "監視Lambda 起動/エラー"
          region = var.region
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.poller.function_name],
            [".", "Errors", ".", "."],
          ]
          period = 300
          stat   = "Sum"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "事前スケール適用 / HOLD"
          region = var.region
          metrics = [
            ["JmaPreScale", "PreScaleApplied"],
            [".", "PreScaleHold"],
          ]
          period = 300
          stat   = "Sum"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "ECS 稼働タスク数"
          region = var.region
          metrics = [
            ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", var.ecs_cluster_name, "ServiceName", var.ecs_service_name],
            [".", "DesiredTaskCount", ".", ".", ".", "."],
          ]
          period = 60
          stat   = "Average"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Aurora ServerlessV2 ACU"
          region = var.region
          metrics = [
            ["AWS/RDS", "ServerlessDatabaseCapacity", "DBClusterIdentifier", var.aurora_cluster_identifier],
            [".", "DatabaseConnections", ".", "."],
          ]
          period = 60
          stat   = "Average"
        }
      },
    ]
  })
}
