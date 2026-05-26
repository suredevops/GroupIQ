# Lambda Layer for shared dependencies
resource "aws_lambda_layer_version" "common" {
  filename            = "../lambdas/common/layer.zip"
  layer_name          = "groupiq-common-${var.environment}"
  compatible_runtimes = ["python3.12"]
  description         = "Shared utilities for GroupIQ lambdas"
}

# Intake Lambda — validates and enriches group booking inquiry
resource "aws_lambda_function" "intake" {
  filename         = "../lambdas/intake/package.zip"
  function_name    = "groupiq-intake-${var.environment}"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  source_code_hash = filebase64sha256("../lambdas/intake/package.zip")

  layers = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      BOOKINGS_TABLE    = aws_dynamodb_table.bookings.name
      PRICING_TABLE     = aws_dynamodb_table.pricing_rules.name
      ENVIRONMENT       = var.environment
    }
  }

  tracing_config {
    mode = "Active"
  }
}

# Proposal Generator Lambda — uses Bedrock to create customized proposals
resource "aws_lambda_function" "proposal_generator" {
  filename         = "../lambdas/proposal_generator/package.zip"
  function_name    = "groupiq-proposal-generator-${var.environment}"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512
  source_code_hash = filebase64sha256("../lambdas/proposal_generator/package.zip")

  layers = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      BOOKINGS_TABLE            = aws_dynamodb_table.bookings.name
      PRICING_TABLE             = aws_dynamodb_table.pricing_rules.name
      PROPOSALS_BUCKET          = aws_s3_bucket.proposals.id
      BEDROCK_MODEL_ID          = var.bedrock_model_id
      BEDROCK_GUARDRAIL_ID      = var.bedrock_guardrail_id
      BEDROCK_GUARDRAIL_VERSION = var.bedrock_guardrail_version
      ENVIRONMENT               = var.environment
    }
  }

  tracing_config {
    mode = "Active"
  }
}

# Negotiation Agent Lambda — handles counter-offers within pre-set bounds
resource "aws_lambda_function" "negotiation_agent" {
  filename         = "../lambdas/negotiation_agent/package.zip"
  function_name    = "groupiq-negotiation-agent-${var.environment}"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512
  source_code_hash = filebase64sha256("../lambdas/negotiation_agent/package.zip")

  layers = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      BOOKINGS_TABLE            = aws_dynamodb_table.bookings.name
      NEGOTIATIONS_TABLE        = aws_dynamodb_table.negotiation_history.name
      PRICING_TABLE             = aws_dynamodb_table.pricing_rules.name
      BEDROCK_MODEL_ID          = var.bedrock_model_id
      BEDROCK_GUARDRAIL_ID      = var.bedrock_guardrail_id
      BEDROCK_GUARDRAIL_VERSION = var.bedrock_guardrail_version
      MAX_DISCOUNT_PERCENT      = tostring(var.max_discount_percent)
      ESCALATION_TOPIC_ARN      = aws_sns_topic.escalation.arn
      ENVIRONMENT               = var.environment
    }
  }

  tracing_config {
    mode = "Active"
  }
}

# Notification Lambda — sends proposals via SES, escalates via SNS
resource "aws_lambda_function" "notification" {
  filename         = "../lambdas/notification/package.zip"
  function_name    = "groupiq-notification-${var.environment}"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  source_code_hash = filebase64sha256("../lambdas/notification/package.zip")

  layers = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      PROPOSALS_BUCKET     = aws_s3_bucket.proposals.id
      SES_SENDER_EMAIL     = var.ses_sender_email
      ESCALATION_TOPIC_ARN = aws_sns_topic.escalation.arn
      ENVIRONMENT          = var.environment
    }
  }

  tracing_config {
    mode = "Active"
  }
}

# Reminder Lambda — sends pre-event alerts 2 days before checkout
resource "aws_lambda_function" "reminder" {
  filename         = "../lambdas/reminder/package.zip"
  function_name    = "groupiq-reminder-${var.environment}"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  source_code_hash = filebase64sha256("../lambdas/reminder/package.zip")

  layers = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      BOOKINGS_TABLE       = aws_dynamodb_table.bookings.name
      SES_SENDER_EMAIL     = var.ses_sender_email
      ESCALATION_TOPIC_ARN = aws_sns_topic.escalation.arn
      REMINDER_DAYS_BEFORE = "2"
      ENVIRONMENT          = var.environment
    }
  }

  tracing_config {
    mode = "Active"
  }
}

# EventBridge rule to trigger reminder Lambda daily at 9 AM UTC
resource "aws_cloudwatch_event_rule" "daily_reminder" {
  name                = "groupiq-daily-reminder-${var.environment}"
  description         = "Triggers reminder check every day at 9 AM UTC"
  schedule_expression = "cron(0 9 * * ? *)"
}

resource "aws_cloudwatch_event_target" "reminder_target" {
  rule      = aws_cloudwatch_event_rule.daily_reminder.name
  target_id = "groupiq-reminder"
  arn       = aws_lambda_function.reminder.arn
}

resource "aws_lambda_permission" "allow_eventbridge_reminder" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reminder.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_reminder.arn
}

# SNS Topic for sales team escalation
resource "aws_sns_topic" "escalation" {
  name = "groupiq-escalation-${var.environment}"
}

resource "aws_sns_topic_subscription" "escalation_email" {
  topic_arn = aws_sns_topic.escalation.arn
  protocol  = "email"
  endpoint  = var.escalation_email
}
