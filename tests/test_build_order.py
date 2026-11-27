"""Planners hand the builder an exact plan and a prompt; nothing is tested until the functionality exists."""
import unittest

from engine import build_order, build_workflow, goal_loop, handoff, initial_planning, roleflow
from engine.session import INSTRUCTIONS

RULE = "Testing comes last"


class RuleTextTests(unittest.TestCase):
    def test_the_rule_forbids_every_kind_of_verification_before_the_functionality_exists(self):
        text = build_order.TEST_LAST.lower()
        for word in ("tests", "verification", "browser", "screenshots", "accessibility", "all functionality"):
            self.assertIn(word, text)
        self.assertIn(RULE.lower(), text)

    def test_a_blocked_verification_never_stalls_the_build(self):
        self.assertIn("do not stall", build_order.TEST_LAST.lower())


class PlannerPromptTests(unittest.TestCase):
    def setUp(self):
        self.prompt = roleflow.plan_prompt("TASK", "")

    def test_the_plan_lists_requirements_with_sub_requirements(self):
        self.assertIn("R1.1", self.prompt)
        self.assertIn("sub-requirement", self.prompt)

    def test_each_sub_requirement_gets_files_interfaces_and_ordered_steps(self):
        for word in ("files", "interfaces", "ordered steps"):
            self.assertIn(word, self.prompt)

    def test_work_is_delegated_to_agents_with_parallel_work_identified(self):
        self.assertIn("delegat", self.prompt.lower())
        self.assertIn("parallel", self.prompt.lower())

    def test_the_plan_ends_with_a_self_contained_builder_prompt(self):
        self.assertIn("## Builder prompt", self.prompt)
        self.assertIn("self-contained", self.prompt)

    def test_the_plan_states_that_testing_is_the_final_phase(self):
        self.assertIn(RULE, self.prompt)

    def test_the_lead_rubric_carries_the_rule_for_every_planner_turn(self):
        lead = roleflow.instructions("lead")
        self.assertIn("## Builder prompt", lead)
        self.assertIn(RULE, lead)

    def test_the_lead_rubric_text_matches_the_shared_plan_shape(self):
        self.assertIn(build_order.PLAN_SHAPE, roleflow.rubric("lead")["instructions"])

    def test_other_planner_prompts_carry_the_rule(self):
        self.assertIn(RULE, initial_planning.brief({"profiles": ["app"]}))
        self.assertIn(RULE, initial_planning.brief({"profiles": ["general"]}))
        self.assertIn(RULE, goal_loop.assignment_prompt({"goal": {"assessment_input": "", "assessments": {}}}))
        self.assertIn(RULE, build_workflow.prompt("r.json", "task"))

    def test_the_plan_reviewer_rejects_a_plan_that_tests_early_or_lacks_a_builder_prompt(self):
        review = roleflow.review_prompt("TASK", ".duo/r/plan.md")
        self.assertIn("## Builder prompt", review)
        self.assertIn("before the functionality", review)
        self.assertLess(len(review), 3000)


class BuilderTests(unittest.TestCase):
    def test_the_builder_follows_the_builder_prompt_and_builds_before_any_checks(self):
        builder = roleflow.instructions("builder")
        self.assertIn("## Builder prompt", builder)
        self.assertIn("largest planned product change first", builder)
        self.assertIn(RULE, builder)

    def test_every_build_turn_repeats_the_rule(self):
        self.assertIn(RULE, INSTRUCTIONS["build"])
        self.assertIn(RULE, handoff.TASK["build"])
        self.assertIn(RULE, build_workflow.package_prompt({"id": "P1"}))

    def test_the_planner_hand_off_text_asks_for_the_builder_prompt(self):
        self.assertIn("Builder prompt", handoff.TASK["exchange"])


if __name__ == "__main__":
    unittest.main()
