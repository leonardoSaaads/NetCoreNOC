-- ADR #500: a case's email, written and answered from its feed.
--
-- A customer's Email tab keeps the server side — which SMTP and IMAP credentials, the sender —
-- and each message is now written on the case it is about: its own To, Cc and Bcc, a subject that
-- keeps the case's tag, a body written in the feed's editor. A reply comes back onto the same
-- feed, and Reply or Reply all answers it without retyping who it goes to.
--
-- 1. `noc_case_post.kind` gains `email`: the post that shows a message on the feed. The post stays
--    append-only (it is evidence of what someone was told); the message's row points at it.
-- 2. `noc_case_mail` keeps the whole message: who it went to and was copied to, the blind copies
--    (shown to the NOC only), the message it answers and the chain it belongs to (for threading
--    and for a reply's recipients), its text, and — for what the NOC wrote — the editor's document
--    the HTML was made from. `recipients` is now the To line alone.

ALTER TABLE noc_case_post DROP CONSTRAINT noc_case_post_kind_check;
ALTER TABLE noc_case_post ADD CONSTRAINT noc_case_post_kind_check
    CHECK (kind IN ('post', 'event', 'email'));

ALTER TABLE noc_case_mail ADD COLUMN sender_name text COLLATE "C" NOT NULL DEFAULT ''
    CHECK (length(sender_name) <= 200);
ALTER TABLE noc_case_mail ADD COLUMN cc text COLLATE "C" NOT NULL DEFAULT ''
    CHECK (length(cc) <= 4000);
ALTER TABLE noc_case_mail ADD COLUMN bcc text COLLATE "C" NOT NULL DEFAULT ''
    CHECK (length(bcc) <= 4000);
ALTER TABLE noc_case_mail ADD COLUMN in_reply_to text COLLATE "C" NOT NULL DEFAULT ''
    CHECK (length(in_reply_to) <= 998);
ALTER TABLE noc_case_mail ADD COLUMN refs text COLLATE "C" NOT NULL DEFAULT ''
    CHECK (length(refs) <= 8000);
ALTER TABLE noc_case_mail ADD COLUMN body_text text COLLATE "C" NOT NULL DEFAULT ''
    CHECK (length(body_text) <= 20000);
ALTER TABLE noc_case_mail ADD COLUMN body_doc text COLLATE "C" NOT NULL DEFAULT ''
    CHECK (length(body_doc) <= 100000);
ALTER TABLE noc_case_mail ADD COLUMN sent_by text COLLATE "C" NOT NULL DEFAULT ''
    CHECK (length(sent_by) <= 200);

CREATE INDEX idx_noc_case_mail_post ON noc_case_mail (post_id) WHERE post_id IS NOT NULL;
