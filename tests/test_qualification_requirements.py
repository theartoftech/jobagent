from __future__ import annotations

import unittest

from scripts.qualification_requirements import (
    contains_required_qualification,
    has_confirmed_alternative,
    qualification_text,
    unconfirmed_required_capabilities,
)


class QualificationRequirementsTests(unittest.TestCase):
    def test_encoded_fiserv_requirement_is_mandatory(self) -> None:
        text = qualification_text(
            '&lt;h2&gt;Experience you&#39;ll need to have:&lt;/h2&gt;'
            '&lt;li&gt;3+ years of experience in AWS, Azure, or GCP services&lt;/li&gt;'
            '&lt;h2&gt;Experience that would be great to have:&lt;/h2&gt;'
            '&lt;li&gt;Financial services&lt;/li&gt;'
        )
        for provider in ('AWS', 'Azure', 'GCP'):
            self.assertTrue(contains_required_qualification(text, provider))
        self.assertFalse(contains_required_qualification(text, 'financial services'))

    def test_html_keeps_list_and_heading_boundaries(self) -> None:
        text = qualification_text('<h2>Requirements &amp; Qualifications</h2><ul><li>Java</li><li>Azure</li></ul>')
        self.assertEqual('Requirements & Qualifications\nJava\nAzure', text)

    def test_entire_required_section_is_checked(self) -> None:
        text = 'Requirements & Qualifications\n' + 'General architecture experience.\n' * 35 + 'Experience with Microsoft Azure or Google Cloud Platform.\nReporting relationship\nSkills: Kotlin'
        self.assertTrue(contains_required_qualification(text, 'azure'))
        self.assertTrue(contains_required_qualification(text, 'google cloud'))
        self.assertFalse(contains_required_qualification(text, 'kotlin'))

    def test_preferred_degree_does_not_make_following_requirements_optional(self) -> None:
        text = "Requirements & Qualifications Bachelor's degree; Master's degree preferred Experience designing hybrid and multi-cloud architectures (SAP Cloud, Microsoft Azure, or Google Cloud Platform) Reporting relationship Director"
        self.assertTrue(contains_required_qualification(text, 'azure'))

    def test_optional_sections_and_prose_requirements_are_not_mandatory_skills(self) -> None:
        for text in ('Meet enterprise security requirements. AWS is a plus.', 'Preferred Qualifications\nExperience with Azure.', 'Requirements:\nJava\nPreferred Qualifications\nAzure'):
            self.assertFalse(contains_required_qualification(text, 'azure'))

    def test_unrelated_java_does_not_satisfy_a_cloud_alternative_list(self) -> None:
        text = 'Requirements & Qualifications Experience designing cloud architectures (SAP Cloud, Microsoft Azure, or Google Cloud Platform) Skills: Java, Kubernetes'
        self.assertFalse(has_confirmed_alternative(text, 'azure', ['Java', 'Kubernetes']))

    def test_alternatives_must_be_members_of_the_same_list(self) -> None:
        self.assertTrue(has_confirmed_alternative('Go, Rust, C++, or Java', 'go', ['Java']))
        self.assertTrue(has_confirmed_alternative('Java or Go', 'go', ['Java']))
        self.assertTrue(has_confirmed_alternative('Go, Java, or Rust', 'go', ['Java']))
        self.assertFalse(has_confirmed_alternative('Java and Go; AWS or Azure', 'go', ['Java']))
        self.assertFalse(has_confirmed_alternative('Java and Go', 'go', ['Java']))
        self.assertFalse(has_confirmed_alternative('Java for APIs; Azure or GCP for cloud', 'azure', ['Java']))

    def test_specialist_evidence_gate_is_scoped_and_configurable(self) -> None:
        rules = [{'label': 'Enterprise multi-cloud architecture', 'terms': ['multi-cloud'], 'title_pattern': r'cloud.*architect', 'evidence_status': 'unconfirmed'}]
        text = 'Requirements & Qualifications\nExperience designing multi-cloud architectures.'
        self.assertEqual(['Enterprise multi-cloud architecture'], unconfirmed_required_capabilities(title='Cloud Architect', description=text, rules=rules))
        self.assertEqual([], unconfirmed_required_capabilities(title='Java Engineer', description=text, rules=rules))
        self.assertEqual([], unconfirmed_required_capabilities(title='Cloud Architect', description='Preferred Qualifications\nMulti-cloud experience', rules=rules))
        self.assertEqual([], unconfirmed_required_capabilities(title='Cloud Architect', description=text, rules=[{**rules[0], 'evidence_status': 'professional_evidence'}]))
        self.assertEqual(['Enterprise multi-cloud architecture'], unconfirmed_required_capabilities(title='Cloud Architect', description=text, rules=rules + rules))

    def test_optional_bullet_does_not_hide_a_later_required_bullet(self) -> None:
        text = 'Requirements:\nAWS preferred\nAzure experience\nPreferred Experience:\nGCP'
        self.assertFalse(contains_required_qualification(text, 'aws'))
        self.assertTrue(contains_required_qualification(text, 'azure'))
        self.assertFalse(contains_required_qualification(text, 'gcp'))

    def test_invalid_rules_fail_explicitly(self) -> None:
        with self.assertRaises(ValueError):
            unconfirmed_required_capabilities(title='Cloud Architect', description='Requirements: cloud', rules=[42])
        for rules in ('invalid', [{}], [{'label': 'X', 'terms': [], 'title_pattern': 'cloud', 'evidence_status': 'unconfirmed'}], [{'label': 'X', 'terms': ['cloud'], 'title_pattern': '[', 'evidence_status': 'unconfirmed'}], [{'label': 'X', 'terms': ['cloud'], 'title_pattern': 'cloud', 'evidence_status': 'invented'}]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                unconfirmed_required_capabilities(title='Cloud Architect', description='Requirements: cloud', rules=rules)


if __name__ == '__main__':
    unittest.main()
