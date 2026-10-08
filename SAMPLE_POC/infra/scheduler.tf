# --------------------------------------------------------------------------- #
# EventBridge Scheduler: kicks off the consistency workflow once per day.        #
# Serverless, on-demand - no always-on infrastructure.                          #
# --------------------------------------------------------------------------- #
resource "aws_scheduler_schedule" "daily" {
  name       = "${local.name}-daily"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "UTC"
  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"

  target {
    arn      = aws_sfn_state_machine.consistency.arn
    role_arn = aws_iam_role.scheduler.arn

    # Empty input -> the state machine derives run_id from the execution name.
    input = jsonencode({})

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
