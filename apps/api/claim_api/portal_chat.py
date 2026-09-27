"""Case-scoped text conversation behind the insured's private deposit session."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from claim_api.deposit_grants import DepositGrantError
from claim_api.insured_portal import InsuredPortalService
from claim_api.sms_link import PROVIDER
from claim_api.sms_service import SmsError


class PortalChatModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PortalChatMessage(PortalChatModel):
    id: UUID
    sender: str
    body: str
    created_at: datetime


class PortalChatSend(PortalChatModel):
    client_message_id: UUID
    body: str = Field(min_length=1, max_length=1600)


class PortalChatService(InsuredPortalService):
    @staticmethod
    def _sms_grant(cursor, grant_id: UUID, case_id: UUID, *, active: bool) -> None:
        cursor.execute("""select g.expires_at, g.revoked_at from public.deposit_grants g
            join public.case_messages m on m.id=g.message_id and m.case_id=g.case_id
            where g.id=%s and g.case_id=%s and m.channel='sms' and m.provider=%s""",
            (grant_id, case_id, PROVIDER))
        row = cursor.fetchone()
        if row is None:
            raise DepositGrantError("forbidden_capability")
        if active and (row["revoked_at"] is not None or row["expires_at"] <= datetime.now(timezone.utc)):
            raise DepositGrantError("expired_grant")

    @staticmethod
    def _messages(cursor, grant_id: UUID) -> list[PortalChatMessage]:
        cursor.execute("""select id, sender, body, created_at from public.case_portal_messages
            where grant_id=%s order by created_at, id""", (grant_id,))
        return [PortalChatMessage.model_validate(row) for row in cursor.fetchall()]

    @staticmethod
    def _insert(cursor, *, case_id: UUID, grant_id: UUID, sender: str,
                client_message_id: UUID, body: str) -> PortalChatMessage:
        text = body.strip()
        if not text or len(text) > 1600:
            raise SmsError("invalid_message", 422, "Write a message of at most 1600 characters")
        cursor.execute("""insert into public.case_portal_messages
            (case_id, grant_id, sender, client_message_id, body)
            values (%s,%s,%s,%s,%s)
            on conflict (grant_id, sender, client_message_id) do nothing
            returning id, sender, body, created_at""",
            (case_id, grant_id, sender, client_message_id, text))
        row = cursor.fetchone()
        if row is None:
            cursor.execute("""select id, sender, body, created_at from public.case_portal_messages
                where grant_id=%s and sender=%s and client_message_id=%s""",
                (grant_id, sender, client_message_id))
            row = cursor.fetchone()
            if row["body"] != text:
                raise SmsError("idempotency_conflict", 409, "Message ID has another text")
        return PortalChatMessage.model_validate(row)

    def list_guest(self, session_token: str) -> list[PortalChatMessage]:
        with self._connect() as connection, connection.cursor() as cursor:
            principal = self._authorize_cursor(cursor, session_token, "read_summary")
            self._sms_grant(cursor, principal.grant_id, principal.case_id, active=True)
            return self._messages(cursor, principal.grant_id)

    def send_guest(self, session_token: str, request: PortalChatSend) -> PortalChatMessage:
        with self._connect() as connection, connection.cursor() as cursor:
            principal = self._authorize_cursor(cursor, session_token, "read_summary", lock_grant=True)
            self._sms_grant(cursor, principal.grant_id, principal.case_id, active=True)
            return self._insert(cursor, case_id=principal.case_id, grant_id=principal.grant_id,
                                sender="insured", client_message_id=request.client_message_id,
                                body=request.body)

    @staticmethod
    def _manager_grant(cursor, actor_id: UUID, case_id: UUID, message_id: UUID) -> UUID:
        cursor.execute("""select g.id as grant_id, g.expires_at, g.revoked_at
            from public.cases c join public.case_messages m on m.case_id=c.id
            join public.deposit_grants g on g.message_id=m.id and g.case_id=c.id
            where c.id=%s and c.created_by_user_id=%s and m.id=%s
              and m.channel='sms' and m.provider=%s""",
            (case_id, actor_id, message_id, PROVIDER))
        row = cursor.fetchone()
        if row is None:
            raise SmsError("not_found", 404, "SMS conversation not found")
        if row["revoked_at"] is not None or row["expires_at"] <= datetime.now(timezone.utc):
            raise SmsError("deposit_link_expired", 409, "Deposit conversation has expired")
        return row["grant_id"]

    def list_manager(self, actor_id: UUID, case_id: UUID, message_id: UUID) -> list[PortalChatMessage]:
        with self._connect() as connection, connection.cursor() as cursor:
            grant_id = self._manager_grant(cursor, actor_id, case_id, message_id)
            return self._messages(cursor, grant_id)

    def send_manager(self, actor_id: UUID, case_id: UUID, message_id: UUID,
                     request: PortalChatSend) -> PortalChatMessage:
        with self._connect() as connection, connection.cursor() as cursor:
            grant_id = self._manager_grant(cursor, actor_id, case_id, message_id)
            return self._insert(cursor, case_id=case_id, grant_id=grant_id,
                                sender="manager", client_message_id=request.client_message_id,
                                body=request.body)
