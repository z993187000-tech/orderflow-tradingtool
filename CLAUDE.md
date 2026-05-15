## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore

## Strategy documentation

The authoritative strategy specification lives at `crypto-perp-scalping-tool/docs/strategy.md`. When making ANY change to:
- `crypto-perp-scalping-tool/src/crypto_perp_tool/signals/engine.py` (trading setups, forbidden conditions)
- `crypto-perp-scalping-tool/src/crypto_perp_tool/profile/engine.py` (level calculations)
- `crypto-perp-scalping-tool/src/crypto_perp_tool/risk/engine.py` (risk controls)
- `crypto-perp-scalping-tool/config/default.yaml` (strategy parameters)

you MUST update `docs/strategy.md` to match before claiming the task is complete.

@Agent.md