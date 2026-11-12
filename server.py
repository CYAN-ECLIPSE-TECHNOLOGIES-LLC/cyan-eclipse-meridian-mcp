#!/usr/bin/env python3
# Eclipse Meridian MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Meridian Core MCP server (stdio): the 19 spec tools, nothing else.

"""Eclipse Meridian — governed telehealth operations —
jurisdiction-gated encounters, signed-note integrity, PHI access
evidence. Not a healthcare provider.

Exactly 19 tools per spec section 6.1. PRODUCT LAW (spec 6.2):
there is NO note_finalize / sign-off-class tool in this surface —
signature is the treating clinician's act in the system of
record; the agent surface ends at note_sign_request. No
prescribing surface exists in any phase. P0 is synthetic-only.

Transport: MCP over stdio (newline-delimited JSON-RPC 2.0),
stdlib only. State: $ECLIPSE_MERIDIAN_STATE or ./data/state.json.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import meridian_engine as engine  # noqa: E402
from meridian_engine import ToolError  # noqa: E402

def S(desc):
    return {"type": "string", "description": desc}


def O(desc):
    return {"type": "object", "description": desc}


def A(desc, items=None):
    prop = {"type": "array", "description": desc}
    if items is not None:
        prop["items"] = items
    return prop


def B(desc):
    return {"type": "boolean", "description": desc}


def _ann(read_only, destructive=False, idempotent=False):
    return {"readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": False}


def _vout(data_props, data_required=None):
    """Output schema for the verdict envelope every tool returns:
    {ok, refusal_class, detail, cited_fact, mandate_ref,
    policy_pack_version, ts, access_record_ref, descriptor, data}
    with a tool-specific 'data' object."""
    data = {"type": "object", "properties": data_props,
            "description": "Tool-specific payload; populated on "
                           "success, empty or partial on refusal."}
    if data_required:
        data["required"] = data_required
    return {"type": "object", "properties": {
        "ok": {"type": "boolean",
               "description": "True on success; false carries a "
                              "named refusal_class."},
        "refusal_class": {"type": ["string", "null"],
                          "description": "Named refusal class on "
                                         "failure; null on success."},
        "detail": {"type": "string",
                   "description": "Human-readable explanation of "
                                  "the verdict or refusal."},
        "cited_fact": {"type": ["object", "null"],
                       "description": "Fact the verdict rests on, "
                                      "with source and as-of, "
                                      "where applicable."},
        "mandate_ref": {"type": ["string", "null"],
                        "description": "Mandate the verdict was "
                                       "evaluated under."},
        "policy_pack_version": {"type": ["string", "null"],
                                "description": "Counsel-signed "
                                               "policy pack version "
                                               "applied, where "
                                               "applicable."},
        "ts": {"type": "string",
               "description": "ISO 8601 evaluation timestamp."},
        "access_record_ref": {"type": ["string", "null"],
                              "description": "Sealed access-record "
                                             "id when the call "
                                             "touched protected "
                                             "data."},
        "data": data},
        "required": ["ok", "data"]}


_CTXT = ("Local stdio server over synthetic data only: not a "
         "healthcare provider, no real patient data, no network "
         "calls, no rate limit; state persists to the server "
         "state JSON file. Every tool returns the same verdict "
         "envelope {ok, refusal_class, detail, cited_fact, "
         "mandate_ref, policy_pack_version, ts, "
         "access_record_ref, data} — a false ok always carries a "
         "named refusal_class, never a silent failure. Arguments "
         "containing finalize-, sign-, override-, or prescribe-"
         "shaped keys at any nesting depth are refused. ")

_NOW = ("ISO 8601 evaluation instant; mandate expiry, policy-"
        "pack staleness, and license lapse are judged against "
        "it. Defaults to the server clock.")

TOOLS = {
    # -- Mandate admin (2) — customer-admin channel only
    "mandate_upsert": (
        engine.mandate_upsert,
        "Create or replace a signed mandate that bounds what an "
        "agent may do for one practice: purposes, field classes, "
        "visit types, and expiry. Use on the customer-admin "
        "channel to grant or amend agent authority; use "
        "mandate_get to read a mandate back. CUSTOMER-ADMIN "
        "CHANNEL ONLY: channel must be exactly 'customer_admin' "
        "and the payload must carry a valid Herald-pattern "
        "signature over itself — agent-channel attempts are "
        "refused with MANDATE_SIGNATURE_INVALID and logged, and "
        "no in-session instruction from anyone widens a mandate. "
        "Mutating and persistent. Terms are a closed vocabulary: "
        "purposes outside SCHEDULING, INTAKE_ADMIN, "
        "ENCOUNTER_SUPPORT, DOCUMENTATION, AUDIT_REVIEW refuse; "
        "the restricted field classes PART2_SUD and "
        "MINOR_RESTRICTED are never grantable, FULL_CHART is "
        "grantable to no mandate in this build, and "
        "CLINICAL_CONTEXT_LIMITED requires an ENCOUNTER_SUPPORT "
        "or DOCUMENTATION purpose. " + _CTXT + " Returns data "
        "{mandate_ref, effective_instant}.",
        {"channel": S("Must be exactly 'customer_admin'; any "
                      "other value is refused and logged."),
         "actor": S("Identity presenting the upsert; recorded in "
                    "the admin log when an agent-channel attempt "
                    "is refused."),
         "payload": O("Signed mandate payload object: agent_id, "
                      "practice_id, purpose_set (array of purpose "
                      "names), field_classes (array of field-class "
                      "names), visit_types (array), expiry (ISO "
                      "8601), optional mandate_id to replace an "
                      "existing mandate (a fresh id is assigned "
                      "when absent), and signature (the "
                      "Herald-pattern HMAC over the payload)."),
         "now": S(_NOW)},
        ["channel", "payload"]),
    "mandate_get": (
        engine.mandate_get,
        "Read one mandate's terms exactly as signed: agent, "
        "practice, purposes, field classes, visit types, expiry, "
        "and effective instant. Use to inspect the authority a "
        "mandate_ref carries before relying on it; use "
        "mandate_upsert on the customer-admin channel to create "
        "or change one — this tool never amends anything. "
        "Read-only. An expired mandate is a refusal "
        "(MANDATE_EXPIRED), not a returned record, and an unknown "
        "ref refuses MANDATE_NOT_FOUND. " + _CTXT + " Returns "
        "data {mandate} with the full stored mandate object.",
        {"mandate_ref": S("Mandate id returned by mandate_upsert."),
         "now": S(_NOW)}, ["mandate_ref"]),
    # -- Jurisdiction (3)
    "license_verify_at_encounter": (
        engine.license_verify_at_encounter,
        "Verify, at encounter time and never from cache, that "
        "the treating clinician's license or privilege covers "
        "the patient's attested state, reading the roster feed "
        "fresh on every call. Use for a standalone licensure "
        "question; use encounter_gate_check or "
        "encounter_schedule when the check is part of gating an "
        "encounter (they run this same verification inside the "
        "gate). Every call mints a new verification id — results "
        "are never reused. Fail closed: no license fact in the "
        "feed refuses LICENSE_STATE_UNVERIFIED rather than "
        "inventing coverage, a fact for a different state or a "
        "lapsed/non-ACTIVE status refuses JURISDICTION_MISMATCH, "
        "and every verdict cites the fact, its source, and its "
        "as-of. " + _CTXT + " Returns data {verification, "
        "covered} on success.",
        {"clinician_id": S("Clinician identifier as it appears "
                           "in the roster feed."),
         "patient_state": S("Patient's attested state, e.g. "
                            "'CA'; coverage is judged against "
                            "exactly this state."),
         "profession_class": S("Profession class to verify, "
                               "e.g. 'MD'; the feed fact must "
                               "match this class."),
         "visit_type": S("Visit type for the encounter, recorded "
                         "on the verification."),
         "encounter_ref": S("Encounter reference recorded on "
                            "the verification, when known."),
         "mandate_ref": S("Mandate reference carried onto the "
                          "verdict envelope for traceability."),
         "now": S(_NOW)},
        ["clinician_id", "patient_state", "profession_class"]),
    "consent_set_required": (
        engine.consent_set_required,
        "List the consent artifacts a counsel-signed policy pack "
        "requires for one jurisdiction and visit type, with the "
        "pack version stamped on the answer. Use to learn what "
        "must be collected before an encounter; use "
        "encounter_gate_check to test a specific draft against "
        "the full gate. Read-only: policy packs are versioned "
        "signed data read fresh from disk on every call — this "
        "tool evaluates no patient and collects nothing. A "
        "jurisdiction with no pack refuses "
        "JURISDICTION_NOT_IN_PACK; an unsigned or stale pack "
        "refuses POLICY_PACK_UNSIGNED or POLICY_PACK_STALE. " +
        _CTXT + " Returns data {jurisdiction, visit_type, "
        "required_consents, recording_consent_model, "
        "ai_disclosure_required, synthetic_pack}.",
        {"jurisdiction": S("Jurisdiction (state) whose policy "
                           "pack applies, e.g. 'CA'."),
         "visit_type": S("Visit type declared by the pack for "
                         "that jurisdiction."),
         "recording_requested": B("When true, the 'recording' "
                                  "artifact is added to the "
                                  "required set."),
         "now": S(_NOW)},
        ["jurisdiction", "visit_type"]),
    "encounter_gate_check": (
        engine.encounter_gate_check,
        "Dry-run the full encounter gate (licensure x consent "
        "set x mandate) against an encounter draft without "
        "booking anything. Use to pre-test a draft any number of "
        "times before committing; use encounter_schedule to "
        "actually book on a pass — a dry-run pass confers zero "
        "authority and books nothing. Side effect on refusal "
        "only: a refused dry-run that touches protected data "
        "still seals an access record, so dry-runs are auditable "
        "events; a passing dry-run changes no state. " + _CTXT +
        " Returns the gate verdict in data, with dry_run=true, "
        "including which gate element refused when ok is false.",
        {"encounter_draft": O("Draft object: clinician_id, "
                              "patient_ref, patient_state, "
                              "visit_type, and mandate_ref are "
                              "required inside the draft; "
                              "practice_id, profession_class "
                              "(defaults to MD), attestation_source, "
                              "consent_artifacts, participants, "
                              "and recording_requested are "
                              "optional."),
         "now": S(_NOW)}, ["encounter_draft"]),
    # -- Encounter flow (3)
    "encounter_schedule": (
        engine.encounter_schedule,
        "Book an encounter, but only on a full gate pass at "
        "schedule time against the booking-time attestation. "
        "Use when a draft is ready to commit; use "
        "encounter_gate_check first to dry-run the same draft "
        "without booking. Mutating on success only: creates the "
        "encounter in SCHEDULED state and opens its encounter "
        "pack with the booking gate verdict recorded. A failed "
        "gate books nothing — there is no book-now-verify-later "
        "state — and the refusal is sealed as an access record "
        "when it touches protected data. " + _CTXT + " Returns "
        "data {encounter_id, encounter_state: SCHEDULED, gate} "
        "on success.",
        {"encounter_draft": O("Draft object: clinician_id, "
                              "patient_ref, patient_state, "
                              "visit_type, and mandate_ref are "
                              "required inside the draft; "
                              "practice_id, profession_class "
                              "(defaults to MD), attestation_source "
                              "(defaults to patient-stated), "
                              "consent_artifacts, participants, "
                              "and recording_requested are "
                              "optional."),
         "now": S(_NOW)}, ["encounter_draft"]),
    "encounter_start_gate": (
        engine.encounter_start_gate,
        "Authorize an encounter to start by re-running the FULL "
        "gate (licensure, consent set, mandate) against a fresh "
        "location attestation. Use at encounter start for an "
        "encounter booked by encounter_schedule; a booking-time "
        "pass confers zero authority here, and a patient state "
        "that changed since booking is re-gated in full. "
        "Mutating: on a pass the encounter moves to STARTED and "
        "the start verdict joins the encounter pack; on a "
        "refusal the verdict and a sealed access record join "
        "the pack and the encounter does not start. Unknown "
        "encounter_ref refuses ENCOUNTER_NOT_FOUND. " + _CTXT +
        " Returns the gate verdict in data, including "
        "location_changed, start_attestation, and "
        "encounter_state.",
        {"encounter_ref": S("Encounter id returned by "
                            "encounter_schedule."),
         "patient_state": S("Freshly attested patient state at "
                            "start time; compared with the "
                            "booking attestation."),
         "attestation_source": S("How the fresh location was "
                                 "attested; defaults to "
                                 "patient-stated."),
         "consent_artifacts": A("Consent artifacts presented at "
                                "start; defaults to the artifacts "
                                "recorded at booking.",
                                items={"type": "string"}),
         "now": S(_NOW)},
        ["encounter_ref", "patient_state"]),
    "encounter_pack_get": (
        engine.encounter_pack_get,
        "Read the sealed encounter pack for one encounter: both "
        "gate verdicts (booking and start), consent artifacts, "
        "access-record references, policy pack versions, and "
        "the pin statement (NO_PIN_RECORD while no signed note "
        "exists). Use for the encounter's evidence trail; use "
        "access_pack_get instead for a practice's protected-data "
        "access records. Read-only. Unknown encounter_ref "
        "refuses ENCOUNTER_NOT_FOUND. " + _CTXT + " Returns data "
        "{encounter_pack, encounter_state}.",
        {"encounter_ref": S("Encounter id returned by "
                            "encounter_schedule.")},
        ["encounter_ref"]),
    # -- Intake administration (2) — read-only in P0
    "intake_template_get": (
        engine.intake_template_get,
        "Read the practice-configured intake template for one "
        "visit type: administrative fields only — the template "
        "contains no clinical-answer content. Use before "
        "directing a patient through intake; use "
        "intake_status_get for a specific intake's completion "
        "state. Read-only and mandate-free (templates are "
        "practice configuration, not patient data). Unknown "
        "practice/visit-type pairs refuse TEMPLATE_NOT_FOUND. " +
        _CTXT + " Returns data {template, note}.",
        {"practice_id": S("Practice whose configured template "
                          "to read."),
         "visit_type": S("Visit type the template is configured "
                         "for.")},
        ["practice_id", "visit_type"]),
    "intake_status_get": (
        engine.intake_status_get,
        "Read one intake's completion state, section by section, "
        "exactly as recorded and source-labeled. Use to check "
        "what a patient has completed; use intake_template_get "
        "for the template itself. This is a protected-data read: "
        "it runs under the declared purpose and mandate, and "
        "every call — granted or refused — seals an access "
        "record, so it is not side-effect free. No inference is "
        "performed: completion state is reported as recorded, "
        "with no eligibility or coverage determination, and "
        "clinically-shaped requests refuse CLINICAL_REQUEST for "
        "the treating clinician. " + _CTXT + " Returns data "
        "{intake_ref, sections, note} on success.",
        {"intake_ref": S("Intake reference to read."),
         "purpose": S("Declared purpose from the closed "
                      "vocabulary (SCHEDULING, INTAKE_ADMIN, "
                      "ENCOUNTER_SUPPORT, DOCUMENTATION, "
                      "AUDIT_REVIEW); must be granted by the "
                      "mandate. There is no default purpose."),
         "mandate_ref": S("Acting mandate; must be live and "
                          "cover the intake's patient tenant."),
         "agent_id": S("Agent identity recorded on the sealed "
                       "access record; defaults to 'agent'."),
         "now": S(_NOW)},
        ["intake_ref", "purpose", "mandate_ref"]),
    # -- Documentation flow (5) — the chain ends at the clinician
    "note_draft_create": (
        engine.note_draft_create,
        "Create a new encounter-note draft, or edit an unsigned "
        "draft, in the DRAFT STORE — outside the clinical "
        "record. Use to draft or revise note content; use "
        "note_addendum_create instead for any change to a "
        "signed note, and note_sign_request when a draft is "
        "ready for the clinician. Mutating and persistent: a "
        "new draft gets a fresh draft id and a canonical "
        "fingerprint; an edit (edit_of_draft_id) increments the "
        "draft version and re-fingerprints it. Every draft "
        "carries the standing status UNSIGNED_DRAFT_NOT_RECORD "
        "and no chart or record read ever returns a draft. "
        "Editing a draft whose state is signed refuses "
        "ADDENDUM_REQUIRED. Clinically-shaped content requests "
        "refuse CLINICAL_REQUEST. The encounter must exist "
        "(ENCOUNTER_NOT_FOUND otherwise). " + _CTXT + " Returns "
        "data {draft, standing_status, note}.",
        {"encounter_ref": S("Encounter the note belongs to; "
                            "must already be scheduled."),
         "content": S("Draft note content as text; stored "
                      "verbatim and fingerprinted in canonical "
                      "form (line endings and per-line "
                      "whitespace normalized for the fingerprint "
                      "only)."),
         "drafting_agent_id": S("Authoring agent identity, bound "
                                "into the draft and its "
                                "fingerprint."),
         "edit_of_draft_id": S("Existing unsigned draft id to "
                               "edit in place (version increments); "
                               "omit to create a new draft.")},
        ["encounter_ref", "drafting_agent_id"]),
    "note_fingerprint_get": (
        engine.note_fingerprint_get,
        "Compute the canonical fingerprint (SHA-256 under the "
        "meridian-canon-v1 profile: sorted fields, normalized "
        "whitespace and line endings, author, encounter, and "
        "draft version bound in) of a draft's exact current "
        "state. Use to obtain the fingerprint note_sign_request "
        "must route; use note_signed_status_get for a note's "
        "signing state. Read-only: computing the fingerprint "
        "changes nothing, and the same draft state always "
        "yields the same fingerprint. Unknown draft_ref refuses "
        "DRAFT_NOT_FOUND. " + _CTXT + " Returns data {draft_ref, "
        "fingerprint, profile, draft_version}.",
        {"draft_ref": S("Draft id returned by note_draft_create "
                        "or note_addendum_create.")},
        ["draft_ref"]),
    "note_sign_request": (
        engine.note_sign_request,
        "Route a draft's exact fingerprint to the treating "
        "clinician for signature in the system of record. Use "
        "when a draft is final from the agent's side; use "
        "note_fingerprint_get first to obtain the current "
        "fingerprint. THIS TOOL SIGNS NOTHING — no tool in this "
        "server signs or finalizes a note; signature is the "
        "treating clinician's act in the system of record. "
        "Mutating: records a sign request in AWAITING_SIGNATURE "
        "and moves the draft to AWAITING_SIGNATURE. The "
        "supplied fingerprint must equal the draft's current "
        "fingerprint exactly — a draft edited after the request "
        "was composed refuses NOTE_FINGERPRINT_MISMATCH. " +
        _CTXT + " Returns data {sign_request_id, "
        "routed_fingerprint, routed_to, note}.",
        {"draft_ref": S("Draft id to route for signature."),
         "fingerprint": S("Fingerprint from "
                          "note_fingerprint_get; must match the "
                          "draft's current fingerprint exactly."),
         "treating_clinician_ref": S("Treating clinician the "
                                     "request is routed to.")},
        ["draft_ref", "fingerprint", "treating_clinician_ref"]),
    "note_signed_status_get": (
        engine.note_signed_status_get,
        "Read the signing state of a draft or note reference: "
        "UNSIGNED_DRAFT with its current fingerprint, or SIGNED "
        "with the signed note, its signed fingerprint and "
        "signer, and the addendum/supersession chain. Use to "
        "learn whether a note was signed and what supersedes "
        "it; use note_fingerprint_get for an unsigned draft's "
        "fingerprint alone. Read-only. A reference that is "
        "neither a draft nor a signed note refuses "
        "DRAFT_NOT_FOUND. " + _CTXT + " Returns data {status, "
        "note, chain, superseded} for signed notes or {status, "
        "standing_status, fingerprint} for drafts.",
        {"note_ref": S("Draft id or signed-note id to inspect.")},
        ["note_ref"]),
    "note_addendum_create": (
        engine.note_addendum_create,
        "Create an addendum draft chained to a signed note — "
        "the ONLY post-signature change path in this server. "
        "Use for any correction or addition after signature; "
        "never try to edit the signed note (signed states are "
        "immutable and note_draft_create refuses them with "
        "ADDENDUM_REQUIRED). Mutating: creates a NEW unsigned "
        "draft for the same encounter, citing the signed "
        "original's fingerprint; the original remains served "
        "exactly as signed, and the addendum is itself only a "
        "draft until the clinician signs it under the same pin "
        "rule. Unknown signed_note_ref refuses DRAFT_NOT_FOUND. "
        + _CTXT + " Returns data {draft, standing_status, "
        "cites_fingerprint, addendum_for_note}.",
        {"signed_note_ref": S("Signed note id the addendum "
                              "chains to."),
         "content": S("Addendum draft content as text."),
         "drafting_agent_id": S("Authoring agent identity, "
                                "bound into the addendum draft "
                                "and its fingerprint.")},
        ["signed_note_ref", "content", "drafting_agent_id"]),
    # -- PHI reads + access evidence (4)
    "patient_context_get": (
        engine.patient_context_get,
        "Read projected patient context: only the field classes "
        "the acting mandate grants for the declared purpose, "
        "projected server-side before anything is serialized — "
        "ungranted classes never leave the server, and asking "
        "for one refuses FIELD_CLASS_NOT_GRANTED for the whole "
        "call (tool parameters never widen a mandate). Use for "
        "patient context under a purpose; use appointment_get "
        "for appointment data specifically. Side effect on "
        "every call: granted or refused, the call seals a "
        "hash-chained access record (in the shape of a "
        "164.312(b) audit record) naming agent, classes "
        "requested and returned, purpose, and verdict — so this "
        "read is auditable, not silent. Restricted classes "
        "(PART2_SUD, MINOR_RESTRICTED) are never returned. " +
        _CTXT + " Returns data {patient_ref, classes_returned, "
        "context} on success, with access_record_ref set.",
        {"patient_ref": S("Patient reference within the "
                          "mandate's tenant scope."),
         "purpose": S("Declared purpose from the closed "
                      "vocabulary (SCHEDULING, INTAKE_ADMIN, "
                      "ENCOUNTER_SUPPORT, DOCUMENTATION, "
                      "AUDIT_REVIEW); must be in the mandate's "
                      "purpose set. There is no default purpose."),
         "mandate_ref": S("Acting mandate; must be live and "
                          "tenant-matched to the patient."),
         "requested_classes": A("Field classes requested, e.g. "
                                "IDENTITY_CONTACT, APPOINTMENT, "
                                "INSURANCE_ADMIN, "
                                "CLINICAL_CONTEXT_LIMITED. Omit "
                                "to request every class the "
                                "patient record carries (still "
                                "intersected with the mandate).",
                                items={"type": "string"}),
         "agent_id": S("Agent identity sealed into the access "
                       "record; defaults to the mandate's agent."),
         "now": S(_NOW)},
        ["patient_ref", "purpose", "mandate_ref"]),
    "appointment_get": (
        engine.appointment_get,
        "Read one appointment's data within the field classes "
        "the acting mandate grants for the declared purpose. "
        "Use for a known appointment reference; use "
        "patient_context_get for broader patient context. Like "
        "every protected-data read here, the call seals an "
        "access record whether granted or refused. Requesting "
        "any class beyond the mandate refuses the whole call "
        "with FIELD_CLASS_NOT_GRANTED and returns nothing; an "
        "unknown appointment_ref refuses with the same class so "
        "existence is not leaked across tenants. " + _CTXT +
        " Returns data {appointment_ref, classes_returned, "
        "appointment} on success, with access_record_ref set.",
        {"appointment_ref": S("Appointment reference to read."),
         "purpose": S("Declared purpose from the closed "
                      "vocabulary (SCHEDULING, INTAKE_ADMIN, "
                      "ENCOUNTER_SUPPORT, DOCUMENTATION, "
                      "AUDIT_REVIEW); must be granted by the "
                      "mandate."),
         "mandate_ref": S("Acting mandate; must be live and "
                          "tenant-matched."),
         "requested_classes": A("Field classes requested from "
                                "the appointment; defaults to "
                                "all classes the appointment "
                                "carries.",
                                items={"type": "string"}),
         "now": S(_NOW)},
        ["appointment_ref", "purpose", "mandate_ref"]),
    "access_pack_get": (
        engine.access_pack_get,
        "Export the acting practice's own access pack: its "
        "hash-chained access records, also mapped to FHIR "
        "AuditEvent plus Provenance resources. Use to hand the "
        "practice its audit evidence or to feed "
        "access_pack_verify; audit reads declare purpose "
        "AUDIT_REVIEW — any other declared purpose refuses "
        "(PURPOSE_NOT_GRANTED), and no purpose refuses "
        "PURPOSE_NOT_DECLARED. Read-only and tenant-isolated: "
        "the pack contains ONLY the requesting mandate's "
        "practice's records, never another tenant's, and "
        "practice_id must equal the mandate's practice. " +
        _CTXT + " Returns data {records, fhir_audit_events, "
        "provenance}.",
        {"practice_id": S("Practice to export; must equal the "
                          "acting mandate's practice."),
         "mandate_ref": S("Acting mandate; must be live and "
                          "carry AUDIT_REVIEW in its purpose "
                          "set."),
         "purpose": S("Must be AUDIT_REVIEW for this tool."),
         "patient_ref": S("Optional filter: only records for "
                          "this patient."),
         "now": S(_NOW)},
        ["practice_id", "mandate_ref", "purpose"]),
    "access_pack_verify": (
        engine.access_pack_verify,
        "Verify an exported access pack offline: hash-chain "
        "integrity per record plus completeness over a declared "
        "expected interval. Use on a pack obtained from "
        "access_pack_get before relying on it as evidence; this "
        "tool reads no server state and trusts nothing in the "
        "pack. Read-only and offline: verification is over the "
        "supplied object only. Gaps are findings, never "
        "silently passed — a chain break, an altered record, a "
        "non-object record, or an interval the records do not "
        "cover each appears as a named finding, and ok is false "
        "when any finding exists (findings are reported as "
        "data, not as a refusal_class). " + _CTXT + " Returns "
        "data {chain_ok, findings, records_checked}.",
        {"access_pack": O("Exported pack object as returned in "
                          "access_pack_get's data, carrying a "
                          "records array."),
         "expected_interval": O("Optional {from, to} ISO 8601 "
                                "interval the pack must cover; "
                                "an uncovered interval is an "
                                "INTERVAL_INCOMPLETE finding.")},
        ["access_pack"]),
}


ANNOTATIONS = {
    "mandate_upsert": _ann(False),
    "mandate_get": _ann(True, idempotent=True),
    # fresh verification per call; advances the verification sequence
    "license_verify_at_encounter": _ann(False),
    "consent_set_required": _ann(True, idempotent=True),
    # seals an access record when a dry-run refuses
    "encounter_gate_check": _ann(False),
    "encounter_schedule": _ann(False),
    "encounter_start_gate": _ann(False),
    "encounter_pack_get": _ann(True, idempotent=True),
    "intake_template_get": _ann(True, idempotent=True),
    # seals an access record on every call
    "intake_status_get": _ann(False),
    "note_draft_create": _ann(False),
    "note_fingerprint_get": _ann(True, idempotent=True),
    "note_sign_request": _ann(False),
    "note_signed_status_get": _ann(True, idempotent=True),
    "note_addendum_create": _ann(False),
    # seals an access record on every call
    "patient_context_get": _ann(False),
    # seals an access record on every call
    "appointment_get": _ann(False),
    "access_pack_get": _ann(True, idempotent=True),
    "access_pack_verify": _ann(True, idempotent=True),
}

_DSTR = {"type": "string", "description": "Value as stored."}
OUTPUT_SCHEMAS = {
    "mandate_upsert": _vout({
        "mandate_ref": S("Mandate id created or replaced."),
        "effective_instant": S("ISO 8601 instant the mandate "
                               "took effect.")},
        ["mandate_ref", "effective_instant"]),
    "mandate_get": _vout({
        "mandate": O("Full mandate object exactly as signed: "
                     "agent_id, practice_id, purpose_set, "
                     "field_classes, visit_types, expiry, "
                     "effective_instant, signature_verified.")},
        ["mandate"]),
    "license_verify_at_encounter": _vout({
        "verification": O("Verification record: verification_id, "
                          "clinician_id, state, profession_class, "
                          "visit_type, encounter_ref, as_of, "
                          "status, source."),
        "covered": {"type": "boolean",
                    "description": "True when the license fact "
                                   "covers the attested state."}},
        ["verification", "covered"]),
    "consent_set_required": _vout({
        "jurisdiction": S("Jurisdiction evaluated."),
        "visit_type": S("Visit type evaluated."),
        "required_consents": A("Consent artifact names required.",
                               items={"type": "string"}),
        "recording_consent_model": _DSTR,
        "ai_disclosure_required": {"type": "boolean",
                                   "description": "Whether the pack "
                                                  "requires AI "
                                                  "disclosure."},
        "synthetic_pack": {"type": "boolean",
                           "description": "Whether the pack in "
                                          "force is a synthetic "
                                          "fixture pack."}},
        ["required_consents"]),
    "encounter_gate_check": _vout({
        "dry_run": {"type": "boolean",
                    "description": "Always true for this tool."},
        "failing_check": {"type": ["string", "null"],
                          "description": "Gate element that "
                                         "refused (e.g. A1), or "
                                         "null on a pass."},
        "phase": S("Gate phase: dry_run."),
        "license_verification": O("Fresh license verification "
                                  "record on a pass."),
        "consents_required": A("Consent artifacts the pack "
                               "requires.", items={"type": "string"}),
        "recording_state": S("RECORDING_ON or RECORDING_OFF.")},
        ["dry_run"]),
    "encounter_schedule": _vout({
        "encounter_id": S("Booked encounter id."),
        "encounter_state": S("SCHEDULED on success."),
        "gate": O("Booking gate verdict envelope.")},
        ["encounter_id", "encounter_state"]),
    "encounter_start_gate": _vout({
        "location_changed": {"type": "boolean",
                             "description": "Whether the start "
                                            "attestation differs "
                                            "from booking."},
        "start_attestation": O("Start attestation recorded: "
                               "state, source, attested_at, phase."),
        "encounter_state": S("STARTED on a pass; otherwise the "
                             "encounter's unchanged state."),
        "failing_check": {"type": ["string", "null"],
                          "description": "Gate element that "
                                         "refused, or null on a "
                                         "pass."},
        "license_verification": O("Fresh license verification "
                                  "record on a pass.")}),
    "encounter_pack_get": _vout({
        "encounter_pack": O("Pack object: pack_id, encounter_id, "
                            "gate_verdicts, consent_artifacts, "
                            "access_record_refs, pack_versions, "
                            "pin_statement."),
        "encounter_state": S("Current encounter state.")},
        ["encounter_pack", "encounter_state"]),
    "intake_template_get": _vout({
        "template": O("Template object: administrative fields "
                      "for the practice and visit type."),
        "note": S("Administrative-fields-only note.")},
        ["template"]),
    "intake_status_get": _vout({
        "intake_ref": S("Intake read."),
        "sections": A("Per-section completion state, "
                      "source-labeled.", items={"type": "object"}),
        "note": S("No-inference note.")},
        ["intake_ref", "sections"]),
    "note_draft_create": _vout({
        "draft": O("Draft object: draft_id, encounter_id, "
                   "author_agent_id, content, version, state, "
                   "signed, fingerprint, created_at."),
        "standing_status": S("UNSIGNED_DRAFT_NOT_RECORD."),
        "note": S("Draft-store note.")},
        ["draft", "standing_status"]),
    "note_fingerprint_get": _vout({
        "draft_ref": S("Draft fingerprinted."),
        "fingerprint": S("SHA-256 canonical fingerprint."),
        "profile": S("Canonicalization profile: meridian-canon-v1."),
        "draft_version": {"type": "integer",
                          "description": "Draft version the "
                                         "fingerprint binds."}},
        ["fingerprint"]),
    "note_sign_request": _vout({
        "sign_request_id": S("Sign request id."),
        "routed_fingerprint": S("Fingerprint routed to the "
                                "clinician."),
        "routed_to": S("Treating clinician reference."),
        "note": S("Routed-only note: this tool signs nothing.")},
        ["sign_request_id", "routed_fingerprint"]),
    "note_signed_status_get": _vout({
        "status": S("UNSIGNED_DRAFT or SIGNED."),
        "note": O("Signed note object when signed."),
        "chain": A("Addendum/supersession chain of signed notes.",
                   items={"type": "object"}),
        "superseded": {"type": "boolean",
                       "description": "Whether the note is "
                                      "superseded by a later one."},
        "standing_status": S("UNSIGNED_DRAFT_NOT_RECORD for "
                             "drafts."),
        "fingerprint": S("Current fingerprint for drafts.")},
        ["status"]),
    "note_addendum_create": _vout({
        "draft": O("New addendum draft object."),
        "standing_status": S("UNSIGNED_DRAFT_NOT_RECORD."),
        "cites_fingerprint": S("Signed original's fingerprint "
                               "the addendum cites."),
        "addendum_for_note": S("Signed note id the addendum "
                               "chains to.")},
        ["draft", "cites_fingerprint", "addendum_for_note"]),
    "patient_context_get": _vout({
        "patient_ref": S("Patient read."),
        "classes_returned": A("Field classes actually returned "
                              "after projection.",
                              items={"type": "string"}),
        "context": O("Projected field-class values, keyed by "
                     "class name.")},
        ["patient_ref", "classes_returned", "context"]),
    "appointment_get": _vout({
        "appointment_ref": S("Appointment read."),
        "classes_returned": A("Field classes returned.",
                              items={"type": "string"}),
        "appointment": O("Projected appointment values, keyed by "
                         "class name.")},
        ["appointment_ref", "classes_returned", "appointment"]),
    "access_pack_get": _vout({
        "records": A("Hash-chained access records for the "
                     "practice only.", items={"type": "object"}),
        "fhir_audit_events": A("The same records mapped to FHIR "
                               "AuditEvent resources.",
                               items={"type": "object"}),
        "provenance": O("Provenance resource for the export.")},
        ["records", "fhir_audit_events"]),
    "access_pack_verify": _vout({
        "chain_ok": {"type": "boolean",
                     "description": "Whether the hash chain "
                                    "verifies end to end."},
        "findings": A("Named findings; empty means the pack "
                      "verified.", items={"type": "object"}),
        "records_checked": {"type": "integer",
                            "description": "Records verified."}},
        ["chain_ok", "findings", "records_checked"]),
}


def tool_list():
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "properties": props,
                             "required": required},
             "annotations": ANNOTATIONS[name],
             "outputSchema": OUTPUT_SCHEMAS[name]}
            for name, (_, description, props, required)
            in TOOLS.items()]


def handle(req: dict):
    method, rid = req.get("method", ""), req.get("id")
    if method == "initialize":
        init_params = req.get("params")
        if not isinstance(init_params, dict):
            init_params = {}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": init_params.get(
                "protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eclipse-meridian",
                           "title": engine.DESCRIPTOR,
                           "version": engine.VERSION}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"tools": tool_list()}}
    if method == "tools/call":
        params = req.get("params")
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32602,
                "message": "params must be an object"}}
        name = params.get("name")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32602,
                "message": "arguments must be an object"}}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32602,
                "message": f"unknown tool {name}: Meridian ships "
                           f"exactly 19 tools; no finalize/sign "
                           f"tool exists by product law (spec 6.2)"}}
        state = engine.load_state()
        try:
            engine.deep_key_scan(args)
            out = TOOLS[name][0](state, args)
            engine.save_state(state)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(out, indent=1)}],
                "isError": not out.get("ok", False)}}
        except ToolError as exc:
            engine.save_state(state)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(
                                 {"ok": False, "error": str(exc)})}],
                "isError": True}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": f"method not found: {method}"}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            if isinstance(req, dict):
                req_id = req.get("id")
            response = handle(req)
        except Exception as exc:  # never crash the transport;
            # the response ALWAYS carries the request id when the
            # request carried one — an id-less error is a defect
            response = {"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32603, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

# Eclipse Meridian MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
