# Governance

`data/catalog/governance.yaml` holds jurisdictions, controls, policies and catalog entries. The app shows them in the **Evidence catalog** and **Cost & governance** tabs.

> Results are **demonstration evidence** from a simulation of a fictional facility. They are not a compliance verdict for any real facility, operator or AI service.

## Jurisdictions

The jurisdiction list is configuration-driven: add one under `jurisdictions:` and give it entries or explicit gap entries. Each entry must reference a known jurisdiction, control and source, and validation fails otherwise.

| Code | Jurisdiction | Role | Coverage (catalog 2026-11-10) |
|---|---|---|---|
| EU | European Union | Applies to the reference facility | 5 entries, all verified from primary text |
| US | United States | Comparison | 2 entries, verified |
| AU | Australia | Comparison | 3 entries: NABERS verified; SOCI and NGER partially verified |
| JP | Japan | Comparison | 2 entries: ANRE data-centre measures verified; METI AI Guidelines partially verified (press release) |
| IL | Israel | Comparison | 2 entries: AI policy verified (official summary); Privacy Amendment 13 partially verified (PPA guidance) |
| IE | Ireland (added by configuration) | Location of the reference facility | CRU2025236 decision verified from the official decision paper |

No initial jurisdiction is left without a verified entry. Every partial verification records in its `uncertainty` field what was and was not read.

## Entries

Each entry records jurisdiction, level, issuing authority, instrument, source and URL, article or section, publication and effective dates, verification date and status, scope, domain (facility or AI service), legal status (binding, official guidance or voluntary standard), applicability to the reference scenario, related control, expected evidence, and uncertainty.

| ID | Instrument | Section | Domain | Status | Applicability |
|---|---|---|---|---|---|
| GOV-EU-01 | Directive (EU) 2023/1791 (EED recast) | Art. 12(1): reporting ≥ 500 kW IT, from 15 May 2024 | facility | binding | applies |
| GOV-EU-02 | Delegated Reg. (EU) 2024/1364 | Art. 3; Annex I–III: KPIs to the EU database from 15 Sep 2024, then 15 May yearly | facility | binding | applies |
| GOV-EU-03 | Directive (EU) 2015/2193 (MCPD) | Art. 2(1) 1–50 MW thermal; Art. 6 limited-hours exemption (500 h/yr) | facility | binding | may apply |
| GOV-EU-04 | Directive (EU) 2022/2555 (NIS 2) | Art. 21; data centre service providers | AI service | binding | may apply |
| GOV-EU-05 | Regulation (EU) 2024/1689 (AI Act) | Art. 113 dates; Art. 53 GPAI; Art. 26 deployers | AI service | binding | unknown (role and risk class not determined) |
| GOV-IE-01 | CRU2025236 Large Energy Users connection policy (s.34 direction; supersedes CRU/21/124) | §3.3–3.5: ≥ 10 MVA dispatchable onsite/proximate capacity matching MIC; ≥ 80% additional Irish renewables | facility | binding | applies |
| GOV-US-01 | 40 CFR 60 Subpart IIII | § 60.4211(f): 100 h/yr maintenance and testing | facility | binding | comparison |
| GOV-US-02 | NIST AI RMF 1.0 | Framework core | AI service | voluntary | comparison |
| GOV-AU-01 | Security of Critical Infrastructure Act 2018 | s12F; s30AC | AI service | binding | comparison |
| GOV-AU-02 | NGER Act 2007 | — | facility | binding | comparison |
| GOV-AU-03 | NABERS for Data Centres (Energy) | 1–6 stars; verified PUE | facility | voluntary (inferred, see uncertainty) | comparison |
| GOV-JP-01 | Act on Rationalizing Energy Use and Shifting to Non-fossil Energy, data-centre measures | PUE ≤ 1.4 target by FY2030; PUE ≤ 1.3 for new builds from FY2029; in force April 2026 | facility | binding | comparison |
| GOV-JP-02 | AI Guidelines for Business Ver 1.0 (19 Apr 2024) | — | AI service | guidance | comparison |
| GOV-IL-01 | AI Policy on Regulation and Ethics, Decision 212 (17 Dec 2023) | Main recommendations ("soft" regulation) | AI service | guidance | comparison |
| GOV-IL-02 | Privacy Protection Law, Amendment 13 (in force 14 Aug 2025) | — | AI service | binding | comparison |

Subnational requirements are not catalogued yet: Irish transposition and local permitting, and US state air permits. The entries' uncertainty fields record this. Amendments to the AI Act after the published 2024 text were not checked.

## Controls and demonstration evidence

| Control | What the demo shows | Where |
|---|---|---|
| CTL-EN-01 Energy and water KPI reporting | PUE/WUE and energy totals with formula and inputs | Cost & governance → Metric detail |
| CTL-AIR-02 Backup generator hours and emissions | Run time, fuel, CO₂ and NOx estimate; CHK-02 and CHK-06 | Cooling/environment; Security & observability |
| CTL-SEC-03 Cybersecurity risk management | Alert feed; CHK-03 critical alerts have a response; CHK-07 auth service powered | Security & observability |
| CTL-AI-04 AI service oversight and accountability | Decision log: every shed, route, restore and shutdown names an owner, a policy and the evidence event (CHK-05) | Cost & governance |
| CTL-GRID-05 Grid connection conditions | Demand-reduction handling and firm-import cap (CHK-01). CHK-09 compares on-site dispatchable capacity (18 MW generators + 6 MW battery) with the 34 MW connection and reports a **Gap** for the fictional configuration. This is demonstration evidence, not a verdict. | Power & facility; Security & observability |
| CTL-PRIV-06 Privacy by design in logs | Requests logged by correlation ID only; no prompt or user fields (CHK-04) | Security & observability → event export |

## Operating policies

| Policy | Owner | Control |
|---|---|---|
| POL-LS-01 Load-shedding priority | Facility shift lead | CTL-GRID-05 |
| POL-RC-02 Staged recovery | Facility shift lead | CTL-AI-04 |
| POL-SV-03 Model routing under constraint | SRE on call | CTL-AI-04 |
| POL-EM-04 Islanding and backup dispatch | Electrical duty engineer | CTL-AIR-02 |
| POL-EM-05 Controlled shutdown | Incident commander | CTL-AI-04 |
| POL-DR-06 Grid operator instructions | Energy desk | CTL-GRID-05 |

Owners and policies are fictional roles for the demonstration.
