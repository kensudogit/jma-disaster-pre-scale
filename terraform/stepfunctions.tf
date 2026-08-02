resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "pre_scale" {
  name     = "${local.name}-pre-scale"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/../statemachine/pre_scale.asl.json", {
    PlannerFunctionArn     = aws_lambda_function.planner.arn
    ScalerFunctionArn      = aws_lambda_function.scaler.arn
    HealthCheckFunctionArn = aws_lambda_function.healthcheck.arn
    NotifyFunctionArn      = aws_lambda_function.notify.arn
    ApprovalTopicArn       = aws_sns_topic.approval.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }
}
