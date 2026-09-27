"""Phone-shaped, private WhatsApp simulation backed by the case outbox."""

from __future__ import annotations

from uuid import UUID

from claim_api.deposit_grants import DepositGrantError
from claim_api.insured_portal import InsuredPortalService
from claim_api.sms_recipient import validate_e164
from claim_api.sms_service import SmsError, _deposit_url


FAKE_PROVIDER = "claimroom.fake_whatsapp"


class FakeWhatsAppGateway:
    """Sending means storing the manager message in the fake phone inbox."""

    provider = FAKE_PROVIDER
    simulated = True

    def send(self, *, message_id: str, recipient: str, body: str, deposit_url: str) -> str:
        validate_e164(recipient)
        if not body.strip() or deposit_url not in body:
            raise ValueError("Fake WhatsApp message needs its deposit link")
        return f"fake:{message_id}"


class FakeWhatsAppService(InsuredPortalService):
    @staticmethod
    def _conversation(cursor, session_token: str) -> dict:
        principal = InsuredPortalService._authorize_cursor(cursor, session_token, "read_summary")
        cursor.execute("""select m.id, m.case_id, m.recipient, m.body from public.case_messages m
                          join public.deposit_grants g on g.message_id = m.id
                          where g.id = %s and m.case_id = %s and m.channel = 'whatsapp'
                            and m.mode = 'mock' and m.provider = %s and m.status = 'delivered'""",
                       (principal.grant_id, principal.case_id, FAKE_PROVIDER))
        message = cursor.fetchone()
        if message is None:
            raise DepositGrantError("invalid_session")
        return {**message, "grant_id": principal.grant_id}

    @staticmethod
    def _messages(cursor, case_id: UUID, number: str) -> list[dict]:
        cursor.execute("""select id, body, created_at from public.case_messages
                          where case_id = %s and recipient = %s and channel = 'whatsapp'
                            and mode = 'mock' and provider = %s and status = 'delivered'
                          order by created_at, id""", (case_id, number, FAKE_PROVIDER))
        outbound = [{"id": str(row["id"]), "side": "agent", "text": row["body"],
                     "created_at": row["created_at"], "evidence_id": None,
                     "filename": None} for row in cursor.fetchall()]
        cursor.execute("""select i.id, i.body, i.received_at, i.evidence_id, e.original_filename
                          from public.case_whatsapp_inbound i
                          left join public.evidence e on e.id = i.evidence_id
                          where i.case_id = %s and i.sender = %s
                            and i.provider_message_id like 'fake:%%'
                          order by i.received_at, i.id""", (case_id, number))
        inbound = [{"id": str(row["id"]), "side": "you", "text": row["body"],
                    "created_at": row["received_at"], "evidence_id": str(row["evidence_id"]) if row["evidence_id"] else None,
                    "filename": row["original_filename"]} for row in cursor.fetchall()]
        return sorted(outbound + inbound, key=lambda row: (row["created_at"], row["id"]))

    def get(self, session_token: str) -> dict:
        with self._connect() as connection, connection.cursor() as cursor:
            message = self._conversation(cursor, session_token)
            entries = self._messages(cursor, message["case_id"], message["recipient"])
        return {"case_id": str(message["case_id"]), "number": message["recipient"],
                "deposit_url": _deposit_url(message["body"]),
                "messages": entries}

    def reply(self, session_token: str, *, client_message_id: UUID, body: str,
              evidence_id: UUID | None) -> dict:
        text = body.strip()
        if not text and evidence_id is None:
            raise SmsError("empty_fake_message", 422, "Write a message or attach a file")
        with self._connect() as connection, connection.cursor() as cursor:
            message = self._conversation(cursor, session_token)
            case_id, number = message["case_id"], message["recipient"]
            if evidence_id is not None:
                cursor.execute("""select 1 from public.evidence e
                                  join public.evidence_upload_intents i on i.finalized_evidence_id = e.id
                                  where e.id = %s and e.case_id = %s and e.source_kind = 'insured_upload'
                                    and i.deposit_grant_id = %s""", (evidence_id, case_id, message["grant_id"]))
                if cursor.fetchone() is None:
                    raise SmsError("evidence_not_found", 404, "Attachment not found on this case")
            provider_id = f"fake:{client_message_id}"
            cursor.execute("""select case_id, sender, body, evidence_id from public.case_whatsapp_inbound
                              where provider_message_id = %s""", (provider_id,))
            prior = cursor.fetchone()
            if prior is not None:
                if (prior["case_id"] != case_id or prior["sender"] != number or
                        prior["body"] != text or prior["evidence_id"] != evidence_id):
                    raise SmsError("idempotency_conflict", 409, "Message ID has another payload")
            else:
                cursor.execute("""insert into public.case_whatsapp_inbound
                                  (case_id, provider_message_id, sender, body, media_count, evidence_id)
                                  values (%s, %s, %s, %s, %s, %s)""",
                               (case_id, provider_id, number, text, 1 if evidence_id else 0, evidence_id))
        return self.get(session_token)
