resource "aws_dynamodb_table" "bookings" {
  name         = "groupiq-bookings-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "booking_id"
  range_key    = "version"

  attribute {
    name = "booking_id"
    type = "S"
  }

  attribute {
    name = "version"
    type = "N"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "event_date"
    type = "S"
  }

  global_secondary_index {
    name            = "status-index"
    hash_key        = "status"
    range_key       = "event_date"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  ttl {
    attribute_name = "ttl_expiry"
    enabled        = true
  }

  tags = {
    Name = "GroupIQ Bookings Table"
  }
}

resource "aws_dynamodb_table" "negotiation_history" {
  name         = "groupiq-negotiations-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "booking_id"
  range_key    = "turn_number"

  attribute {
    name = "booking_id"
    type = "S"
  }

  attribute {
    name = "turn_number"
    type = "N"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name = "GroupIQ Negotiation History"
  }
}

resource "aws_dynamodb_table" "pricing_rules" {
  name         = "groupiq-pricing-rules-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "property_id"
  range_key    = "rule_type"

  attribute {
    name = "property_id"
    type = "S"
  }

  attribute {
    name = "rule_type"
    type = "S"
  }

  tags = {
    Name = "GroupIQ Pricing Rules"
  }
}
