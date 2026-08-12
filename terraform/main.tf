resource "google_firestore_database" "fs_database" {
  project     = var.project_id
  name        = var.firestore_database_name
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
}

resource "google_project_service" "kms" {
  project            = var.project_id
  service            = "cloudkms.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secret_manager" {
  project            = var.project_id
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_kms_key_ring" "signing" {
  project  = var.project_id
  name     = "veriproof-signing"
  location = var.region

  depends_on = [google_project_service.kms]
}

resource "google_kms_crypto_key" "platform_signing" {
  name     = "veriproof-platform-signing"
  key_ring = google_kms_key_ring.signing.id
  purpose  = "ASYMMETRIC_SIGN"

  version_template {
    algorithm        = "EC_SIGN_ED25519"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key" "buyer_signing" {
  name     = "veriproof-buyer-signing"
  key_ring = google_kms_key_ring.signing.id
  purpose  = "ASYMMETRIC_SIGN"

  version_template {
    algorithm        = "EC_SIGN_ED25519"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "platform_signer" {
  crypto_key_id = google_kms_crypto_key.platform_signing.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${var.cloud_run_service_account_email}"
}

resource "google_kms_crypto_key_iam_member" "buyer_signer" {
  crypto_key_id = google_kms_crypto_key.buyer_signing.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${var.cloud_run_service_account_email}"
}

resource "google_kms_crypto_key_iam_member" "platform_key_metadata" {
  crypto_key_id = google_kms_crypto_key.platform_signing.id
  role          = "roles/cloudkms.viewer"
  member        = "serviceAccount:${var.cloud_run_service_account_email}"
}

resource "google_kms_crypto_key_iam_member" "buyer_key_metadata" {
  crypto_key_id = google_kms_crypto_key.buyer_signing.id
  role          = "roles/cloudkms.viewer"
  member        = "serviceAccount:${var.cloud_run_service_account_email}"
}

locals {
  runtime_secret_ids = toset([
    "veriproof-django-secret-key",
    "veriproof-wallet-encryption-key",
    "veriproof-postgres-password",
    "veriproof-paysh-webhook-secret",
    "veriproof-buyer-wallet-secret-key",
  ])
}

resource "google_secret_manager_secret" "runtime" {
  for_each  = local.runtime_secret_ids
  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.secret_manager]
}

resource "google_secret_manager_secret_iam_member" "runtime_accessor" {
  for_each  = google_secret_manager_secret.runtime
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.cloud_run_service_account_email}"
}
