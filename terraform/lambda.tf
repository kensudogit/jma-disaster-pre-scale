locals {
  lambda_functions = {
    poller = {
      handler     = "jma_pre_scale.handlers.poller.lambda_handler"
      timeout     = 60
      memory      = 512
      description = "気象庁Atomフィードを監視し、判定して Step Functions を起動する"
    }
    planner = {
      handler     = "jma_pre_scale.handlers.planner.lambda_handler"
      timeout     = 30
      memory      = 256
      description = "現況取得と適用計画(Dry Run出力)の作成"
    }
    scaler = {
      handler     = "jma_pre_scale.handlers.scaler.lambda_handler"
      timeout     = 120
      memory      = 256
      description = "ECS Fargate / Aurora Serverless v2 の事前拡張を適用する"
    }
    healthcheck = {
      handler     = "jma_pre_scale.handlers.healthcheck.lambda_handler"
      timeout     = 60
      memory      = 256
      description = "拡張後のタスク起動とターゲット健全性を確認する"
    }
    notify = {
      handler     = "jma_pre_scale.handlers.notify.lambda_handler"
      timeout     = 30
      memory      = 256
      description = "成功・失敗を SNS へ通知する"
    }
    override = {
      handler     = "jma_pre_scale.handlers.override.lambda_handler"
      timeout     = 30
      memory      = 256
      description = "手動オーバーライド(強制レベル / 自動制御停止)"
    }
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.lambda_functions

  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "poller" {
  function_name    = "${local.name}-poller"
  role             = aws_iam_role.lambda.arn
  handler          = local.lambda_functions.poller.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  timeout          = local.lambda_functions.poller.timeout
  memory_size      = local.lambda_functions.poller.memory
  description      = local.lambda_functions.poller.description

  # 多重起動を避ける。ロックもあるが二重の防御。
  reserved_concurrent_executions = 1

  environment {
    variables = local.lambda_env
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_function" "planner" {
  function_name    = "${local.name}-planner"
  role             = aws_iam_role.lambda.arn
  handler          = local.lambda_functions.planner.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  timeout          = local.lambda_functions.planner.timeout
  memory_size      = local.lambda_functions.planner.memory
  description      = local.lambda_functions.planner.description

  environment {
    variables = local.lambda_env
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_function" "scaler" {
  function_name    = "${local.name}-scaler"
  role             = aws_iam_role.lambda.arn
  handler          = local.lambda_functions.scaler.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  timeout          = local.lambda_functions.scaler.timeout
  memory_size      = local.lambda_functions.scaler.memory
  description      = local.lambda_functions.scaler.description

  reserved_concurrent_executions = 1

  environment {
    variables = local.lambda_env
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_function" "healthcheck" {
  function_name    = "${local.name}-healthcheck"
  role             = aws_iam_role.lambda.arn
  handler          = local.lambda_functions.healthcheck.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  timeout          = local.lambda_functions.healthcheck.timeout
  memory_size      = local.lambda_functions.healthcheck.memory
  description      = local.lambda_functions.healthcheck.description

  environment {
    variables = local.lambda_env
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_function" "notify" {
  function_name    = "${local.name}-notify"
  role             = aws_iam_role.lambda.arn
  handler          = local.lambda_functions.notify.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  timeout          = local.lambda_functions.notify.timeout
  memory_size      = local.lambda_functions.notify.memory
  description      = local.lambda_functions.notify.description

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_function" "override" {
  function_name    = "${local.name}-override"
  role             = aws_iam_role.lambda.arn
  handler          = local.lambda_functions.override.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  timeout          = local.lambda_functions.override.timeout
  memory_size      = local.lambda_functions.override.memory
  description      = local.lambda_functions.override.description

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}
