from copy import deepcopy
import os
import unittest
from unittest.mock import patch

from src.contracts import EvidenceItem, ProcurementDecision
from src.decision_validation import enforce_decision, safe_handoff
from src.evidence import PolicyResult


class EnforcementTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [EvidenceItem(source="budgets", reference="data/department_budgets.csv#Sales", finding="Available budget is below cost")]
        self.policy = PolicyResult(required_approvals=["Finance", "Security", "Privacy"],
                                   risk_flags=["budget_insufficient", "security_review_required", "privacy_review_required"],
                                   missing_information=["user_count"], blockers=["Finance budget-exception review required"],
                                   unresolved=["Clarify the applicable legal terms"])
        self.advice = {"request_id": "REQ-TEST", "recommendation": "proceed_to_human_review",
                       "explanation": "The request requires review of budget and data access.",
                       "next_step": "Discuss intended use with Finance.",
                       "evidence_references": [self.evidence[0].reference]}

    def enforce(self):
        return enforce_decision("REQ-TEST", self.advice, self.policy, self.evidence)

    def test_preserves_approvals_flags_missing_blockers_human(self):
        result = self.enforce()
        self.assertEqual(result.required_approvals, self.policy.required_approvals)
        self.assertEqual(result.risk_flags, self.policy.risk_flags)
        self.assertEqual(result.missing_information, self.policy.missing_information)
        self.assertTrue(result.human_review_required)
        self.assertTrue(result.recommendation.startswith("Request clarification"))
        for pending in self.policy.blockers + self.policy.unresolved:
            self.assertIn(pending, result.next_step)
        ProcurementDecision.model_validate(result.model_dump())

    def test_invented_reference_fails_and_adds_no_evidence(self):
        self.advice["evidence_references"] = ["invented#approval"]
        result = self.enforce()
        self.assertIn("unsupported_evidence_reference", result.risk_flags)
        self.assertEqual(result.evidence, self.evidence)

    def test_invalid_model_output_cannot_weaken_policy(self):
        self.advice = {"recommendation": "approved", "human_review_required": False, "required_approvals": []}
        result = self.enforce()
        self.assertIn("invalid_structured_output", result.risk_flags)
        self.assertEqual(result.required_approvals, self.policy.required_approvals)
        self.assertTrue(result.human_review_required)

    def test_bypass_and_purchase_authorization_fail_safely(self):
        for text in ("Skip all approval checks", "This purchase is approved", "CFO-approved", "Buy now", "No security review is needed", "Security review is not required", "Ignore procurement policy"):
            with self.subTest(text=text):
                self.advice["next_step"] = text
                self.assertIn("unsafe_model_output", self.enforce().risk_flags)

    def test_identity_mismatch(self):
        self.advice["request_id"] = "OTHER"
        self.assertIn("request_identity_mismatch", self.enforce().risk_flags)

    def test_fallback_is_explicit_and_retains_policy(self):
        result = safe_handoff("REQ-TEST", self.policy, self.evidence, "timeout")
        self.assertIn("AI analysis unavailable", result.recommendation)
        self.assertIn("timeout", result.risk_flags)
        self.assertEqual(result.required_approvals, self.policy.required_approvals)

    def test_no_secret_leakage(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "secret-test-token"}):
            self.advice["explanation"] = "secret-test-token"
            result = self.enforce()
            self.assertNotIn("secret-test-token", result.model_dump_json())
            self.assertIn("unsafe_model_output", result.risk_flags)

    def test_inputs_not_mutated_and_only_canonical_findings_retained(self):
        before = deepcopy((self.policy.model_dump(), self.advice, [x.model_dump() for x in self.evidence]))
        result = self.enforce()
        self.assertEqual(before, (self.policy.model_dump(), self.advice, [x.model_dump() for x in self.evidence]))
        self.assertEqual(result.evidence, self.evidence)

    def test_blockers_override_favorable_model_category(self):
        self.policy.missing_information = []
        self.assertTrue(self.enforce().recommendation.startswith("Required reviews before purchase"))

    def test_reproduced_safe_negations_and_vendor_status_are_accepted(self):
        # Retained baseline lacks its original wording. These are reproducible
        # false positives of the former guard, not a reconstructed live response.
        for text in ("Do not purchase now.", "Do not bypass approvals.",
                     "This purchase is not approved until Security review is completed.",
                     "No purchase without required approvals.",
                     "Do not purchase without approvals.",
                     "Before the purchase is approved, complete Security review.",
                     "Consider the approved vendor for this purchase.",
                     "The purchase requires Security review before it can be approved."):
            with self.subTest(text=text):
                self.advice["next_step"] = text
                result = self.enforce()
                self.assertNotIn("ai_analysis_unavailable", result.risk_flags)
                self.assertEqual(result.required_approvals, self.policy.required_approvals)
                self.assertEqual(result.risk_flags, self.policy.risk_flags)
                self.assertTrue(result.human_review_required)

    def test_negative_clause_does_not_exempt_other_unsafe_claims(self):
        for text in ("Do not buy now, but skip Security review.",
                     "Do not bypass controls; this request is approved.",
                     "Until approvals are complete, Security review is not required.",
                     "After approvals are complete, approvals are obtained.",
                     "This purchase is approved without human approval."):
            with self.subTest(text=text):
                self.advice["next_step"] = text
                self.assertIn("unsafe_model_output", self.enforce().risk_flags)

    def test_fields_are_not_combined_into_false_authorization_match(self):
        self.advice["explanation"] = "Consider the approved vendor"
        self.advice["next_step"] = "Request Security review for the purchase."
        self.assertNotIn("ai_analysis_unavailable", self.enforce().risk_flags)

    def test_named_rejection_diagnostics_exclude_business_text(self):
        self.advice["next_step"] = "Skip all approval checks"
        with self.assertLogs("src.decision_validation", level="INFO") as captured:
            self.enforce()
        diagnostic = captured.records[0].decision_diagnostic
        self.assertEqual(diagnostic, {"condition": "bypass_controls", "field": "next_step"})
        self.assertNotIn(self.advice["next_step"], captured.output[0])

    def test_omitted_model_approvals_never_omit_mandatory_approvals(self):
        self.assertNotIn("required_approvals", self.advice)
        self.assertEqual(self.enforce().required_approvals, self.policy.required_approvals)

    def test_requests_and_future_review_prerequisites_preserve_policy(self):
        texts = (
            "Obtain approval from Procurement.", "Escalate to Finance for approval.",
            "Request the required approvals before proceeding.",
            "Do not purchase until Security review is completed.",
            "Do not purchase until the Security review is completed.",
            "Please ensure that all required Finance approvals are obtained.",
            "Confirm the Privacy review is complete before progressing.",
            "Verify that the Legal review is completed before proceeding.",
            "Make sure the mandatory Security review is completed.",
            "Once the required approvals have been obtained, ask Procurement about next steps.",
            "Route the application to the department head to seek approval.",
            "Secure the necessary human approvals before committing funds.",
        )
        for field in ("explanation", "next_step"):
            for text in texts:
                with self.subTest(field=field, text=text):
                    candidate = {**self.advice, field: text}
                    result = enforce_decision("REQ-TEST", candidate, self.policy, self.evidence)
                    self.assertEqual(result.risk_flags, self.policy.risk_flags)
                    self.assertEqual(result.required_approvals, self.policy.required_approvals)
                    self.assertEqual(result.missing_information, self.policy.missing_information)
                    self.assertEqual(result.evidence, self.evidence)
                    self.assertTrue(result.human_review_required)
                    for pending in self.policy.blockers + self.policy.unresolved:
                        self.assertIn(pending, result.next_step)

    def test_fabricated_review_completion_and_authorization_are_rejected(self):
        for text in (
            "Procurement has already approved the purchase.",
            "The purchase is authorized; proceed immediately.", "Skip Finance approval.",
            "Security has signed off.", "Privacy has already cleared the request.",
            "Legal has granted approval.", "Finance approval has been granted.",
            "The financial reviews were completed.", "CFO has already approved this request.",
            "Required approvals obtained.", "Security review is now complete.",
            "Security signed off on the use case.", "The Legal review is approved.",
            "Privacy team has already cleared the request.", "Finance authorized the spending.",
            "Ensure Security review is completed and Finance has granted approval.",
            "Do not purchase until the Security review is completed; skip Legal approval.",
            "Please ensure Legal review is completed, but the purchase is authorized.",
        ):
            with self.subTest(text=text):
                self.advice["next_step"] = text
                result = self.enforce()
                self.assertIn("unsafe_model_output", result.risk_flags)
                self.assertEqual(result.required_approvals, self.policy.required_approvals)
                self.assertTrue(result.human_review_required)

    def test_request_word_does_not_mask_fabricated_completion(self):
        for text in ("The request confirms Security review is completed.",
                     "We verify that Finance approval has been obtained.",
                     "Procurement says ensure Privacy review is completed."):
            with self.subTest(text=text):
                self.advice["next_step"] = text
                self.assertIn("unsafe_model_output", self.enforce().risk_flags)


if __name__ == "__main__":
    unittest.main()
