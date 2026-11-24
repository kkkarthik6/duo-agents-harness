"""Official Antigravity families, checked 2026-11-22; tiers are Duo policy.

Identifiers: https://antigravity.google/docs/enterprise/
Plan access: https://antigravity.google/docs/models
Account-specific identifiers must be confirmed with `agy models` after sign-in.
"""

MODELS = [
    dict(cli='agy', id=identifier, display_name=name, tier=tier, capabilities=['tools'], available=True,
         account_access='unverified', availability_source='official documentation; account access checked on invocation',
         source_url='https://antigravity.google/docs/enterprise/',
         access_source_url='https://antigravity.google/docs/models', verified_on='2026-11-22',
         tier_source='Duo role policy; not model parameter count or reasoning effort', plans=plans)
    for identifier, name, tier, plans in (
        ('gemini-4-neon', 'Gemini 4 Neon', 'large', ['Google AI Pro (eligible)', 'Google AI Ultra', 'Enterprise']),
        ('gemini-4-carbon', 'Gemini 4 Carbon', 'large', ['Google AI Ultra', 'Enterprise']),
        ('gemini-4-argon', 'Gemini 4 Argon', 'large', ['Google AI Pro (eligible)', 'Google AI Ultra', 'Enterprise']),
        ('gemini-4-hydrogen', 'Gemini 4 Hydrogen', 'small', ['Google AI Pro (paid)', 'Google AI Ultra', 'Enterprise']),
        ('gemini-3.8-flash', 'Gemini 3.8 Flash', 'small', ['Free', 'Google AI Plus', 'Google AI Pro', 'Google AI Ultra', 'Enterprise']),
        ('gemini-3.7-flash', 'Gemini 3.7 Flash', 'small', ['Enterprise']),
        ('gemini-3.6-flash', 'Gemini 3.6 Flash', 'small', ['Enterprise']),
        ('gemini-3.1-pro-preview', 'Gemini 3.1 Pro', 'large', ['Enterprise']),
        ('claude-sonnet-5-5', 'Claude Sonnet 5.5 (Thinking)', 'medium', ['Google AI Pro (paid)', 'Google AI Ultra', 'Enterprise (admin opt-in)']),
        ('claude-opus-5-5', 'Claude Opus 5.5 (Thinking)', 'large', ['Google AI Pro (paid)', 'Google AI Ultra', 'Enterprise (admin opt-in)']),
    )
]

# Exact effort aliases documented by the CLI, not size guesses from model names.
EFFORT_ALIASES = {
    'gemini-4-neon': ('low', 'medium', 'high', 'xhigh'),
    'gemini-4-carbon': ('low', 'medium', 'high'),
    'gemini-4-argon': ('medium', 'high'),
    'gemini-4-hydrogen': ('low', 'medium', 'high', 'xhigh'),
    'gemini-3.8-flash': ('medium', 'high'),
    'gemini-3.7-flash': ('medium', 'high'),
    'gemini-3.6-flash': ('medium', 'high'),
    'gemini-3.1-pro-preview': ('high',),
    'claude-sonnet-5-5': ('low', 'medium', 'high', 'max'),
    'claude-opus-5-5': ('low', 'medium', 'high', 'max'),
}
for row in MODELS:
    base = 'gemini-3.1-pro' if row['id'] == 'gemini-3.1-pro-preview' else row['id']
    row['discovery_aliases'] = [base + '-' + effort for effort in EFFORT_ALIASES[row['id']]]
