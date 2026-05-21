# API Gateway REST API
resource "aws_apigatewayv2_api" "groupiq" {
  name          = "groupiq-api-${var.environment}"
  protocol_type = "HTTP"
  description   = "GroupIQ AI Group Booking Negotiation API"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "GET", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization", "X-Api-Key"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.groupiq.id
  name        = var.environment
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
    })
  }
}

# POST /inquiries — new group booking inquiry
resource "aws_apigatewayv2_integration" "intake" {
  api_id                 = aws_apigatewayv2_api.groupiq.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.intake.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "post_inquiry" {
  api_id    = aws_apigatewayv2_api.groupiq.id
  route_key = "POST /inquiries"
  target    = "integrations/${aws_apigatewayv2_integration.intake.id}"
}

# POST /inquiries/{bookingId}/negotiate — counter-offer submission
resource "aws_apigatewayv2_integration" "negotiate" {
  api_id                 = aws_apigatewayv2_api.groupiq.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.negotiation_agent.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "post_negotiate" {
  api_id    = aws_apigatewayv2_api.groupiq.id
  route_key = "POST /inquiries/{bookingId}/negotiate"
  target    = "integrations/${aws_apigatewayv2_integration.negotiate.id}"
}

# GET /inquiries/{bookingId} — get booking status and proposal
resource "aws_apigatewayv2_integration" "get_booking" {
  api_id                 = aws_apigatewayv2_api.groupiq.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.intake.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_booking" {
  api_id    = aws_apigatewayv2_api.groupiq.id
  route_key = "GET /inquiries/{bookingId}"
  target    = "integrations/${aws_apigatewayv2_integration.get_booking.id}"
}

# Lambda permissions for API Gateway invocation
resource "aws_lambda_permission" "api_intake" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.intake.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.groupiq.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_negotiate" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.negotiation_agent.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.groupiq.execution_arn}/*/*"
}

# CloudWatch Log Group for API Gateway
resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/groupiq-${var.environment}"
  retention_in_days = 30
}
