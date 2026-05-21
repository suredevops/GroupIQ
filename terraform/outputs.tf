output "api_endpoint" {
  description = "GroupIQ API Gateway endpoint URL"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "bookings_table_name" {
  description = "DynamoDB bookings table name"
  value       = aws_dynamodb_table.bookings.name
}

output "proposals_bucket" {
  description = "S3 bucket for generated proposals"
  value       = aws_s3_bucket.proposals.id
}

output "state_machine_arn" {
  description = "Step Functions state machine ARN"
  value       = aws_sfn_state_machine.booking_workflow.arn
}

output "escalation_topic_arn" {
  description = "SNS topic ARN for sales escalations"
  value       = aws_sns_topic.escalation.arn
}
