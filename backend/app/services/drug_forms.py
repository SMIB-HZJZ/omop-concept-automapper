"""Dose form names as they appear in European drug lists -> RxNorm dose forms.

RxNorm names dose forms differently from the EDQM standard terms used in EU product information
(and in Croatian, their HALMED translations): "Film-coated tablet" is an "Oral Tablet" in RxNorm,
"Solution for injection" an "Injectable Solution". A name that is itself an RxNorm dose form is
resolved directly; these aliases add the RxNorm forms a source name stands for. Several targets
mean the source does not tell them apart (RxNorm uses both "Injection" and "Injectable Solution"
for injectables); every drug with one of them is then a candidate.

Keys are compared after ``norm`` (lower case, no accents or punctuation). Every target must be a
valid RxNorm dose form name (checked against the 2026 RxNorm dose forms when written). The list
is a curated starting point: have a pharmacist review it before relying on it.
"""

import re
import unicodedata

_TABLET = ["Oral Tablet"]
_ER_TABLET = ["Extended Release Oral Tablet"]
_DR_TABLET = ["Delayed Release Oral Tablet"]
_CAPSULE = ["Oral Capsule"]
_ER_CAPSULE = ["Extended Release Oral Capsule"]
_DR_CAPSULE = ["Delayed Release Oral Capsule"]
_INJECTION = ["Injectable Solution", "Injection"]
_SYRINGE = ["Prefilled Syringe"]
_CREAM = ["Topical Cream", "Cream"]
_OINTMENT = ["Topical Ointment", "Ointment"]
_GEL = ["Topical Gel", "Gel"]
_EYE_DROPS = ["Ophthalmic Solution"]
_NASAL_SPRAY = ["Nasal Spray", "Metered Dose Nasal Spray"]
_SUPPOSITORY = ["Rectal Suppository", "Suppository"]

_ALIASES: dict[str, list[str]] = {
    # --- English (EDQM standard terms and common label words)
    "tablet": _TABLET,
    "tablets": _TABLET,
    "film coated tablet": _TABLET,
    "coated tablet": _TABLET,
    "prolonged release tablet": _ER_TABLET,
    "extended release tablet": _ER_TABLET,
    "modified release tablet": _ER_TABLET + _DR_TABLET,
    "gastro resistant tablet": _DR_TABLET,
    "orodispersible tablet": ["Disintegrating Oral Tablet"],
    "chewable tablet": ["Chewable Tablet"],
    "effervescent tablet": ["Effervescent Oral Tablet"],
    "sublingual tablet": ["Sublingual Tablet"],
    "capsule": _CAPSULE,
    "capsules": _CAPSULE,
    "capsule hard": _CAPSULE,
    "hard capsule": _CAPSULE,
    "capsule soft": _CAPSULE,
    "soft capsule": _CAPSULE,
    "prolonged release capsule": _ER_CAPSULE,
    "prolonged release capsule hard": _ER_CAPSULE,
    "gastro resistant capsule": _DR_CAPSULE,
    "gastro resistant capsule hard": _DR_CAPSULE,
    "syrup": ["Syrup", "Oral Solution"],
    "oral drops solution": ["Oral Solution"],
    "granules": ["Oral Granules"],
    "solution for injection": _INJECTION,
    "solution for infusion": _INJECTION,
    "solution for injection or infusion": _INJECTION,
    "concentrate for solution for infusion": _INJECTION,
    "powder for solution for injection": _INJECTION,
    "powder for solution for infusion": _INJECTION,
    "suspension for injection": ["Injectable Suspension"],
    "solution for injection in pre filled syringe": _SYRINGE,
    "solution for injection in prefilled syringe": _SYRINGE,
    "solution for injection in pre filled pen": ["Pen Injector"],
    "cutaneous solution": ["Topical Solution"],
    "eye drops": _EYE_DROPS,
    "eye drops solution": _EYE_DROPS,
    "eye drops suspension": ["Ophthalmic Suspension"],
    "eye ointment": ["Ophthalmic Ointment"],
    "ear drops": ["Otic Solution"],
    "ear drops solution": ["Otic Solution"],
    "nasal spray": _NASAL_SPRAY,
    "nasal spray solution": _NASAL_SPRAY,
    "nasal spray suspension": ["Nasal Suspension", "Metered Dose Nasal Spray"],
    "pressurised inhalation suspension": ["Metered Dose Inhaler"],
    "pressurised inhalation solution": ["Metered Dose Inhaler"],
    "inhalation powder": ["Dry Powder Inhaler", "Inhalation Powder"],
    "nebuliser solution": ["Inhalation Solution"],
    "transdermal patch": ["Transdermal System"],
    "pessary": ["Vaginal Suppository", "Vaginal Insert"],
    "rectal solution": ["Rectal Solution", "Enema"],
    # --- Croatian (HALMED translations of the EDQM terms and list abbreviations)
    "tableta": _TABLET,
    "tbl": _TABLET,
    "tabl": _TABLET,
    "filmom oblozena tableta": _TABLET,
    "film tableta": _TABLET,
    "film tbl": _TABLET,
    "oblozena tableta": _TABLET,
    "tableta s produljenim oslobadanjem": _ER_TABLET,
    "tableta s prilagodenim oslobadanjem": _ER_TABLET + _DR_TABLET,
    "zelucanootporna tableta": _DR_TABLET,
    "raspadljiva tableta za usta": ["Disintegrating Oral Tablet"],
    "tableta za zvakanje": ["Chewable Tablet"],
    "sumeca tableta": ["Effervescent Oral Tablet"],
    "kapsula": _CAPSULE,
    "caps": _CAPSULE,
    "tvrda kapsula": _CAPSULE,
    "meka kapsula": _CAPSULE,
    "kapsula s produljenim oslobadanjem": _ER_CAPSULE,
    "tvrda kapsula s produljenim oslobadanjem": _ER_CAPSULE,
    "zelucanootporna kapsula": _DR_CAPSULE,
    "tvrda zelucanootporna kapsula": _DR_CAPSULE,
    "oralna otopina": ["Oral Solution"],
    "oralna suspenzija": ["Oral Suspension"],
    "oralne kapi otopina": ["Oral Solution"],
    "sirup": ["Syrup", "Oral Solution"],
    "prasak za oralnu suspenziju": ["Powder for Oral Suspension"],
    "granule": ["Oral Granules"],
    "otopina za injekciju": _INJECTION,
    "otopina za infuziju": _INJECTION,
    "otopina za injekciju ili infuziju": _INJECTION,
    "koncentrat za otopinu za infuziju": _INJECTION,
    "prasak za otopinu za injekciju": _INJECTION,
    "prasak za otopinu za infuziju": _INJECTION,
    "suspenzija za injekciju": ["Injectable Suspension"],
    "otopina za injekciju u napunjenoj strcaljki": _SYRINGE,
    "otopina za injekciju u napunjenoj brizgalici": ["Pen Injector"],
    "krema": _CREAM,
    "mast": _OINTMENT,
    "gel": _GEL,
    "otopina za kozu": ["Topical Solution"],
    "kapi za oko": _EYE_DROPS,
    "kapi za oko otopina": _EYE_DROPS,
    "kapi za oko suspenzija": ["Ophthalmic Suspension"],
    "mast za oko": ["Ophthalmic Ointment"],
    "kapi za uho": ["Otic Solution"],
    "kapi za uho otopina": ["Otic Solution"],
    "sprej za nos": _NASAL_SPRAY,
    "sprej za nos otopina": _NASAL_SPRAY,
    "sprej za nos suspenzija": ["Nasal Suspension", "Metered Dose Nasal Spray"],
    "stlaceni inhalat suspenzija": ["Metered Dose Inhaler"],
    "stlaceni inhalat otopina": ["Metered Dose Inhaler"],
    "prasak inhalata": ["Dry Powder Inhaler", "Inhalation Powder"],
    "otopina za atomizator": ["Inhalation Solution"],
    "transdermalni flaster": ["Transdermal System"],
    "cepic": _SUPPOSITORY,
    "vaginalna tableta": ["Vaginal Tablet"],
    "vaginalni cepic": ["Vaginal Suppository"],
    # A bare generic RxNorm form also stands for its usual specific one
    "cream": _CREAM,
    "ointment": _OINTMENT,
    "suppository": _SUPPOSITORY,
}


def norm(name: str) -> str:
    """Lower case, accents removed (đ -> d), punctuation and spaces collapsed, as ``norm_text``."""
    decomposed = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", plain.lower()).strip()


ALIASES: dict[str, list[str]] = {norm(k): v for k, v in _ALIASES.items()}


def alias_targets(name: str | None) -> list[str]:
    """RxNorm dose form names a source dose form stands for (empty if it has no alias)."""
    return list(ALIASES.get(norm(name), [])) if name and name.strip() else []
