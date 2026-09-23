output "state_bucket_name" {
  description = "S3 bucket name to use as the 'bucket' backend value."
  value       = aws_s3_bucket.state.bucket
}

