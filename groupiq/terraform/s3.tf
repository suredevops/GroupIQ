resource "aws_s3_bucket" "proposals" {
  bucket = "groupiq-proposals-${var.environment}-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "GroupIQ Proposal Documents"
  }
}

resource "aws_s3_bucket_versioning" "proposals" {
  bucket = aws_s3_bucket.proposals.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "proposals" {
  bucket = aws_s3_bucket.proposals.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "proposals" {
  bucket = aws_s3_bucket.proposals.id

  rule {
    id     = "archive-old-proposals"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 365
      storage_class = "GLACIER"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "proposals" {
  bucket = aws_s3_bucket.proposals.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_caller_identity" "current" {}
