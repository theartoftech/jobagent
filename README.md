# Job Agent

An assistant-guided job search you configure for your own experience, location and goals. Clone this repository, open it in Codex, Claude Code, Antigravity, or another coding assistant with local file and web access, and **paste this entire README into a new chat**. The assistant instructions below start the setup interview.

No model API key, paid job-search service, private server or external application tracker is required. Your assistant supplies the conversation, web research and browser access. The Python tools supply ATS discovery, search plans, deduplication, configurable screening and local reports. They never submit applications.

## Install

Install Git and **Python 3.11 or newer** (including pip and venv). A supported Python distribution from [python.org](https://www.python.org/downloads/) is sufficient. Windows users should use WSL with Python/Git/Bash for the commands below. macOS and Linux use a terminal with Bash. Public ATS requests also use `curl` where available; install it if your system does not provide it.

Substitute the repository URL and public branch supplied by the maintainer:

```bash
git clone --single-branch --branch PUBLIC_BRANCH REPOSITORY_URL jobagent
cd jobagent
python3 -m venv .venv
scripts/jobagent_python.sh -m pip install -r requirements-dev.txt
scripts/jobagent_python.sh -m unittest discover -s tests
```

If a Homebrew Python fails with a `pyexpat` / `Symbol not found` error, repair/update Python and expat, or use a Python distribution with matching libraries. This is an environment issue; do not disable tests. The Bash wrapper supports the usual Homebrew expat library location if installed. On an affected Apple Silicon Homebrew installation, after confirming expat is installed, create the virtual environment with `DYLD_LIBRARY_PATH="$(brew --prefix expat)/lib" python3 -m venv .venv`, then install dependencies through the wrapper as above.

For job searching you do **not** need LibreOffice, Docker, a resume template, a LinkedIn export, or JobTracker. Optional Word-document production requires a verified DOCX source and LibreOffice plus Poppler (`pdftoppm`), or Docker with the renderer built from `docker/resume-renderer`. Document production has additional template requirements described below.

## Instructions for the assistant: start here

Treat this README as the user's request to set up this checkout and conduct their first job search. Work inside the clone. Read the implementation and local instructions before executing commands. Do not assume the user's identity, career, salary, country, language, technical stack, employer preferences or access to any connector.

1. Check the working directory, Python version, dependency installation and tests. Explain any installation issue clearly. Use the virtual environment for every command.
2. Check whether private configuration already exists. If it does, offer to continue or review that setup; never overwrite it or mix two candidates' data. The setup generator refuses overwrites. For a fresh candidate use a separate clone.
3. Interview the user in short groups of 2–4 questions, waiting for answers between groups. Resume answers already given. Explain why a missing answer matters. Save confirmed answers in ignored `setup.answers.json`, using `setup.answers.example.json` as the schema. Do not use sample text as candidate evidence.
4. Summarize the evidence, search boundaries, direct titles and adjacent titles. Get corrections before writing the runnable configuration. Distinguish professional experience, training, personal projects, limited exposure and skills the user must not claim.
5. Research employers and build the bounded site list described below. Generate private configuration, run a small first search, inspect actual results, and then offer the full search and scheduling setup.

### Interview questions

Ask all applicable questions; do not infer qualifications from a job the candidate likes.

- **Resume and evidence:** Can you upload your resume or point to a local file? Copy it only into ignored `Source Docs/` with permission. If no resume is available, interview them about work history instead; discovery can still run. What name/email should local records use? What was your last role, scope, industry, level and employment period? What did you personally do, and what measurable outcomes can you substantiate? Which statements may be reused in cover letters? Do not invent years, metrics, degrees, employers or certifications.
- **Roles and strengths:** Which titles interest you? What are your strongest skills and recent hands-on areas? Which are limited exposure or training only? What should never be claimed? Are you seeking individual-contributor, management, entry-level or career-change roles? What adjacent roles would you consider? Propose adjacent titles with an evidence-based explanation and ask for confirmation before adding them.
- **Employers and examples:** Which companies, industries and company sizes interest you? Which should be excluded? Share URLs of jobs you liked or applied to. Capture why they appealed; check live official postings. Keep already-applied jobs separate from merely interesting jobs, including exact URL/requisition, company, title, status and application date (unknown dates stay blank). Ask explicitly whether there are prior applications before recording an empty history.
- **Location:** What city do you live in, and what major metro should the search use? Which surrounding cities are within your actual commute? Do you want onsite, hybrid, remote, or several? How much office attendance and travel is acceptable? Would you relocate, and where? Which countries may you legally work in? Is sponsorship required? Remote does not mean unrestricted worldwide work.
- **Language:** Which languages can you speak and write, at what professional proficiency? Which mandatory language requirements would be blockers? There is no default German rejection. Compare each posting's spoken/written requirements with the confirmed proficiency; ask about an unlisted required language rather than assume proficiency.
- **Terms:** What minimum compensation, currency and pay period apply? Convert to an annual base only with a stated, confirmed conversion; `salary_floor: 0` means no minimum. What employment types, hours, shifts, clearance/licensing constraints or other dealbreakers apply? How should unpublished pay be handled? Unknown requirements stay questions.
- **Cadence and outputs:** What timezone, days and local time should searches run? What runtime/site budget do you prefer? Would you like results in Markdown/HTML? Are documents wanted later? Keep `schedule_enabled` and `document_generation` false during onboarding. Ask about optional networking exports or tracker integration only if wanted; never require them.

Store extra preferences and proficiency detail in `candidate_evidence.json` and `search_rules.md` after generation. Those files are authoritative for the assistant's full-posting review. The keyword screener does not fully interpret licensing, sponsorship, pay currencies, shifts, travel, or all natural-language requirements.

### Research the search sites and roles

Use live internet searches informed by the confirmed titles, skills, languages, country, metro and work model. Start with the user's companies, then find employers hiring for direct and agreed adjacent roles. For each employer inspect its official careers page and current role/location evidence; save `name`, `careers_url`, `evidence_url`, `reason`, and `verified_on` (YYYY-MM-DD).

Use **at most 200 distinct search sites total**, including LinkedIn and any supplementary boards or startup sites. The generator allows 1–199 deduplicated employer sites plus LinkedIn. Fewer well-supported sites are preferable to invented or irrelevant sites; 200 is a ceiling, not a quota. Deduplicate shared company boards. Record inaccessible or unverified prospects separately in local notes, not as verified companies. Never make up ATS endpoints. Explain source coverage and gaps.

Open [LinkedIn Jobs](https://www.linkedin.com/jobs/) in the assistant's **internal browser** for additional hits. Let the user sign in themselves when needed. If the assistant lacks an internal browser, say so and let the user review the search links and provide URLs. Login or browser unavailability must be reported explicitly; it does not block official ATS discovery. Resolve useful LinkedIn or aggregator findings to live employer/ATS pages before recommending them. Do not apply, message recruiters, scrape private profiles, or bypass site access restrictions.

Put verified employer entries in `setup.answers.json`. After generation, populate `ats_registry.yml` only with verified supported ATS URLs; unsupported websites remain browser-assisted search tasks. Move startup employers between the company list and optional watchlist without increasing the total site budget. Keep `job_sources.yml` synchronized and do not add a second copy of an existing site. Run `scripts/jobagent_python.sh scripts/setup_jobagent.py --check` before each search; the check includes ATS URLs, so reduce the shortlist if adding boards would exceed 200.

### Generate the candidate's files

Once the user has reviewed the interview summary and employer shortlist:

```bash
scripts/jobagent_python.sh scripts/setup_jobagent.py --answers setup.answers.json
```

The checked-in example contains empty required fields and cannot be run as a candidate profile. The generator validates types, required answers, timezone, URL format, site limit, duplicates and conflicting languages before writing. It refuses to replace any existing output. It does not itself verify that a website is live; the assistant must inspect the saved evidence URLs.

| Private file | Contents and interview source |
| --- | --- |
| `setup.answers.json` | Confirmed interview answers; kept for audit/resumption |
| `candidate_profile.yml` | Direct/adjacent titles, skills, exclusions, countries, commute, work models, freshness and compensation |
| `candidate_workflow_profile.yml` | Confirmed strengths, limited exposure, do-not-claim skills, salary, language/location gates and exact-posting gap answers |
| `candidate_evidence.json` | Identity, last role, truthful reusable statements, resume path, authorization and other interview facts |
| `companies.yml` | Verified, reasoned employer shortlist |
| `job_sources.yml` | Employer sites plus LinkedIn; maximum 200 distinct sites across all search inputs |
| `ats_registry.yml` | Initially empty; add only verified ATS board mappings for selected employers |
| `startup_watchlist.yml` | Initially empty; optional candidate-specific rotating employer list |
| `manual_job_urls.yml` | Official posting URLs found by the candidate/browser, excluding aggregator-only leads |
| `job_overrides.yml` | Initially empty; later explicit posting-specific skip decisions or dated overrides |
| `applied_jobs.json` | Local application history; updates require the candidate's actual application evidence |
| `search_rules.md` | Evidence/verification rules plus candidate-specific constraints and unresolved preferences |
| `run_preferences.json` | Timezone, proposed cadence, freshness and disabled-by-default scheduling/documents |
| `resume_templates.json` | Initially empty; populate only for validated optional DOCX templates |
| `.env.local` | Optional local environment settings; no source/API credentials required |

All these files, resumes and `outputs/` are ignored. The filename is **`candidate_profile.yml`**, not `candidate_profile.yaml`. Run `git status --short` after setup; private answers and generated files must not appear as additions. Never force-add them.

### First run: prove the setup with real results

1. Generate the plan:

   ```bash
   scripts/jobagent_python.sh scripts/setup_jobagent.py --check
   scripts/jobagent_python.sh scripts/search_jobs.py --out outputs
   ```

2. Read the dated plan. For the first run select 1–3 researched employers, run official discovery, and perform the LinkedIn/browser pass. Use the plan's commands with `--limit-companies 3 --no-rotation` on discovery. Use `freshness_days` from `run_preferences.json`; do not substitute an inherited default. The local scripts date outputs using the operating-system date: verify it agrees with the configured timezone before starting and restart in a new output directory if the date changes during a run.
3. Inspect the discovered JSON, errors and live official postings. Correct missed employer board mappings. Record employment/authorization/language/experience gaps in `candidate_workflow_profile.yml` before strict review. `do_not_claim` is candidate-wide; `gap_resolutions` is keyed by exact posting URL. Supported answers are `do_not_claim`, `limited_exposure: explanation`, `professional_evidence: explanation`, or posting-specific `posting_false_positive: explanation`. Never mark a required gap resolved based only on training or a high score.
4. Reconcile the local applied history and run the strict reviewer using the dated commands in the plan. No SSH server is needed. Open `outputs/YYYY-MM-DD-workflow-review.html`; show the candidate recommended roles, unresolved questions, rejected roles and reasons, already-applied matches, source failures and coverage. A plan or empty report template does not count as a completed live search. Zero matches is valid; failed/unavailable sources must not be presented as “no jobs.”
5. Review several results with the user. Confirm titles, location, language and qualifications actually fit. Adjust private configuration from their answers. Then run the remaining chosen sites. Give URLs and clear next actions, never claim an application was submitted.
6. After the first run is accepted, confirm the proposed cadence and configure the assistant's supported scheduler with `daily_job_search_prompt.md`, the absolute clone path and the chosen timezone. If no scheduler exists, say so and provide a manual-run reminder. Writing `run_preferences.json` alone does **not** schedule anything. Record the scheduler ID and next run only after successful creation. Notify on new actionable roles, meaningful changes, failures or questions; do not spam unchanged status.

### Optional capabilities

The ATS adapters include Workday, Greenhouse, Lever, SmartRecruiters, Workable, Ashby, ADP, iCIMS and some employer-specific parsers. Website coverage varies. The sharded scanner supports daily listing checks, rotating deep scans and carried-forward failures. The reviewer deduplicates postings, carries unresolved jobs forward, separates required/preferred gaps and excludes applied requisitions.

Resume and cover-letter production is optional and deliberately separate from first-run discovery. Existing DOCX tooling expects specific document structure: template headline, summary prefix, checksum and a two-column contact header for cover-letter sender extraction. Arbitrary uploaded PDFs/DOCX files are **not** automatically compatible. Inspect and adapt a copy with the user's approval, preserve the original, populate `resume_templates.json` using `resume_templates.example.json`, set `default_resume_template` and contact settings, and validate rendering before enabling production. Cover-letter statements come only from `candidate_evidence.json`. Never claim document support has passed without actually rendering and inspecting the output.

Only `outputs/YYYY-MM-DD-resume-ready-jobs.md` can authorize documents; prior applications remain excluded. Build the optional isolated renderer with:

```bash
docker build --tag jobsearchagent-libreoffice-renderer:1.0.0 docker/resume-renderer
```

Local networking reports can use a voluntarily supplied LinkedIn export. Notes are drafts; nothing is sent automatically. `mark_applied_jobs.py` also supports an explicitly configured JobTracker SSH host. `publish_skill_gap_analytics.py` is an optional exporter/integration; no server URL, token or publication is required for onboarding. The deterministic runner under `poc/` is an advanced experiment with additional integration requirements, not the default setup path.

## Development and release validation

```bash
scripts/jobagent_python.sh -m unittest discover -s tests
scripts/jobagent_python.sh -m coverage run --source=scripts -m unittest discover -s tests
scripts/jobagent_python.sh -m coverage report
```

Tests must use synthetic profiles and temporary directories, not local candidate inputs. Keep server changes at 90% coverage or higher; measure new onboarding logic independently as well as reporting full-suite coverage. Never label a mocked ATS test as a live search.

For the maintainer's acceptance test: prepare the sanitized public branch, push it explicitly, clone that branch into another directory, install from this README, and paste this README into a new chat. Complete a fresh interview and first live search there. Verify private files stay ignored and no original-candidate preference appears. Local unit/integration results do not replace this human walkthrough.

**Publication and history:** deleting a private file in a normal branch does not remove it from Git history. Publish a reviewed clean-history snapshot to a separate public repository/branch without merging private history. Making an existing repository public exposes its other branches and history too. Inspect the exact release tree and history for personal files and credentials before publishing; do not push tags or old branches with it.
