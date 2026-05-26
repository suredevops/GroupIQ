variable "aws_region" {
  description = "AWS region for deployment. us-east-2 recommended for lower attack surface and full Bedrock support."
  type        = string
  default     = "us-east-2"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "bedrock_model_id" {
  description = "Bedrock foundation model ID for AI negotiation"
  type        = string
  default     = "anthropic.claude-3-sonnet-20240229-v1:0"
}

variable "max_discount_percent" {
  description = "Maximum discount percentage the AI can offer without escalation"
  type        = number
  default     = 15
}

variable "escalation_email" {
  description = "Sales manager email for escalation notifications"
  type        = string
}

variable "ses_sender_email" {
  description = "Verified SES sender email for outbound proposals"
  type        = string
}

variable "proposal_expiry_days" {
  description = "Number of days before a proposal expires"
  type        = number
  default     = 7
}

variable "bedrock_guardrail_id" {
  description = "AWS Bedrock Guardrail ID for content safety filtering. Leave empty to disable."
  type        = string
  default     = ""
}

variable "bedrock_guardrail_version" {
  description = "Bedrock Guardrail version number or DRAFT"
  type        = string
  default     = "DRAFT"
}
