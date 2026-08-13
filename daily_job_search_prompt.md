Run the ATS-aware discovery first:

scripts/jobagent_python.sh scripts/discover_jobs_sharded.py --companies companies.yml --profile candidate_profile.yml --out outputs --max-age-days 60 --ats-registry ats_registry.yml --manual-job-urls manual_job_urls.yml --rotation --pause-seconds 0 --shard-count 10 --max-companies-per-shard 20 --per-shard-timeout-seconds 900

Rotation is intentional: every structured ATS board receives a lightweight daily
sentinel check, while one stable weekday bucket (approximately 20% of established
companies) receives the expensive deep title/keyword scan. Newly observed relevant
job IDs bypass the rotation and enter the current discovery output immediately.
Failed or overdue deep scans are retried on the next weekday.

This discovery includes USAJOBS federal-job discovery when `USAJOBS_USER_AGENT`
and `USAJOBS_AUTHORIZATION_KEY` are present in the local environment. If either
value is missing, skip USAJOBS and record the diagnostic; do not fail the run.

Then generate the search plan:

scripts/jobagent_python.sh scripts/search_jobs.py --companies companies.yml --profile candidate_profile.yml --rules search_rules.md --out outputs

After the daily report has been filled with Apply/Maybe/Skip recommendations, mark jobs that already exist in jobtracker:

scripts/jobagent_python.sh scripts/mark_applied_jobs.py --daily-report outputs/YYYY-MM-DD-daily-job-search.md

Then run the networking pass:

scripts/jobagent_python.sh scripts/match_network.py --daily-report outputs/YYYY-MM-DD-daily-job-search.md --out outputs --date YYYY-MM-DD

Then, for the top contacts tied to Apply jobs, use the candidate's logged-in LinkedIn browser session to verify each contact's current company from the live profile. Write the verification results to:

outputs/YYYY-MM-DD-linkedin-live-verifications.json

JSON format:
- `linkedin_url`
- `current_company`
- `current_role`
- `verified_on`

After writing that file, rerun the networking pass so the report, networking markdown, and browser HTML all reflect `export-confirmed` vs `live-profile-confirmed` contact status:

scripts/jobagent_python.sh scripts/match_network.py --daily-report outputs/YYYY-MM-DD-daily-job-search.md --out outputs --date YYYY-MM-DD --linkedin-live-verifications outputs/YYYY-MM-DD-linkedin-live-verifications.json

Search the configured target companies for software engineering jobs posted within the last 60 days.

Prioritize:
1. Posted within 7 days
2. Posted within 21 days
3. Posted within 60 days

Exclude anything older than 60 days.

Apply the candidate’s no-stretch rule using the verified skills, seniority, delivery lanes, locations, compensation, and exclusions in the configured profile.

Professional services, consulting, advisory, solution architecture, implementation architecture, technical account architecture, professional services engineering, integration consulting, enterprise application consulting, CPQ consulting, and customer solutions architecture roles are valid targets when technical ownership is central.

Include Forward Deployed Engineer, Forward Deployed Software Engineer, Senior or Principal Forward Deployed Engineer, Forward Deployed Architect, and Forward Deployed Solutions Architect as a first-class search lane. Include Customer Engineer, Solutions Engineer, and similarly structured titles only when the posting shows substantial hands-on production software development plus customer problem solving, architecture, enterprise integration, deployment, troubleshooting, or technical leadership. Evaluate seniority by scope, autonomy, compensation, and complexity rather than title alone. Deprioritize configuration-only implementation, demo-led presales, support/TAM, junior field support, and onsite installation work without significant software ownership.

Use public job boards as discovery sources when company career pages are hard to search, but verify every recommended role against an official company or ATS job page. Reject stale indexed, expired, delisted, 404, materially different, or public-board-only postings.

Treat browser-reviewed job findings from LinkedIn, Indeed, or any other site as first-class manual intake. Preserve the originating source and listing URL, resolve each finding to its live official company or ATS posting, add that official URL to `manual_job_urls.yml`, and include qualifying roles in the current dated discovery and strict workflow reports. Do not leave reviewed findings only in chat. Carry them forward until applied, delisted, or disqualified. If an otherwise strong official posting has no confirmable date, place it in `Needs Freshness Verification`; if no official posting can be resolved, report it as needing official verification rather than as Apply or Maybe.

Treat LinkedIn Jobs as the primary browser-assisted discovery source whenever the candidate has a logged-in LinkedIn Jobs tab available. Search the configured title, skill, location, compensation, and exclusion lanes. Open the complete LinkedIn description, verify employer and application-domain identity, resolve the official posting, assess third-party recruiter credibility, flag suspicious redirects or premature sensitive-data requests, detect cross-source duplicates, preserve both URLs, score fit 0–100, and assign one browser-review action: Apply Now, Apply After Resume Tailoring, Investigate Further, Network First, or Skip. If no authenticated browser is available, continue the unattended ATS run and write the deterministic LinkedIn search links; do not fail the daily search.

Do not recommend roles primarily requiring React-heavy frontend, Salesforce platform development, AWS/Terraform/EKS infrastructure, production AI/ML systems, active clearance, sales-only presales, account management, non-technical business advisory, project management without technical ownership, or impractical office attendance.

The default human-facing output should be a broad inspection queue built from verified live official pages, not an over-narrow manually culled shortlist. Keep the no-stretch rule, but preserve the wider set of plausible roles for review.

For that broad inspection queue, order Apply and Maybe roles by fit score first, then posting freshness, then company/title. Freshness still matters and should remain visible, but it should not push weaker-fit jobs above stronger-fit jobs.

If a role was previously flagged in a recent daily report and the candidate has not applied yet, carry it forward in later reports even if it does not resurface in that day’s fresh discovery queue. Do not make the candidate rediscover still-open jobs manually. Once a role is marked as already applied in jobtracker, remove it from the active Apply/Maybe carry-forward set.

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

If you also produce a narrower final shortlist, treat it as secondary to the broad inspection queue and make that distinction explicit in the report.

After recommendations are finalized, match Apply and Maybe companies against the candidate's LinkedIn `Connections.csv`. Add a People to Contact section to the daily report and generate a dated networking report with matched contacts and introduction messages. For top contacts tied to Apply jobs, verify the live LinkedIn profile current company when possible and mark each contact as `export-confirmed` or `live-profile-confirmed`.
