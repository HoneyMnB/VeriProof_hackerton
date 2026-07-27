-- Delete all VeriProof IP-domain data.
--
-- Scope:
-- - IP assets, creators, drafts, assistant records, subscription records/plans.
-- - Settlement and negotiation rows that reference IP assets.
-- - Common AgentEvent rows tied to IP assets or IP negotiation sessions.
--
-- This script intentionally does not delete Django auth/account rows.
-- Run only against a disposable/local database or after taking a backup.

BEGIN;

-- Events point at assets and negotiation sessions. Remove them before deleting
-- the referenced IP/negotiation rows.
DELETE FROM common_agentevent
WHERE asset_id IS NOT NULL
   OR session_id IN (SELECT id FROM negotiation_negotiationsession);

-- Settlement references.
DELETE FROM settlement_royaltydistribution
WHERE license_id IN (SELECT id FROM settlement_license);

DELETE FROM settlement_batchitem
WHERE asset_id IN (SELECT id FROM ip_ipasset)
   OR license_id IN (SELECT id FROM settlement_license);

DELETE FROM settlement_license
WHERE asset_id IN (SELECT id FROM ip_ipasset);

DELETE FROM settlement_batchorder;

-- Negotiation sessions reference assets and are also referenced by some
-- licenses/events in normal runtime paths. Those references are cleared above.
DELETE FROM negotiation_negotiationsession
WHERE asset_id IN (SELECT id FROM ip_ipasset);

-- IP asset child/protected references.
DELETE FROM ip_registrationcharge
WHERE asset_id IN (SELECT id FROM ip_ipasset)
   OR subscription_id IN (
       SELECT id FROM ip_creatorsubscription
       WHERE creator_id IN (SELECT id FROM ip_creator)
   );

DELETE FROM ip_registrationdraft
WHERE creator_id IN (SELECT id FROM ip_creator)
   OR executed_asset_id IN (SELECT id FROM ip_ipasset);

DELETE FROM ip_assetcomponent
WHERE asset_id IN (SELECT id FROM ip_ipasset);

DELETE FROM ip_assetimage
WHERE asset_id IN (SELECT id FROM ip_ipasset);

-- Creator assistant and operating records.
DELETE FROM ip_conversationattachment
WHERE creator_id IN (SELECT id FROM ip_creator)
   OR source_message_id IN (
       SELECT id FROM ip_assistantmessage
       WHERE creator_id IN (SELECT id FROM ip_creator)
   );

DELETE FROM ip_assistantaction
WHERE creator_id IN (SELECT id FROM ip_creator)
   OR source_message_id IN (
       SELECT id FROM ip_assistantmessage
       WHERE creator_id IN (SELECT id FROM ip_creator)
   );

DELETE FROM ip_assistantmessage
WHERE creator_id IN (SELECT id FROM ip_creator);

DELETE FROM ip_agentdirective
WHERE creator_id IN (SELECT id FROM ip_creator);

DELETE FROM ip_creatorexpense
WHERE creator_id IN (SELECT id FROM ip_creator);

DELETE FROM ip_creatorsubscription
WHERE creator_id IN (SELECT id FROM ip_creator);

-- Break self-references defensively, then delete assets.
UPDATE ip_ipasset
SET parent_asset_id = NULL,
    royalty_share_bps = NULL
WHERE parent_asset_id IS NOT NULL;

DELETE FROM ip_ipasset;

DELETE FROM ip_creator;

DELETE FROM ip_subscriptionplan;

COMMIT;
