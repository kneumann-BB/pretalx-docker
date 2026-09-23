terraform {
  # Values are supplied via backend.hcl (see backend.hcl.example) so the
  # bucket/table are not hardcoded per environment.
  backend "s3" {}
}
