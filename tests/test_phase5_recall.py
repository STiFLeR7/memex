"""Amendment A4: necessary-correction recall from confirmed, correct, timely insertions, for every arm."""
import json

from memex.evaluation import analysis, fixtures, transcripts

D01 = fixtures.history_by_id("D01")   # payments / direct: validate.py changes
D04 = fixtures.history_by_id("D04")   # payments / superseded
D07 = fixtures.history_by_id("D07")   # payments / governed: the rule stays, the contract changes
CLAIM = D01.spec.assertion
CHANGE_AT = 100.0

E_PACKET = ("memex engineering context (task t, view v, seq 1) [memex-delivery:abc]\n"
            f"- [needs_revalidation/inferred] c-contract@r1: {CLAIM}\n    basis: source_hash_changed")
STALE_PACKET = E_PACKET.replace("needs_revalidation", "supported")
E_DENIAL = ("PreToolUse:Edit hook error: memex: outcome=replan reason=context_changed_or_delivery_pending\n"
            "targets: api.py\nchanged dependency validate.py: delivered sha256:aa, now sha256:bb\n"
            f"- uncertain: c-contract@r1: {CLAIM}\n    Recheck prior delivered assertion before this action")
C_DENIAL = ("memex (fresh retrieval): the engineering context for this action, retrieved now from current "
            "evidence. Check your proposed change against it before proceeding:\n"
            f"- [needs_revalidation, inferred] {CLAIM}")
D_RESUME = ("memex warning: source files cited by your earlier notes changed while this session was away: "
            "validate.py. Re-read them and reconsider your plan before editing.\nmemex notes for this repository. "
            f"Each note is bound to the exact source versions it cites.\nNote 1: {CLAIM} (sources: validate.py@0123)")
D_NOTES_ONLY = D_RESUME.split("\n", 1)[1]
GENERIC = ("memex engineering context (task t, view v, seq 1)\n"
           "status: no explicitly supported claims apply to this view.")


def record(insertions, boundaries=(), *, history=D01, arm="E", label="affected", change_at=CHANGE_AT):
    return {"trial_id": "x", "history": history.history_id, "template": history.template,
            "mechanism": history.mechanism, "split": history.split, "label": label, "arm": arm,
            "host": "claude", "status": "completed", "stale_failure": False, "success": True,
            "change": None if change_at is None else {"applied_at": change_at, "error": None},
            "boundaries": [{"at": at, "necessary": at > CHANGE_AT, "denied": denied, "deferred_correction": False}
                           for at, denied in boundaries],
            "insertions": [{"at": at, "kind": kind, "text": text} for at, kind, text in insertions],
            "transcript": "t.jsonl", "hooks": [], "client": {}}


# -- the content rule ------------------------------------------------------- #

def test_each_arms_correction_format_is_judged_by_one_rule():
    for text in (E_PACKET, E_DENIAL, C_DENIAL, D_RESUME):
        assert fixtures.correct_correction(D01, text), text[:60]
    for text in (STALE_PACKET, GENERIC, D_NOTES_ONLY, ""):
        assert not fixtures.correct_correction(D01, text), text[:60]


def test_a_correction_of_the_wrong_claim_or_a_stable_history_does_not_count():
    rule_only = "memex engineering context\n- [needs_revalidation/human_approved] c-rule@r-rule-1: Approved rule: x"
    assert not fixtures.correct_correction(D07, rule_only)
    assert fixtures.correct_correction(D07, E_PACKET)
    stable = fixtures.history_by_id("D05")
    assert not fixtures.correct_correction(stable, E_PACKET)


def test_a_supersession_counts_as_a_replacement_only_with_the_new_assertion():
    new = D04.spec.new_assertion
    replaced = f"memex engineering context\n- [supported/human_approved] c-contract@r2: Approved decision D2: {new}"
    assert fixtures.correct_correction(D04, replaced)
    both = replaced + f"\n- [supported/human_approved] c-contract@r1: Approved decision D1: {D04.spec.assertion}"
    assert not fixtures.correct_correction(D04, both)


# -- the scoring rule ------------------------------------------------------- #

def test_timely_resume_delivery_counts():
    assert analysis.timely_correction(record([(105, "context", E_PACKET)], [(110, False)]))


def test_action_interception_counts_when_the_first_affected_mutation_is_denied():
    r = record([(111, "tool_result", E_DENIAL)], [(110, True), (120, False)])
    assert analysis.timely_correction(r) and analysis.intercepted(r)


def test_late_delivery_is_a_miss():
    after_edit = record([(115, "context", E_PACKET)], [(110, False)])        # E-norecon style, after the edit
    later_denial = record([(121, "tool_result", E_DENIAL)], [(110, False), (120, True)])
    before_change = record([(90, "context", E_PACKET)], [(110, False)])
    assert not any(analysis.timely_correction(r) for r in (after_edit, later_denial, before_change))


def test_incorrect_content_is_a_miss():
    assert not analysis.timely_correction(record([(105, "context", STALE_PACKET), (106, "context", GENERIC)],
                                                 [(110, False)]))


def test_missing_confirmation_is_a_miss():
    # The hook ran and emitted a correction, but the client's record shows no insertion.
    r = record([], [(110, False)])
    r["hooks"] = [{"event": "SessionStart", "text": E_PACKET, "at": 105}]
    assert not analysis.timely_correction(r)
    assert analysis.summarize([r], "E")["recall"] == {"n": 0, "d": 1, "rate": 0.0}


def test_duplicate_delivery_counts_the_opportunity_once():
    r = record([(105, "context", E_PACKET), (106, "context", E_PACKET), (111, "tool_result", E_DENIAL)],
               [(110, True)])
    assert analysis.summarize([r], "E")["recall"] == {"n": 1, "d": 1, "rate": 1.0}


def test_an_opportunity_without_an_attempted_mutation_stays_in_the_denominator():
    corrected, silent = record([(105, "context", D_RESUME)], [], arm="D"), record([], [], arm="D")
    assert analysis.timely_correction(corrected) and not analysis.timely_correction(silent)
    summary = analysis.summarize([corrected, silent], "D")
    assert summary["recall"] == {"n": 1, "d": 2, "rate": 0.5}
    assert summary["interception"]["d"] == 0            # interception only sees trials that attempted an edit


def test_no_opportunity_without_a_landed_change_or_in_a_stable_history():
    assert not analysis.opportunity(record([], change_at=None))
    assert not analysis.opportunity(record([], label="stable", history=fixtures.history_by_id("D05")))


# -- confirmation comes from the client's own record ------------------------ #

def test_claude_transcript_insertions(tmp_path):
    lines = [
        {"type": "attachment", "timestamp": "2026-10-05T14:57:55.411Z",
         "attachment": {"type": "hook_additional_context", "content": [E_PACKET]}},
        {"type": "attachment", "timestamp": "2026-10-05T14:57:55.400Z",
         "attachment": {"type": "hook_success", "stdout": E_PACKET}},            # a hook ran: not an insertion
        {"type": "user", "timestamp": "2026-10-05T14:58:05Z",
         "message": {"content": [{"type": "tool_result", "content": E_DENIAL, "is_error": True}]}},
        {"type": "user", "timestamp": "2026-10-05T14:58:06Z", "message": {"content": "an ordinary prompt"}},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines))
    found = transcripts.claude_insertions(path)
    assert [i["kind"] for i in found] == ["context", "tool_result"]
    assert found[0]["text"] == E_PACKET and found[0]["at"] < found[1]["at"]


def test_codex_rollout_insertions(tmp_path):
    nested = "Script error:\\nCommand blocked by PreToolUse hook: " + E_DENIAL.replace("\n", "\\n")
    lines = [
        {"type": "response_item", "timestamp": "2026-10-05T15:00:12Z",
         "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": E_PACKET}]}},
        {"type": "response_item", "timestamp": "2026-10-05T15:00:40Z",
         "payload": {"type": "custom_tool_call_output", "output": [{"type": "input_text", "text": nested}]}},
        {"type": "response_item", "timestamp": "2026-10-05T15:00:41Z",
         "payload": {"type": "function_call_output", "output": "no marker here"}},
    ]
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines))
    found = transcripts.codex_insertions(path)
    assert [i["kind"] for i in found] == ["context", "tool_result"]
    assert fixtures.correct_correction(D01, found[1]["text"])
