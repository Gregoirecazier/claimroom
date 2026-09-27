"""Run with: python -m claim_api.garage_worker [--once]."""

from __future__ import annotations

import argparse
import os
import time

from claim_api.garage_repository import GarageRepository, delivery_mode
from claim_api.garages import GarageError, OsmGarageFinder, Point
from claim_api.twilio_sms import SmsError, TwilioSms


def process_one(repository, finder, transport) -> bool:
    job = repository.claim_next()
    if job is None:
        return False
    try:
        if job["mode"] == "live":
            if delivery_mode() != "live":
                repository.finish(job["id"], status="cancelled", error="live_sms_disabled")
                return True
            transport.check_configuration()
        origin = Point.model_validate(job["origin_json"]) if job["origin_json"] else None
        preview = finder.preview(job["location_text"], origin)
        if preview.status == "needs_location":
            repository.finish(job["id"], status="needs_location", error="incident_location_needs_confirmation")
            return True
        message_id = repository.prepare(job, preview)
        if message_id is None:
            return True
        sid = transport.send(job["recipient"], preview.sms_body, str(message_id)) if job["mode"] == "live" else None
        repository.finish(job["id"], status="done", sid=sid)
    except GarageError as error:
        repository.finish(job["id"], status="failed", error=error.code)
    except SmsError as error:
        repository.finish(job["id"], status="unknown" if error.uncertain else "failed", error=error.code)
    # Unexpected errors stop the worker. Persisted leases remain recoverable;
    # never mask an unknown send as a failure eligible for automatic retries.
    return True


def main():
    parser = argparse.ArgumentParser(description="Process durable garage SMS jobs.")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    args = parser.parse_args()
    transport = TwilioSms()
    if delivery_mode() == "live":
        transport.check_configuration()
    repository = GarageRepository(os.environ["DATABASE_URL"])
    finder = OsmGarageFinder()
    while True:
        worked = process_one(repository, finder, transport)
        if args.once:
            return
        if not worked:
            time.sleep(5)


if __name__ == "__main__":
    main()
