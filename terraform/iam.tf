# ---------------------------------------------------------------------------
# 最小権限 IAM。
# 既存アプリケーションのコード・データには一切触れない。
# 操作対象は「指定した1つの ECS サービス」と「指定した1つの Aurora クラスタ」のみ。
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  # 状態・重複排除・ロック・監査
  statement {
    sid    = "StateTable"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.state.arn]
  }

  # ECS: 対象サービスの読み取りと希望数変更のみ
  statement {
    sid       = "EcsDescribe"
    effect    = "Allow"
    actions   = ["ecs:DescribeServices"]
    resources = ["*"]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = ["arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:cluster/${var.ecs_cluster_name}"]
    }
  }

  statement {
    sid       = "EcsUpdateService"
    effect    = "Allow"
    actions   = ["ecs:UpdateService"]
    resources = ["arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:service/${var.ecs_cluster_name}/${var.ecs_service_name}"]
  }

  # Application Auto Scaling: MinCapacity の引き上げ
  statement {
    sid    = "ApplicationAutoScaling"
    effect = "Allow"
    actions = [
      "application-autoscaling:DescribeScalableTargets",
      "application-autoscaling:RegisterScalableTarget",
    ]
    resources = ["*"]
  }

  # Application Auto Scaling が ECS を操作するためのサービスリンクロール作成
  statement {
    sid       = "ServiceLinkedRole"
    effect    = "Allow"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-service-role/ecs.application-autoscaling.amazonaws.com/*"]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["ecs.application-autoscaling.amazonaws.com"]
    }
  }

  # Aurora Serverless v2: 最小/最大 ACU の変更のみ。スナップショット削除等は不許可。
  dynamic "statement" {
    for_each = var.aurora_cluster_identifier == "" ? [] : [1]

    content {
      sid       = "AuroraDescribe"
      effect    = "Allow"
      actions   = ["rds:DescribeDBClusters"]
      resources = ["arn:aws:rds:${var.region}:${data.aws_caller_identity.current.account_id}:cluster:${var.aurora_cluster_identifier}"]
    }
  }

  dynamic "statement" {
    for_each = var.aurora_cluster_identifier == "" ? [] : [1]

    content {
      sid       = "AuroraModify"
      effect    = "Allow"
      actions   = ["rds:ModifyDBCluster"]
      resources = ["arn:aws:rds:${var.region}:${data.aws_caller_identity.current.account_id}:cluster:${var.aurora_cluster_identifier}"]
    }
  }

  # ALB ヘルスチェック
  dynamic "statement" {
    for_each = var.alb_target_group_arn == "" ? [] : [1]

    content {
      sid       = "AlbTargetHealth"
      effect    = "Allow"
      actions   = ["elasticloadbalancing:DescribeTargetHealth"]
      resources = ["*"]
    }
  }

  statement {
    sid       = "Notify"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops.arn, aws_sns_topic.approval.arn]
  }

  statement {
    sid       = "StartStateMachine"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pre_scale.arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.name}-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

# ------------------------------------------------------------- Step Functions

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.name}-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn" {
  statement {
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.planner.arn,
      aws_lambda_function.scaler.arn,
      aws_lambda_function.healthcheck.arn,
      aws_lambda_function.notify.arn,
      "${aws_lambda_function.planner.arn}:*",
      "${aws_lambda_function.scaler.arn}:*",
      "${aws_lambda_function.healthcheck.arn}:*",
      "${aws_lambda_function.notify.arn}:*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.approval.arn, aws_sns_topic.ops.arn]
  }

  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${local.name}-sfn"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

# --------------------------------------------------------- EventBridge Scheduler

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${local.name}-scheduler"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.poller.arn, "${aws_lambda_function.poller.arn}:*"]
      }
    ]
  })
}
