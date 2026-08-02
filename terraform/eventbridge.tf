resource "aws_scheduler_schedule" "poller" {
  name                         = "${local.name}-poller"
  description                  = "気象庁防災情報XMLの定期監視"
  schedule_expression          = "rate(${var.poll_interval_minutes} minute${var.poll_interval_minutes == 1 ? "" : "s"})"
  schedule_expression_timezone = "Asia/Tokyo"
  state                        = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.poller.arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts       = 1
      maximum_event_age_in_seconds = 120
    }
  }
}
