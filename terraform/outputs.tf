output "poller_function_name" {
  description = "監視Lambda関数名"
  value       = aws_lambda_function.poller.function_name
}

output "override_function_name" {
  description = "手動オーバーライドLambda関数名。緊急時にこれを叩く"
  value       = aws_lambda_function.override.function_name
}

output "state_machine_arn" {
  description = "事前スケール ステートマシンARN"
  value       = aws_sfn_state_machine.pre_scale.arn
}

output "state_table_name" {
  description = "状態・重複排除・監査テーブル"
  value       = aws_dynamodb_table.state.name
}

output "ops_topic_arn" {
  description = "運用通知トピック"
  value       = aws_sns_topic.ops.arn
}

output "approval_topic_arn" {
  description = "LEVEL_3 承認要求トピック"
  value       = aws_sns_topic.approval.arn
}

output "dashboard_url" {
  description = "CloudWatch ダッシュボード"
  value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "dry_run_enabled" {
  description = "true の間、実APIは一切呼ばれない"
  value       = var.dry_run
}

output "emergency_commands" {
  description = "緊急時の手動操作コマンド"
  value = {
    force_level_3      = "aws lambda invoke --function-name ${aws_lambda_function.override.function_name} --cli-binary-format raw-in-base64-out --payload '{\"op\":\"force_level\",\"level\":3,\"operator\":\"YOUR_NAME\"}' /dev/stdout"
    clear_force        = "aws lambda invoke --function-name ${aws_lambda_function.override.function_name} --cli-binary-format raw-in-base64-out --payload '{\"op\":\"clear_force\",\"operator\":\"YOUR_NAME\"}' /dev/stdout"
    disable_automation = "aws lambda invoke --function-name ${aws_lambda_function.override.function_name} --cli-binary-format raw-in-base64-out --payload '{\"op\":\"disable_automation\",\"operator\":\"YOUR_NAME\"}' /dev/stdout"
    status             = "aws lambda invoke --function-name ${aws_lambda_function.override.function_name} --cli-binary-format raw-in-base64-out --payload '{\"op\":\"status\"}' /dev/stdout"
  }
}
