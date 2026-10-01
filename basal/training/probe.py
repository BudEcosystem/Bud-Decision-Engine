"""`python -m basal.training.probe`: prints what this computer can train on, as JSON. Run by the studio server in a
subprocess (the server never imports PyTorch)."""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        from . import device
        p = device.profile()
        out = {"ok": True, **p.public()}
        try:
            out["available_gb"] = round(device.available_bytes(p, reserve_gb=0) / 1e9, 1)
        except Exception:  # noqa: BLE001
            out["available_gb"] = None
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "tier": "off", "reason": str(e) if isinstance(e, RuntimeError) else
               "The GPU could not be used for training on this computer."}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
