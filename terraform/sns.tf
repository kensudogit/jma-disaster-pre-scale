resource "aws_sns_topic" "ops" {
  name              = "${local.name}-ops"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic" "approval" {
  name              = "${local.name}-approval"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "ops_email" {
  for_each = toset(var.notification_emails)

  topic_arn = aws_sns_topic.ops.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_sns_topic_subscription" "approval_email" {
  for_each = toset(var.notification_emails)

  topic_arn = aws_sns_topic.approval.arn
  protocol  = "email"
  endpoint  = each.value
}
