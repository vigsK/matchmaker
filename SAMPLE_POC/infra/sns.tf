resource "aws_sns_topic" "results" {
  name = "${local.name}-results"
  tags = { Name = "${local.name}-results" }
}

# Optional email subscription (requires confirming the email after apply).
resource "aws_sns_topic_subscription" "email" {
  count     = var.notification_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.results.arn
  protocol  = "email"
  endpoint  = var.notification_email
}
