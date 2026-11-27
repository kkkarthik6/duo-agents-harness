"""Build order shared by every planner and builder prompt: functionality first, verification last."""

TEST_LAST = (
    "Testing comes last. Do not write or run tests, browser checks, screenshots, accessibility measurements or any other "
    "verification until all functionality in the plan is implemented. Before that point run only the compile, syntax or "
    "startup check needed to keep editing. Verification is one final phase after all functionality exists. If verification "
    "is blocked, record the blocker and keep building. Do not stall on it.")

BUILDER_PROMPT_HEADING = "## Builder prompt"

PLAN_SHAPE = (
    "Write an exact implementation plan, not a summary, in this order. "
    "1) Requirements: a numbered list (R1, R2) with sub-requirements (R1.1, R1.2). Each states the observable behavior it adds. "
    "2) Implementation: for each sub-requirement, the files to create or change, the interfaces and data involved, and the ordered steps. "
    "Say which items depend on others and which are independent. "
    "3) Delegation: assign each group of requirement IDs to an agent or role, and name the work that can proceed in parallel. "
    "4) Final section '" + BUILDER_PROMPT_HEADING + "': a self-contained prompt for the programming agent with the goal, context, "
    "requirement IDs, files, order of work, constraints and definition of done. Every build turn follows it. "
    "5) Testing and verification: a final phase scheduled after all functionality is built. "
    + TEST_LAST)

TEST_LAST_SHORT = "Testing comes last: no tests or verification until all functionality is implemented."
