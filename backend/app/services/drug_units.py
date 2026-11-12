"""Units for drug strength matching.

OMOP ``drug_strength`` refers to UCUM unit concepts (concept_code e.g. ``mg``, ``mL``, ``[iU]``).
Source terms use many spellings (``mcg``, ``µg``, ``ml``, ``I.U.``). Everything is normalised to a
base unit per dimension so that 0.5 g equals 500 mg and 500 mcg/5 mL equals 0.1 mg/mL.
"""

from dataclasses import dataclass

# UCUM code -> (dimension, factor to the base unit of that dimension)
_UCUM: dict[str, tuple[str, float]] = {
    # mass, base mg
    "kg": ("mass", 1e6),
    "g": ("mass", 1e3),
    "mg": ("mass", 1.0),
    "ug": ("mass", 1e-3),
    "ng": ("mass", 1e-6),
    # volume, base mL
    "L": ("volume", 1e3),
    "mL": ("volume", 1.0),
    "uL": ("volume", 1e-3),
    # amount of substance, base mmol
    "mol": ("substance", 1e3),
    "mmol": ("substance", 1.0),
    "umol": ("substance", 1e-3),
    # equivalents, base mEq
    "meq": ("equivalents", 1.0),
    "eq": ("equivalents", 1e3),
    # international units, base IU
    "[iU]": ("iu", 1.0),
    "[IU]": ("iu", 1.0),
    "k[iU]": ("iu", 1e3),
    "M[iU]": ("iu", 1e6),
    "[U]": ("units", 1.0),
    # dose-form units used as denominators
    "{actuat}": ("actuation", 1.0),
    "{dose}": ("dose", 1.0),
    "cm2": ("area", 1.0),
    "h": ("time", 1.0),
    "%": ("percent", 1.0),
}

# Common spellings in drug labels (lower-cased) -> UCUM code
_ALIASES: dict[str, str] = {
    "mcg": "ug",
    "µg": "ug",
    "μg": "ug",
    "microgram": "ug",
    "micrograms": "ug",
    "milligram": "mg",
    "milligrams": "mg",
    "gram": "g",
    "grams": "g",
    "gr": "g",
    "ml": "mL",
    "millilitre": "mL",
    "milliliter": "mL",
    "l": "L",
    "litre": "L",
    "liter": "L",
    "µl": "uL",
    "ul": "uL",
    "iu": "[iU]",
    "i.u.": "[iU]",
    "ie": "[iU]",  # Internationale Einheiten
    "i.j.": "[iU]",  # internacionalne jedinice (hr)
    "ij": "[iU]",
    "ui": "[iU]",  # unités/unità internationales
    "kiu": "k[iU]",
    "miu": "M[iU]",
    "mio. i.j.": "M[iU]",
    "u": "[U]",
    "unit": "[U]",
    "units": "[U]",
    "mmol": "mmol",
    "mol": "mol",
    "meq": "meq",
    "actuation": "{actuat}",
    "puff": "{actuat}",
    "dose": "{dose}",
    "doza": "{dose}",
    "cm2": "cm2",
    "cm²": "cm2",
    "h": "h",
    "hr": "h",
    "hour": "h",
    "%": "%",
}


@dataclass(frozen=True)
class Quantity:
    value: float
    dimension: str  # mass, volume, iu, ... ("percent" is handled by ``concentration``)


def normalise_unit(unit: str | None) -> str | None:
    """A UCUM code for a unit as written in a label, or None if unknown."""
    if not unit:
        return None
    raw = unit.strip()
    if raw in _UCUM:
        return raw
    return _ALIASES.get(raw.lower())


def quantity(value: float | None, unit: str | None) -> Quantity | None:
    code = normalise_unit(unit)
    if value is None or code is None or code not in _UCUM or value <= 0:
        return None
    dimension, factor = _UCUM[code]
    return Quantity(value * factor, dimension)


def concentration(
    num_value: float | None,
    num_unit: str | None,
    den_value: float | None,
    den_unit: str | None,
) -> tuple[float, str, str] | None:
    """Strength per one base denominator unit: (value, numerator dimension, denominator dimension).

    A missing denominator value means "per 1 unit" (as in OMOP clinical drugs). Percent is
    read as mass per 100 of the denominator (1% = 10 mg per g or per mL)."""
    if normalise_unit(num_unit) == "%":
        if num_value is None or num_value <= 0:
            return None
        den = quantity(den_value or 1.0, den_unit or "mL")
        den_dimension = den.dimension if den else "volume"
        # 1% w/v or w/w = 1 g / 100 mL (or g) = 10 mg per mL (or per g, treated alike below)
        return (
            num_value * 10.0,
            "mass",
            "volume" if den_dimension in ("mass", "volume") else den_dimension,
        )
    num = quantity(num_value, num_unit)
    den = quantity(den_value if den_value else 1.0, den_unit)
    if num is None or den is None:
        return None
    if den.dimension == "mass":
        # Semi-solids (mg per g) are compared with solutions (mg per mL): 1 g ~ 1 mL
        return (num.value / (den.value / 1e3), num.dimension, "volume")
    return (num.value / den.value, num.dimension, den.dimension)


def close(a: float, b: float, rel: float = 0.01) -> bool:
    """Equal within 1% (labels round, e.g. 0.333 mg/mL)."""
    return abs(a - b) <= rel * max(abs(a), abs(b))
