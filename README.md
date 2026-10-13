# Job Agent

Personal job-discovery and fit-screening agent for verified employer and ATS postings.

## Setup

1. Copy `.env.example` to `.env.local` and provide any optional source credentials.
2. Copy `resume_templates.example.json` to `resume_templates.json` and point it at local source resumes when document production is needed.
3. Customize `candidate_profile.yml` and `candidate_workflow_profile.yml`, or pass alternate local profile paths on the command line.
4. Run the project Python wrapper for commands and tests:

```bash
scripts/jobagent_python.sh -m unittest discover -s tests
```

## Daily Discovery

```bash
scripts/jobagent_python.sh scripts/search_jobs.py \
  --companies companies.yml \
  --profile candidate_profile.yml \
  --rules search_rules.md \
  --out outputs
```

Generated reports, networking data, and local credentials remain untracked.

LinkedIn connection-request Add Notes are generated through one shared formatter
using the approved `jhaynes-writing-voice`. The formatter enforces a 299-character
maximum across networking reports, daily contact pages, and strict workflow detail pages.

## Privacy Boundary

The repository intentionally excludes `.env.local`, `outputs/`, `Source Docs/`,
`resume_templates.json`, LinkedIn exports, and personalized legacy profile files.
Never commit resumes, contact exports, API keys, email addresses, phone numbers,
home-directory paths, private network addresses, or generated application artifacts.
Public configuration uses neutral candidate examples; keep real identity and local
paths in `.env.local` or another ignored file.

Resume contact hyperlinks read `JOBAGENT_CANDIDATE_EMAIL`,
`JOBAGENT_RESUME_LINKEDIN_URL`, and `JOBAGENT_RESUME_PORTFOLIO_URL` at runtime.
The resume URL settings default to the shared `JOBAGENT_LINKEDIN_URL` and
`JOBAGENT_PORTFOLIO_URL` settings when present, or to the neutral examples in
`.env.example`. Set the resume-specific values locally to retain exact existing
document targets without changing networking links.

## Daily Sentinel And Deep Rotation

The default sharded command checks every supported structured ATS board each weekday
with one lightweight listing request. It compares stable job IDs with
`outputs/ats-sentinel-state.json`; newly observed relevant jobs enter that day's
discovery output immediately.

The expensive company search runs against one stable weekday bucket, approximately
20% of `companies.yml`. The bucket key is derived from the shared ATS identity when
available, so companies using the same board stay together. Across Monday through
Friday every established company receives one deep scan. Failed and overdue scans
are carried into the next weekday using `outputs/company-deep-scan-state.json`.

The dated `outputs/YYYY-MM-DD-ats-sentinel.json`, shard manifest, and shard summary
show boards checked, new jobs, failures, companies deeply searched, and companies
deferred. Use `--no-rotation` only for an intentional full deep scan.

## Rotating Startup Discovery

`startup_watchlist.yml` contains 50–75 curated startup and growth-stage employers.
Each sharded discovery run selects 15 non-duplicate companies using a deterministic
daily rotation, merges them with the established company universe, and records the
selection in the dated shard manifest and summary. Startup roles retain the same
discovery scoring and strict resume gate as all other roles.

## Manual Search Feedback Loop

LinkedIn and other human searches are a discovery audit, not a runtime dependency.
When a human search finds a role that automated discovery missed:

1. Add the exact live official posting to `manual_job_urls.yml` so the next run cannot lose it.
2. Record the miss in `tests/fixtures/linkedin_misses.yml` with the expected employer and ATS board.
3. Add or correct the deterministic board in `ats_registry.yml`, then run the regression tests.
4. Keep a newly found employer in `startup_watchlist.yml` until separate runs verify at least two matching roles.
5. Remove a manual URL only after normal ATS discovery finds that exact posting without the manual entry.

Recruiter-hosted listings are bounded to ten inspected hits per run. Only preliminary
fits above 75 proceed, the canonical employer must be resolved with high confidence,
and verification stops after the first official employer or ATS posting. Reports retain
the recruiter source, canonical employer, source URL, reason, and confidence.

## Strict Resume Workflow Gate

After discovery produces `outputs/YYYY-MM-DD-discovered-jobs.json`, reconcile the
daily report with jobtracker and persist the required applied-job snapshot:

```bash
scripts/jobagent_python.sh scripts/mark_applied_jobs.py \
  --daily-report outputs/YYYY-MM-DD-daily-job-search.md \
  --ssh-host jobtracker-host \
  --applied-jobs-out outputs/YYYY-MM-DD-applied-jobs.json
```

Then run the strict workflow reviewer before starting any resume or cover-letter
work:

```bash
scripts/jobagent_python.sh scripts/review_job_fit.py \
  --discovered-jobs outputs/YYYY-MM-DD-discovered-jobs.json \
  --workflow-profile candidate_workflow_profile.yml \
  --applied-jobs outputs/YYYY-MM-DD-applied-jobs.json \
  --out outputs \
  --date YYYY-MM-DD
```

Use `outputs/YYYY-MM-DD-resume-ready-jobs.md` as the only source for roles that
should proceed to Word resume or cover-letter generation. Use
`outputs/YYYY-MM-DD-human-review-packets.md` for roles that still need gap
questions or a manual override.

After the strict reviewer succeeds, run the resume-production stage:

```bash
scripts/jobagent_python.sh scripts/produce_resumes.py \
  --resume-ready outputs/YYYY-MM-DD-resume-ready-jobs.md \
  --applied-jobs outputs/YYYY-MM-DD-applied-jobs.json \
  --source-dir "Source Docs" \
  --out outputs/resumes
```

The producer reuses a previously validated DOCX when the exact job posting is
authorized again on a later day, and the new workflow HTML links to that prior
artifact. It does not regenerate or duplicate the file. Requisitions with
different job IDs remain separate roles and receive separate resumes when
authorized.

The production stage reads no discovery decision queue. For every exact,
unapplied posting authorized by `resume-ready-jobs.md`, it produces both a
validated resume and a validated cover letter. It reuses eligible artifacts,
renders every document to page images, rejects structural or rendering failures,
independently rejects any job matched as applied in the required snapshot, writes
the dated resume and cover-letter production manifests, and finally regenerates
the workflow HTML so both documents become links.

New cover letters use the approved `jhaynes-writing-voice` professional-message
profile: concise, cordial, purpose-first, and grounded only in verified candidate
evidence. The cover-letter manifest records that voice. When an authorized exact
posting has a legacy letter that does not contain the approved voice markers, the
producer regenerates and revalidates it instead of reusing it.

On macOS, the producer attempts the installed LibreOffice renderer first. If it
fails, it uses the isolated Linux fallback image
`jobsearchagent-libreoffice-renderer:1.0.0`, with no container network access
and read-only document mounts. Build that pinned local image once before a
scheduled run:

```bash
docker build --tag jobsearchagent-libreoffice-renderer:1.0.0 docker/resume-renderer
```

The container writes only PDF and page-image validation outputs to the staging
directory. If both native and container rendering fail, resume production fails
closed and publishes no new resume batch.

After resume production completes, export the complete strict review and publish
it to jobtracker's dedicated analytics endpoint:

```bash
scripts/jobagent_python.sh scripts/publish_skill_gap_analytics.py \
  --workflow-review outputs/YYYY-MM-DD-workflow-review.json \
  --workflow-profile candidate_workflow_profile.yml \
  --out outputs \
  --date YYYY-MM-DD \
  --tracker-url http://127.0.0.1:8086/api/analytics/workflow-runs \
  --ssh-host jobtracker-host
```

With `--ssh-host jobtracker-host`, the scoped Bearer token remains in the server's
`jobtracker-analytics-import` Kubernetes secret. For direct HTTP publishing
without SSH, configure `JOBTRACKER_ANALYTICS_IMPORT_TOKEN` in `.env.local`; it is
the scoped Bearer token from jobtracker, not the browser password. The exporter always
writes `outputs/YYYY-MM-DD-skill-gap-observations.json` before attempting the
import. A successful import also writes
`outputs/YYYY-MM-DD-skill-gap-import-result.json`. The review date is the
idempotency key, so retrying the same artifact does not duplicate data and a
revised same-day artifact replaces only that day's observations. Analytics runs
after resume production so an explicit analytics failure cannot block or undo
authorized resume work.

The reviewer also generates `outputs/YYYY-MM-DD-workflow-review.html`, a
browser-friendly actionable dashboard. This is the primary human-facing report;
the broad daily discovery report and inspection queue are supporting diagnostics.
Its Action Queue contains Resume Ready, Gap Questions for Candidate, and Already
Applied sections, plus a Companies to Check tab. Moderate Stretch, Extreme
Stretch, Skip, and raw discovery links are kept in a collapsed diagnostic
archive. Its Resume Ready table links each generated DOCX found in
`outputs/resumes`; approved roles without a matching artifact are labeled
`Not generated`.

The reviewer carries active jobs forward from the latest strict review even if
the current discovery run misses them. Current discoveries take precedence, and
every current or carried role is re-reviewed against the latest workflow profile
and applied-jobs snapshot. First-stage buckets are labeled `Discovery Candidates
— Higher Signal` and `Discovery Candidates — Review Signal`; neither label is
permission to create a resume.

For unresolved jobs, use the HTML card's **Copy Resolution Prompt** action and
return the prompt with truthful evidence, limited exposure, a do-not-claim answer,
or confirmation that the posting match was a false positive. Re-run the reviewer
after resolving the questions; the refreshed resume-ready file remains the gate.
The **Copy All Gap Questions** action above the queue packages every actionable
Strong Apply, Apply, or Moderate Stretch gap job into one prompt. It waits for
the candidate's truthful answers, persists reusable skill facts once and posting-specific
interpretations by exact posting URL, reruns
the reviewer, summarizes which jobs moved or remain blocked, and runs production
only from the refreshed resume-ready file. Per-job prompt buttons remain available
for resolving a single role.
Persist candidate-wide skill facts in `candidate_workflow_profile.yml` under
`candidate_gap_resolutions`, keyed by skill. The reviewer reuses these facts across
postings so the candidate does not answer the same experience question repeatedly.
Persist posting-specific interpretations under `gap_resolutions`, keyed by the exact
posting URL; these override candidate-wide facts only for that posting. Supported answers are `do_not_claim`,
`limited_exposure: <context>`, `professional_evidence: <evidence>`, and
`posting_false_positive: <reason>`. The last status is valid only for a posting-specific
answer. An answered preferred gap can clear its
question without becoming a résumé claim; required gaps remain blocking unless
professional evidence or a false-positive correction resolves them.

Daily reports summarize `outputs/YYYY-MM-DD-human-review-packets.md` under
`## Gap Questions for Candidate`. A resume or cover letter must never be recommended
for a role absent from `outputs/YYYY-MM-DD-resume-ready-jobs.md`.
Previously applied roles are excluded by requisition ID or exact URL. The
company/title fallback is used only when neither record has a requisition ID, so
different job IDs remain distinct.
