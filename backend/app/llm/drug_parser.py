"""LLM extraction of a drug's structure (ingredients, strengths, dose form) from free text.

Only the extraction uses the LLM; units and numbers are validated afterwards and the matching
itself is deterministic SQL (``app.services.drug_matching``). Results are cached per model+term.
"""

import asyncio
import hashlib
import logging

import instructor

from app.core.config import get_settings
from app.core.redis import get_redis
from app.llm.gateway import chat_model_settings
from app.llm.schemas import ParsedDrug
from app.llm.structured import get_instructor

logger = logging.getLogger(__name__)

DRUG_PARSE_PROMPT = """\
You extract the structure of a medicinal product from its name, as written in a European
(often Croatian) drug list, into the fields of the OMOP drug_strength table.

Rules:
- One component per active ingredient, named by its English INN as used in RxNorm
  (paracetamol -> acetaminophen, adrenaline -> epinephrine, salbutamol -> albuterol,
  amoxicilin + klavulanska kiselina -> amoxicillin + clavulanate). Salts: keep the salt only if the
  strength refers to it.
- Solid forms: amount_value + amount_unit per unit (tablet, capsule, suppository).
- Liquids, creams and concentrations: numerator_value/unit per denominator_value/unit
  (100 mg/5 mL -> numerator 100 mg, denominator 5 mL; 1 % -> numerator 1 %).
- Units as written in labels: mg, g, mcg, mL, IU, mmol, %. Never invent a strength that is not in
  the name; leave the fields empty instead.
- dose_form: the closest RxNorm dose form name from the list below, or null if the name gives no
  form. Map local abbreviations: tbl/tableta -> Oral Tablet, film tbl -> Film Coated Oral Tablet
  (if listed), caps/kapsula -> Oral Capsule, sirup -> Oral Solution or Oral Suspension as stated,
  inj./otopina za injekciju -> Injectable Solution, krema -> Topical Cream, mast -> Topical Ointment.
- brand_name: only if the name contains a trade name.
- is_drug=false if the term is not a medicinal product (e.g. a device or a diagnosis).

Dose forms:
{dose_forms}"""


class DrugParser:
    CACHE_TTL = 90 * 24 * 3600

    def __init__(
        self,
        model: str,
        dose_forms: list[str],
        client: instructor.AsyncInstructor | None = None,
    ):
        self.model = model
        self.dose_forms = dose_forms
        self._client = client

    def _key(self, term: str) -> str:
        digest = hashlib.sha1(term.strip().encode()).hexdigest()
        return f"drugparse:{self.model}:{digest}"

    async def parse(self, term: str, hint: str | None = None) -> ParsedDrug:
        text = term if not hint else f"{term}\n(known structure, may be partial: {hint})"
        key = self._key(text)
        try:
            hit = await asyncio.to_thread(get_redis().get, key)
            if hit:
                return ParsedDrug.model_validate_json(hit)
        except Exception:
            logger.debug("Drug parse cache read failed", exc_info=True)
        client = self._client or get_instructor(self.model)
        parsed: ParsedDrug = await client.chat.completions.create(
            model=self.model,
            response_model=ParsedDrug,
            messages=[
                {
                    "role": "system",
                    "content": DRUG_PARSE_PROMPT.format(dose_forms="\n".join(self.dose_forms)),
                },
                {"role": "user", "content": f"Drug name: {text}"},
            ],
            max_retries=get_settings().llm_max_retries,
            **chat_model_settings(),
        )
        try:
            await asyncio.to_thread(
                get_redis().set, key, parsed.model_dump_json(), ex=self.CACHE_TTL
            )
        except Exception:
            logger.debug("Drug parse cache write failed", exc_info=True)
        return parsed
