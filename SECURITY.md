# Security

## Reporting a vulnerability

Open a [security advisory](https://github.com/Shivanshu49/EternityX-Interview-Agent/security/advisories/new)
rather than a public issue. Please include reproduction steps and the commit
you tested.

## Secrets

**`.env` is gitignored and must never be committed.** It holds
`ANTHROPIC_API_KEY`. `.env.example` is the tracked template and contains
placeholders only.

Verified clean as of the current HEAD: `.env` appears in no commit, and no
key-shaped string (`sk-ant-…`, `sk-or-…`, `gho_…`, `ghp_…`) exists in any blob
in the repository's history.

If a key is ever committed, treat it as compromised the moment it is pushed:
revoke it at <https://console.anthropic.com/settings/keys> first, then rewrite
history. Rotating after a public push is not optional: automated scrapers find
keys in public repositories within minutes.

The application never logs key material. The startup banner reports only the
character count (`app/main.py`), and `app/llm.py` raises
`LLMConfigurationError` with an instruction rather than echoing what it found.

## Third-party API endpoints

The SDK honours `ANTHROPIC_BASE_URL`, so requests can be pointed at any
compatible endpoint. Two cautions:

- The startup banner prints the resolved endpoint deliberately. A demo should
  never talk to an unexpected host without saying so.
- Only use gateways you hold a legitimate account with. A service that requires
  impersonating another client's `User-Agent` to accept traffic is circumventing
  an access control, and the account at risk is yours.

## Candidate data

`candidates.json` is synthetic fixture data provided with the challenge. It is
committed deliberately so the project is reproducible from a clone. Do not add
real candidate records to this repository; it is public. Interview transcripts
are held in process memory only (`app/session_store.py`) and are lost on
restart; there is no persistence layer and none should be added without a
retention decision.

## Prompt injection

Candidate answers are untrusted input and are replayed into the model's context
as conversation turns. An answer can therefore attempt to steer the
interviewer. The current mitigations are structural rather than absolute:

- The engine, not the model, chooses which day is asked next. A candidate cannot
  talk their way onto easier material; day selection is deterministic and
  derived from their cohort record.
- Feedback is produced with structured outputs, so the response shape is
  enforced by the API rather than requested in prose.
- The model has no tools and no side effects. The worst case is a poor question
  or an inaccurate assessment, not an action taken on the candidate's behalf.

Before this is used for real hiring decisions, the transcript should be treated
as adversarial input and the feedback path given an explicit injection review.

## Dependencies

`requirements.txt` pins minimum versions only. Run `pip list --outdated` before
a release and check GitHub's Dependabot alerts for the repository.
