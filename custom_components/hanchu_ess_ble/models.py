"""Inverter model identification from register P003.

P003 reports the inverter's model name directly over BLE (e.g.
"HESS-HY-S-5.0K", confirmed on hardware). The table below is the same one
the hanchu-ess-ha cloud integration uses, built from the Hanchu web
console's devModel mapping (pcsEnMap) with power ratings from Hanchu's
manuals:

- Single-phase HESS-HY-S-3.0K to 6.0K share one battery stage capped at
  5000 W (100 A), whatever their AC rating.
- Single-phase 7.0K/8.0K/10.0K and every three-phase and commercial model
  have a battery maximum equal to the AC rating.
- The 48 V HESS-HY-S-6K-S/12K-S datasheets give only charge currents, so no
  battery maximum is assumed for them.

Keep this table in step with hanchu-ess-ha's models.py. Unknown names return
None and callers fall back to P005 (Inverter Power Limit).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any



@dataclass(frozen=True)
class InverterModel:
    """A known inverter model and its power ratings."""

    code: int
    name: str
    ac_rating_w: int
    battery_max_w: int | None  # None when the manuals don't state one


def _same_as_ac(code: int, name: str, ac: int) -> InverterModel:
    return InverterModel(code, name, ac, ac)


INVERTER_MODELS: dict[int, InverterModel] = {
    m.code: m
    for m in (
        # Single-phase 3-6 kW: battery stage capped at 5000 W
        InverterModel(1, "HESS-HY-S-3.0K", 3000, 5000),
        InverterModel(2, "HESS-HY-S-3.68K", 3680, 5000),
        InverterModel(3, "HESS-HY-S-4.0K", 4000, 5000),
        InverterModel(4, "HESS-HY-S-5.0K", 5000, 5000),
        InverterModel(5, "HESS-HY-S-6.0K", 6000, 5000),
        # Three-phase
        _same_as_ac(6, "HESS-HY-T-05K", 5000),
        _same_as_ac(7, "HESS-HY-T-06K", 6000),
        _same_as_ac(8, "HESS-HY-T-08K", 8000),
        _same_as_ac(9, "HESS-HY-T-10K", 10000),
        _same_as_ac(10, "HESS-HY-T-12K", 12000),
        _same_as_ac(11, "HESS-HY-T-4K-M", 4000),
        _same_as_ac(12, "HESS-HY-T-5K-M", 5000),
        _same_as_ac(13, "HESS-HY-T-6K-M", 6000),
        _same_as_ac(14, "HESS-HY-T-8K-M", 8000),
        _same_as_ac(15, "HESS-HY-T-10K-M", 10000),
        _same_as_ac(16, "HESS-HY-T-12K-M", 12000),
        _same_as_ac(17, "HESS-HY-T-10K-M4", 10000),
        _same_as_ac(18, "HESS-HY-T-12K-M4", 12000),
        _same_as_ac(19, "HESS-HY-T-15K-M4", 15000),
        _same_as_ac(20, "HESS-HY-T-20K-M4", 20000),
        # Commercial
        _same_as_ac(21, "CESS-HY-25K", 25000),
        _same_as_ac(22, "CESS-HY-30K", 30000),
        _same_as_ac(23, "CESS-HY-36K", 36000),
        _same_as_ac(24, "CESS-HY-40K", 40000),
        _same_as_ac(25, "CESS-HY-50K", 50000),
        _same_as_ac(26, "CESS-HY-40K-P", 40000),
        _same_as_ac(27, "CESS-HY-50K-P", 50000),
        # 48 V single-phase: manuals give charge current only
        InverterModel(28, "HESS-HY-S-6K-S", 6000, None),
        InverterModel(29, "HESS-HY-S-12K-S", 12000, None),
        # Single-phase 7-10 kW (added later, out of size order)
        _same_as_ac(30, "HESS-HY-S-7.0K", 7000),
        _same_as_ac(31, "HESS-HY-S-8.0K", 8000),
        _same_as_ac(32, "HESS-HY-S-10.0K", 10000),
    )
}



_MODELS_BY_NAME: dict[str, InverterModel] = {
    m.name.upper(): m for m in INVERTER_MODELS.values()
}

MODEL_KEY = "P003"  # Inverter Model, e.g. "HESS-HY-S-5.0K"
MODEL_READ_ATTEMPTS = 5  # poll cycles to try P003 before giving up


def lookup_inverter_model(name: Any) -> InverterModel | None:
    """Return the model for a P003 value, or None if not recognised."""
    if not isinstance(name, str):
        return None
    return _MODELS_BY_NAME.get(name.strip().upper())


def model_from_values(values: dict | None) -> InverterModel | None:
    """Resolve the model from a coordinator's register values."""
    if not values:
        return None
    return lookup_inverter_model(values.get(MODEL_KEY))


def inverter_device_model(coordinator: Any) -> str:
    """Model name for the inverter's DeviceInfo, or the generic default."""
    from .const import MODEL  # local import: const has no dependency on us

    data = getattr(coordinator, "data", None)
    model = model_from_values(getattr(data, "values", None))
    return model.name if model is not None else MODEL
