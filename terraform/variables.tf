variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-northeast3"
}

variable "cloud_run_service_account_email" {
  description = "Service account used by the VeriProof web and Buyer Agent services."
  type        = string
}

variable "firestore_database_name" {
  type    = string
  default = "paulahn-fs"
}
