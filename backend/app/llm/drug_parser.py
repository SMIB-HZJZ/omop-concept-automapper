"""LLM steps of drug mapping: the structure of a drug name (ingredients, strengths, dose form)
and the RxNorm dose forms a source dose form term stands for, in any language.

Only reading the source uses the LLM; units and numbers are validated afterwards and the matching
itself is deterministic SQL (``app.services.drug_matching``). Results are cached per model+term.
"""

import asyncio
import hashlib
import logging

import instructor

from app.core.config import get_settings
from app.core.redis import get_redis
from app.llm.gateway import chat_model_settings
from app.llm.schemas import DoseFormChoice, ParsedDrug
from app.llm.structured import get_instructor

logger = logging.getLogger(__name__)

DRUG_PARSE_PROMPT = """\
You extract the structure of a medicinal product from its name, as written in a drug list in
any language, into the fields of the OMOP drug_strength table.

Rules:
- One component per active ingredient, named by its English INN as used in RxNorm
  (paracetamol -> acetaminophen, adrenaline -> epinephrine, salbutamol -> albuterol,
  amoxicillin + clavulanic acid -> amoxicillin + clavulanate), whatever the source language.
  Salts: keep the salt only if the strength refers to it.
- Solid forms: amount_value + amount_unit per unit (tablet, capsule, suppository).
- Liquids, creams and concentrations: numerator_value/unit per denominator_value/unit
  (100 mg/5 mL -> numerator 100 mg, denominator 5 mL; 1 % -> numerator 1 %).
- quantity_value/unit: the total volume or mass of ONE unit of the product, only when the name
  states it (ampoule 2 mL, vial 10 mL, prefilled syringe 0.4 mL): "diazepam 10 mg/2 mL ampule" ->
  numerator 10 mg, denominator 2 mL, quantity 2 mL. A concentration alone (syrup 100 mg/5 mL) has
  no quantity. Never use the pack size (20 tablets, 5 x 2 mL) as the quantity; for "5 x 2 mL" the
  quantity is 2 mL.
- Strengths per dose, actuation or puff (inhalers, sprays): numerator per denominator unit
  "actuation" (200 micrograms/dose -> numerator 200 mcg, denominator unit actuation).
- Units in English abbreviations, whatever the source language: mg, g, mcg, mL, IU, mmol, %,
  actuation (Einheiten, jedinice, unités -> IU). Never invent a strength that is not in the name;
  leave the fields empty instead.
- dose_form: the RxNorm dose form from the list below that means the same as the form in the
  name (in any language or abbreviation), copied exactly, or null if the name gives no form.
  RxNorm does not record coatings: a film-coated tablet is an Oral Tablet. Keep the route and the
  release: a prolonged-release tablet is an Extended Release Oral Tablet, a gastro-resistant one a
  Delayed Release Oral Tablet; a cream for the skin is a Topical Cream.
- brand_name: only if the name contains a trade name.
- is_drug=false if the term is not a medicinal product (e.g. a device or a diagnosis).

Dose forms:
{dose_forms}"""


_PARSE_VERSION = hashlib.sha1(DRUG_PARSE_PROMPT.encode()).hexdigest()[:8]


class DrugParser:
    CACHE_TTL = 90 * 24 * 3600

    def __init__(
        self,
        model: str,
        dose_forms: list[str],
        client: instructor.AsyncInstructor | None = None,
        effort: str | None = None,
    ):
        self.model = model
        self.dose_forms = dose_forms
        self.effort = effort or None
        self._client = client

    def _key(self, term: str) -> str:
        # The prompt is part of the key: a changed prompt must not reuse old answers
        digest = hashlib.sha1(f"{_PARSE_VERSION}\n{term.strip()}".encode()).hexdigest()
        label = f"{self.model}@{self.effort}" if self.effort else self.model
        return f"drugparse:{label}:{digest}"

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
            **chat_model_settings(self.model, self.effort),
        )
        try:
            await asyncio.to_thread(
                get_redis().set, key, parsed.model_dump_json(), ex=self.CACHE_TTL
            )
        except Exception:
            logger.debug("Drug parse cache write failed", exc_info=True)
        return parsed


DOSE_FORM_PROMPT = """\
You map the dose form of a medicinal product, as written in a drug list in any language or
abbreviation, to RxNorm dose forms.

Rules:
- Choose only forms from the list below that mean the same as the source form, copied exactly.
- RxNorm does not record coatings: a film-coated or coated tablet is an Oral Tablet.
- Keep the route and the release: a prolonged-release tablet is an Extended Release Oral Tablet,
  a gastro-resistant one a Delayed Release Oral Tablet, an eye drop an Ophthalmic Solution or
  Suspension, a cream for the skin a Topical Cream.
- Where RxNorm uses two names for the same thing (Injectable Solution and Injection for a
  solution for injection or infusion), return both; at most three.
- RxNorm products mostly use the specific forms (Oral Tablet, Oral Capsule, Oral Solution,
  Topical Cream, Injectable Solution), rarely the generic ones (Tablet, Capsule, Syrup, Cream,
  Injection): for a generic term return the specific form(s) it usually stands for.
- Return an empty list if no listed form means the same; never a merely similar one.

Dose forms:
{dose_forms}"""


_FORM_VERSION = hashlib.sha1(DOSE_FORM_PROMPT.encode()).hexdigest()[:8]


class DoseFormPicker:
    """Source dose form term (any language) -> RxNorm dose form names, chosen by the LLM."""

    CACHE_TTL = 90 * 24 * 3600

    def __init__(
        self,
        model: str,
        dose_forms: list[str],
        client: instructor.AsyncInstructor | None = None,
        effort: str | None = None,
    ):
        self.model = model
        self.dose_forms = dose_forms
        self.effort = effort or None
        self._client = client

    def _key(self, text: str) -> str:
        digest = hashlib.sha1(f"{_FORM_VERSION}\n{text}".encode()).hexdigest()
        label = f"{self.model}@{self.effort}" if self.effort else self.model
        return f"doseform:{label}:{digest}"

    async def pick(self, form: str, product: str | None = None) -> list[str]:
        """``product``: the source name, which can settle an ambiguous form (drops: eye or oral)."""
        text = f"Dose form: {form.strip()}" + (f"\nProduct: {product.strip()}" if product else "")
        key = self._key(text)
        try:
            hit = await asyncio.to_thread(get_redis().get, key)
            if hit:
                return DoseFormChoice.model_validate_json(hit).forms
        except Exception:
            logger.debug("Dose form cache read failed", exc_info=True)
        client = self._client or get_instructor(self.model)
        choice: DoseFormChoice = await client.chat.completions.create(
            model=self.model,
            response_model=DoseFormChoice,
            messages=[
                {
                    "role": "system",
                    "content": DOSE_FORM_PROMPT.format(dose_forms="\n".join(self.dose_forms)),
                },
                {"role": "user", "content": text},
            ],
            context={"dose_forms": self.dose_forms},
            max_retries=get_settings().llm_max_retries,
            **chat_model_settings(self.model, self.effort),
        )
        try:
            await asyncio.to_thread(
                get_redis().set, key, choice.model_dump_json(), ex=self.CACHE_TTL
            )
        except Exception:
            logger.debug("Dose form cache write failed", exc_info=True)
        return choice.forms
