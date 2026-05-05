"""ResearchAgent — validation mixin."""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("agentpexi.research")


class _ResearchValidationMixin:

    async def _parse_and_validate(
        self, text: str, system_prompt: str
    ) -> dict[str, Any] | None:
        """Parse JSON con retry su fallimento e validazione struttura."""
        # Tentativo 1
        result = self._try_parse_json(text)
        if result is None:
            logger.warning(
                "research: JSON parse fallito (tentativo 1) — raw[:300]='%s'",
                text[:300].replace("\n", "↵"),
            )
            # Retry con correction prompt
            corrected = await self._call_llm(
                messages=[{
                    "role": "user",
                    "content": (
                        "Il JSON seguente è malformato o incompleto. "
                        "Riscrivilo correttamente rispettando esattamente la struttura "
                        "indicata nel system prompt. Rispondi SOLO con JSON valido.\n\n"
                        f"JSON da correggere:\n{text}"
                    ),
                }],
                system_prompt=system_prompt,
            )
            result = self._try_parse_json(corrected)
            if result is None:
                logger.warning(
                    "research: JSON parse fallito anche dopo retry — raw[:200]='%s'",
                    corrected[:200].replace("\n", "↵"),
                )

        if result is None:
            return None  # Caller restituirà FAILED

        # Validazione campi obbligatori
        if not isinstance(result.get("niches"), list) or len(result["niches"]) == 0:
            logger.warning("research: _parse_and_validate: 'niches' assente o vuoto nel JSON LLM")
            return None
        for niche in result["niches"]:
            required = ["name", "keywords", "pricing", "recommended_product_type", "demand"]
            if not all(k in niche for k in required):
                logger.warning(
                    "research: _parse_and_validate: nicchia '%s' manca campi obbligatori: %s",
                    niche.get("name", "?"),
                    [k for k in required if k not in niche],
                )
                return None
            if not isinstance(niche.get("keywords"), list) or len(niche["keywords"]) == 0:
                return None

        # Fix 3 — Valida e ottimizza tag per ogni nicchia
        for i, niche in enumerate(result["niches"]):
            result["niches"][i] = self._validate_and_fix_tags(niche)

        # Fix 4 — Viability gate
        # NOTA: se l'LLM marca tutte le nicchie viable=false (es. dati assenti),
        # _apply_viability_gate ritorna None. In quel caso NON trattiamo come "JSON parse failed"
        # ma restituiamo il result originale (con viable=false su tutto) così la confidence gate
        # può produrre un errore FAILED con messaggio corretto ("nessuna nicchia viable trovata").
        filtered_result, discarded = self._apply_viability_gate(result)
        if filtered_result is None:
            logger.warning(
                "research: _parse_and_validate: tutte le nicchie marcate non-viable dall'LLM "
                "(dati insufficienti o criteri business non soddisfatti). "
                "Scartate: %s. Restituisco result non-viable per errore leggibile.",
                [d.get("name") for d in discarded],
            )
            # Ritorna il result originale (non filtrato) — confidence gate darà FAILED
            # con "nessuna nicchia viable trovata" invece di "JSON parsing fallito"
            return result
        result = filtered_result

        return result

    @staticmethod
    def _try_parse_json(text: str) -> dict[str, Any] | None:
        """Tenta il parse JSON, None se fallisce. Tolera markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end]).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _validate_and_fix_tags(niche_data: dict) -> dict:
        """
        Valida e ottimizza i 13 tag Etsy per ogni nicchia.
        Etsy tag rules: max 20 chars each, no special chars except spaces,
        lowercase preferibile, possono essere frasi (multi-word).
        """
        import re as _re

        tags = niche_data.get("etsy_tags_13", [])

        if len(tags) != 13:
            if len(tags) > 13:
                tags = tags[:13]
            elif len(tags) < 13 and len(tags) > 0:
                keywords = niche_data.get("keywords", [])
                for kw in keywords:
                    if kw not in tags and len(tags) < 13:
                        tags.append(kw)
            niche_data["etsy_tags_13"] = tags[:13]

        fixed_tags = []
        for tag in tags:
            tag = str(tag).lower().strip()
            tag = _re.sub(r'[^a-z0-9\s\-]', '', tag)
            if len(tag) > 20:
                tag = tag[:20].rsplit(' ', 1)[0]
            if tag and len(tag) >= 2:
                fixed_tags.append(tag)

        seen: set[str] = set()
        unique_tags: list[str] = []
        for tag in fixed_tags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        niche_data["etsy_tags_13"] = unique_tags[:13]

        short_tags = [t for t in unique_tags if len(t.split()) == 1]
        long_tags = [t for t in unique_tags if len(t.split()) >= 2]

        if len(short_tags) > 8:
            niche_data.setdefault("notes", "")
            niche_data["notes"] += " [WARNING: troppi tag singola parola, considera frasi long-tail]"

        if len(long_tags) < 3:
            niche_data.setdefault("notes", "")
            niche_data["notes"] += " [WARNING: pochi tag long-tail, visibilità potenzialmente bassa]"

        return niche_data

    @staticmethod
    def _apply_viability_gate(result: dict) -> tuple[dict | None, list[dict]]:
        """
        Applica criteri di business per scartare nicchie non profittevoli.
        Ritorna (result_filtrato, lista_motivi_scarto).
        """
        discarded: list[dict] = []
        viable_niches: list[dict] = []

        for niche in result.get("niches", []):
            reasons_to_discard: list[str] = []

            pricing = niche.get("pricing", {})
            demand = niche.get("demand", {})
            competition = niche.get("competition", {})

            sweet_spot = (
                pricing.get("conversion_sweet_spot_usd", 0)
                or pricing.get("sweet_spot_usd", 0)
            )
            difficulty = niche.get("entry_difficulty", "medium")
            demand_level = demand.get("level", "medium")
            demand_trend = demand.get("trend", "stable")
            viable_flag = niche.get("viable", True)

            if viable_flag is False:
                discarded.append({
                    "name": niche["name"],
                    "reason": niche.get("viability_reason", "Marcata non viable dall'analisi"),
                })
                viable_niches.append(niche)
                continue

            if 0 < sweet_spot < 2.99:
                reasons_to_discard.append(
                    f"Prezzo sweet spot ${sweet_spot} troppo basso: "
                    f"dopo fee Etsy (6.5% + €0.20) e costi API, margine negativo o nullo"
                )

            if difficulty == "high" and demand_level == "low":
                reasons_to_discard.append(
                    "Combinazione fatale: alta difficoltà d'ingresso + bassa domanda. "
                    "ROI atteso negativo."
                )

            if demand_trend == "declining" and competition.get("level") == "high":
                reasons_to_discard.append(
                    "Mercato in declino con alta competizione: finestra di opportunità chiusa."
                )

            if not niche.get("etsy_tags_13"):
                reasons_to_discard.append(
                    "Nessun tag Etsy generato: Publisher Agent non può creare il listing."
                )

            if reasons_to_discard:
                niche["viable"] = False
                niche["viability_reason"] = " | ".join(reasons_to_discard)
                discarded.append({
                    "name": niche["name"],
                    "reason": niche["viability_reason"],
                })

            viable_niches.append(niche)

        result["niches"] = viable_niches

        all_viable = [n for n in viable_niches if n.get("viable", True)]
        if not all_viable:
            return None, discarded

        result["discarded_niches"] = discarded
        return result, discarded

    def _enforce_failure_constraints(
        self,
        output: dict,
        failure_context: list[dict],
    ) -> tuple[dict, list[str]]:
        """
        Verifica strutturalmente che l'output rispetti le failure analysis.
        Modifica l'output direttamente se trova violazioni.
        Ritorna (output_modificato, lista_violazioni).
        """
        violations: list[str] = []

        if not failure_context:
            return output, violations

        failure_map: dict[str, list[dict]] = {}
        for fc in failure_context:
            meta = fc.get("metadata", {})
            niche_name = meta.get("niche", "").lower()
            if niche_name:
                if niche_name not in failure_map:
                    failure_map[niche_name] = []
                failure_map[niche_name].append({
                    "failure_type": meta.get("failure_type", ""),
                    "avoid_in_future": meta.get("avoid_in_future", ""),
                    "document": fc.get("document", ""),
                })

        filtered_niches: list[dict] = []
        for niche in output.get("niches", []):
            niche_name = niche.get("name", "").lower()

            matched_failures: list[dict] = []
            for failed_niche, failures in failure_map.items():
                if (
                    failed_niche in niche_name
                    or niche_name in failed_niche
                    or any(
                        word in niche_name
                        for word in failed_niche.split()
                        if len(word) > 4
                    )
                ):
                    matched_failures.extend(failures)

            if not matched_failures:
                filtered_niches.append(niche)
                continue

            has_fatal = any(f["failure_type"] == "no_views_no_sales" for f in matched_failures)
            has_no_views = any(f["failure_type"] == "no_views" for f in matched_failures)
            has_no_conversion = any(f["failure_type"] == "no_conversion" for f in matched_failures)

            if has_fatal:
                violations.append(
                    f"Nicchia '{niche['name']}' SCARTATA: failure history no_views_no_sales. "
                    f"Avoid: {[f['avoid_in_future'] for f in matched_failures if f['failure_type'] == 'no_views_no_sales']}"
                )
                niche["viable"] = False
                niche["viability_reason"] = (
                    f"SCARTATA automaticamente: failure history no_views_no_sales. "
                    f"Problema: {matched_failures[0].get('avoid_in_future', 'non specificato')}"
                )
                filtered_niches.append(niche)
                continue

            if has_no_views:
                avoid_kw = [
                    f["avoid_in_future"]
                    for f in matched_failures
                    if f["failure_type"] == "no_views"
                ]
                violations.append(
                    f"Nicchia '{niche['name']}': no_views history — tag strategy deve evitare: {avoid_kw}"
                )
                faa = niche.setdefault("failure_analysis_applied", {"failures_found": 0, "actions_taken": [], "avoided": []})
                faa.setdefault("actions_taken", []).append(
                    f"Tag strategy modificata per failure no_views: evitati {avoid_kw}"
                )
                faa.setdefault("avoided", []).extend(avoid_kw)
                niche["tag_strategy"] = (
                    f"[FAILURE-ADJUSTED] {niche.get('tag_strategy', '')} — "
                    f"Evitati tag che hanno causato 0 views in precedenza: {avoid_kw}"
                )

            if has_no_conversion:
                avoid_price = [
                    f["avoid_in_future"]
                    for f in matched_failures
                    if f["failure_type"] == "no_conversion"
                ]
                violations.append(
                    f"Nicchia '{niche['name']}': no_conversion history — prezzo deve cambiare. Avoid: {avoid_price}"
                )
                faa = niche.setdefault("failure_analysis_applied", {"failures_found": 0, "actions_taken": [], "avoided": []})
                faa.setdefault("actions_taken", []).append(
                    f"Price point ajustato per failure no_conversion: {avoid_price}"
                )
                current_price = niche.get("pricing", {}).get("conversion_sweet_spot_usd", 0)
                niche["pricing"]["price_reasoning"] = (
                    f"[FAILURE-ADJUSTED] Prezzo modificato rispetto a history no_conversion. "
                    f"Precedente problematico: {avoid_price}. Nuovo: {current_price}"
                )

            filtered_niches.append(niche)

        output["niches"] = filtered_niches
        return output, violations
