Run every morning.

Include the deterministic startup rotation from `startup_watchlist.yml` in official-company discovery. The watchlist must contain 50–75 curated startup or growth-stage employers. Select 15 companies per day, exclude companies already present in `companies.yml`, and record the selected names in the dated shard manifest and summary. Startup jobs use the same freshness, location, fit, applied-job, strict-review, and resume-production gates as every other job; startup membership is never itself a positive fit signal.

Search the configured target companies for software engineering jobs posted within the last 60 days.

Prioritize:
1. Posted within 7 days
2. Posted within 21 days
3. Posted within 60 days

Exclude postings older than 60 days unless the official page is still live and applyable. Route those live evergreen postings to `Needs Freshness Verification`; do not place them in Apply or Maybe until the candidate confirms they remain worth pursuing.

Apply the candidate’s no-stretch rule. Recommend only jobs that align with the verified skills, experience, seniority, and delivery lanes configured in the candidate profile.

Both product-driven and services-driven employers are acceptable targets. Include services or consulting companies when the specific role is hands-on software engineering, technical consulting, implementation engineering, platform/application development, architecture/advisory, solution design, integration strategy, technical delivery leadership, or professional-services work that fits the candidate's resume. Consulting and professional-services roles remain strong targets even when the day-to-day work is lighter on hands-on coding, provided the role still requires technical ownership, solution/system learning, implementation guidance, integration leadership, technical workshops, design review, delivery leadership, or acting as a technical lead for client-facing work. Roles may include directing implementation, decomposing technical work, reviewing designs/code, handing off implementation to junior developers, mentoring delivery teams, or guiding clients through technical decisions. Travel above 50% is acceptable for strong-fit roles when the technical match is compelling and the role remains clearly technical. Exclude only roles where the primary work is non-technical business advisory, sales-only presales, account management, project management without technical ownership, or client-delivery roles where constant onsite presence is a primary requirement.

Do not treat `full stack` as a negative by itself. the candidate can work effectively in UI technologies when needed and has prior Oracle JET experience in addition to deep backend Java ownership. Full-stack roles are valid targets when the backend, API, data, integration, and technical-ownership portions are still substantial. Exclude only when the role is primarily frontend-centric or React-heavy to the point that backend ownership is secondary.

Professional Services / Consulting Lane: treat Senior Technical Consultant, Principal Technical Consultant, Solution Architect, Implementation Architect, Professional Services Engineer, Professional Services Consultant, Integration Consultant, Enterprise Application Consultant, CPQ Consultant, CPQ Solution Architect, Customer Solutions Architect, Delivery Architect, Technical Lead, and Technical Account Architect roles as valid targets when they involve technical advisory, solution design, integrations, CPQ/quote-to-cash, enterprise application delivery, implementation guidance, system learning, or delivery leadership.

Forward Deployed Engineering Lane: treat Forward Deployed Engineer, Forward Deployed Software Engineer, Senior or Principal Forward Deployed Engineer, Forward Deployed Architect, and Forward Deployed Solutions Architect as target titles. Also evaluate Customer Engineer and Solutions Engineer roles when their actual responsibilities combine substantial hands-on production software development with customer problem solving, architecture, enterprise integration, implementation, deployment, troubleshooting, or technical leadership. Evaluate seniority from autonomy, compensation, engineering complexity, and end-to-end ownership rather than title alone. Treat the candidate's professional-services delivery and Principal-level enterprise SaaS engineering as a combined strength. Give additional consideration to this work at AI/platform, defense technology, developer infrastructure, data platform, cloud/platform engineering, and enterprise SaaS companies, but never waive existing stack, compensation, location, freshness, or no-stretch requirements. Deprioritize configuration-only implementation, demo-led presales, support/TAM, junior field support, and installation/travel roles without substantial software engineering ownership.

Use public job boards as discovery sources when official company career pages are difficult to search or hide posting dates. Good discovery sources include LinkedIn Jobs, Indeed, Google Jobs, Glassdoor, Dice, Built In, RemoteOK, Otta/Welcome to the Jungle, Levels.fyi Jobs, Wellfound, and company-specific ATS search result pages. Public job boards can identify candidate roles, alternate titles, posting freshness, and exact requisition titles, but every Apply or Maybe recommendation should still be verified against the official company career page whenever possible.

LinkedIn Jobs is the primary browser-assisted discovery source. When a logged-in LinkedIn browser is available, search the title, skill, location, compensation, and exclusion lanes configured in the candidate profile. Exclude required .NET/C#/Microsoft application-development roles, clearly sub-scope roles, expired or substance-free reposts, staffing databases, commission-only work, and suspicious listings unless the configured profile explicitly says otherwise.

For every browser-reviewed LinkedIn listing, read the complete description and preserve the LinkedIn URL. Resolve and verify the official employer/ATS URL whenever possible; confirm that the LinkedIn employer identity and application domain represent the same company; identify any third-party recruiter and state whether the relationship to the employer appears credible; flag unrelated redirects, premature requests for sensitive information, unexplained concealed employers, generic email accounts, and misleading application flows; and deduplicate by requisition ID first, then exact official URL, across LinkedIn and all other sources. Score fit from 0–100 against the configured candidate profile. Report strongest matches, meaningful gaps, compensation, location, and application risks. Recommend exactly one LinkedIn-review action: `Apply Now`, `Apply After Resume Tailoring`, `Investigate Further`, `Network First`, or `Skip`. Rank verified recent postings above promoted or repeatedly reposted listings, and show both LinkedIn and official application URLs when available.

Do not rely on a public job-board result when the official job page is delisted, expired, unavailable, materially different, or lacks evidence that the role is still open. If only an aggregator page is available, mark verification risk clearly and rank it below roles with official live pages.

Use USAJOBS as an official federal-posting source when local USAJOBS API credentials are configured. Focus on 2210 / application software roles and screen strictly for public eligibility, seniority/pay fit, Java/backend/API/SQL relevance, realistic location or remote status, and clearance requirements. Reject active-clearance-required roles unless the posting clearly says clearance can be obtained after hire and the technical fit is otherwise strong.

Do not recommend roles primarily requiring React-heavy frontend, Salesforce platform development, AWS/Terraform/EKS infrastructure, production AI/ML systems, active clearance, or impractical office attendance.

If an official job page explicitly says "Flexible or Remote," treat it as meeting the candidate's location criteria even when the same page is associated with a particular city. Only reject for location when the page also requires specific office attendance, West Coast presence, or travel beyond the acceptable threshold in the profile.

Atlanta-area roles that require living within commuting distance of the office are acceptable. Do not reject a strong-fit role solely because it expects regular commuting into Atlanta.

Carry forward active roles from the latest strict workflow review when the candidate has not applied yet. A role does not need to be rediscovered in the current day's queue to remain actionable. Re-run every carried role through the strict reviewer using the latest workflow profile and applied-jobs snapshot. Once jobtracker marks a role as already applied, move it to Already Applied and do not carry it into resume work.

When the candidate manually sends an official live job URL during the day, treat that role as a valid candidate input for the current report even if ATS or sharded discovery missed it. Verify the official page, evaluate it under the same rules, and include it in the dated report if it qualifies. Carry manually surfaced roles forward in later reports until they are applied, clearly delisted, or clearly fail a hard filter. If a manually surfaced role was already reviewed in a prior report or follow-up, do not make the candidate rediscover it; bring it forward automatically until applied or disqualified.

Apply that same intake behavior to browser-reviewed job findings from LinkedIn, Indeed, or any other site the candidate opens for review. Preserve the originating site and public listing URL when available, resolve the finding to the live official company or ATS posting, and add the official URL to `manual_job_urls.yml`. Include qualifying roles in the current dated discovery and strict workflow reports; do not leave reviewed findings only in chat. If the official page is live but its date cannot be confirmed, route a strong-fit role to `Needs Freshness Verification`. If no official posting can be resolved, retain the finding as an explicit verification item rather than presenting it as Apply or Maybe.

Keep enforcing the freshness rule for `Apply` and `Maybe`: only include verified live official postings within the last 60 days, prioritizing 7 days, then 21 days, then 60 days. If a strong-fit role is live but the official posting date cannot be confirmed, or is a live evergreen posting older than 60 days, place it in `Needs Freshness Verification` rather than discarding it.

For each recommended job, return:
- company
- title
- job URL
- location/work model
- posting age
- already applied
- fit rating
- why it fits
- risks
- decision: Apply / Maybe / Skip
- whether to run the resume workflow

Strict resume workflow gate:

Broad discovery remains intentionally permissive, but it is a supporting
diagnostic artifact rather than the primary report. Label its first-stage
buckets `Discovery Candidates — Higher Signal` and `Discovery Candidates —
Review Signal`; do not present either label as an application recommendation.

Use `YYYY-MM-DD-workflow-review.html` as the primary human-facing report. Its
action queue must contain Resume Ready, Gap Questions for Candidate, and Already
Applied, plus Companies to Check. Keep Moderate Stretch, Extreme Stretch, Skip,
and links to raw discovery artifacts inside a collapsed diagnostic archive.
Every current or carry-forward job displayed in the workflow report must have a
strict workflow decision.

The workflow HTML should offer one `Copy All Gap Questions` action for the full
actionable gap queue while retaining per-job resolution actions. The batch prompt
must keep answers scoped to exact posting URLs, wait for the candidate's truthful evidence,
rerun the strict reviewer, summarize moved/unresolved/rejected jobs, and invoke
resume production only from the refreshed resume-ready file.

Do not run the resume workflow directly from the discovery score. Discovery may
classify a job as Apply or Maybe for tracking purposes, but a job should only
reach Word resume or cover-letter generation after `scripts/review_job_fit.py` classifies it as
STRONG APPLY or APPLY and writes it to `YYYY-MM-DD-resume-ready-jobs.md`.

Maybe means review the job packet first. Moderate-stretch jobs require the candidate's
approval and any missing gap answers before resume generation. Skip and Extreme
Stretch jobs should not trigger resume work unless the candidate explicitly overrides the
decision.

After the broad inspection queue is built, run `scripts/mark_applied_jobs.py`
with `--applied-jobs-out YYYY-MM-DD-applied-jobs.json`. Then run
`scripts/review_job_fit.py` with that required snapshot and
use `YYYY-MM-DD-resume-ready-jobs.md` as the only source for resume or
cover-letter generation. Do not recommend creating a resume for any job absent
from that file. Summarize `YYYY-MM-DD-human-review-packets.md` under the exact
heading `## Gap Questions for Candidate`, preserving the affected company and role.

Then run `scripts/produce_resumes.py` with that dated resume-ready file and the
same applied-jobs snapshot. The
production stage must generate only missing authorized DOCX files. When the
exact job posting already has a validated artifact in a prior production
manifest, reuse and link that artifact instead of regenerating or duplicating
it; different job IDs remain separate roles. Revalidate existing and reused
authorized files, render and validate every document, fail without publishing
new artifacts if any validation fails, and regenerate the workflow HTML only
after the full authorized batch passes. Discovery JSON, Apply/Maybe
tables, and human-review packets must never be treated as alternate production
queues.

After resume production, run `scripts/publish_skill_gap_analytics.py` against the
complete dated `workflow-review.json`. It must write the dated
`skill-gap-observations.json` contract artifact before POSTing to
`http://127.0.0.1:8086/api/analytics/workflow-runs` with the scoped
Bearer token. Use `--ssh-host jobtracker-host` so the token remains in the server's
`jobtracker-analytics-import` Kubernetes secret; direct publishing may instead
read `JOBTRACKER_ANALYTICS_IMPORT_TOKEN`. Export every reviewed posting, including
postings with no gaps, and preserve required/preferred classifications and exact
resolution states. Report export or import failures explicitly, but do not let
analytics failure block or undo the already completed strict resume-production
stage. Same-date retries must remain idempotent.

Applied status is a fail-closed resume gate. Exclude tracker matches during
review and reject them again during production. Prefer requisition ID, then
exact URL; use company/title fallback only when neither side has a requisition
ID. Never transfer applied status between different job IDs.
