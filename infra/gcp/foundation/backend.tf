terraform {
  backend "gcs" {
    bucket = "simple-unmark-prod-tfstate"
    prefix = "confidential/foundation"
  }
}
