# Step Functions State Machine for the booking workflow
resource "aws_sfn_state_machine" "booking_workflow" {
  name     = "groupiq-booking-workflow-${var.environment}"
  role_arn = aws_iam_role.step_functions.arn

  definition = templatefile("${path.module}/../step_functions/workflow.asl.json", {
    intake_lambda_arn      = aws_lambda_function.intake.arn
    proposal_lambda_arn    = aws_lambda_function.proposal_generator.arn
    negotiation_lambda_arn = aws_lambda_function.negotiation_agent.arn
    notification_lambda_arn = aws_lambda_function.notification.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }
}

resource "aws_cloudwatch_log_group" "step_functions" {
  name              = "/aws/states/groupiq-booking-workflow-${var.environment}"
  retention_in_days = 30
}
