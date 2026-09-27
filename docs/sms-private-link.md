# French SMS invitation and private conversation

Claimroom uses the Twilio Messaging Service configured in
`TWILIO_SMS_MESSAGING_SERVICE_SID`, with the French alphanumeric sender
`CLAIMROOM`. Configure the voice-only number privately in Vapi; its inbound
call webhook remains
`https://api.vapi.ai/twilio/inbound_call`. The old fake WhatsApp demo stays on
`WHATSAPP_DELIVERY_MODE=fake_whatsapp`.

1. Run Alembic through `20260926_sms_portal` before deploying the API. This
   migration follows `20260926_media_workflow` and adds the private text chat
   table. The existing `case_messages` table stores the SMS request and status.
2. Set the following API Production variables: `TWILIO_ACCOUNT_SID`,
   `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_MESSAGING_SERVICE_SID`, and
   `TWILIO_SMS_WEBHOOK_BASE_URL=https://claimroom-demo-api.vercel.app/v1/webhooks/twilio/sms`.
   The token is server-only. Deploy the API and web, then set
   `SMS_LINK_DELIVERY_MODE=twilio` and redeploy the API. Until then leave it
   `disabled`. No WhatsApp or Vapi setting needs to change.
3. An ended, non-urgent inbound Vapi phone call automatically sends one SMS
   with the private link to the caller's French mobile number. This includes
   calls whose claim details remain incomplete; browser voice tests do not
   send an SMS. The manager case screen shows **Ouvrir le chat privé** for the
   most recent delivered SMS whose link is still valid. Manual SMS preview and
   dispatch remain available through the API. An existing SMS for the case
   prevents a second automatic send. Do not retry an uncertain status blindly.
   The outbound request includes a signed Twilio status callback; the API
   rejects missing or invalid signatures and verifies the account, service,
   message SID and recipient before applying a status.
4. Open the SMS on that phone. The link creates a short guest session under
   `/depot`; the token is removed from browser history and never put into an
   API query string. The recipient can send text to the manager in the page and
   upload JPEG, PNG, WebP, PDF or MP4 files using the existing signed private
   upload flow. After delivery, **Ouvrir le chat privé** opens the corresponding
   private chat; files appear in case evidence. The link expires after 24
   hours; a new link requires a new SMS.
5. The Vapi assistant may announce the SMS for non-urgent phone calls. An
   urgent handoff does not send a link automatically. Check the Twilio delivery
   status in the case before claiming that a particular SMS reached the phone.

`CLAIMROOM` is a one-way sender, so the SMS itself says not to reply by SMS.
The recipient writes via `/depot` instead. Twilio's [France SMS guidelines](https://www.twilio.com/en-us/guidelines/fr/sms)
and [alphanumeric sender documentation](https://www.twilio.com/docs/messaging/services/alphanumeric-sender-ids-in-messaging-services)
describe this route.
