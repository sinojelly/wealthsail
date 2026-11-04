"""Preview a frozen export without a DB connection, model call or publication.

This verifies an export's internal hashes, not server attestation. Historical
generation/review/publication must use the authenticated server API instead.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from uuid import UUID

from backend.macro.research_frozen_bundle import read_frozen_bundle


async def preview_export(path, research_id, *, observed_at):
    frozen = read_frozen_bundle(path, research_id, observed_at=observed_at)
    from backend.macro.research_acceptance_replay import run_offline_repair

    result = await run_offline_repair(frozen.repair_payload())
    allowed = (
        "decision",
        "strictPass",
        "autoPublishAllowed",
        "historicalPublishAllowed",
        "retained",
        "blockers",
        "repairVersion",
        "repairRevision",
        "buildCommit",
    )
    return {
        **{key: result[key] for key in allowed if key in result},
        "counts": result.get("retained", {}).get("counts", {}),
        "repairRevision": result.get("revision"),
        "quarantined": [
            {
                key: row[key]
                for key in (
                    "itemType",
                    "itemIndex",
                    "businessKey",
                    "ruleId",
                    "reason",
                    "importance",
                    "dependencies",
                    "sourceIds",
                )
                if key in row
            }
            for row in result.get("quarantined", [])
        ],
        "operation": "OFFLINE_DRY_RUN",
        "sourceRunId": frozen.research_id,
        "originalValidatedAt": frozen.validated_at.isoformat(),
        "materialHashes": frozen.material_hashes,
        "identityVerification": "EXPORT_SELF_CONSISTENT_NOT_SERVER_ATTESTED",
        "publicationScope": "HISTORICAL_READ_ONLY",
        "actionable": False,
        "published": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle", required=True, help="UTF-8 archive artifacts export (read only)"
    )
    parser.add_argument("--run-id", required=True, type=UUID)
    args = parser.parse_args()
    try:
        value = asyncio.run(
            preview_export(
                args.bundle, str(args.run_id), observed_at=datetime.now(timezone.utc)
            )
        )
    except Exception as error:  # noqa: BLE001 -- never echo raw evidence, paths or exception messages
        value = {
            "operation": "OFFLINE_DRY_RUN",
            "decision": "BLOCKED",
            "strictPass": False,
            "errorClass": type(error).__name__,
            "published": False,
            "actionable": False,
        }
        from backend.macro.research_acceptance_replay import OfflineReplayError

        if isinstance(error, OfflineReplayError) and error.code == "INCOMPATIBLE":
            value["errorCode"] = "INCOMPATIBLE"
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return 0 if value.get("strictPass") and value.get("historicalPublishAllowed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
