resource "google_firestore_database" "fs_database" {
  project     = "paulahn"
  name        = "paulahn-fs"
  location_id = "asia-northeast3"
  type        = "FIRESTORE_NATIVE"
}