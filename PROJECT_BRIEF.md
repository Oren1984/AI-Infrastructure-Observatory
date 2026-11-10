# Behind the Chat — AI Infrastructure Observatory

| | |
|---|---|
| Repository | `AI-Infrastructure-Observatory` |
| Portfolio category | System Design & Architecture |
| Project type | Interactive infrastructure simulation POC |
| Version | V1.0 |
| Credit | Developed and built by Oren Salami |

## What, why, and how

Build an interactive Streamlit application that reveals the infrastructure behind an AI chat request. Start with the user's message, trace it through the AI service and data center, and show how power, cooling, and operational decisions change the response the user sees.

The demo should answer a concrete question: if the same user sends the same request during normal operation, a capacity shortage, and a grid outage, what stays online, what slows down, and what does each operating decision cost the operator, the environment, and the surrounding community?

Use one fictional reference facility in one clearly identified real region, a deterministic simulation, and versioned public reference data. Keep this brief at the repository root as the public implementation specification and link to it from the README.

## 1. Visitor experience

Build a polished Streamlit interface with Plotly charts, consistent spacing and typography, accessible status labels, and clear units. Use color alongside text and icons. Verify the layout at desktop and narrow browser widths. Make the main experience discoverable from a single landing view.

- Place a simulated chat and the infrastructure overview side by side. Provide a prefilled request, a **Send request** action, and a **Run scenario** control.
- Show the current operating state, source of power, available capacity, cooling status, active alert, and user-visible response side by side.
- Provide play, pause, step, reset, and replay. Use the same request and workload profile across scenarios so the difference is attributable to the operating conditions.
- Provide a scenario comparison view and a metric detail panel with the input data, calculation, units, source, reference date, and assumptions.
- Use an in-app Help Center to explain the three operating states, essential terms, how to read the charts, how to replay a scenario, and the distinction between public reference data and simulated telemetry.

## 2. Simulate three operating states

| State | Power and cooling | AI service and user experience |
|---|---|---|
| Normal | Grid-connected operation. Adjust the modeled generation mix as demand, availability, cost, and emissions change. Allow concurrent contributions from natural gas, oil-derived fuels, hydro, wind, and solar. | Serve the baseline chat profile. Display latency, throughput, queue depth, accelerator utilization, and GPU memory usage. |
| Capacity constrained | A loss of generation or a demand spike creates a power or cooling shortfall. Redispatch eligible sources and shed discretionary workloads in priority order when necessary. | Pause training, batch jobs, and optional work first. Reduce concurrency or route to a configured smaller model when needed. Show how these choices change latency, queueing, and service quality. |
| Emergency | Simulate grid loss, UPS ride-through, local generation or battery dispatch, and standby generator startup. Track generator capacity, fuel, battery state of charge, and cooling headroom. Include sustained shortfall, controlled shutdown, and staged recovery. | Preserve the minimum viable inference stack while its dependencies have capacity. Surface degraded service or an explicit unavailable response when the remaining budget cannot support inference. Restore workloads in priority order after the system stabilizes. |

Implement an illustrative cascade in which gas or oil-derived generation falls out, then hydro, wind, and solar become unavailable in sequence. The cascade shows what the system tries and why. Before islanding, these sources can contribute to the modeled grid mix. After islanding, only assets explicitly configured as electrically reachable and capable of serving the facility may supply it. The generator can start immediately after UPS bridging if local conditions require it. Show a reason code whenever a source is dispatched, exhausted, unavailable, or unreachable.

Define transition and recovery thresholds in scenario configuration. Model power and energy separately, enforce capacity limits, and prevent storage or fuel from going below zero. Compute a critical-load budget covering power distribution, essential cooling, network and security services, serving nodes, accelerators and GPU memory, required storage, and minimum observability. Gate inference on these dependencies. Apply input validation and handle zero available capacity explicitly.

## 3. Show the path from the facility to the user

Use a live request trace with a correlation ID. Highlight the request as it moves through ingress, authentication or policy checks, routing, queueing, model serving, inference, and response. Show the physical dependencies alongside the trace and explain the effect of each constraint.

| View | Data and visual treatment |
|---|---|
| Power and facility | Stacked area chart for generation contributions. Overlay demand and available supply. Show separate indicators for grid connection, local dispatch, UPS, battery, and generator. Label MW and MWh distinctly. |
| AI service | Request flow and line charts for latency, throughput, queue depth, GPU utilization, and memory. Show the model profile and list active, deferred, and suspended workloads. |
| Cooling, environment, and community | Compare cooling headroom, direct facility water use, estimated water used in electricity generation, emissions, and generator run time. Show local air quality, noise, and community effects only when supported by relevant data or explicitly labeled assumptions. Separate greenhouse-gas estimates from local air pollution. |
| Cost and governance | Compare modeled capital and operating costs by category. Show electricity, water, fuel, and maintenance assumptions with currency, price basis, period, and location. Tie operational decisions to owner, policy, and evidence. |
| Security and observability | Show service health, simulated control checks, utilization history, an alert feed, and a filterable event timeline. Include severity, affected service, threshold, response, and recovery. |
| 2023–2030 | Build an interactive timeline for planning, site selection, permits, utility connection, construction, commissioning, go-live, and expansion. Distinguish published historical evidence, announced plans, and forward scenarios. Keep the timeline separate from the incident simulation clock. |

For environmental and cost results, state the calculation boundary. Distinguish generator emissions from grid electricity emissions and avoid double counting. Keep water withdrawal and water consumption distinct when sources define them differently. Use explicit unknown values for missing measurements.

## 4. Build a traceable evidence and governance catalog

Start with the European Union, United States, Australia, Japan, and Israel. Make additional jurisdictions configuration-driven. Use official primary sources and verify applicability, effective dates, and the relevant article or section before publication. Account for national and subnational requirements where the reference scenario depends on location.

Each entry records jurisdiction, issuing authority, instrument, official URL, article or section, publication and effective dates where available, verification date, scope, legal status, related control, and evidence expected. Distinguish binding requirements, official guidance, and voluntary standards. Separate facility requirements such as permitting, energy or water reporting, and backup generation from AI service requirements such as security, oversight, or operational accountability. Record uncertainty or missing evidence explicitly. Describe control results as demonstration evidence, without issuing a compliance verdict.

Use the following as starting points and record exact editions and excerpts in the data catalog.

- [IEA — Energy and AI](https://www.iea.org/reports/energy-and-ai) for electricity demand and scenarios.
- [Lawrence Berkeley National Laboratory — United States Data Center Energy Usage Report](https://bies.lbl.gov/publications/2024-lbnl-data-center-energy-usage-report) for energy and water methodology.
- [EUR-Lex — EU 2024/1364](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202401364) as a starting point for data center sustainability indicators.
- [U.S. Department of Energy — Islanding a Microgrid](https://www.energy.gov/cmei/femp/articles/islanding-microgrid) for the conceptual distinction between grid-connected and islanded operation.
- [Claude Dashboards walkthrough](https://www.youtube.com/watch?v=EFc5FfhXxT0) as product-design inspiration for exposing the calculation behind a displayed metric.

Classify every dataset, KPI, and timeline milestone as published data, scenario assumption, or simulated result. Ship a curated local snapshot so the demo runs without paid APIs or live dependencies. If an official source cannot be verified, identify the gap rather than inferring an obligation or inventing a value.

## 5. Architecture and operational logs

Implement one Dockerized Python application with a Streamlit entry point, Plotly charts, and small, testable OOP modules. Keep simulation, energy dispatch and load priority, AI service capacity, metrics, event recording, and source and governance catalogs outside the UI. Use composition and explicit inputs where it keeps the design readable. Local versioned fixtures and session-scoped run state are sufficient for V1.0.

Use a deterministic simulation clock and explicit scenario inputs. UI reruns must preserve the current run and avoid duplicate transitions and alerts. Support a clean reset and independent runs across sessions.

Record structured events with run ID, simulated timestamp, request ID when applicable, component, severity, event type, reason, observed value and unit, threshold, action, and outcome. Expose filtered events in the dashboard and support CSV or JSON export. Deduplicate repeated alerts and record recovery. Avoid logging prompt text, credentials, or user identifiers. Keep demo controls and system actions simulated. Provide a Compose health check and start the complete application with:

```bash
docker compose up --build
```

## 6. Repository deliverables

- `README.md` — a short, human-written explanation of what the project does, why it matters, and how to run the demo. Include a compact Mermaid flowchart linking request flow to facility dependencies, screenshots from the running app, and the V1.0 author credit.
- `PROJECT_BRIEF.md` — this public specification at the repository root.
- `docs/ARCHITECTURE.md` — module boundaries, request path, runtime and data flow.
- `docs/SCENARIOS.md` — the three states, dispatch rules, critical-load order, thresholds, and recovery.
- `docs/DATA-SOURCES.md` — data provenance, units, formulas, assumptions, and known limitations.
- `docs/GOVERNANCE.md` — jurisdictional references, applicability, controls, and demonstration evidence.
- `docs/images/` — three or four real screenshots, covering the overview, emergency behavior, and at least one evidence or governance view. Include meaningful alt text where they appear.
- Application source, local fixture data, essential regression tests, Dockerfile, Compose configuration, and a focused `.gitignore`.

## 7. Implementation and acceptance

1. Inspect the repository and preserve existing user work. Set up the runnable app and the shared scenario schema.
2. Implement and verify the simulation, dispatch, service dependencies, event model, and metrics before connecting them to the dashboard.
3. Build the overview, scenario comparison, request trace, observability, governance, timeline, and Help Center. Load verified reference data and label fixtures clearly.
4. Run the complete application with Docker Compose, exercise the UI, capture screenshots from the running app, and finish the README and four docs.

The work is complete when the following checks pass.

- A fresh checkout reaches a healthy dashboard from one Compose command.
- The same request can be demonstrated in all three states. The emergency walkthrough covers grid loss, UPS bridging, local backup, service degradation, controlled shutdown if capacity is exhausted, and recovery.
- Automated checks cover dispatch limits, islanding eligibility, the critical-load order, repeatable replay, alert deduplication, and shutdown and recovery. The UI shows the same state and values as the engine output.
- A visitor can trace a displayed number to its source or formula, replay an incident, and tell which 2023–2030 milestones are observed, announced, or hypothetical.
- All five initial jurisdictions have verified entries or explicitly marked coverage gaps. All four docs, the Help Center, screenshots, and the README flowchart match the running app.

Report the implementation, actual verification results, and any remaining data or execution gaps in the handoff. Keep any unverified claims clearly identified.
