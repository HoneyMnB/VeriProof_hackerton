output "fs-id" {
  value = google_firestore_database.fs_database.id
}

output "platform_kms_key_name" {
  value = google_kms_crypto_key.platform_signing.id
}

output "buyer_kms_key_name" {
  value = google_kms_crypto_key.buyer_signing.id
}

output "runtime_secret_ids" {
  value = { for key, secret in google_secret_manager_secret.runtime : key => secret.secret_id }
}
